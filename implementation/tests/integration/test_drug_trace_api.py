"""药品追溯 API 层测试（W07；DTR-A15/A16、DTR-D02—D04、角色审计）。

- TestClient + drug_trace 启用的 registry（含 core 全量路由）。
- 角色矩阵：operator 采集/查询、business_reviewer 处置、platform_admin 证据/发送批准、
  auditor 只读；无权限的写入口 403。
- 列表脱敏：码值掩码、无完整患者信息。
- 对账演练闭环：合成漏单 → 差异清单 → 人工处置（理由/审计）→ 重跑幂等。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import (AuthSubject, ConnectorInstance, Destination,
                            DictionaryRevision, ReconciliationItem, Submission,
                            Tenant)


@pytest.fixture()
def api(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/api.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    client = TestClient(create_app(session_factory=sf, engine=engine,
                                   registry=reg))
    with sf() as db:
        t = Tenant(id=new_uuid(), code="first-hospital", name="首院")
        db.add(t)
        db.flush()
        tid = t.id
        subjects = {}
        for role in ("operator", "reviewer", "admin", "auditor"):
            token = f"tok-{role}"
            s = AuthSubject(id=new_uuid(), tenant_id=tid, kind="user",
                            subject_code=f"user-{role}",
                            roles={"operator": ["operator"],
                                   "reviewer": ["business_reviewer"],
                                   "admin": ["platform_admin"],
                                   "auditor": ["auditor"]}[role],
                            token_hash=__import__("contracts.api",
                                                  fromlist=["hash_token"]).hash_token(token),
                            status="ACTIVE")
            db.add(s)
            subjects[role] = token
        conn = ConnectorInstance(id=new_uuid(), tenant_id=tid,
                                 direction="source",
                                 plugin_id="sources.db_view.drug_trace",
                                 name="his", status="ENABLED")
        db.add(conn)
        for ns, items in (
                ("drugtrace.bill_type", [{"code": "323", "target": "323"}]),
                ("drugtrace.ref_ent_id", [{"code": "YF01", "target": "ENT-H"}]),
                ("drugtrace.query_bill_type", [{"code": "323", "target": "A"}])):
            db.add(DictionaryRevision(
                id=new_uuid(), tenant_id=tid, namespace=ns, version="1",
                items=items, approved_by="admin"))
        db.add(Destination(id=new_uuid(), tenant_id=tid, code="ali-mock",
                           profile_revision="alihealth_yljg_retail_v1",
                           mode="PUSH", capabilities=["upload"],
                           is_mock=True, status="ENABLED"))
        db.commit()
        dest_id = db.query(Destination).first().id
    yield {"client": client, "sf": sf, "tenant_id": tid,
           "subjects": subjects, "connector_id": conn.id, "dest_id": dest_id,
           "engine": engine}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def H(api, role):
    return {"Authorization": f"Bearer {api['subjects'][role]}"}


def _seed_one_bill(api, bill_id="API1", kind="PATIENT_DISPENSE",
                   code="81000000000123"):
    """直写一单（绕过采集，构造对账/查询场景）。"""
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBillRevision
    from sources.db_view.drug_trace import SyntheticDrugTraceWarehouse
    wh = SyntheticDrugTraceWarehouse()
    wh.add_bill({"source_system": "HIS", "source_bill_id": bill_id,
                 "source_bill_no": bill_id, "source_revision": "1",
                 "business_kind": kind, "pharmacy_code": "YF01",
                 "bill_time": "2026-10-12T07:55:00",
                 "updated_at": "2026-10-12T08:00:00", "finalized": True,
                 "is_void": False, "source_bill_kind_raw": "323"})
    wh.add_code({"source_system": "HIS", "source_bill_id": bill_id,
                 "pharmacy_code": "YF01", "business_kind": kind,
                 "source_revision": "1", "source_line_id": "L1",
                 "source_code_record_id": f"R-{bill_id}", "trace_code": code,
                 "updated_at": "2026-10-12T08:00:00", "is_deleted": False,
                 "quantity": "1", "unit": "盒"})
    with api["sf"]() as db:
        service.run_collection(db, api["tenant_id"], api["connector_id"], wh,
                               window_begin=datetime(2026, 10, 12),
                               window_end=datetime(2026, 10, 13))
        rev = db.query(DrugTraceBillRevision).first()
        return rev.id


def test_roles_and_masking(api):
    rev_id = _seed_one_bill(api)
    c = api["client"]
    # auditor 可读列表；码值掩码
    r = c.get("/api/v1/drug-trace/bills", headers=H(api, "auditor"))
    assert r.status_code == 200 and len(r.json()["data"]) == 1
    r = c.get(f"/api/v1/drug-trace/revisions/{rev_id}/codes",
              headers=H(api, "auditor"))
    assert r.status_code == 200
    entry = r.json()["data"][0]
    assert entry["trace_code_masked"].startswith("8100") and "*" in \
        entry["trace_code_masked"]
    assert "trace_code" not in entry  # 不回完整码
    # operator 不能建证据；reviewer 不能采集
    r = c.post("/api/v1/drug-trace/evidence", headers=H(api, "operator"),
               json={"profile": "alihealth_yljg_retail_v1",
                     "capability": "send", "items": []})
    assert r.status_code == 403
    r = c.post("/api/v1/drug-trace/collection-runs", headers=H(api, "reviewer"),
               json={"connector_id": api["connector_id"],
                     "window_begin": "2026-10-12T00:00:00",
                     "window_end": "2026-10-13T00:00:00"})
    assert r.status_code == 403
    # auditor 只读：采集 403
    r = c.post("/api/v1/drug-trace/collection-runs", headers=H(api, "auditor"),
               json={"connector_id": api["connector_id"],
                     "window_begin": "2026-10-12T00:00:00",
                     "window_end": "2026-10-13T00:00:00"})
    assert r.status_code == 403


def test_collection_run_via_api(api):
    c = api["client"]
    r = c.post("/api/v1/drug-trace/collection-runs", headers=H(api, "operator"),
               json={"connector_id": api["connector_id"],
                     "window_begin": "2026-10-12T00:00:00",
                     "window_end": "2026-10-13T00:00:00",
                     "mode": "synthetic"})
    assert r.status_code == 200, r.text
    run_id = r.json()["data"]["collection_run_id"]
    # 窗口校验：begin>=end 拒绝
    r2 = c.post("/api/v1/drug-trace/collection-runs", headers=H(api, "operator"),
                json={"connector_id": api["connector_id"],
                      "window_begin": "2026-10-13T00:00:00",
                      "window_end": "2026-10-13T00:00:00"})
    assert r2.status_code == 422
    # 暂停
    r3 = c.post(f"/api/v1/drug-trace/collection-runs/{run_id}/pause",
                headers=H(api, "operator"))
    assert r3.status_code == 200
    # 真实 oracle 模式无证据 → 409
    r4 = c.post("/api/v1/drug-trace/collection-runs", headers=H(api, "operator"),
                json={"connector_id": api["connector_id"],
                      "window_begin": "2026-10-12T00:00:00",
                      "window_end": "2026-10-13T00:00:00",
                      "mode": "oracle",
                      "profile": "alihealth_yljg_retail_v1"})
    assert r4.status_code == 409 and "EXT" in r4.text


def test_send_requires_confirm_and_evidence(api):
    rev_id = _seed_one_bill(api)
    c = api["client"]
    r = c.post(f"/api/v1/drug-trace/bills/BILL?", headers=H(api, "operator"),
               json={})
    # prepare
    bills = c.get("/api/v1/drug-trace/bills", headers=H(api, "operator")).json()["data"]
    bill_id = bills[0]["id"]
    r = c.post(f"/api/v1/drug-trace/bills/{bill_id}/prepare",
               headers=H(api, "operator"),
               json={"profile": "alihealth_yljg_retail_v1",
                     "destination_id": api["dest_id"]})
    assert r.status_code == 200, r.text
    sub_id = r.json()["data"]["submission_id"]
    # 缺 confirm → 428
    r = c.post(f"/api/v1/drug-trace/submissions/{sub_id}/send",
               headers=H(api, "operator"), json={})
    assert r.status_code == 428
    # confirm 但无证据（send 为真实能力域）→ 409
    r = c.post(f"/api/v1/drug-trace/submissions/{sub_id}/send",
               headers=H(api, "operator"),
               json={"confirm": True, "reason": "test"})
    assert r.status_code == 409 and "EXT" in r.text
    # reviewer 无 send 权限
    r = c.post(f"/api/v1/drug-trace/submissions/{sub_id}/send",
               headers=H(api, "reviewer"),
               json={"confirm": True})
    assert r.status_code == 403


def test_readiness_shows_missing_ext(api):
    c = api["client"]
    r = c.get("/api/v1/drug-trace/readiness", headers=H(api, "auditor"))
    assert r.status_code == 200
    data = r.json()["data"]
    # FIN-06：响应含 profiles + 分层 layers（旧列表包进 profiles 键）
    by_profile = {d["profile"]: d for d in data["profiles"]}
    assert "alihealth_yljg_retail_v1" in by_profile
    retail = by_profile["alihealth_yljg_retail_v1"]
    assert retail["signature_verified"] is True  # EXT-07 已按批准修订关闭（FB-02）
    caps = {x["capability"]: x for x in retail["capabilities"]}
    assert caps["send"]["status"] == "MISSING"
    assert "EXT-07" in caps["send"]["missing"]
    layers = data["layers"]
    assert layers["CODE_READY"]["status"] == "READY"
    assert layers["NETWORK_READY"]["status"] == "UNKNOWN"   # 未测不是绿灯
    # FB-01 统一层语义：证据 MISSING → 层 NOT_READY（层状态只有
    # READY/NOT_READY/UNKNOWN；证据原始状态与缺项在 detail）
    assert layers["SEND_SCOPE_READY"]["status"] == "NOT_READY"
    assert "MISSING" in layers["SEND_SCOPE_READY"]["detail"]
    assert layers["PLATFORM_READY"]["status"] == "NOT_READY"


def test_evidence_lifecycle_and_reconciliation_drill(api):
    """DTR-D04 演练闭环：批准证据 → 对账 run → 差异处置（理由+审计）→ 幂等重跑。"""
    rev_id = _seed_one_bill(api, bill_id="RC1")
    c = api["client"]
    # 无 reconcile 证据 → 拒
    r = c.post("/api/v1/drug-trace/reconciliation-runs",
               headers=H(api, "operator"),
               json={"profile": "alihealth_yljg_retail_v1",
                     "destination_id": api["dest_id"]})
    assert r.status_code == 409
    # admin 批准 reconcile 证据（EXT-09）
    r = c.post("/api/v1/drug-trace/evidence", headers=H(api, "admin"),
               json={"profile": "alihealth_yljg_retail_v1",
                     "capability": "reconcile",
                     "destination_id": api["dest_id"],
                     "items": [{"key": "EXT-09", "evidence_ref": "doc:q"}]})
    assert r.status_code == 200 and r.json()["data"]["status"] == "APPROVED"
    # 建对账 run（直调 service 层跑同步执行器；API 层排 job 属 worker 路径）
    from domains.drug_trace import service
    with api["sf"]() as db:
        result = service.run_reconciliation(
            db, api["tenant_id"], destination_id=api["dest_id"],
            profile="alihealth_yljg_retail_v1",
            window_begin=datetime(2026, 10, 12), window_end=datetime(2026, 10, 13))
        run_id = result["run_id"]
        assert result["summary"]["missing_local"] == 1, "合成漏单被识别"
        item = db.query(ReconciliationItem).filter_by(run_id=run_id).first()
        item_id = item.id
    # 处置：无理由拒绝；有理由通过（reviewer）
    r = c.post(f"/api/v1/drug-trace/reconciliation-items/{item_id}/dispositions",
               headers=H(api, "reviewer"),
               json={"action": "COMPENSATE", "reason": ""})
    assert r.status_code == 422
    r = c.post(f"/api/v1/drug-trace/reconciliation-items/{item_id}/dispositions",
               headers=H(api, "reviewer"),
               json={"action": "COMPENSATE", "reason": "历史漏单，批准补录"})
    assert r.status_code == 200
    # operator 无处置权
    r = c.post(f"/api/v1/drug-trace/reconciliation-items/{item_id}/dispositions",
               headers=H(api, "operator"),
               json={"action": "IGNORE", "reason": "x"})
    assert r.status_code == 403
    # 审计可查
    with api["sf"]() as db:
        from core_db.models import AuditEvent, ManualDecision
        assert db.query(ManualDecision).filter_by(
            entity_ref=f"reconciliation_item:{item_id}").count() == 1
        assert db.query(AuditEvent).filter(
            AuditEvent.action == "drug_trace.reconcile.disposition").count() >= 1
    # 幂等重跑：同范围再跑产生新 run，摘要一致（重跑不重复生效处置）
    with api["sf"]() as db:
        result2 = service.run_reconciliation(
            db, api["tenant_id"], destination_id=api["dest_id"],
            profile="alihealth_yljg_retail_v1",
            window_begin=datetime(2026, 10, 12), window_end=datetime(2026, 10, 13))
        assert result2["summary"]["missing_local"] == 1
    # 撤销证据 → 再建 run 被拒
    r = c.get("/api/v1/drug-trace/readiness", headers=H(api, "admin"))
    with api["sf"]() as db:
        from domains.drug_trace.models import DrugTraceProductionEvidence
        ev = db.query(DrugTraceProductionEvidence).first()
        ev_id = ev.id
    r = c.post(f"/api/v1/drug-trace/evidence/{ev_id}/revoke",
               headers=H(api, "admin"))
    assert r.status_code == 200
    r = c.post("/api/v1/drug-trace/reconciliation-runs",
               headers=H(api, "operator"),
               json={"profile": "alihealth_yljg_retail_v1",
                     "destination_id": api["dest_id"]})
    assert r.status_code == 409
