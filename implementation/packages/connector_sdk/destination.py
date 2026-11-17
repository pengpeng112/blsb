"""统一目的地适配器协议（LT-03）。

所有真实推送目的地（generic_http_json 及后续新平台）实现 DestinationAdapter
协议；workflow 的 perform_push 按 Destination.plugin_id 派发到适配器函数。
本模块只定义形状与解析工具，不触网、不依赖 ORM。

约定：
- outcome ∈ OUTCOMES；UNKNOWN 表示"不知道对方是否已收到"（网络异常耗尽、
  服务端异常耗尽、响应不可解析），绝不臆断为失败。
- error_message 必须已脱敏（调用方 redact_text）且 ≤500 字。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

OUTCOMES = ("ACCEPTED", "CONFIRMED", "PARTIAL", "REJECTED",
            "RETRYABLE", "UNKNOWN", "UNSUPPORTED")


@dataclass
class SendResult:
    outcome: str                      # ∈ OUTCOMES
    http_status: int | None = None
    remote_ref: str | None = None     # 对方流水号（有则填）
    error_code: str | None = None     # 对方业务码，如 "02"
    error_message: str = ""           # 已脱敏、≤500 字
    retry_after_seconds: int | None = None
    attempts: int = 1
    raw_digest: str = ""              # 响应体 sha256 前 16 位（不存原文）


@dataclass
class QueryResult:
    outcome: str
    remote_ref: str | None = None
    error_code: str | None = None
    error_message: str = ""


@dataclass
class ReceiptRule:
    """声明式回执判定。按顺序匹配，命中即返回 outcome。"""
    json_path: str                    # 简单点路径，如 "result" / "data.code" / "items.0.ok"
    op: str                           # eq | in | exists | not_exists
    value: Any = None
    outcome: str = "ACCEPTED"


class DestinationAdapter(Protocol):
    plugin_id: str

    def capabilities(self) -> set[str]:
        """{"send","query","void"} 的子集。"""
        ...

    def validate_config(self, config: dict) -> list[str]:
        """返回中文错误列表；空列表 = 配置合法。"""
        ...

    def send(self, config: dict, payload: dict, idempotency_key: str) -> SendResult:
        ...

    def query(self, config: dict, remote_ref: str) -> QueryResult:
        """不支持查询的平台 raise connector_sdk.CapabilityError("CAPABILITY_UNSUPPORTED")。"""
        ...


# ---------------------------------------------------------------- json_path
_SEG_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def json_path_get(data: Any, path: str) -> tuple[bool, Any]:
    """按 `a.b.c` / `a.0.b`（数组下标）取值；返回 (found, value)。

    - 任一段缺失/类型不可导航 → (False, None)；
    - 段内只允许字母数字下划线连字符（拒绝 `__`、空白与特殊符号的路径注入）；
    - 空路径 → (False, None)。
    """
    if not path or not isinstance(path, str):
        return False, None
    cur = data
    for seg in path.split("."):
        if not _SEG_RE.fullmatch(seg):
            return False, None
        if isinstance(cur, dict):
            if seg not in cur:
                return False, None
            cur = cur[seg]
        elif isinstance(cur, list):
            if not seg.isdigit():
                return False, None
            idx = int(seg)
            if idx >= len(cur) or idx < 0:
                return False, None
            cur = cur[idx]
        else:
            return False, None
    return True, cur


def rule_matches(data: Any, rule: ReceiptRule) -> bool:
    """单条回执规则判定；未知 op → False（fail-closed，规则配置错误不发假成功）。"""
    found, value = json_path_get(data, rule.json_path)
    if rule.op == "exists":
        return found
    if rule.op == "not_exists":
        return not found
    if not found:
        return False
    if rule.op == "eq":
        return value == rule.value
    if rule.op == "in":
        seq = rule.value if isinstance(rule.value, (list, tuple, set)) else None
        return seq is not None and value in seq
    return False


def apply_receipt_rules(data: Any, rules: list[ReceiptRule],
                        default_outcome: str = "UNKNOWN") -> str:
    """按顺序匹配，命中即返回 outcome；全部未命中返回 default_outcome。"""
    for r in rules or []:
        if rule_matches(data, r):
            return r.outcome
    return default_outcome
