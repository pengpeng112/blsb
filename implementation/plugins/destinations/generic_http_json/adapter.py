"""通用 HTTP JSON 推送适配器（LT-03；标准库 urllib，无新依赖）。

语义（照 inf_pusher 实测口径）：
- 回执规则命中 → 对应 outcome；
- errorCode 01/99（服务端异常）→ RETRYABLE；HTTP 408/429/5xx、网络异常 → RETRYABLE；
- 其它 errorCode / 其余 4xx → REJECTED（业务拒绝，不重试）；
- 响应非 JSON → UNKNOWN("响应不是 JSON")；
- **禁止重定向**（LT-03-R1/R02）：任何 3xx 一律 REJECTED，不逐跳跟随——
  原始 URL 白名单校验覆盖不了 urllib 的自动跳转，重定向可能把凭据/报文
  转发到未核准主机，因此服务端直接拒绝跟随；
- **POST 不确定结果不盲重发**（LT-03-R1/R02）：超时/断连/5xx/服务端异常都
  可能已被对端接收。默认（未证实对端幂等）第一次 RETRYABLE 即 **UNKNOWN**
  （进入查询/人工核查，不自动再次 POST）；仅配置 idempotency_contract=
  "CONFIRMED"（已证实对端按 Idempotency-Key 幂等）时按同冻结请求退避重试。
  `Idempotency-Key` 请求头本身不构成对端幂等保证。
每次 send 前对最终 URL 做白名单二次校验（防 path_template 注入）。
on_attempt 回调（LT-03-R1）：每次尝试（含重试）各回调一次，供服务层逐次
落 DeliveryAttempt 审计行——不能只留最后一次而丢前序尝试。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse

from connector_sdk.destination import (OUTCOMES, ReceiptRule, SendResult,
                                       apply_receipt_rules)
from connector_sdk import CapabilityError

from .config_schema import render_path, validate_config
from .rate import bucket_for
from .redact import redact_text


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """禁止自动跟随重定向（R02）：redirect_request 返回 None 时 urllib 对
    3xx 抛 HTTPError，由 _attempt_once 归类为 REJECTED（不触网跟随）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_NoRedirectHandler)

DEFAULTS = {
    "method": "POST",
    "path_template": "/",
    "headers": {"Content-Type": "application/json"},
    "timeout_seconds": 15,
    "max_attempts": 3,
    "backoff_seconds": [2, 4, 6],
    "rate_limit_per_minute": 60,
    "receipt_rules": [],
    "default_outcome": "UNKNOWN",
    "error_code_path": "errorCode",
    "error_message_path": "desc",
    "remote_ref_path": None,
}


def _cfg_with_defaults(config: dict) -> dict:
    cfg = {**DEFAULTS, **(config or {})}
    rules = []
    for r in cfg.get("receipt_rules") or []:
        rules.append(ReceiptRule(json_path=r.get("json_path", ""),
                                 op=r.get("op", "exists"),
                                 value=r.get("value"),
                                 outcome=r.get("outcome", "ACCEPTED")))
    cfg["_rules"] = rules
    return cfg


def _url_allowed(cfg: dict, url: str) -> bool:
    """最终 URL 二次白名单校验（send 前必过；防 path_template/base_url 拼接注入）。"""
    p = urlparse(url)
    schemes = cfg.get("allowed_schemes") or ["http", "https"]
    hosts = cfg.get("allowed_hosts") or []
    ports = cfg.get("allowed_ports") or []
    port = p.port or (443 if p.scheme == "https" else 80)
    return (p.scheme in schemes and p.hostname in hosts and port in ports
            and not p.username and not p.password and p.query == ""
            and p.fragment == "")


def _resolve_credential(cfg: dict) -> tuple[str | None, str]:
    """env:VAR 引用 → (header值, 错误)；变量缺失返回 (None, "凭据未配置: VAR")，不回显值。"""
    ref = cfg.get("credential_ref")
    if not ref:
        return "", ""
    var = str(ref[4:])
    val = os.environ.get(var, "")
    if not val:
        return None, f"凭据未配置: {var}"
    return val, ""


class GenericHttpJsonAdapter:
    """实现 DestinationAdapter 协议；sleeper 可注入（测试用假时钟）。

    用法：
        ad = GenericHttpJsonAdapter(sleeper=fake_sleep)
        errs = ad.validate_config(cfg)
        res = ad.send(cfg, payload, idem_key)
    """

    plugin_id = "destination.generic_http_json"

    def __init__(self, sleeper=time.sleep):
        self._sleep = sleeper

    def capabilities(self) -> set[str]:
        return {"send"}          # 查询平台各异，通用适配器暂只声明发送

    def validate_config(self, config: dict) -> list[str]:
        return validate_config(config)

    # ---------------------------------------------------------------- send
    def send(self, config: dict, payload: dict, idempotency_key: str,
             on_attempt=None) -> SendResult:
        """执行发送。on_attempt(SendResult) 在每次尝试后回调（含最终那次）。

        重试语义（LT-03-R1/R02）：
        - 未证实对端幂等（idempotency_contract 缺省）→ 第一次 RETRYABLE 类
          结果直接 UNKNOWN，**不再次 POST**（对方可能已收到，盲重发=重复上报）；
        - idempotency_contract="CONFIRMED" → 按 backoff 用同一冻结请求重试，
          耗尽 → UNKNOWN。
        """
        cfg = _cfg_with_defaults(config)
        errs = validate_config(config)
        if errs:
            return SendResult(outcome="UNSUPPORTED",
                              error_message=redact_text("；".join(errs)))
        base = str(cfg.get("base_url", "")).rstrip("/")
        ok, path, err = render_path(str(cfg.get("path_template", "/")), payload)
        if not ok:
            return SendResult(outcome="UNSUPPORTED",
                              error_message=redact_text(err))
        url = base + path
        if not _url_allowed(cfg, url):
            return SendResult(
                outcome="UNSUPPORTED",
                error_message="最终 URL 不在白名单（scheme/host/port 二次校验失败）")
        cred, cred_err = _resolve_credential(cfg)
        if cred is None:
            return SendResult(outcome="UNSUPPORTED",
                              error_message=cred_err)   # 不含任何值
        bucket_for(str(config.get("_dest_id", idempotency_key)),
                   int(cfg.get("rate_limit_per_minute", 60))
                   ).take_delay(self._sleep)

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {str(k): str(v) for k, v in
                   (cfg.get("headers") or {}).items()}
        if cred:
            headers["Authorization"] = cred
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("Idempotency-Key", idempotency_key)

        timeout = float(cfg.get("timeout_seconds", 15))
        max_attempts = int(cfg.get("max_attempts", 3))
        backoff = list(cfg.get("backoff_seconds", [2, 4, 6]))
        rules = cfg.get("_rules") or []
        default_outcome = str(cfg.get("default_outcome", "UNKNOWN"))
        ec_path = str(cfg.get("error_code_path", "errorCode"))
        em_path = str(cfg.get("error_message_path", "desc"))
        rr_path = cfg.get("remote_ref_path")

        attempts = 0
        contract = str(cfg.get("idempotency_contract") or "")
        last: SendResult | None = None
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            result = self._attempt_once(url, cfg.get("method", "POST"), headers,
                                        body, timeout, rules, default_outcome,
                                        ec_path, em_path, rr_path, attempt)
            if on_attempt is not None:
                try:
                    on_attempt(result)
                except Exception:  # noqa: BLE001 —— 审计回调不影响发送语义
                    pass
            if result.outcome != "RETRYABLE":
                result.attempts = attempts
                return result
            last = result
            if contract != "CONFIRMED":
                # LT-03-R1（R02）：未证实对端幂等——POST 结果不确定（可能已被
                # 接收）→ 立即 UNKNOWN 交查询/人工核查，不自动再次 POST。
                return SendResult(
                    outcome="UNKNOWN",
                    http_status=result.http_status,
                    error_code=result.error_code,
                    error_message=redact_text(
                        f"POST 结果不确定且未证实对端幂等（无 idempotency_"
                        f"contract=CONFIRMED），不自动重发：{result.error_message}"),
                    retry_after_seconds=result.retry_after_seconds,
                    attempts=attempts)
            if attempt < max_attempts:
                delay = backoff[attempt - 1] if attempt - 1 < len(backoff) \
                    else (backoff[-1] if backoff else 0)
                if result.retry_after_seconds:
                    delay = max(delay, result.retry_after_seconds)
                self._sleep(max(0, delay))
        assert last is not None
        # RETRYABLE 耗尽（已证实幂等契约下的同冻结请求重试）：仍无确定结果
        # → UNKNOWN；429 的 Retry-After 保留传递
        return SendResult(
            outcome="UNKNOWN",
            http_status=last.http_status,
            error_code=last.error_code,
            error_message=redact_text(f"重试 {attempts} 次后仍无确定结果："
                                      f"{last.error_message}"),
            retry_after_seconds=last.retry_after_seconds,
            attempts=attempts)

    def _attempt_once(self, url, method, headers, body, timeout,
                      rules, default_outcome, ec_path, em_path, rr_path,
                      attempt) -> SendResult:
        try:
            req = urllib.request.Request(url, data=body, method=method,
                                         headers=headers)
            with _OPENER.open(req, timeout=timeout) as resp:
                status = resp.status
                raw = resp.read().decode("utf-8", "replace")
                retry_after = resp.headers.get("Retry-After")
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = ""
            try:
                raw = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            retry_after_hdr = exc.headers.get("Retry-After") if exc.headers else None
            if 300 <= status < 400:
                # 重定向被禁止（R02）：不逐跳跟随，未核准主机可能借此截获
                # 凭据/报文；归类 REJECTED（fail-closed，不发第二次请求）
                return SendResult(outcome="REJECTED", http_status=status,
                                  error_message=redact_text(
                                      f"HTTP {status} 重定向被禁止"
                                      f"（不跟随 Location，未核准主机不转发）"),
                                  attempts=attempt)
            if status in (408, 429) or status >= 500:
                ra = None
                if retry_after_hdr and retry_after_hdr.isdigit():
                    ra = int(retry_after_hdr)
                return SendResult(outcome="RETRYABLE", http_status=status,
                                  error_message=redact_text(
                                      f"HTTP {status}（可重试）"),
                                  retry_after_seconds=ra, attempts=attempt)
            return SendResult(outcome="REJECTED", http_status=status,
                              error_message=redact_text(f"HTTP {status}（请求被拒绝）"),
                              attempts=attempt)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return SendResult(outcome="RETRYABLE",
                              error_message=redact_text(
                                  f"网络异常: {type(exc).__name__}"),
                              attempts=attempt)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        try:
            data = json.loads(raw) if raw.strip() else {}
        except (ValueError, TypeError):
            return SendResult(outcome="UNKNOWN", http_status=status,
                              error_message="响应不是 JSON", raw_digest=digest,
                              attempts=attempt)
        outcome = apply_receipt_rules(data, rules, default_outcome)
        found, ec = (True, None)
        f1, ec_v = _safe_get(data, ec_path)
        ec = ec_v if f1 else None
        _, em_v = _safe_get(data, em_path)
        rr = None
        if rr_path:
            _, rr_v = _safe_get(data, rr_path)
            rr = str(rr_v) if rr_v is not None else None
        em = str(em_v) if em_v is not None else ""
        return SendResult(outcome=outcome, http_status=status,
                          remote_ref=rr, error_code=str(ec) if ec is not None else None,
                          error_message=redact_text(em), raw_digest=digest,
                          attempts=attempt)

    # query：通用平台各异，暂不支持（显式能力边界）
    def query(self, config: dict, remote_ref: str) -> None:
        raise CapabilityError("CAPABILITY_UNSUPPORTED",
                              "generic_http_json 暂不支持查询（capability=send）")


def _safe_get(data, path):
    from connector_sdk.destination import json_path_get
    return json_path_get(data, path)


# ---------------------------------------------------------------- 服务层
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _is_loopback(base_url: str) -> bool:
    try:
        return (urlparse(str(base_url or "")).hostname or "").lower() \
            in _LOOPBACK_HOSTS
    except ValueError:
        return False


def perform_generic_push(db, tenant_id: str, submission, payload: dict) -> dict:
    """读 Destination 配置 → send → 每次尝试一行 DeliveryAttempt → 状态转换 → commit。

    返回与 perform_push 同形状 dict（outcome/http_status/remote_ref/...）。
    UNSUPPORTED → submission 置 REJECTED 且 snapshot 注明。

    LT-03-R1（R02）：
    - **Mock 回环强制**：Destination.is_mock=True 时 base_url 主机必须是本机
      回环地址（127.0.0.1/localhost/::1）——影子/联调目的地只能访问本机
      Mock，改 is_mock 字段不能把非本机地址带进 Mock 分支；非回环一律
      UNSUPPORTED（触网前拒绝，不发出任何请求）。
    - **逐次尝试审计**：on_attempt 回调把每次 POST 尝试（含重试）各落一行
      DeliveryAttempt（attempt_no 递增），不只留最后一次。
    """
    from contracts import new_uuid
    from core_db.engine import utcnow
    from core_db.models import DeliveryAttempt, Destination, Submission

    dest = db.get(Destination, submission.destination_id)
    # 配置读取：generic 目的地配置存于其 PUBLISHED ConfigRevision
    # （config_type="destination", owner_id=dest.id）；列名以模型为准
    # （Destination 无 config 列，配置在 ConfigRevision.content_json）。
    cfg: dict = {}
    dest_id = dest.id if dest is not None else "unknown"
    if dest is not None and dest.config_revision:
        from core_db.models import ConfigRevision
        cr = db.get(ConfigRevision, dest.config_revision)
        if cr is not None and cr.state == "PUBLISHED":
            cfg = dict(cr.content_json or {})
    cfg = {**cfg, "_dest_id": dest_id}

    request_digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False).encode()).hexdigest()[:64]
    attempt_no = (db.query(DeliveryAttempt)
                  .filter_by(submission_id=submission.id).count())

    def _record(result) -> None:
        nonlocal attempt_no
        attempt_no += 1
        db.add(DeliveryAttempt(
            id=new_uuid(), tenant_id=tenant_id, submission_id=submission.id,
            attempt_no=attempt_no, request_digest=request_digest,
            started_at=utcnow(), ended_at=utcnow(),
            http_status=result.http_status, outcome=result.outcome,
            response_snapshot={"error_code": result.error_code,
                               "error_message": result.error_message,
                               "remote_ref": result.remote_ref,
                               "raw_digest": result.raw_digest,
                               "attempt": result.attempts,
                               # Mock 证据单列（R02）：Mock 目的地的受理/确认
                               # 不得与真实上报成功混计
                               "mock": bool(dest.is_mock) if dest is not None
                               else False}))

    # Mock 回环强制（触网前；R02：影子/联调只可访问本机 Mock）
    if dest is not None and dest.is_mock and \
            not _is_loopback(cfg.get("base_url")):
        _record(SendResult(
            outcome="UNSUPPORTED",
            error_message="MOCK_LOOPBACK_REQUIRED：Mock/影子目的地只允许"
                          "本机回环地址（127.0.0.1/localhost/::1），"
                          "禁止指向院内/外真实地址"))
        submission.state = "REJECTED"
        db.commit()
        return {"outcome": "UNSUPPORTED", "http_status": None,
                "remote_ref": None, "error_code": None,
                "error_message": "MOCK_LOOPBACK_REQUIRED：Mock/影子目的地"
                                 "只允许本机回环地址", "retry_after_seconds": None,
                "attempts": 1}

    adapter = GenericHttpJsonAdapter()
    n0 = attempt_no
    result = adapter.send(cfg, payload, submission.idempotency_key,
                          on_attempt=_record)
    if attempt_no == n0:
        # 触网前即被拒（配置/凭据/白名单 UNSUPPORTED 等）——也落一行审计，
        # 不静默丢证据
        _record(result)

    # 状态转换（与 perform_push 同语义；UNSUPPORTED→REJECTED 并注明）
    new_state = {"ACCEPTED": "ACCEPTED", "CONFIRMED": "CONFIRMED",
                 "PARTIAL": "PARTIAL", "REJECTED": "REJECTED",
                 "UNKNOWN": "UNKNOWN"}.get(result.outcome)
    if result.outcome == "UNSUPPORTED":
        new_state = "REJECTED"
    submission.state = new_state or "UNKNOWN"
    db.commit()
    return {"outcome": result.outcome, "http_status": result.http_status,
            "remote_ref": result.remote_ref, "error_code": result.error_code,
            "error_message": result.error_message,
            "retry_after_seconds": result.retry_after_seconds,
            "attempts": result.attempts}
