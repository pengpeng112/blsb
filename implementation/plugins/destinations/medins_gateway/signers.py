"""签名器接口（LT-07；R09）。

规范只定义了 cainfo（数字签名信息）/signtype（签名类型）两个字段，**未给出
算法、密钥形态或测试向量**——因此：
- 正式签名请求 → SIGNATURE_UNCONFIRMED 显式能力错误（不实现行业猜测算法，
  如 TifHmac 之类不在无证据下交付）；
- NoneSigner 仅用于明确无签名要求的 Mock 联调；
- 待院方/医保提供算法说明与向量后，在此实现并用向量验证（另行任务）。
"""
from __future__ import annotations

from typing import Protocol

from connector_sdk import CapabilityError

SIGNATURE_UNCONFIRMED = "SIGNATURE_UNCONFIRMED"


class Signer(Protocol):
    def headers(self, body_bytes: bytes, now) -> dict:
        ...


class NoneSigner:
    """明确无签名的 Mock 联调用（不产生任何签名头）。"""

    def headers(self, body_bytes: bytes, now) -> dict:
        return {}


def require_formal_signer():
    """正式发送入口调用：规范未证实签名算法 → 显式拒绝。"""
    raise CapabilityError(
        SIGNATURE_UNCONFIRMED,
        "规范未给出签名算法与测试向量（仅定义 cainfo/signtype 字段）——"
        "正式签名能力待院方/医保确认后实现；Mock 联调可用 NoneSigner")
