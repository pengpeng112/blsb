"""LT-03 集成：台账 3 个 API 端点（列表/详情/人工处置）+ 角色/校验/隔离。"""
from __future__ import annotations

import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.engine import utcnow  # noqa: E402
from core_db.models import (AuthSubject, DataPackage, Destination,
                            PackageRevision, Submission, Tenant)  # noqa: E402
from plugins.report_targets import seed_default_targets  # noqa: E402
from contracts.api import hash_token  # noqa: E402

from tests.integration.test_report_ledger import _mk  # noqa: E402  (row factory)


@pytest.fixture()
def api(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/ledger-api.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace", "pathology"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    client = TestClient(create_app(session_factory=sf, engine=engine,
                                   registry=reg))
    with sf() as db:
        t = Tenant(id=new_uuid(), code="lc-api", name="台账API")
        db.add(t)
        db.flush()
        tid = t.id
        dest = Destination(id=new_uuid(), tenant_id=tid, code="path-mock",
                           profile_revision="profile.pathology_internal_v1",
                           mode="PUSH", is_mock=True, status="ENABLED")
        db.add(dest)
        db.flush()
        seed_default_targets(db, tid)
        db.commit()
        from core_db.models import ReportTarget
        db.query(ReportTarget).filter_by(
            code="pathology_cloud").one().destination_id = dest.id
        subjects = {}
        for role, roles in (("reviewer", ["business_reviewer"]),
                            ("admin", ["platform_admin"]),
                            ("operator", ["operator"])):
            tok = f"tok-{role}"
            db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind="user",
                               subject_code=f"user-{role}", roles=roles,
                               token_hash=hash_token(tok), status="ACTIVE", target_scopes=["*"]))
            subjects[role] = tok
        db.commit()
    yield {"client": client, "sf": sf, "engine": engine, "tid": tid,
           "dest_id": dest.id, "subjects": subjects}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def H(api, role):
    return {"Authorization": f"Bearer {api['subjects'][role]}"}


def test_list_requires_target_param(api):
    r = api["client"].get("/api/v1/report-center/submissions",
                          headers=H(api, "reviewer"))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "TARGET_REQUIRED"


def test_list_unknown_target_404(api):
    r = api["client"].get("/api/v1/report-center/submissions?target=ghost",
                          headers=H(api, "reviewer"))
    assert r.status_code == 404


def test_list_bucket_and_limit_validation(api):
    r = api["client"].get(
        "/api/v1/report-center/submissions?target=pathology_cloud&bucket=NOPE",
        headers=H(api, "reviewer"))
    assert r.status_code == 422
    r = api["client"].get(
        "/api/v1/report-center/submissions?target=pathology_cloud&limit=500",
        headers=H(api, "reviewer"))
    assert r.status_code == 422


def test_list_empty_target_no_hook_returns_none_datasource(api):
    # medins_imaging_jn：无钩子认领、无绑定目的地 → 空页 + data_source=none
    r = api["client"].get(
        "/api/v1/report-center/submissions?target=medins_imaging_jn",
        headers=H(api, "reviewer"))
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["data_source"] == "none" and d["items"] == []


def test_list_finds_oldest_unknowns_via_api(api):
    """API 层复现 300 条反例（任务书核心回归）。"""
    made = [_mk(api, "ACCEPTED", age_minutes=0) for _ in range(295)]
    unk = [_mk(api, "UNKNOWN", age_minutes=60 * 24 * 30) for _ in range(5)]
    r = api["client"].get(
        "/api/v1/report-center/submissions"
        "?target=pathology_cloud&bucket=UNKNOWN&limit=50",
        headers=H(api, "reviewer"))
    assert r.status_code == 200
    d = r.json()["data"]
    assert {i["id"] for i in d["items"]} == set(unk)
    assert d["total_estimate"] == 5


def test_detail_route_for_drug_target(api):
    r = api["client"].get(
        "/api/v1/report-center/submissions/drug_trace_alihealth/some-id",
        headers=H(api, "reviewer"))
    assert r.status_code == 200
    assert r.json()["data"]["detail_route"] == "/drugtrace"


def test_detail_generic_submission(api):
    sid = _mk(api, "UNKNOWN")
    r = api["client"].get(
        f"/api/v1/report-center/submissions/pathology_cloud/{sid}",
        headers=H(api, "reviewer"))
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["state"] == "UNKNOWN" and "attempts" in d


def _mk_resolvable(api, state="UNKNOWN"):
    """LT-03-R1：满足处置前提（冻结摘要+尝试证据）的提交。"""
    from core_db.models import DeliveryAttempt
    sid = _mk(api, state, snapshot_digest="cd" * 32)
    with api["sf"]() as db:
        db.add(DeliveryAttempt(
            id=new_uuid(), tenant_id=api["tid"], submission_id=sid,
            attempt_no=1, request_digest="d" * 64, started_at=utcnow(),
            ended_at=utcnow(), outcome="UNKNOWN", response_snapshot={}))
        db.commit()
    return sid


def test_manual_resolution_by_reviewer(api):
    sid = _mk_resolvable(api)
    r = api["client"].post(
        f"/api/v1/report-center/submissions/pathology_cloud/{sid}/manual-resolution",
        headers=H(api, "reviewer"),
        json={"resolution": "CONFIRMED", "reason": "线下核实回执无误备注",
              "evidence_ref": "rcpt-1", "expected_state": "UNKNOWN"})
    assert r.status_code == 200
    assert r.json()["data"]["to_state"] == "CONFIRMED"


def test_manual_resolution_by_operator_403(api):
    sid = _mk(api, "UNKNOWN")
    r = api["client"].post(
        f"/api/v1/report-center/submissions/pathology_cloud/{sid}/manual-resolution",
        headers=H(api, "operator"),
        json={"resolution": "CONFIRMED", "reason": "线下核实回执无误备注",
              "expected_state": "UNKNOWN"})
    assert r.status_code == 403


def test_manual_resolution_drug_target_400(api):
    sid = _mk(api, "UNKNOWN")
    r = api["client"].post(
        "/api/v1/report-center/submissions/drug_trace_alihealth/"
        f"{sid}/manual-resolution",
        headers=H(api, "reviewer"),
        json={"resolution": "CONFIRMED", "reason": "药品走专页受控流程x",
              "expected_state": "UNKNOWN"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "USE_DOMAIN_WORKFLOW"


def test_manual_resolution_cas_conflict_409(api):
    sid = _mk_resolvable(api)
    body = {"resolution": "CONFIRMED", "reason": "第一次处置理由充分x",
            "expected_state": "UNKNOWN"}
    r1 = api["client"].post(
        f"/api/v1/report-center/submissions/pathology_cloud/{sid}/manual-resolution",
        headers=H(api, "reviewer"), json=body)
    assert r1.status_code == 200
    r2 = api["client"].post(
        f"/api/v1/report-center/submissions/pathology_cloud/{sid}/manual-resolution",
        headers=H(api, "admin"),
        json={"resolution": "REJECTED", "reason": "第二次并发处置理由x",
              "expected_state": "UNKNOWN"})
    assert r2.status_code == 409
