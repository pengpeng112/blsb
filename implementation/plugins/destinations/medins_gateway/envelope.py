"""公共报文头组装（LT-07）：字段值一律来自配置，不从样例读取。"""
from __future__ import annotations

from .msgid import build_msgid

# 报文头字段清单（与 fields/_envelope.json 对齐；Y=必填按规范）
ENVELOPE_FIELDS = ("info", "msgid", "mdtrtarea_admvs", "insuplc_admdvs",
                   "recer_sys_code", "dev_no", "dev_safe_info", "cainfo",
                   "signtype", "infver", "opter_type", "opter", "opter_name",
                   "inf_time", "fixmedins_code", "sign_no")


def build_envelope(config: dict, info_code: str, input_body: dict,
                   now, seq: int = 1) -> dict:
    """config 取值（缺省为空串/None 由调用方校验必填）；inf_time 用传入时钟。

    msgid 按规范已证实规则生成（机构号12+时间14+顺序号4）。"""
    env = {
        "info": info_code,
        "msgid": build_msgid(str(config.get("fixmedins_code", "") or ""),
                             now, seq),
        "mdtrtarea_admvs": config.get("mdtrtarea_admvs", ""),
        "insuplc_admdvs": config.get("insuplc_admdvs", ""),
        "recer_sys_code": config.get("recer_sys_code", ""),
        "dev_no": config.get("dev_no", ""),
        "dev_safe_info": config.get("dev_safe_info", ""),
        "cainfo": config.get("cainfo", ""),
        "signtype": config.get("signtype", ""),
        "infver": config.get("infver", ""),
        "opter_type": config.get("opter_type", ""),
        "opter": config.get("opter", ""),
        "opter_name": config.get("opter_name", ""),
        "inf_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "fixmedins_code": config.get("fixmedins_code", ""),
        "sign_no": config.get("sign_no", ""),
        "input": input_body,
    }
    return env
