"""医保网关封装（LT-07 步骤5）。

R09 落地：
- msgid：规范已证实规则（机构号12+时间14+顺序号4=30字符，yyyyMMddHHmmss，
  不允许重复）→ 实现并测试长度/唯一性；
- 签名：规范只定义 cainfo/signtype 字段、未给算法与向量 → 正式签名返回
  SIGNATURE_UNCONFIRMED（不实现猜测算法）；NoneSigner 仅限明确无签名的 Mock；
- envelope 取值来自配置（不从样例读值）；真实发送被 MOCK_ONLY+loopback 服务端
  拒绝（不依赖可编辑的 Target 字段）。
"""
from .envelope import build_envelope  # noqa: F401
from .msgid import build_msgid, MSGID_RULE_UNCONFIRMED  # noqa: F401
from .signers import (NoneSigner, SIGNATURE_UNCONFIRMED, Signer,  # noqa: F401
                       require_formal_signer)
