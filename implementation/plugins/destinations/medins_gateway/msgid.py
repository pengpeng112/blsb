"""msgid 生成（LT-07）：规范已证实规则——定点医药机构编号(12)+时间(14,
yyyyMMddHHmmss)+顺序号(4)，共 30 字符，不允许重复（规范:公共报文头表 msgid 行）。
"""
from __future__ import annotations

MSGID_RULE_UNCONFIRMED = "MSGID_RULE_UNCONFIRMED"


def build_msgid(fixmedins_code: str, now, seq: int = 1) -> str:
    """机构号左补齐/截断到 12 位 + 时间 14 位 + 顺序号 4 位。

    机构号非 12 位时按规范宽度处理（右补 0 / 超长截断），并由调用方负责
    机构号本身与医保登记一致；seq 由调用方保证同秒内递增以维持唯一性。"""
    org = (str(fixmedins_code or "").strip() + "0" * 12)[:12]
    ts = now.strftime("%Y%m%d%H%M%S")
    s = f"{int(seq) % 10000:04d}"
    mid = org + ts + s
    assert len(mid) == 30
    return mid
