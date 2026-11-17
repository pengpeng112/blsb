"""告警脱敏（LT-04 C 段；RT-03 S06 递归深修）。

白名单字段输出，长数字/证件号打码（复用 LT-03 generic_http_json 的
redact_text 做兜底）；额外确保正文不含 URL query 串、payload、SQL。

RT-03 S06：
- ``sanitize_detail``（新增，递归）：嵌套 dict/list 中的字符串继续脱敏，
  深度/长度受限，非白名单顶层键不透传；
- ``redact_detail``（旧名，仅顶层字符串）保留并委托 sanitize_detail——
  既有调用零改动即获得递归语义，不新增无调用的死函数。
"""
from __future__ import annotations

from plugins.destinations.generic_http_json.redact import redact_text

# 白名单键（detail 输出过滤；值仍经 redact_text / 递归脱敏）
ALLOWED_DETAIL_KEYS = frozenset({
    "code", "count", "threshold", "hours", "rate", "min_total", "days",
    "warn_days", "label", "date", "source_ref", "evidence_id", "state",
    "window_days", "backlog", "backlog_total", "quality_waiting",
    "last_success_at", "target", "schedule_code", "consecutive",
    "run_key", "deadline_at", "occurrences", "observed",
    # RT-03 S06：实际使用的安全元数据补充
    "error_code", "schedule_id", "late", "unmapped_states",
    "last_notified_at", "observed_at", "collapsed_from", "collapsed_to",
    "was_timed_out", "was_stalled", "started_at_unknown", "window_kind",
})

_MAX_DEPTH = 6            # 递归深度上限（更深的结构整体丢弃）
_MAX_STR_LEN = 500        # 单字符串长度上限（截断后再脱敏）
_MAX_ITEMS = 100          # 容器元素数上限

# 纯机器时间戳键：ISO 可解析则原样保留（redact_text 会把微秒段当长数字
# 打码成 ***，冷却判定 fromisoformat 失败→通知风暴——S06 明确要求
# 不因脱敏丢 last_notified_at）
_TIMESTAMP_KEYS = frozenset({
    "last_notified_at", "last_success_at", "deadline_at", "observed_at",
    "collapsed_from", "collapsed_to"})


def _safe_ts(key: str, v) -> str | None:
    """时间戳键且值可解析 ISO → 原样返回；否则 None（走脱敏路径）。"""
    if key not in _TIMESTAMP_KEYS or not isinstance(v, str):
        return None
    from datetime import datetime
    try:
        datetime.fromisoformat(v)
    except ValueError:
        return None
    return v


def _sanitize_value(v, depth: int):
    """白名单键的值清洗：嵌套 dict/list 递归，字符串脱敏+截断。"""
    if isinstance(v, str):
        return redact_text(v[:_MAX_STR_LEN])   # 嵌套层无时间戳键语义
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, dict):
        if depth >= _MAX_DEPTH:
            return {}
        out = {}
        for i, (k, val) in enumerate(v.items()):
            if i >= _MAX_ITEMS:
                break
            out[str(k)[:64]] = _sanitize_value(val, depth + 1)
        return out
    if isinstance(v, (list, tuple)):
        if depth >= _MAX_DEPTH:
            return []
        return [_sanitize_value(x, depth + 1)
                for x in list(v)[:_MAX_ITEMS]]
    # 其他类型（set 等）：转字符串脱敏，不透传原对象
    return redact_text(str(v)[:_MAX_STR_LEN])


def sanitize_detail(detail: dict) -> dict:
    """递归脱敏：顶层白名单键过滤；白名单值内嵌套字符串继续脱敏。

    非白名单 payload/SQL/身份信息不透传；深度/长度受限；错误日志不回显
    Secret（字符串经 redact_text 长数字/凭据形态打码）。纯时间戳键
    （last_notified_at 等）ISO 可解析时原样保留——冷却判定依赖它。"""
    out: dict = {}
    for k, v in (detail or {}).items():
        if str(k) not in ALLOWED_DETAIL_KEYS:
            continue
        if isinstance(v, str):
            ts = _safe_ts(str(k), v)
            out[str(k)] = ts if ts is not None else redact_text(v[:_MAX_STR_LEN])
        elif isinstance(v, (dict, list, tuple)):
            out[str(k)] = _sanitize_value(v, 0)
        else:
            out[str(k)] = v
    return out


def redact_detail(detail: dict) -> dict:
    """旧接口名（LT-04）：委托 sanitize_detail（递归语义），调用方零改动。"""
    return sanitize_detail(detail)


def alert_text(title: str, detail: dict, *, target_name: str = "",
               rule_name: str = "") -> str:
    """告警正文（白名单字段；不含患者/payload/SQL/URL query）。"""
    parts = [redact_text(title[:500])]
    if rule_name:
        parts.append(f"规则: {redact_text(rule_name[:200])}")
    if target_name:
        parts.append(f"业务: {redact_text(target_name[:200])}")
    for k, v in sanitize_detail(detail).items():
        parts.append(f"{k}={v}")
    return "\n".join(parts)
