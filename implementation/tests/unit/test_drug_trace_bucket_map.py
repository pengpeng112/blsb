"""LT-01 药品状态→桶映射测试：DRUG_TRACE_STATE_TO_BUCKET + 钩子只读统计。"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import (REPORT_BUCKETS, SUBMISSION_STATE_TO_BUCKET,  # noqa: E402
    new_uuid)
from plugins.domains.drug_trace.hooks import (  # noqa: E402
    DRUG_TRACE_STATE_TO_BUCKET, TARGET_CODE)


def test_map_extends_common_mapping():
    for k, v in SUBMISSION_STATE_TO_BUCKET.items():
        assert DRUG_TRACE_STATE_TO_BUCKET[k] == v


def test_map_covers_domain_states():
    assert DRUG_TRACE_STATE_TO_BUCKET["NOT_REPORTABLE"] == "NOT_REPORTABLE"
    assert DRUG_TRACE_STATE_TO_BUCKET["WAITING"] == "PENDING"


def test_map_values_all_in_buckets():
    assert set(DRUG_TRACE_STATE_TO_BUCKET.values()) <= set(REPORT_BUCKETS)


def test_target_code_guard():
    assert TARGET_CODE == "drug_trace_alihealth"


# ---------------------------------------------------------------- 钩子统计（集成夹具内做只读验证）
@pytest.fixture()
def drug_env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/dt-hook.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    from core_db import make_engine, make_session_factory, run_migrations
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        from core_db.models import Tenant
        t = Tenant(id=new_uuid(), code="hook-tenant", name="钩子测试")
        db.add(t)
        db.commit()
        tid = t.id
    yield {"sf": sf, "engine": engine, "tid": tid}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _mk_submission(env, state="CONFIRMED", domain="drug_trace"):
    from contracts import new_uuid
    from core_db.models import DataPackage, PackageRevision, Submission
    with env["sf"]() as db:
        from core_db.models import Destination
        dest = Destination(id=new_uuid(), tenant_id=env["tid"],
                           code=f"d-{domain}", mode="PUSH", is_mock=True)
        db.add(dest)
        db.flush()
        pkg = DataPackage(id=new_uuid(), tenant_id=env["tid"],
                          domain=domain, business_key=f"bk-{new_uuid()[:8]}")
        db.add(pkg)
        db.flush()
        pr = PackageRevision(id=new_uuid(), tenant_id=env["tid"],
                             package_id=pkg.id, revision=1, state="FROZEN")
        db.add(pr)
        db.flush()
        db.add(Submission(id=new_uuid(), tenant_id=env["tid"],
                          package_revision_id=pr.id,
                          destination_id=dest.id,
                          idempotency_key=f"k-{new_uuid()[:8]}",
                          state=state))
        db.commit()


def test_summary_counts_by_bucket(drug_env):
    from plugins.domain_hooks import HOOK_TARGET_SUMMARY
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    for s in ("PREPARED", "PREPARED", "ACCEPTED", "CONFIRMED", "CONFIRMED",
              "CONFIRMED", "UNKNOWN"):
        _mk_submission(drug_env, s)
    with drug_env["sf"]() as db:
        result = reg.target_summary(db, drug_env["tid"], TARGET_CODE, 7)
    assert result is not None and result.target_code == TARGET_CODE
    assert result.buckets["PENDING"] == 2      # PREPARED×2
    assert result.buckets["ACCEPTED"] == 1
    assert result.buckets["CONFIRMED"] == 3
    assert result.buckets["UNKNOWN"] == 1
    assert result.backlog == 3                 # PENDING2+UNKNOWN1
    assert result.unmapped_states == {}
    assert result.last_success_at is not None  # 有 CONFIRMED


def test_summary_other_target_returns_none(drug_env):
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    with drug_env["sf"]() as db:
        assert reg.target_summary(db, drug_env["tid"], "pathology_cloud", 7) \
            is None
        assert reg.target_summary(db, drug_env["tid"], "nope", 7) is None


def test_summary_unmapped_state_goes_to_unknown(drug_env):
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    _mk_submission(drug_env, "WEIRD_STATE")
    with drug_env["sf"]() as db:
        result = reg.target_summary(db, drug_env["tid"], TARGET_CODE, 7)
    assert result.buckets["UNKNOWN"] == 1
    assert result.unmapped_states == {"WEIRD_STATE": 1}


def test_summary_empty_window(drug_env):
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    _mk_submission(drug_env, "CONFIRMED")
    with drug_env["sf"]() as db:
        result = reg.target_summary(db, drug_env["tid"], TARGET_CODE, 0)
        # days=0 → since=now → 窗口内无行（created_at <= now）；钩子不抛错
        assert sum(result.buckets.values()) == 0


def test_summary_is_readonly(drug_env):
    """只读红线：钩子执行前后行计数不变（submission/outbox/job）。"""
    import app_runtime
    from core_db.models import Job, OutboxEvent, Submission
    reg = app_runtime.load_registry(["drug_trace"])
    _mk_submission(drug_env, "PREPARED")
    with drug_env["sf"]() as db:
        before = (db.query(Submission).count(),
                  db.query(OutboxEvent).count(),
                  db.query(Job).count())
        reg.target_summary(db, drug_env["tid"], TARGET_CODE, 7)
        db.rollback()
        after = (db.query(Submission).count(),
                 db.query(OutboxEvent).count(),
                 db.query(Job).count())
    assert before == after
