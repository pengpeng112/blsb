"""通知通道（LT-04 C 段；RT-03 S07 深修；PT-02 新增企微双通道）：
file / smtp / webhook / **wecom_relay** / **wecom_robot**。

安全约束（R01/R05 + RT-03 S07 + PT-02）：
- config **禁止内联秘密**：smtp 密码=password_env、webhook/wecom 地址与密钥
  =url_env/secret_env（环境变量名引用），且环境变量名必须匹配
  ``^[A-Z][A-Z0-9_]{2,63}$``；
- webhook 的 url_env 必填——出现 url/webhook/key/token/secret/password 等
  内联秘密字段（任意嵌套层级）即拒绝，不能因未传 url_env 而绕过；任意
  ``xxx_env`` 不是免检通行证（每个通道按专属允许字段白名单校验）；
- **PT-02 双重安全外发门控**：``wecom_relay``/``wecom_robot`` 允许随时创建与
  编辑，``dry_run=True``（零出网渲染预览）无条件开放；**真实外发/启用/实发
  测试须环境变量 ``BLYB_ALLOW_EXTERNAL_NOTIFY=1``**（生产授权开关）——未开启
  时返回清晰中文说明，引导使用"预览卡片"验证配置；本地测试 100% 零真实外发；
- smtp/webhook 维持本批服务端停用（历史 enabled=True 也不实际网络外发）；
  file_default 保留用户已确认的 enabled=True；
- 所有发送失败返回错误字符串（由调用方落 notification_log），不外抛。

wecom_relay 协议（与院内 wechat-relay v2 / inf_watchdog driver_relay 完全一致）：
  POST {url}；raw=紧凑 UTF-8 JSON 字节；
  X-Signature = HMAC-SHA256(secret, f"{source_id}.{ts}.".encode() + raw) 小写 hex；
  附 X-Source-Id / X-Timestamp；**严禁携带 receivers 字段**（收件人由 relay 订阅路由管理）；
  200/202 成功；502 原字节重试 1 次；429/503 读 Retry-After 短退避（≤3s）重试 1 次。
wecom_robot：企业微信群机器人 markdown/text 卡片；**必须解析响应 JSON 校验
  errcode == 0**（HTTP 200 但 errcode=93000 不得误判为成功）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time as _time

from core_db.engine import utcnow

FORBIDDEN_TYPES_FOR_ENABLE = ("smtp", "webhook")   # 本批停用（R01）
_SECRET_KEY_RE = re.compile(r"(password|secret|token|key|url|webhook)", re.I)
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
# PT-02：企微双通道的真实外发/启用授权开关（环境变量名，非明文）
EXTERNAL_NOTIFY_ENV = "BLYB_ALLOW_EXTERNAL_NOTIFY"

CHANNEL_TYPES = ("file", "smtp", "webhook", "wecom_relay", "wecom_robot")

# 通道专属允许字段（S07：不把任意 xxx_env 当免检通行证；PT-02 扩企微双通道）
_CHANNEL_ALLOWED_FIELDS = {
    "file": {"path"},
    "smtp": {"host", "port", "ssl", "user", "from", "to", "password_env"},
    "webhook": {"url_env", "template"},
    "wecom_relay": {"url_env", "secret_env", "source_id", "category",
                    "quiet_hours", "rate_limit_per_min"},
    "wecom_robot": {"url_env", "msgtype", "quiet_hours", "rate_limit_per_min"},
}

_EXTERNAL_GATE_HINT = ("当前环境未开启外发开关（BLYB_ALLOW_EXTERNAL_NOTIFY），"
                       "可使用“预览卡片”验证配置")


def external_notify_allowed() -> bool:
    """真实外发/启用授权开关：BLYB_ALLOW_EXTERNAL_NOTIFY=1 才放行企微实发。"""
    return os.environ.get(EXTERNAL_NOTIFY_ENV) == "1"


def _iter_secret_paths(obj, prefix: str = ""):
    """递归找内联秘密字段路径（嵌套 dict/list 一并检查；*_env 名除外）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            ks = str(k)
            if _SECRET_KEY_RE.search(ks) and not ks.endswith("_env"):
                yield path
            yield from _iter_secret_paths(v, path)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _iter_secret_paths(v, f"{prefix}[{i}]")


def _validate_quiet_hours(qh) -> None:
    """quiet_hours 结构校验：{enabled:bool, start/end:"HH:MM", suppress_levels:[...]}。"""
    from contracts import ApiError
    if qh is None:
        return
    if not isinstance(qh, dict):
        raise ApiError("VALIDATION_FAILED", "quiet_hours 须为对象"
                       "（enabled/start/end/suppress_levels）")
    for k in qh:
        if k not in ("enabled", "start", "end", "suppress_levels"):
            raise ApiError("VALIDATION_FAILED", f"quiet_hours 不允许字段 {k!r}")
    for tk in ("start", "end"):
        v = qh.get(tk)
        if v is None:
            continue
        if not isinstance(v, str) or not re.fullmatch(r"\d{1,2}:\d{2}", v):
            raise ApiError("VALIDATION_FAILED",
                           f"quiet_hours.{tk} 须为 HH:MM 时间（如 23:00）")
    sl = qh.get("suppress_levels")
    if sl is not None:
        if not isinstance(sl, list) or any(
                x not in ("INFO", "WARN", "CRITICAL") for x in sl):
            raise ApiError("VALIDATION_FAILED",
                           "quiet_hours.suppress_levels 只允许 INFO/WARN/CRITICAL")


def _validate_rate_limit(v) -> None:
    from contracts import ApiError
    if v is None:
        return
    if not isinstance(v, int) or isinstance(v, bool) or not (1 <= v <= 600):
        raise ApiError("VALIDATION_FAILED",
                       "rate_limit_per_min 须为 1–600 的整数（每分钟外发上限）")


def validate_channel_config(ctype: str, config: dict) -> None:
    """通道配置校验：类型专属字段与内联秘密拒绝（400 语义由 API 层包装）。

    RT-03 S07：错误信息只含字段名/路径，不回显提交值。"""
    from contracts import ApiError
    if ctype not in CHANNEL_TYPES:
        raise ApiError("VALIDATION_FAILED", f"通道类型 {ctype!r} 非法"
                       f"（允许：{'/'.join(CHANNEL_TYPES)}）")
    cfg = config or {}
    # 1) 递归内联秘密扫描（url_env 缺失也不能绕过 url/key/… 的检查）
    for path in _iter_secret_paths(cfg):
        raise ApiError(
            "SECRET_INLINE_FORBIDDEN",
            f"配置字段 {path!r} 含秘密语义：只允许 *_env（环境变量名引用），"
            "明文秘密禁止入库")
    # 2) 通道专属允许字段（未知字段拒绝——含任意 xxx_env）
    allowed = _CHANNEL_ALLOWED_FIELDS[ctype]
    for k in cfg:
        if str(k) not in allowed:
            raise ApiError(
                "VALIDATION_FAILED",
                f"{ctype} 通道不允许配置字段 {k!r}"
                f"（允许：{'/'.join(sorted(allowed))}）")
    # 3) 环境变量名规范（引用名本身也要合法，防注入怪名）
    for env_key in ("url_env", "password_env", "secret_env"):
        v = str(cfg.get(env_key, "") or "")
        if v and not _ENV_NAME_RE.match(v):
            raise ApiError(
                "VALIDATION_FAILED",
                f"{env_key} 须匹配 ^[A-Z][A-Z0-9_]{{2,63}}$ 的环境变量名，"
                f"当前长度 {len(v)}")
    # 4) 类型必填项
    if ctype == "webhook" and not str(cfg.get("url_env", "") or ""):
        raise ApiError(
            "SECRET_INLINE_FORBIDDEN",
            "webhook 通道必须经 url_env 引用地址（环境变量名），"
            "不接受内联 url，也不允许缺失")
    if ctype == "wecom_relay":
        if not str(cfg.get("url_env", "") or ""):
            raise ApiError("SECRET_INLINE_FORBIDDEN",
                           "wecom_relay 必须经 url_env 引用网关地址（环境变量名）")
        if not str(cfg.get("secret_env", "") or ""):
            raise ApiError("SECRET_INLINE_FORBIDDEN",
                           "wecom_relay 必须经 secret_env 引用签名密钥（环境变量名，"
                           "严禁明文入库）")
        sid = str(cfg.get("source_id", "") or "")
        if not sid:
            raise ApiError("VALIDATION_FAILED",
                           "wecom_relay 必填 source_id（如 blyb-platform-alerting）")
        if not _SOURCE_ID_RE.match(sid):
            raise ApiError("VALIDATION_FAILED",
                           "source_id 须匹配 ^[a-z0-9][a-z0-9_-]{2,63}$")
        cat = str(cfg.get("category", "") or "")
        if cat and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", cat):
            raise ApiError("VALIDATION_FAILED",
                           "category 须为小写字母/数字/中划线短串")
    if ctype == "wecom_robot":
        if not str(cfg.get("url_env", "") or ""):
            raise ApiError("SECRET_INLINE_FORBIDDEN",
                           "wecom_robot 必须经 url_env 引用机器人地址（环境变量名）")
        mt = str(cfg.get("msgtype", "markdown") or "markdown")
        if mt not in ("markdown", "text"):
            raise ApiError("VALIDATION_FAILED", "msgtype 只允许 markdown/text")
    if ctype == "smtp":
        if str(cfg.get("password", "") or ""):
            raise ApiError("SECRET_INLINE_FORBIDDEN",
                           "smtp 密码只能用 password_env（环境变量名）")
        if not (cfg.get("to") and cfg.get("host")):
            raise ApiError("VALIDATION_FAILED",
                           "smtp 通道须配置 host 与 to（收件人）")
    if ctype == "file":
        path = str(cfg.get("path", "") or "")
        if path and (os.path.isabs(path) or ".." in path):
            raise ApiError("VALIDATION_FAILED",
                           "file 通道 path 必须为相对路径且不含 ..（防穿越）")
    # 5) 共用可选结构（PT-02：夜间免打扰 + 限速）
    _validate_quiet_hours(cfg.get("quiet_hours"))
    _validate_rate_limit(cfg.get("rate_limit_per_min"))


def assert_channel_change_allowed(ctype: str, *, enabling: bool,
                                  testing: bool = False) -> None:
    """外发授权门控（PT-02 双重安全）：

    - smtp/webhook 维持本批服务端强制停用（启用与测试外发均拒绝）；
    - wecom_relay/wecom_robot：**启用**（enabling=True）或**实发测试**
      （testing=True）须 ``BLYB_ALLOW_EXTERNAL_NOTIFY=1``；未开启返回清晰
      中文说明（可随时创建/编辑配置、可用 dry-run 预览，零外发副作用）。"""
    from contracts import ApiError
    if ctype in FORBIDDEN_TYPES_FOR_ENABLE and (enabling or testing):
        raise ApiError(
            "NOT_ALLOWED_THIS_BATCH",
            f"{ctype} 通道本批服务端强制停用（不启用、不外发测试）；"
            "file_default 可用。真实外发待用户单独授权")
    if ctype in ("wecom_relay", "wecom_robot") and (enabling or testing) \
            and not external_notify_allowed():
        raise ApiError("EXTERNAL_NOTIFY_DISABLED", _EXTERNAL_GATE_HINT)


def channel_quiet_hours(channel) -> dict | None:
    """通道级 quiet_hours 读取（evaluator 静默窗用）。"""
    qh = (getattr(channel, "config", None) or {}).get("quiet_hours")
    return qh if isinstance(qh, dict) and qh.get("enabled") else None


def _resolve_url(channel, key: str = "url_env") -> tuple[str, str]:
    var = str((channel.config or {}).get(key, "") or "")
    url = os.environ.get(var, "") if var else ""
    if not url:
        return "", f"URL 未配置: {var or '(url_env 缺失)'}"
    return url, ""


def _http_post_raw(url: str, raw: bytes, headers: dict,
                   *, retry_codes=(502,), backoff_codes=(429, 503)) -> tuple[int, str]:
    """统一 POST：200/202 成功；502 原字节重试 1 次；429/503 读 Retry-After
    （≤3 秒）退避重试 1 次。返回 (最终状态码, 响应体文本)。非 2xx 由调用方判。"""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=raw, method="POST",
                                 headers={"Content-Type": "application/json", **headers})

    def _do():
        with urllib.request.urlopen(req, timeout=10) as resp:
            return int(resp.status), resp.read(65536).decode("utf-8", "replace")

    try:
        code, body = _do()
    except urllib.error.HTTPError as e:
        code, body = int(e.code), ""
        try:
            body = e.read(2048).decode("utf-8", "replace")
        except Exception:
            pass
        if code in retry_codes:
            code, body = _do()
        elif code in backoff_codes:
            ra = ""
            try:
                ra = (e.headers or {}).get("Retry-After", "") or ""
            except Exception:
                ra = ""
            delay = 1.0
            if str(ra).strip().isdigit():
                delay = min(3.0, max(0.0, float(ra)))
            _time.sleep(delay)
            code, body = _do()
    return code, body


def _send_wecom_relay(channel, text: str, kind: str) -> str:
    """wecom_relay 传输实现：HMAC-SHA256 签名 v2 事件（协议与 inf_watchdog 一致）。

    幂等键 = source_id|通道码|kind|小时桶 的稳定摘要——动态计数文本不参与，
    同小时内重试幂等，跨小时同类告警可再投递。"""
    cfg = channel.config or {}
    url, err = _resolve_url(channel)
    if err:
        return err
    var = str(cfg.get("secret_env", "") or "")
    secret = os.environ.get(var, "") if var else ""
    if not var:
        return "secret_env 未配置"
    if not secret:
        return f"凭据未配置: {var}"
    lines = text.splitlines()
    title = lines[0][:200] if lines else "平台告警"
    severity = {"ALERT": "red", "RECOVERED": "blue", "TEST": "gray",
                "DIGEST": "yellow"}.get(kind, "gray")
    hour_bucket = utcnow().strftime("%Y%m%d%H")
    ident = "%s|%s|%s|%s" % (cfg.get("source_id"), channel.code, kind, hour_bucket)
    payload = {
        "source_event_id": hashlib.sha256(ident.encode("utf-8")).hexdigest()[:24],
        "event_type": "alert",
        "category": str(cfg.get("category") or "blyb-platform"),
        "severity": severity,
        "title": title,
        "body": text[:4000],
        "occurred_at": utcnow().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ts = str(int(_time.time()))
    sig = hmac.new(secret.encode("utf-8"),
                   ("%s.%s." % (cfg.get("source_id"), ts)).encode("utf-8") + raw,
                   hashlib.sha256).hexdigest()
    code, body_resp = _http_post_raw(url, raw, {
        "X-Source-Id": str(cfg.get("source_id")),
        "X-Timestamp": ts, "X-Signature": sig})
    if code not in (200, 202):
        return f"relay HTTP {code}: {body_resp[:200]}"
    try:
        rc = json.loads(body_resp)
        if rc.get("ok") is False:
            return f"relay ok=false: {body_resp[:200]}"
    except ValueError:
        pass                      # 非 JSON 响应，2xx 视为受理
    return ""


_WECOM_ROBOT_COLORS = {"ALERT": "warning", "RECOVERED": "info", "TEST": "comment"}


def _send_wecom_robot(channel, text: str, kind: str) -> str:
    """wecom_robot 传输实现：群机器人 markdown/text；**必须校验响应 errcode==0**
    （HTTP 200 + errcode 93000 不得误判成功）。"""
    cfg = channel.config or {}
    url, err = _resolve_url(channel)
    if err:
        return err
    msgtype = str(cfg.get("msgtype", "markdown") or "markdown")
    if msgtype == "text":
        payload = {"msgtype": "text", "text": {"content": text[:4000]}}
    else:
        lines = text.splitlines()
        title = lines[0][:64] if lines else "平台告警"
        color = _WECOM_ROBOT_COLORS.get(kind, "info")
        payload = {"msgtype": "markdown",
                   "markdown": {"content": f'<font color="{color}">{title}</font>\n'
                                           + text[:4000]}}
    code, body_resp = _http_post_raw(
        url, json.dumps(payload, ensure_ascii=False).encode("utf-8"), {})
    if code >= 300:
        return f"wecom_robot HTTP {code}: {body_resp[:200]}"
    try:
        rc = json.loads(body_resp)
    except ValueError:
        return f"wecom_robot 响应非 JSON: {body_resp[:200]}"
    if int(rc.get("errcode", -1)) != 0:
        return f"wecom_robot errcode={rc.get('errcode')}: {str(rc.get('errmsg', ''))[:200]}"
    return ""


def render_preview(channel, text: str, kind: str = "ALERT") -> dict:
    """PT-02：零出网渲染预览（dry-run）。返回脱敏后的请求头摘要与完整企微卡片
    标题/正文；环境变量未注入时在 env_status 如实标注（绝不外发）。"""
    cfg = channel.config or {}
    ctype = channel.type
    out: dict = {"type": ctype, "kind": kind,
                 "card_title": text.splitlines()[0][:200] if text else "",
                 "card_body": text[:2000],
                 "env_status": {}, "headers_preview": {}, "protocol": ""}
    for env_key in ("url_env", "secret_env", "password_env"):
        var = str(cfg.get(env_key, "") or "")
        if var:
            out["env_status"][var] = "已注入" if os.environ.get(var) else "未注入"
    if ctype == "wecom_relay":
        out["headers_preview"] = {
            "X-Source-Id": str(cfg.get("source_id", "") or ""),
            "X-Timestamp": "<发送时刻秒级时间戳>",
            "X-Signature": "HMAC-SHA256(secret_env 值, source_id.ts.raw) 64 位 hex"}
        out["protocol"] = ("POST {url}（紧凑 JSON 字节先签名后发送；严禁 receivers 字段；"
                           "200/202 成功，502 原样重试 1 次，429/503 退避重试 1 次）")
    elif ctype == "wecom_robot":
        mt = str(cfg.get("msgtype", "markdown") or "markdown")
        out["protocol"] = f"POST {{url}}（群机器人 {mt} 卡片；响应 errcode==0 才算成功）"
    elif ctype == "file":
        out["protocol"] = "写入本地文件（file_default 默认 var/alerts/alerts-YYYYMMDD.jsonl）"
    return out


def probe_relay(channel) -> tuple[bool, str]:
    """PT-02：网关真达探测——对 wecom_relay 发送**无签名空包**，预期 HTTP 401
    （Missing X-Source-Id）＝应用层真达（TCP 连通 ≠ 真达）。零消息副作用，
    仅管理员且外发开关开启时可调（API 层门控）。"""
    import urllib.error
    import urllib.request
    url, err = _resolve_url(channel)
    if err:
        return False, err
    try:
        req = urllib.request.Request(url, data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                code = int(resp.status)
                body = resp.read(2048).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            code = int(e.code)
            body = e.read(2048).decode("utf-8", "replace")
        if code == 401:
            return True, "网关真达（401 Missing X-Source-Id）"
        return False, f"HTTP {code} {body[:120]}（网关异常放行无签名请求？需核查）"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"[:200]


def send(channel, text: str, *, kind: str = "ALERT") -> str:
    """按通道类型外发；返回错误字符串（""=成功）。异常不外抛。

    RT-03 S07：smtp/webhook 在 **dispatch 入口**即拒绝（本批服务端停用）
    ——即使历史行 enabled=True（或被直接 dispatch）也不实际网络发送；
    返回"需修订"错误串由调用方落 notification_log，不静默重写配置。
    PT-02：wecom_relay/wecom_robot 须 BLYB_ALLOW_EXTERNAL_NOTIFY=1（未开启
    返回清晰中文说明引导预览）；file 通道正常落盘。"""
    try:
        if channel.type in FORBIDDEN_TYPES_FOR_ENABLE:
            return (f"{channel.type} 通道本批服务端停用：不外发"
                    f"（历史配置需修订；真实外发待用户单独授权）")
        if channel.type in ("wecom_relay", "wecom_robot") \
                and not external_notify_allowed():
            return _EXTERNAL_GATE_HINT
        if channel.type == "file":
            return _send_file(channel, text, kind)
        if channel.type == "wecom_relay":
            return _send_wecom_relay(channel, text, kind)
        if channel.type == "wecom_robot":
            return _send_wecom_robot(channel, text, kind)
        return f"unknown channel type {channel.type!r}"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"[:200]


def _send_file(channel, text: str, kind: str) -> str:
    cfg = channel.config or {}
    rel = str(cfg.get("path", "") or "")
    if not rel:
        day = utcnow().strftime("%Y%m%d")
        rel = f"var/alerts/alerts-{day}.jsonl"
    base = os.getcwd()
    root = os.path.abspath(base)
    target = os.path.abspath(os.path.join(root, rel))
    if not target.startswith(root + os.sep) and target != root:
        return "file path escapes workspace root"
    os.makedirs(os.path.dirname(target), exist_ok=True)
    line = json.dumps({"at": utcnow().isoformat(), "kind": kind,
                       "channel": channel.code, "text": text},
                      ensure_ascii=False)
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return ""


def _send_smtp(channel, text: str, kind: str) -> str:
    """SMTP 传输实现（真实外发授权后由 send() 放行；单测直接调用验证管道）。"""
    cfg = channel.config or {}
    var = str(cfg.get("password_env", "") or "")
    password = os.environ.get(var, "") if var else ""
    if var and not password:
        return f"凭据未配置: {var}"
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = f"[BLYB] {kind}"
    msg["From"] = str(cfg.get("from", "blyb-alerts@localhost"))
    msg["To"] = ", ".join(cfg.get("to") or [])
    with smtplib.SMTP(str(cfg.get("host", "")), int(cfg.get("port", 25)),
                      timeout=10) as smtp:
        if cfg.get("ssl"):
            smtp.starttls()
        if cfg.get("user"):
            smtp.login(str(cfg.get("user")), password)
        smtp.sendmail(msg["From"], list(cfg.get("to") or []),
                      msg.as_string())
    return ""


_WEBHOOK_TEMPLATES = {
    "wecom": lambda text: {"msgtype": "text", "text": {"content": text}},
    "dingtalk": lambda text: {"msgtype": "text", "text": {"content": text}},
    "generic": lambda text: {"text": text, "source": "blyb-alerts"},
}


def _send_webhook(channel, text: str, kind: str) -> str:
    """webhook 传输实现（真实外发授权后由 send() 放行；单测直接调用验证管道）。"""
    cfg = channel.config or {}
    var = str(cfg.get("url_env", "") or "")
    url = os.environ.get(var, "") if var else ""
    if not url:
        return f"URL 未配置: {var or '(url_env 缺失)'}"
    import urllib.request
    tpl = str(cfg.get("template", "generic"))
    payload = json.dumps(_WEBHOOK_TEMPLATES.get(
        tpl, _WEBHOOK_TEMPLATES["generic"])(text)).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status >= 300:
            return f"HTTP {resp.status}"
    return ""
