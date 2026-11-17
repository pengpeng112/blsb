"""LT-04 运行治理单测：TIMED_OUT/STALLED 只标记不杀；Job 行零修改。"""
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
from core_db.models import AlertEvent, Job, Schedule, ScheduleRun, Tenant  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/guard.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry([])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="guard-h", name="治理测试")
        db.add(t)
        db.commit()
        tid = t.id
    yield {"sf": sf, "engine": engine, "tid": tid}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _mk_overdue(env, *, run_state="RUNNING", job_state="LEASED",
                lease_expired_by=600, max_rt=60):
    with env["sf"]() as db:
        s = Schedule(id=new_uuid(), tenant_id=env["tid"], code=f"g{new_uuid()[:5]}",
                     job_type="metrics.run", cron="*/5 * * * *", enabled=True,
                     max_runtime_seconds=max_rt)
        db.add(s)
        db.flush()
        job = Job(id=new_uuid(), tenant_id=env["tid"], job_type="metrics.run",
                  dedup_key="dk", payload={}, state=job_state,
                  lease_until=utcnow() - timedelta(seconds=lease_expired_by),
                  lease_epoch=3, attempts=2, last_error={"code": "X"})
        db.add(job)
        db.flush()
        run = ScheduleRun(
            id=new_uuid(), tenant_id=env["tid"], schedule_id=s.id,
            run_key=f"cron:{utcnow().isoformat()}", fire_time=utcnow() - timedelta(hours=2),
            state=run_state, job_id=job.id,
            deadline_at=utcnow() - timedelta(hours=1),
            started_at=utcnow() - timedelta(hours=2))
        db.add(run)
        db.commit()
        return run.id, job.id


def test_over_deadline_marked_timed_out_and_alert_event(env):
    from scheduling.run_guard import scan
    # 租约仅过期 100s < 停滞阈值 max(300, 0.2*60)=300s → 只 TIMED_OUT
    rid, jid = _mk_overdue(env, lease_expired_by=100)
    with env["sf"]() as db:
        out = scan(db, env["tid"])
        db.commit()
        assert out["timed_out"] >= 1
        r = db.get(ScheduleRun, rid)
        assert r.state == "TIMED_OUT"
        ev = (db.query(AlertEvent)
              .filter(AlertEvent.tenant_id == env["tid"],
                      AlertEvent.alert_type == "RUN_TIMEOUT",
                      AlertEvent.resolved_at.is_(None)).first())
        assert ev is not None and ev.fingerprint.startswith("run.run_timeout:")
        # Job 行零修改（只标记只告警不 kill）
        j = db.get(Job, jid)
        assert j.state == "LEASED" and j.lease_epoch == 3 and j.attempts == 2


def test_stalled_when_lease_long_expired(env):
    from scheduling.run_guard import scan
    rid, jid = _mk_overdue(env, job_state="LEASED", lease_expired_by=3600,
                           max_rt=60)
    with env["sf"]() as db:
        out = scan(db, env["tid"])
        db.commit()
        r = db.get(ScheduleRun, rid)
        assert r.state == "STALLED"          # 覆盖 TIMED_OUT（更严重观测）
        assert (db.query(AlertEvent)
                .filter_by(alert_type="RUN_STALLED").count()) >= 1
        j = db.get(Job, jid)
        assert j.state == "LEASED"           # Job 仍原样


def test_normal_runs_untouched(env):
    from scheduling.run_guard import scan
    with env["sf"]() as db:
        s = Schedule(id=new_uuid(), tenant_id=env["tid"], code="ok1",
                     job_type="metrics.run", cron="*/5 * * * *", enabled=True)
        db.add(s)
        db.flush()
        db.add(ScheduleRun(id=new_uuid(), tenant_id=env["tid"],
                           schedule_id=s.id, run_key="cron:a",
                           fire_time=utcnow(), state="RUNNING",
                           deadline_at=utcnow() + timedelta(hours=1)))
        db.commit()
    with env["sf"]() as db:
        out = scan(db, env["tid"])
        db.commit()
        assert out == {"timed_out": 0, "stalled": 0}
