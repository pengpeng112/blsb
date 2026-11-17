"""LT-03-R1 集成：台账游标上下文绑定 / 详情与处置 target 归属 / 院内日界
（北京零点）/ summary 口径分离（窗口 vs 全量积压 vs 质量等待 / days 应用 /
Mock 证据形态）。"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.engine import utcnow  # noqa: E402
from core_db.models import (DataPackage, DeliveryAttempt, Destination,  # noqa: E402
                            PackageRevision, ReportTarget, Submission, Tenant)
from plugins.report_targets import seed_default_targets  # noqa: E402
from contracts.api import hash_token  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/r1ls.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace", "pathology"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="r1-ls", name="台账摘要")
        db.add(t)
        db.flush()
        tid = t.id
        dest_a = Destination(id=new_uuid(), tenant_id=tid, code="dest-a",
                             profile_revision="destination.generic_http_json@1",
                             mode="PUSH", is_mock=True, status="ENABLED")
        dest_b = Destination(id=new_uuid(), tenant_id=tid, code="dest-b",
                             profile_revision="destination.generic_http_json@1",
                             mode="PUSH", is_mock=True, status="ENABLED")
        db.add_all([dest_a, dest_b])
        db.flush()
        seed_default_targets(db, tid)
        db.commit()
        db.query(ReportTarget).filter_by(
            code="pathology_cloud").one().destination_id = dest_a.id
        db.add(ReportTarget(
            id=new_uuid(), tenant_id=tid, code="shadow_a", name="影子A",
            revision=1, lifecycle="MOCK_ONLY", runtime_kind="PLATFORM",
            sole_pusher="UNCONFIRMED", destination_id=dest_a.id))
        db.add(ReportTarget(
            id=new_uuid(), tenant_id=tid, code="shadow_b", name="影子B",
            revision=1, lifecycle="MOCK_ONLY", runtime_kind="PLATFORM",
            sole_pusher="UNCONFIRMED", destination_id=dest_b.id))
        db.commit()
    yield {"sf": sf, "engine": engine, "tid": tid, "registry": reg,
           "dest_a": dest_a.id, "dest_b": dest_b.id}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _mk(env, state="ACCEPTED", *, domain="pathology", dest_id=None,
        created_at=None):
    with env["sf"]() as db:
        pkg = DataPackage(id=new_uuid(), tenant_id=env["tid"], domain=domain,
                          business_key=f"bk-{new_uuid()[:8]}")
        db.add(pkg)
        db.flush()
        pr = PackageRevision(id=new_uuid(), tenant_id=env["tid"],
                             package_id=pkg.id, revision=1, state="FROZEN")
        db.add(pr)
        db.flush()
        s = Submission(id=new_uuid(), tenant_id=env["tid"],
                       package_revision_id=pr.id,
                       destination_id=dest_id or env["dest_a"],
                       idempotency_key=f"idem-{new_uuid()[:8]}",
                       state=state, snapshot_digest="ab" * 32)
        db.add(s)
        db.commit()
        if created_at is not None:
            from sqlalchemy import update
            db.execute(update(Submission).where(Submission.id == s.id)
                       .values(created_at=created_at, updated_at=created_at))
            db.commit()
        return s.id


# ================================================================ 游标上下文绑定
def _search(env, target, bucket=None, q=None, cursor=None, limit=50):
    from plugins import report_ledger as RL
    from plugins.domain_hooks import LedgerFilter
    flt = LedgerFilter(bucket=bucket, date_from=None, date_to=None, q=q)
    return RL.search(env["sf"]().__enter__() if False else _db(env),
                     env["tid"], target, flt, cursor, limit)


def _db(env):
    # 与钩子相同的调用形态：复用同一 session 查询（只读）
    return env["sf"]()


def test_cursor_bound_to_filter_context(env):
    from contracts import ApiError
    from plugins import report_ledger as RL
    from plugins.domain_hooks import LedgerFilter

    for _ in range(3):
        _mk(env, "UNKNOWN")
    with env["sf"]() as db:
        flt = LedgerFilter(bucket=None)
        rows, cur = RL.search(db, env["tid"], "pathology_cloud", flt, None, 2)
        assert cur is not None
        # 同上下文复用游标：合法（不抛）
        flt2 = LedgerFilter(bucket=None)
        RL.search(db, env["tid"], "pathology_cloud", flt2, cur, 2)
        # 换筛选条件（bucket 变化）→ 拒绝
        with pytest.raises(ApiError) as ei:
            RL.search(db, env["tid"], "pathology_cloud",
                      LedgerFilter(bucket="UNKNOWN"), cur, 2)
        assert ei.value.code == "CURSOR_CONTEXT_MISMATCH"
        # 换 q → 拒绝
        with pytest.raises(ApiError):
            RL.search(db, env["tid"], "pathology_cloud",
                      LedgerFilter(q="abc"), cur, 2)
        # 换 target → 拒绝
        with pytest.raises(ApiError):
            RL.search(db, env["tid"], "shadow_a",
                      LedgerFilter(bucket=None), cur, 2)


def test_cursor_v1_without_fingerprint_rejected(env):
    """旧 v1 游标（无指纹）在要求绑定上下文时按不匹配拒绝。"""
    import base64
    from contracts import ApiError
    from plugins import report_ledger as RL
    from plugins.domain_hooks import LedgerFilter
    with env["sf"]() as db:
        v1 = base64.urlsafe_b64encode(
            "2026-11-10T00:00:00|some-id".encode()).decode()
        with pytest.raises(ApiError) as ei:
            RL.search(db, env["tid"], "pathology_cloud",
                      LedgerFilter(bucket=None), v1, 10)
        assert ei.value.code == "CURSOR_CONTEXT_MISMATCH"


# ================================================================ 详情/处置归属
def test_detail_cross_target_returns_none(env):
    from plugins import report_ledger as RL
    sid = _mk(env, "UNKNOWN")           # dest_a → pathology_cloud/shadow_a
    with env["sf"]() as db:
        # 正确 target（绑定 dest_a）→ 有详情
        assert RL.submission_detail(db, env["tid"], sid,
                                    target_code="shadow_a") is not None
        # 跨 target（shadow_b 绑定 dest_b）→ 按不存在处理
        assert RL.submission_detail(db, env["tid"], sid,
                                    target_code="shadow_b") is None
        # 无绑定 target（传染病目录等 destination_id 为空）→ 拒绝（不泄露）
        assert RL.submission_detail(db, env["tid"], sid,
                                    target_code="medins_emr_sd") is None


def test_detail_domain_binding(env):
    from plugins import report_ledger as RL
    path_sid = _mk(env, "ACCEPTED", domain="pathology")
    metric_sid = _mk(env, "ACCEPTED", domain="metrics")
    with env["sf"]() as db:
        assert RL.submission_detail(db, env["tid"], path_sid,
                                    domain="pathology") is not None
        assert RL.submission_detail(db, env["tid"], metric_sid,
                                    domain="pathology") is None


def test_detail_requires_binding_argument(env):
    from plugins import report_ledger as RL
    sid = _mk(env, "ACCEPTED")
    with env["sf"]() as db:
        with pytest.raises(ValueError):
            RL.submission_detail(db, env["tid"], sid)


def test_manual_resolution_cross_target_not_found(env):
    from plugins import report_ledger as RL
    from contracts import ApiError
    sid = _mk(env, "UNKNOWN")
    with env["sf"]() as db:
        db.add(DeliveryAttempt(
            id=new_uuid(), tenant_id=env["tid"], submission_id=sid,
            attempt_no=1, request_digest="d" * 64, started_at=utcnow(),
            ended_at=utcnow(), outcome="UNKNOWN", response_snapshot={}))
        db.commit()
        # 跨 target 处置 → NOT_FOUND
        with pytest.raises(ApiError) as ei:
            RL.manual_resolution(db, env["tid"], "shadow_b", sid,
                                 resolution="REJECTED", reason="跨目标处置",
                                 evidence_ref="E", expected_state="UNKNOWN",
                                 actor="r")
        assert ei.value.code == "NOT_FOUND"
        # 无绑定 target → NOT_FOUND（旧实现 dest_ids 空会放行）
        with pytest.raises(ApiError) as ei2:
            RL.manual_resolution(db, env["tid"], "medins_emr_sd", sid,
                                 resolution="REJECTED", reason="无绑定处置",
                                 evidence_ref="E", expected_state="UNKNOWN",
                                 actor="r")
        assert ei2.value.code == "NOT_FOUND"


def test_manual_resolution_zero_attempts_rejected(env):
    from plugins import report_ledger as RL
    from contracts import ApiError
    sid = _mk(env, "UNKNOWN")            # 有 digest、零尝试
    with env["sf"]() as db:
        with pytest.raises(ApiError) as ei:
            RL.manual_resolution(db, env["tid"], "shadow_a", sid,
                                 resolution="CONFIRMED", reason="无尝试证据",
                                 evidence_ref="E", expected_state="UNKNOWN",
                                 actor="r")
        assert "DeliveryAttempt" in ei.value.message


# ================================================================ 北京零点日界
def test_hospital_day_boundary_beijing_midnight(env):
    """date_from/date_to 按院内日（UTC+8）换算：2026-11-10 院日 =
    UTC [11-09T16:00:00, 11-10T16:00:00)。"""
    from plugins import report_ledger as RL
    from plugins.domain_hooks import LedgerFilter
    in_day_early = _mk(env, "ACCEPTED",
                       created_at=datetime(2026, 11, 9, 16, 0, 0,
                                           tzinfo=timezone.utc).replace(
                           tzinfo=None))
    before_day = _mk(env, "ACCEPTED",
                     created_at=datetime(2026, 11, 9, 15, 59, 59,
                                         tzinfo=timezone.utc).replace(
                         tzinfo=None))
    last_second = _mk(env, "ACCEPTED",
                      created_at=datetime(2026, 11, 10, 15, 59, 59,
                                          tzinfo=timezone.utc).replace(
                          tzinfo=None))
    next_day_first = _mk(env, "ACCEPTED",
                         created_at=datetime(2026, 11, 10, 16, 0, 0,
                                             tzinfo=timezone.utc).replace(
                             tzinfo=None))
    with env["sf"]() as db:
        flt = LedgerFilter(bucket=None, date_from="2026-11-10",
                           date_to="2026-11-10")
        rows, _ = RL.search(db, env["tid"], "pathology_cloud", flt, None, 50)
        ids = {r["id"] for r in rows}
        assert in_day_early in ids
        assert last_second in ids
        assert before_day not in ids
        assert next_day_first not in ids


# ================================================================ summary 口径
def _mk_bill_waiting(env, quality_state="WAITING"):
    with env["sf"]() as db:
        from domains.drug_trace.models import DrugTraceBill, \
            DrugTraceBillRevision
        bill = DrugTraceBill(
            id=new_uuid(), tenant_id=env["tid"], connector_id="c1",
            source_system="HIS", pharmacy_code="050102",
            business_kind="OUTPATIENT", source_bill_id=f"sb{new_uuid()[:6]}")
        db.add(bill)
        db.flush()
        rev = DrugTraceBillRevision(
            id=new_uuid(), tenant_id=env["tid"], bill_id=bill.id,
            source_revision="r1", content_digest="d" * 64, header={},
            lines=[], quality_state=quality_state, quality_issues=[])
        db.add(rev)
        db.flush()
        bill.current_revision_id = rev.id
        db.commit()


def test_drug_summary_window_vs_total_backlog(env):
    """窗口外 UNKNOWN 不进窗口桶，但必须进 backlog_total；质量等待单据
    （无 Submission）单列 quality_waiting。"""
    from domains.drug_trace import hooks as dt_hooks
    _mk(env, "UNKNOWN", domain="drug_trace",
        created_at=utcnow() - timedelta(days=30))
    _mk(env, "CONFIRMED", domain="drug_trace")
    _mk_bill_waiting(env, "WAITING")
    _mk_bill_waiting(env, "VALID")          # 非等待不计
    with env["sf"]() as db:
        s = dt_hooks.target_summary(db, env["tid"], "drug_trace_alihealth", 7)
        assert s is not None
        assert s.window_kind == "rolling_days"
        assert s.evidence_kind == "real"
        assert s.buckets.get("UNKNOWN", 0) == 0          # 30 天前不在窗口
        assert s.buckets.get("CONFIRMED", 0) == 1
        assert s.backlog_total == 1                       # 窗外 UNKNOWN 全量计
        assert s.backlog == 0                              # 窗口内无未解决
        assert s.quality_waiting == 1                      # WAITING 单据单列
        assert s.last_success_at                           # 全量最近确认存在


def test_pathology_summary_applies_days_window(env):
    """病理 summary 应用 days（旧实现统计全量、忽略 days）。"""
    from domains.pathology import hooks as path_hooks
    old_unknown = _mk(env, "UNKNOWN",
                      created_at=utcnow() - timedelta(days=30))
    _mk(env, "ACCEPTED")
    with env["sf"]() as db:
        s = path_hooks.target_summary(db, env["tid"], "pathology_cloud", 7)
        assert s is not None
        assert s.buckets.get("UNKNOWN", 0) == 0
        assert s.buckets.get("ACCEPTED", 0) == 1
        assert s.backlog_total == 1                        # 全量积压含窗外
        assert s.backlog == 0
        assert s.evidence_kind == "mock"
        assert s.window_kind == "rolling_days"
        _ = old_unknown


def test_pathology_summary_days_30_includes_old(env):
    from domains.pathology import hooks as path_hooks
    _mk(env, "UNKNOWN", created_at=utcnow() - timedelta(days=30))
    with env["sf"]() as db:
        s = path_hooks.target_summary(db, env["tid"], "pathology_cloud", 31)
        assert s.buckets.get("UNKNOWN", 0) == 1
        assert s.backlog == 1


# ================================================================ API 层
@pytest.fixture()
def api(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/r1api.sqlite3"
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
        t = Tenant(id=new_uuid(), code="r1-api", name="R1API")
        db.add(t)
        db.flush()
        tid = t.id
        seed_default_targets(db, tid)
        db.commit()
        from core_db.models import AuthSubject
        tok = "tok-r1"
        db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind="user",
                           subject_code="u1", roles=["business_reviewer"],
                           token_hash=hash_token(tok), status="ACTIVE", target_scopes=["*"]))
        db.commit()
    yield {"client": client, "sf": sf, "engine": engine, "tid": tid,
           "tok": tok, "dest": None}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _api_env_with_dest(api):
    """给 pathology_cloud 绑定一个目的地并造两条 UNKNOWN（其一含尝试证据）。"""
    with api["sf"]() as db:
        dest = Destination(id=new_uuid(), tenant_id=api["tid"], code="pd",
                           profile_revision="destination.generic_http_json@1",
                           mode="PUSH", is_mock=True, status="ENABLED")
        db.add(dest)
        db.flush()
        db.query(ReportTarget).filter_by(
            code="pathology_cloud").one().destination_id = dest.id
        db.commit()
        dest_id = dest.id
    sid = _mk(api, "UNKNOWN", dest_id=dest_id)
    _mk(api, "UNKNOWN", dest_id=dest_id)      # 第二条：limit=1 时产生游标
    with api["sf"]() as db:
        db.add(DeliveryAttempt(
            id=new_uuid(), tenant_id=api["tid"], submission_id=sid,
            attempt_no=1, request_digest="d" * 64, started_at=utcnow(),
            ended_at=utcnow(), outcome="UNKNOWN", response_snapshot={}))
        db.commit()
    return sid, dest_id


def test_api_cursor_context_mismatch_400(api):
    sid, _ = _api_env_with_dest(api)
    h = {"Authorization": f"Bearer {api['tok']}"}
    c = api["client"]
    r = c.get("/api/v1/report-center/submissions"
              "?target=pathology_cloud&limit=1", headers=h)
    assert r.status_code == 200
    cur = r.json()["data"]["next_cursor"]
    assert cur
    r2 = c.get("/api/v1/report-center/submissions"
               f"?target=pathology_cloud&bucket=UNKNOWN&cursor={cur}",
               headers=h)
    assert r2.status_code == 400
    assert r2.json()["error"]["code"] == "CURSOR_CONTEXT_MISMATCH"


def test_api_detail_cross_target_404(api):
    """API：同一提交经未绑定它的 target 查详情 → 404（不泄露）。"""
    sid, _ = _api_env_with_dest(api)
    h = {"Authorization": f"Bearer {api['tok']}"}
    c = api["client"]
    r = c.get(f"/api/v1/report-center/submissions/pathology_cloud/{sid}",
              headers=h)
    assert r.status_code == 200
    assert r.json()["data"]["id"] == sid
    # medins_emr_sd（PLANNED、无绑定）查同一 id → 404
    r2 = c.get(f"/api/v1/report-center/submissions/medins_emr_sd/{sid}",
               headers=h)
    assert r2.status_code == 404


def test_api_summary_new_fields(api):
    h = {"Authorization": f"Bearer {api['tok']}"}
    r = api["client"].get("/api/v1/report-targets/pathology_cloud/summary",
                          headers=h)
    assert r.status_code == 200
    d = r.json()["data"]
    for k in ("window_kind", "backlog_total", "quality_waiting",
              "evidence_kind"):
        assert k in d
    assert d["evidence_kind"] == "mock"
