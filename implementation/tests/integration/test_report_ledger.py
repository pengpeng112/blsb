"""LT-03 集成：统一提交台账（先筛后限+游标+隔离）与 UNKNOWN 受控人工处置。

核心反例（2026-10-26 历史 bug）：造 300 条记录（295 条新 ACCEPTED + 5 条最旧
UNKNOWN），bucket=UNKNOWN&limit=50 必须捞到这 5 条——先 limit 后筛选的实现做不到。
"""
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
from plugins import report_ledger as RL  # noqa: E402
from plugins.domain_hooks import LedgerFilter  # noqa: E402
from plugins.report_targets import seed_default_targets  # noqa: E402

from contracts.api import hash_token  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/ledger.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace", "pathology"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="ledger-hospital", name="台账测试")
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
        pt = db.query(ReportTarget).filter_by(code="pathology_cloud").one()
        pt.destination_id = dest.id
        db.commit()
        yield {"sf": sf, "engine": engine, "tid": tid, "dest_id": dest.id,
               "subjects": {}}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _mk(env, state="ACCEPTED", *, domain="pathology", age_minutes=0,
        dest_id=None, snapshot_digest=None):
    with env["sf"]() as db:
        pkg = DataPackage(id=new_uuid(), tenant_id=env["tid"], domain=domain,
                          business_key=f"bk-{new_uuid()[:8]}")
        db.add(pkg)
        db.flush()
        pr = PackageRevision(id=new_uuid(), tenant_id=env["tid"],
                             package_id=pkg.id, revision=1, state="FROZEN")
        db.add(pr)
        db.flush()
        created = utcnow() - timedelta(minutes=age_minutes)
        s = Submission(id=new_uuid(), tenant_id=env["tid"],
                       package_revision_id=pr.id,
                       destination_id=dest_id or env["dest_id"],
                       idempotency_key=f"idem-{new_uuid()[:8]}",
                       state=state, snapshot_digest=snapshot_digest)
        db.add(s)
        db.commit()
        # 直接回写时间戳（default=utcnow 只在插入时生效）
        from sqlalchemy import update
        db.execute(update(Submission).where(Submission.id == s.id)
                   .values(created_at=created, updated_at=created))
        db.commit()
        return s.id


# ---------------------------------------------------------------- 反例与分页
def test_300_rows_filter_first_limit_last_finds_oldest_unknowns(env):
    """历史 bug 反例：300 条中 5 条最旧 UNKNOWN，bucket=UNKNOWN 必须全捞到。"""
    # 295 条新 ACCEPTED（updated_at 新）
    for i in range(295):
        _mk(env, "ACCEPTED", age_minutes=0)
    # 5 条最旧 UNKNOWN（30 天前）
    unk_ids = [_mk(env, "UNKNOWN", age_minutes=60 * 24 * 30) for _ in range(5)]
    with env["sf"]() as db:
        page = RL_search(env, db, "pathology_cloud", "UNKNOWN")
    ids = {r.id for r in page.items}
    assert ids == set(unk_ids)                       # 全部 5 条旧 UNKNOWN
    assert page.total_estimate == 5


def RL_search(env, db, target, bucket=None, cursor=None, limit=50,
              date_from=None, date_to=None):
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace", "pathology"])
    return reg.ledger_search(db, env["tid"], target,
                             LedgerFilter(bucket=bucket, date_from=date_from,
                                          date_to=date_to),
                             cursor, limit)


def test_cursor_pagination_no_dup_no_loss(env):
    """游标翻页：全量遍历 id 集合 == 全集（无重复无遗漏）。"""
    made = {_mk(env, "ACCEPTED", age_minutes=i + 1) for i in range(37)}
    got: list[str] = []
    cursor = None
    for _ in range(10):
        with env["sf"]() as db:
            page = RL_search(env, db, "pathology_cloud", None, cursor, 10)
        got.extend(r.id for r in page.items)
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    assert len(got) == 37 and set(got) == made       # 无重复无遗漏


def test_bucket_filter_matches_states(env):
    # SUBMISSION_STATE_TO_BUCKET: PREPARED→PENDING、SENDING→SENDING（各自桶）
    _mk(env, "PREPARED")
    _mk(env, "SENDING")
    with env["sf"]() as db:
        page_pending = RL_search(env, db, "pathology_cloud", "PENDING")
        page_sending = RL_search(env, db, "pathology_cloud", "SENDING")
    assert {r.raw_state for r in page_pending.items} == {"PREPARED"}
    assert all(r.bucket == "PENDING" for r in page_pending.items)
    assert {r.raw_state for r in page_sending.items} == {"SENDING"}
    assert all(r.bucket == "SENDING" for r in page_sending.items)


def test_cross_tenant_isolation(env):
    from core_db.models import Tenant
    with env["sf"]() as db:
        t2 = Tenant(id=new_uuid(), code="ledger-other", name="别家")
        db.add(t2)
        db.commit()
        env2 = {"tid": t2.id, "dest_id": env["dest_id"], "sf": env["sf"]}
        _mk(env2, "UNKNOWN")
    with env["sf"]() as db:
        page_a = RL_search(env, db, "pathology_cloud", "UNKNOWN")
    # A 租户看不到 B 的 UNKNOWN（A 无 UNKNOWN 数据）
    assert page_a.total_estimate == 0


def test_date_window_filter(env):
    _mk(env, "ACCEPTED", age_minutes=60 * 24 * 40)   # 40 天前
    recent = _mk(env, "ACCEPTED", age_minutes=60)    # 1 小时前
    import datetime as dt
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    with env["sf"]() as db:
        page = RL_search(env, db, "pathology_cloud", None, None, 50,
                         date_from=(today - dt.timedelta(days=7)).isoformat(),
                         date_to=today.isoformat())
    ids = {r.id for r in page.items}
    assert recent in ids and len(ids) == 1


# ---------------------------------------------------------------- 详情/处置
def _mk_resolvable(env, state="UNKNOWN"):
    """LT-03-R1：满足处置前提的提交（冻结摘要 + ≥1 行尝试证据）。"""
    from core_db.models import DeliveryAttempt
    sid = _mk(env, state, snapshot_digest="ab" * 32)
    with env["sf"]() as db:
        db.add(DeliveryAttempt(
            id=new_uuid(), tenant_id=env["tid"], submission_id=sid,
            attempt_no=1, request_digest="d" * 64, started_at=utcnow(),
            ended_at=utcnow(), outcome="UNKNOWN", response_snapshot={}))
        db.commit()
    return sid


def test_detail_returns_attempts_and_receipts(env):
    sid = _mk(env, "UNKNOWN")
    with env["sf"]() as db:
        d = RL.submission_detail(db, env["tid"], sid,
                                 target_code="pathology_cloud")
    assert d is not None and d["state"] == "UNKNOWN"
    assert "attempts" in d and "receipts" in d


def test_detail_404(env):
    with env["sf"]() as db:
        assert RL.submission_detail(db, env["tid"], "ghost",
                                    target_code="pathology_cloud") is None


def _reviewer(env):
    with env["sf"]() as db:
        s = AuthSubject(id=new_uuid(), tenant_id=env["tid"], kind="user",
                        subject_code="user-rev", roles=["business_reviewer"],
                        token_hash=hash_token("tok-rev"), status="ACTIVE")
        db.add(s)
        db.commit()
    return "tok-rev"


def test_manual_resolution_confirm_flow(env):
    _reviewer(env)
    sid = _mk_resolvable(env)
    from contracts import ApiError
    with env["sf"]() as db:
        r = RL.manual_resolution(
            db, env["tid"], "pathology_cloud", sid,
            resolution="CONFIRMED", reason="平台回执已线下核实无误",
            evidence_ref="receipt-20261108-001",
            expected_state="UNKNOWN", actor="user-rev")
        db.commit()
        from core_db.models import Submission
        assert db.get(Submission, sid).state == "CONFIRMED"
        assert r["to_state"] == "CONFIRMED"


def test_manual_resolution_resend_only_reverts_to_prepared(env):
    _reviewer(env)
    sid = _mk_resolvable(env)
    with env["sf"]() as db:
        RL.manual_resolution(db, env["tid"], "pathology_cloud", sid,
                             resolution="RESEND_ALLOWED",
                             reason="对端重启丢失，允许重发",
                             evidence_ref="ops-ticket-1",
                             expected_state="UNKNOWN", actor="user-rev")
        db.commit()
        from core_db.models import Submission
        assert db.get(Submission, sid).state == "PREPARED"   # 不自动发送


def test_manual_resolution_not_unknown_conflict(env):
    _reviewer(env)
    sid = _mk_resolvable(env, "ACCEPTED")
    from contracts import ApiError
    with env["sf"]() as db:
        with pytest.raises(ApiError) as ei:
            RL.manual_resolution(db, env["tid"], "pathology_cloud", sid,
                                 resolution="CONFIRMED", reason="理由足够长xx",
                                 evidence_ref="", expected_state="UNKNOWN",
                                 actor="user-rev")
        assert ei.value.code == "CONFLICT"


def test_manual_resolution_short_reason_422(env):
    _reviewer(env)
    sid = _mk(env, "UNKNOWN")
    from contracts import ApiError
    with env["sf"]() as db:
        with pytest.raises(ApiError) as ei:
            RL.manual_resolution(db, env["tid"], "pathology_cloud", sid,
                                 resolution="CONFIRMED", reason="太短",
                                 evidence_ref="", expected_state="UNKNOWN",
                                 actor="user-rev")
        assert ei.value.code == "VALIDATION_FAILED"


def test_manual_resolution_bad_resolution(env):
    _reviewer(env)
    sid = _mk(env, "UNKNOWN")
    from contracts import ApiError
    with env["sf"]() as db:
        with pytest.raises(ApiError):
            RL.manual_resolution(db, env["tid"], "pathology_cloud", sid,
                                 resolution="MAYBE", reason="理由足够长xx",
                                 evidence_ref="", expected_state="UNKNOWN",
                                 actor="user-rev")


def test_manual_resolution_cas_concurrent_single_winner(env):
    """并发两次处置只有一次成功（条件 UPDATE CAS）。"""
    _reviewer(env)
    sid = _mk_resolvable(env)
    from contracts import ApiError
    with env["sf"]() as db:
        RL.manual_resolution(db, env["tid"], "pathology_cloud", sid,
                             resolution="CONFIRMED", reason="第一次处置生效xx",
                             evidence_ref="", expected_state="UNKNOWN",
                             actor="user-rev")
        db.commit()
        with pytest.raises(ApiError) as ei:
            RL.manual_resolution(db, env["tid"], "pathology_cloud", sid,
                                 resolution="REJECTED", reason="第二次并发处置xx",
                                 evidence_ref="", expected_state="UNKNOWN",
                                 actor="user-rev")
        assert ei.value.code == "CONFLICT"


def test_manual_resolution_writes_audit(env):
    _reviewer(env)
    sid = _mk_resolvable(env)
    from core_db.models import AuditEvent, ManualDecision
    with env["sf"]() as db:
        RL.manual_resolution(db, env["tid"], "pathology_cloud", sid,
                             resolution="REJECTED", reason="确认对端拒绝无误xx",
                             evidence_ref="rcpt-x", expected_state="UNKNOWN",
                             actor="user-rev")
        db.commit()
        md = db.query(ManualDecision).filter_by(
            entity_ref=f"submission:{sid}").one()
        assert md.action == "manual_resolution:REJECTED"
        ev = (db.query(AuditEvent)
              .filter_by(action="report_ledger.manual_resolution",
                         target=f"submission:{sid}").one())
        assert ev.actor == "user-rev"


def test_search_unknown_target_returns_none(env):
    """未注册 target：无钩子认领 → registry.ledger_search 返回 None
    （路由层负责转 data_source:"none" 空页；服务层不做静默猜测）。"""
    with env["sf"]() as db:
        page = RL_search(env, db, "ghost_target", None)
        assert page is None


def test_drug_trace_ledger_hook_claims_only_own_target(env):
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace", "pathology"])
    from plugins.domain_hooks import HOOK_LEDGER_SEARCH
    flt = LedgerFilter()
    with env["sf"]() as db:
        # pathology_cloud 由病理钩子认领；drug_trace_alihealth 由药品钩子认领
        r1 = reg.ledger_search(db, env["tid"], "pathology_cloud", flt, None, 10)
        r2 = reg.ledger_search(db, env["tid"], "drug_trace_alihealth", flt,
                               None, 10)
    assert r1 is not None and r1.next_cursor is None
    assert r2 is not None                       # 药品钩子返回（域过滤，可为空页）
