"""W08 浏览器合成闭环的本地服务（仅离线合成数据）。

启动：python tests/e2e/serve_drug_trace_ui.py [port]
- core_api（drug_trace 启用）+ /ui 静态托管（admin_web/dist）
- 内嵌 worker 线程持续 tick
- 预置租户/角色令牌（operator/reviewer/admin+auditor=同一 admin 令牌简化演示：
  admin 具备全部所需角色）+ 源连接器 + 批准字典 + Mock 目的地
- 令牌：ui-admin-token-0123456789abcdef / ui-operator-token-0123456789abcdef
"""
from __future__ import annotations

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (HERE, os.path.join(HERE, "packages"), os.path.join(HERE, "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _bootstrap  # noqa: F401

DB = os.path.join(os.environ.get("TEMP", "/tmp"), "blyb-dt-ui.sqlite3")
if os.path.exists(DB):
    os.remove(DB)
os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{DB}"

from core_db import make_engine, make_session_factory, run_migrations
import app_runtime

reg = app_runtime.load_registry(["drug_trace"])
engine = make_engine()
run_migrations(engine, extra_migrations=reg.migrations)
sf = make_session_factory(engine)

from contracts import new_uuid
from contracts.api import hash_token
from core_db.models import (AuthSubject, ConnectorInstance, Destination,
                            DictionaryRevision, Tenant)

with sf() as db:
    t = Tenant(id=new_uuid(), code="first-hospital", name="首院")
    db.add(t)
    db.flush()
    tid = t.id
    for code, roles, token in (
            ("user-admin", ["platform_admin", "operator", "business_reviewer"],
             "ui-admin-token-0123456789abcdef"),
            ("user-operator", ["operator"], "ui-operator-token-0123456789abcdef"),
            ("user-reviewer", ["business_reviewer"], "ui-reviewer-token-0123456789abcdef")):
        db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind="user",
                           subject_code=code, display_name=code, roles=roles,
                           token_hash=hash_token(token), status="ACTIVE"))
    conn = ConnectorInstance(id=new_uuid(), tenant_id=tid, direction="source",
                             plugin_id="sources.db_view.drug_trace",
                             name="his-drugtrace-synthetic", status="ENABLED")
    db.add(conn)
    for ns, items in (
            ("drugtrace.bill_type", [{"code": "323", "target": "323"},
                                     {"code": "101", "target": "101"}]),
            ("drugtrace.ref_ent_id", [{"code": "YF01", "target": "ENT-H-001"},
                                      {"code": "CK01", "target": "ENT-H-001"}]),
            # FIX3-01：查询选择器独立字典（官方 A/AI/AO；冻结进 payload）
            ("drugtrace.query_bill_type", [{"code": "323", "target": "A"},
                                           {"code": "101", "target": "AI"}]),
            # FIX3-01：库存单收发企业 ent_id（医院/供应商各一字典，值与 ref 不同）
            ("drugtrace.ent_id", [{"code": "YF01", "target": "HOSP-ENT-001"},
                                  {"code": "CK01", "target": "HOSP-ENT-002"}]),
            ("drugtrace.supplier_ent_id", [{"code": "S1", "target": "SUP-ENT-001"}])):
        db.add(DictionaryRevision(
            id=new_uuid(), tenant_id=tid, namespace=ns, version="1",
            items=items, approved_by="seed-admin"))
    db.add(Destination(id=new_uuid(), tenant_id=tid, code="alihealth-mock",
                       profile_revision="alihealth_yljg_retail_v1", mode="PUSH",
                       capabilities=["upload", "query"], is_mock=True,
                       status="ENABLED"))
    db.commit()

# 注入合成数据到 make_warehouse（采集演练用）
from sources.db_view.drug_trace import SyntheticDrugTraceWarehouse
from domains.drug_trace import service

_wh = SyntheticDrugTraceWarehouse()
for bid, kind, pharm, raw, updated in (
        ("UI-B001", "PATIENT_DISPENSE", "YF01", "323", "2026-10-12T08:00:00"),
        ("UI-B002", "UNKNOWN", "YF01", "???", "2026-10-12T09:00:00"),
        ("UI-B003", "SUPPLIER_IN", "CK01", "101", "2026-10-12T10:00:00")):
    _wh.add_bill({"source_system": "HIS", "source_bill_id": bid,
                  "source_bill_no": bid, "source_revision": "1",
                  "business_kind": kind, "pharmacy_code": pharm,
                  "bill_time": "2026-10-12T07:55:00", "updated_at": updated,
                  "finalized": True, "is_void": False,
                  "supplier_code": "S1",
                  "source_bill_kind_raw": raw})
_wh.add_code({"source_system": "HIS", "source_bill_id": "UI-B001",
              "pharmacy_code": "YF01", "business_kind": "PATIENT_DISPENSE",
              "source_revision": "1", "source_line_id": "L1001",
              "source_code_record_id": "R9000001",
              "trace_code": "000123456789012345",
              "updated_at": "2026-10-12T08:00:00", "is_deleted": False,
              "quantity": "1", "unit": "盒", "code_level": "T"})
_wh.add_code({"source_system": "HIS", "source_bill_id": "UI-B002",
              "pharmacy_code": "YF01", "business_kind": "UNKNOWN",
              "source_revision": "1", "source_line_id": "L1002",
              "source_code_record_id": "R9000002",
              "trace_code": "000223456789012345",
              "updated_at": "2026-10-12T09:00:00", "is_deleted": False,
              "quantity": "1", "unit": "盒"})
_wh.add_code({"source_system": "HIS", "source_bill_id": "UI-B003",
              "pharmacy_code": "CK01", "business_kind": "SUPPLIER_IN",
              "source_revision": "1", "source_line_id": "L3001",
              "source_code_record_id": "R9000003",
              "trace_code": "000323456789012345",
              "updated_at": "2026-10-12T10:00:00", "is_deleted": False,
              "quantity": "10", "unit": "盒", "code_level": "F"})
service.make_warehouse = lambda cfg: _wh

from apps.worker.engine import WorkerEngine, PluginRegistryAdapter
_we = WorkerEngine(sf, registry=PluginRegistryAdapter(reg))


def _worker_loop():
    while True:
        try:
            _we.tick()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)


threading.Thread(target=_worker_loop, daemon=True).start()

import uvicorn
from apps.core_api.app import create_app

app = create_app(session_factory=sf, engine=engine, registry=reg)

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"UI: http://127.0.0.1:{port}/ui/  tokens: ui-admin-token-0123456789abcdef")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
