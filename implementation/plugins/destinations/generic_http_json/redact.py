"""错误信息脱敏（LT-03）：回执/异常文本入库前必须经过 redact_text。

- 连续 ≥6 位数字 → ***（证件号/电话/病案号等）；
- 18 位身份证模式（含校验位 X）整体替换；
- 中文姓名无法可靠识别 → error_message 上限截断为前 200 字（调用方再 ≤500）。
"""
from __future__ import annotations

import re

_DIGITS6 = re.compile(r"\d{6,}")
_ID18 = re.compile(
    r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:[0-2]\d|3[01])\d{3}[0-9Xx]")

MAX_MESSAGE_LEN = 200


def redact_text(s: str | None) -> str:
    if not s:
        return ""
    out = _ID18.sub("***", s)
    out = _DIGITS6.sub("***", out)
    if len(out) > MAX_MESSAGE_LEN:
        out = out[:MAX_MESSAGE_LEN] + "…"
    return out
