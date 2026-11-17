"""generic_http_json 配置校验（LT-03；全部中文错误说明，空列表=合法）。"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from connector_sdk.destination import OUTCOMES
from connector_sdk.destination import ReceiptRule  # noqa: F401（类型引用）

VALID_METHODS = ("POST", "PUT")
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_VALID_OPS = ("eq", "in", "exists", "not_exists")
# LT-03-R1（R02）：对端幂等契约——只有显式登记 CONFIRMED（有书面/平台确认的
# 幂等语义证据）才允许 POST 不确定结果后的自动重发；空=未证实，一律 UNKNOWN。
VALID_IDEMPOTENCY_CONTRACTS = ("", "CONFIRMED")


def validate_config(cfg: dict) -> list[str]:
    """校验通用 HTTP JSON 目的地配置；返回中文错误列表（空=合法）。"""
    errs: list[str] = []
    if not isinstance(cfg, dict):
        return ["配置必须是 JSON 对象"]
    base = str(cfg.get("base_url", "") or "")
    if not base:
        errs.append("base_url 必填")
    else:
        p = urlparse(base)
        schemes = cfg.get("allowed_schemes") or ["http", "https"]
        hosts = cfg.get("allowed_hosts") or []
        ports = cfg.get("allowed_ports") or []
        if p.scheme not in schemes:
            errs.append(f"scheme {p.scheme!r} 不在 allowed_schemes {schemes}")
        if not p.hostname:
            errs.append("base_url 缺少主机名")
        elif p.hostname not in hosts:
            errs.append(f"主机 {p.hostname!r} 不在 allowed_hosts {hosts}")
        port = p.port or (443 if p.scheme == "https" else 80)
        if port not in ports:
            errs.append(f"端口 {port} 不在 allowed_ports {ports}")
        if p.path not in ("", "/") or p.query or p.fragment or p.username or \
                p.password:
            errs.append("base_url 只允许 scheme://host:port（路径/查询/凭据放"
                        " path_template，不允许带在 base_url 里）")
    method = str(cfg.get("method", "POST") or "POST").upper()
    if method not in VALID_METHODS:
        errs.append(f"method 只允许 {'/'.join(VALID_METHODS)}，当前 {method!r}")
    tpl = str(cfg.get("path_template") or "/")
    if not tpl.startswith("/"):
        errs.append("path_template 必须以 / 开头")
    # 占位符之外的部分只允许 [A-Za-z0-9_/-]；占位符名称本身由
    # _PLACEHOLDER_RE 限定为 [A-Za-z_][A-Za-z0-9_]*
    tail = _PLACEHOLDER_RE.sub("", tpl)
    if tail and not re.fullmatch(r"[A-Za-z0-9_/-]+", tail):
        errs.append("path_template 含占位符之外的非法字符（只允许 [A-Za-z0-9_/-] 与"
                    " {字段} 占位符）")
    timeout = cfg.get("timeout_seconds", 15)
    if not isinstance(timeout, (int, float)) or not (1 <= float(timeout) <= 120):
        errs.append("timeout_seconds 必须在 1—120 之间")
    max_attempts = cfg.get("max_attempts", 3)
    if not isinstance(max_attempts, int) or not (1 <= max_attempts <= 5):
        errs.append("max_attempts 必须在 1—5 之间")
    backoff = cfg.get("backoff_seconds", [2, 4, 6])
    if not isinstance(backoff, list) or not all(
            isinstance(x, (int, float)) and x >= 0 for x in backoff):
        errs.append("backoff_seconds 必须为非负数字列表")
    rate = cfg.get("rate_limit_per_minute", 60)
    if not isinstance(rate, (int, float)) or not (1 <= float(rate) <= 6000):
        errs.append("rate_limit_per_minute 必须在 1—6000 之间")
    cred = cfg.get("credential_ref")
    if cred is not None:
        if not isinstance(cred, str) or not re.fullmatch(
                r"env:[A-Za-z_][A-Za-z0-9_]*", cred):
            errs.append('credential_ref 只允许形如 "env:VAR_NAME"（运行时读环境变量，'
                        "明文凭据禁止入库）")
    rules = cfg.get("receipt_rules", [])
    if not isinstance(rules, list):
        errs.append("receipt_rules 必须为数组")
    else:
        for i, r in enumerate(rules):
            if not isinstance(r, dict):
                errs.append(f"receipt_rules[{i}] 必须为对象")
                continue
            if not str(r.get("json_path", "") or "").strip():
                errs.append(f"receipt_rules[{i}].json_path 必填")
            if r.get("op") not in _VALID_OPS:
                errs.append(f"receipt_rules[{i}].op 必须是 "
                            f"{'/'.join(_VALID_OPS)}，当前 {r.get('op')!r}")
            if r.get("outcome") not in OUTCOMES:
                errs.append(f"receipt_rules[{i}].outcome 非法：{r.get('outcome')!r}；"
                            f"允许 {'/'.join(OUTCOMES)}")
            if r.get("op") == "in" and not isinstance(r.get("value"), (list, tuple)):
                errs.append(f"receipt_rules[{i}] op=in 时 value 必须为数组")
    default_outcome = cfg.get("default_outcome", "UNKNOWN")
    if default_outcome not in OUTCOMES:
        errs.append(f"default_outcome 非法：{default_outcome!r}")
    contract = cfg.get("idempotency_contract", "")
    if contract is None:
        contract = ""
    if not isinstance(contract, str) or contract not in VALID_IDEMPOTENCY_CONTRACTS:
        errs.append('idempotency_contract 只允许缺省/""/“CONFIRMED”（未证实对端'
                    "幂等时不得登记；POST 不确定结果默认进 UNKNOWN 不自动重发）")
    evidence = cfg.get("real_send_evidence_ref")
    if evidence is not None and (not isinstance(evidence, str)
                                 or len(evidence) > 512):
        errs.append("real_send_evidence_ref 必须为 ≤512 字符的字符串（真实发送"
                    "启用证据引用，如工单/批复编号）")
    return errs


def render_path(template: str, payload: dict) -> tuple[bool, str, str]:
    """渲染 path_template；占位符值只允许 [A-Za-z0-9_-]（防路径注入）。

    返回 (ok, path_or_original, error)。占位字段缺失或含非法字符 → 不通过。
    """
    def _sub(m: re.Match) -> str:
        field = m.group(1)
        v = payload.get(field)
        if v is None:
            raise ValueError(f"占位符字段缺失: {field}")
        sv = str(v)
        if not _SAFE_VALUE_RE.fullmatch(sv):
            raise ValueError(f"占位符 {field} 值含非法字符（只允许"
                             "字母/数字/_/-）")
        return sv
    try:
        return True, _PLACEHOLDER_RE.sub(_sub, template), ""
    except ValueError as exc:
        return False, template, str(exc)
