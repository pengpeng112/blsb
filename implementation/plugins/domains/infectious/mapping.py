"""影子映射（LT-05 B3）：复用 pusher 纯函数——只读加载，不复制、不修改生产。

pusher.py 顶层仅 stdlib + tables（oracledb 在 _init_client 内延迟导入），
可安全 spec 加载；camel/norm_date_str/apply_view_shims/validate_required
以生产源码为准（等价性由 test_inf_payload_equivalence 用**真实分支**驱动
run_table 证明，非源码字符串比对）。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timezone, timedelta

_PUSHER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))),
    "deploy", "inf_pusher", "pusher.py")
_pusher = None


def load_pusher():
    """只读加载 deploy/inf_pusher/pusher.py（无副作用；失败抛 ImportError）。"""
    global _pusher
    if _pusher is not None:
        return _pusher
    pusher_dir = os.path.dirname(_PUSHER_PATH)
    saved = sys.path[:]
    sys.path.insert(0, pusher_dir)          # pusher 顶层 `from tables import ...`
    try:
        spec = importlib.util.spec_from_file_location(
            "_shadow_inf_pusher", _PUSHER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved
    _pusher = mod
    return mod


CST = timezone(timedelta(hours=8))
DATE_FMT = "%Y-%m-%d %H:%M:%S"


def build_payload(table_key: str, row: dict, parent_ops: dict) -> dict:
    """视图行 → 规范 payload（camel+shims；与 pusher 真实分支同函数）。"""
    p = load_pusher()
    from domains.infectious.tables import TABLES
    t = TABLES[table_key]
    payload = p.camel(t["map"], row)
    p.apply_view_shims(table_key, payload, parent_ops)
    return payload


def apply_shims(table_key: str, payload: dict, parent_ops: dict) -> dict:
    """单独重跑视图垫片（父补查回填 parent_ops 后二跑；幂等：值在即不覆盖）。"""
    p = load_pusher()
    p.apply_view_shims(table_key, payload, parent_ops)
    return payload


def validate(table_key: str, payload: dict) -> list[str]:
    p = load_pusher()
    from domains.infectious.tables import TABLES
    return p.validate_required(payload, TABLES[table_key]["required"])


def source_op_time(payload: dict) -> str:
    return str(payload.get("operationTime", "") or "")


def rewrite_upload_time(payload: dict, now_cst: datetime) -> dict:
    """真实分支上传语义（手册整改§三-2）：operationTime=接口上传时刻；
    源时间用于水位/去重/对账（与 pusher run_table 真实分支同式）。"""
    out = dict(payload)
    out["operationTime"] = now_cst.strftime(DATE_FMT)
    return out


def apply_truncation(table_key: str, payload: dict) -> int:
    """截断统计（与 pusher 相同只计数不截断语义）。"""
    from domains.infectious.tables import TABLES
    n = 0
    for f, cap in TABLES[table_key].get("truncation_fields", {}).items():
        if payload.get(f) and len(payload[f]) >= cap:
            n += 1
    return n
