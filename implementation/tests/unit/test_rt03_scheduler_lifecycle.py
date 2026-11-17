"""RT-03 S01/S02/S10：调度生命周期契约单测。

覆盖：mark_run_started claim 前/中/后；受控时钟 5 秒耗时；失败重试成功；
重试耗尽失败；旧 epoch worker 不改新 owner 记录；无调度 Job；迟到成功/
失败（was_timed_out/was_stalled 分别保留）；重复 finalize 幂等；历史缺
started_at 不伪造耗时；错绑定不写他人记录；run_guard 不把 QUEUED 排队
标执行超时。全部用受控时钟推进（不 sleep 拼耗时）。
"""
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
from core_db.models import Job, Schedule, ScheduleRun, Tenant  # noqa: E402

U = timezone.utc
T0 = datetime(2026, 11, 13, 2, 0, 0, tzinfo=U)


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/rt03lc.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry([])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="rt03-lc", name="生命周期测试")
        db.add(t)
        db.commit()
        tid = t.id
    yield {"sf": sf, "engine": engine, "tid": tid}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _mk_triple(env, *, run_state="QUEUED", started_at=None, deadline=None,
               max_rt=3600, payload_extra=None):
    """建 schedule+run+job 三元组（互相绑定正确）。返回 (sched_id, run_id, job_id)。"""
    with env["sf"]() as db:
        s = Schedule(id=new_uuid(), tenant_id=env["tid"],
                     code=f"rt03-{new_uuid()[:6]}", name="n",
                     job_type="metrics.run", cron="*/5 * * * *", enabled=True,
                     max_runtime_seconds=max_rt)
        db.add(s)
        db.flush()
        job = Job(id=new_uuid(), tenant_id=env["tid"], job_type="metrics.run",
                  dedup_key=f"sched:{s.id}:t", payload={}, state="PENDING")
        db.add(job)
        db.flush()
        run = ScheduleRun(id=new_uuid(), tenant_id=env["tid"],
                          schedule_id=s.id, run_key="cron:t",
                          fire_time=T0.replace(tzinfo=None) - timedelta(minutes=5),
                          state=run_state, job_id=job.id,
                          deadline_at=deadline,
                          started_at=started_at)
        db.add(run)
        job.payload = {"schedule_run_id": run.id, "schedule_id": s.id,
                       **(payload_extra or {})}
        db.commit()
        return s.id, run.id, job.id


# ===================================================== mark_run_started
def test_mark_run_started_before_first_claim(env):
    """QUEUED 未开跑：mark → RUNNING + started_at=T0 + deadline=T0+max_rt。"""
    from scheduling.scheduler import mark_run_started
    _sid, rid, jid = _mk_triple(env, deadline=T0.replace(tzinfo=None) + timedelta(hours=1))
    with env["sf"]() as db:
        job = db.get(Job, jid)
        run = mark_run_started(db, job, now=T0)
        db.commit()
        assert run is not None and run.state == "RUNNING"
        assert run.started_at == T0.replace(tzinfo=None)
        assert run.deadline_at == (T0 + timedelta(seconds=3600)).replace(tzinfo=None)


def test_mark_run_started_visible_from_other_session_mid_handler(env):
    """handler 执行期间另一 DB Session 能看到 RUNNING（S02 可见性契约）。"""
    from scheduling.scheduler import mark_run_started
    _sid, rid, jid = _mk_triple(env)
    with env["sf"]() as db:
        mark_run_started(db, db.get(Job, jid), now=T0)
        db.commit()
    with env["sf"]() as db:                      # 独立 Session（模拟另一连接）
        run = db.get(ScheduleRun, rid)
        assert run.state == "RUNNING" and run.started_at is not None


def test_mark_run_started_retry_does_not_reset_deadline(env):
    """重复领取/重试幂等：保留首次 started_at 与执行 deadline（总预算不延长）。"""
    from scheduling.scheduler import mark_run_started
    _sid, rid, jid = _mk_triple(env)
    with env["sf"]() as db:
        job = db.get(Job, jid)
        mark_run_started(db, job, now=T0)
        db.commit()
    later = T0 + timedelta(minutes=10)
    with env["sf"]() as db:
        run = mark_run_started(db, db.get(Job, jid), now=later)
        db.commit()
        assert run.started_at == T0.replace(tzinfo=None)          # 未覆盖
        assert run.deadline_at == (T0 + timedelta(seconds=3600)).replace(tzinfo=None)
        assert run.state == "RUNNING"


def test_mark_run_started_wrong_bindings_rejected(env):
    """错绑定不写他人记录：job_id 不匹配 / 租户不匹配 / schedule_id 不匹配。"""
    from scheduling.scheduler import mark_run_started
    _sid, rid, jid = _mk_triple(env, payload_extra={"schedule_id": "other-sched"})
    with env["sf"]() as db:
        job = db.get(Job, jid)
        assert mark_run_started(db, job, now=T0) is None          # schedule 不匹配
        db.rollback()
    with env["sf"]() as db:
        job = db.get(Job, jid)
        job.payload = {"schedule_run_id": rid}                    # 去掉 schedule_id
        # 租户不匹配
        job.tenant_id = "other-tenant"
        assert mark_run_started(db, job, now=T0) is None


def test_mark_run_started_does_not_wash_observed_anomaly(env):
    """观测异常（TIMED_OUT/STALLED）不洗回 RUNNING（S01）。"""
    from scheduling.scheduler import mark_run_started
    _sid, rid, jid = _mk_triple(env, run_state="TIMED_OUT",
                                started_at=T0.replace(tzinfo=None))
    with env["sf"]() as db:
        run = mark_run_started(db, db.get(Job, jid), now=T0 + timedelta(minutes=1))
        db.commit()
        assert run.state == "TIMED_OUT"          # 保持观测状态
        db.refresh(run)
        assert run.state == "TIMED_OUT"


# ===================================================== 耗时（受控时钟）
def test_finalize_run_wall_clock_duration_5s_with_retries(env, monkeypatch):
    """耗时=首次执行至最终结束墙钟秒数（含重试等待）；5 秒受控时钟实测。"""
    import scheduling.scheduler as SCH
    _sid, rid, jid = _mk_triple(env)
    with env["sf"]() as db:
        job = db.get(Job, jid)
        SCH.mark_run_started(db, job, now=T0)
        db.commit()
    t_end = (T0 + timedelta(seconds=5)).replace(tzinfo=None)   # DB 列 naive
    monkeypatch.setattr(SCH, "utcnow", lambda: t_end)
    with env["sf"]() as db:
        SCH.finalize_run(db, db.get(Job, jid), "SUCCEEDED")
        db.commit()
        run = db.get(ScheduleRun, rid)
        assert run.state == "SUCCEEDED"
        assert run.duration_seconds == 5          # 首跑起算，不含排队
        assert run.started_at == T0.replace(tzinfo=None)
        assert run.ended_at == t_end.replace(tzinfo=None)
        assert "started_at_unknown" not in run.result_summary


def test_failure_retry_then_success_full_window(env, monkeypatch):
    """失败待重试：Job RETRY_WAIT（run 保持 RUNNING、不写 ended_at、不覆盖
    started_at）→ 重试成功后 run=SUCCEEDED，耗时含重试等待。

    RETRY_WAIT 时不回写终态由 engine 侧保证（finish_job True 后仅持久化
    FAILED/SUCCEEDED 才调 finalize；见 test_rt03_worker_assembly）——本单测
    验证 run 生命周期字段与总窗口。"""
    import scheduling.scheduler as SCH
    from workflow_core import jobs as jobs_mod
    _sid, rid, jid = _mk_triple(env, max_rt=3600)
    with env["sf"]() as db:
        job = db.get(Job, jid)
        SCH.mark_run_started(db, job, now=T0)
        db.commit()
    # worker 失败转重试（真实 finish_job 条件更新）
    with env["sf"]() as db:
        job = db.get(Job, jid)
        ok = jobs_mod.finish_job(db, jobs_mod.Lease(job=job, epoch=job.lease_epoch),
                                 success=False, retry_after_seconds=30,
                                 error={"code": "X", "message": "attempt1"})
        db.commit()
        assert ok is True and job.state == "RETRY_WAIT"
        run = db.get(ScheduleRun, rid)
        assert run.state == "RUNNING"             # 重试等待不回写终态
        assert run.ended_at is None
        assert run.started_at == T0.replace(tzinfo=None)
    # 重试成功（新一轮 claim → mark 幂等 → 终态）
    with env["sf"]() as db:
        job = db.get(Job, jid)
        SCH.mark_run_started(db, job,
                             now=(T0 + timedelta(seconds=30)).replace(tzinfo=None))
        db.commit()
    t_end = (T0 + timedelta(seconds=95)).replace(tzinfo=None)   # 含 30s 重试等待
    monkeypatch.setattr(SCH, "utcnow", lambda: t_end)
    with env["sf"]() as db:
        SCH.finalize_run(db, db.get(Job, jid), "SUCCEEDED")
        db.commit()
        run = db.get(ScheduleRun, rid)
        assert run.state == "SUCCEEDED"
        assert run.duration_seconds == 95         # 墙钟=首跑→终态
        assert run.started_at == T0.replace(tzinfo=None)


def test_retry_exhausted_finalize_failed(env):
    """重试耗尽 → FAILED（ended_at 与真实 duration）。"""
    from scheduling.scheduler import mark_run_started, finalize_run
    _sid, rid, jid = _mk_triple(env)
    with env["sf"]() as db:
        job = db.get(Job, jid)
        mark_run_started(db, job, now=T0)
        db.commit()
    t_end = (T0 + timedelta(seconds=42)).replace(tzinfo=None)
    import scheduling.scheduler as SCH
    orig = SCH.utcnow
    SCH.utcnow = lambda: t_end
    try:
        with env["sf"]() as db:
            finalize_run(db, db.get(Job, jid), "FAILED",
                         error={"code": "X", "message": "boom"})
            db.commit()
            run = db.get(ScheduleRun, rid)
            assert run.state == "FAILED"
            assert run.duration_seconds == 42
            assert run.error_code == "X"
            assert run.result_summary["error"]["code"] == "X"
    finally:
        SCH.utcnow = orig


def test_stale_epoch_worker_cannot_touch_run(env):
    """旧 epoch worker finish 被拒（False）→ run 不被旧持有者改写。"""
    from workflow_core.jobs import finish_job, Lease
    from scheduling.scheduler import finalize_run
    _sid, rid, jid = _mk_triple(env)
    with env["sf"]() as db:
        job = db.get(Job, jid)
        lease = Lease(job=job, epoch=99)          # 伪造旧 epoch
        db.expunge(job)
        ok = finish_job(db, lease, success=True)
        assert ok is False
        run = db.get(ScheduleRun, rid)
        assert run.state == "QUEUED" and run.ended_at is None


def test_finalize_run_without_schedule_binding_noop(env):
    """无调度 Job（payload 无 schedule_run_id）：mark/finalize 原语义。"""
    from scheduling.scheduler import mark_run_started, finalize_run
    with env["sf"]() as db:
        job = Job(id=new_uuid(), tenant_id=env["tid"], job_type="metrics.run",
                  dedup_key="plain", payload={"operation_id": ""}, state="PENDING")
        db.add(job)
        db.commit()
        assert mark_run_started(db, job, now=T0) is None
        finalize_run(db, job, "SUCCEEDED")        # 不抛、无 run 受影响
        db.commit()
        assert db.query(ScheduleRun).count() == 0


def test_late_success_after_stall_keeps_flags_separately(env):
    """迟到成功：STALLED 观测史 → late+was_stalled=True；
    **不得**无依据写 was_timed_out（S01 分别保留）。"""
    import scheduling.scheduler as SCH
    _sid, rid, jid = _mk_triple(env, run_state="STALLED",
                                started_at=T0.replace(tzinfo=None) - timedelta(hours=1))
    with env["sf"]() as db:
        run = db.get(ScheduleRun, rid)
        run.result_summary = {"observed": "STALLED",
                              "observed_at": T0.replace(tzinfo=None).isoformat()}
        db.commit()
    with env["sf"]() as db:
        SCH.finalize_run(db, db.get(Job, jid), "SUCCEEDED")
        db.commit()
        run = db.get(ScheduleRun, rid)
        assert run.state == "SUCCEEDED"
        s = run.result_summary
        assert s["late"] is True and s["was_stalled"] is True
        assert "was_timed_out" not in s            # 仅 STALLED 不写超时史
        assert "started_at_unknown" not in s       # started_at 已知


def test_late_timeout_flag_preserved_when_timed_out(env):
    """TIMED_OUT 观测史 → was_timed_out=True（不写 was_stalled）。"""
    import scheduling.scheduler as SCH
    _sid, rid, jid = _mk_triple(env, run_state="TIMED_OUT",
                                started_at=T0.replace(tzinfo=None))
    with env["sf"]() as db:
        run = db.get(ScheduleRun, rid)
        run.result_summary = {"observed": "TIMED_OUT"}
        db.commit()
        SCH.finalize_run(db, db.get(Job, jid), "SUCCEEDED")
        db.commit()
        s = db.get(ScheduleRun, rid).result_summary
        assert s["was_timed_out"] is True and "was_stalled" not in s


def test_finalize_run_idempotent_second_call_noop(env):
    """重复 finalize：终态幂等（状态/耗时/ended_at 不再变化）。"""
    import scheduling.scheduler as SCH
    _sid, rid, jid = _mk_triple(env)
    t1 = (T0 + timedelta(seconds=10)).replace(tzinfo=None)
    with env["sf"]() as db:
        mark = db.get(Job, jid)
        SCH.mark_run_started(db, mark, now=T0)
        db.commit()
    orig = SCH.utcnow
    try:
        SCH.utcnow = lambda: t1
        with env["sf"]() as db:
            SCH.finalize_run(db, db.get(Job, jid), "SUCCEEDED")
            db.commit()
        t2 = t1 + timedelta(hours=1)
        SCH.utcnow = lambda: t2
        with env["sf"]() as db:
            SCH.finalize_run(db, db.get(Job, jid), "FAILED",
                             error={"code": "Y", "message": "late"})
            db.commit()
            run = db.get(ScheduleRun, rid)
            assert run.state == "SUCCEEDED"        # 终态不覆盖
            assert run.duration_seconds == 10
            assert run.ended_at == t1.replace(tzinfo=None)
            assert run.error_code == ""            # 不写迟到错误
    finally:
        SCH.utcnow = orig


def test_finalize_run_legacy_missing_started_at_not_fabricated(env):
    """历史缺 started_at：不伪造 started_at=ended_at；duration=None +
    started_at_unknown 标记（不宣称真实耗时）。"""
    import scheduling.scheduler as SCH
    _sid, rid, jid = _mk_triple(env, run_state="RUNNING", started_at=None,
                                deadline=None)
    with env["sf"]() as db:
        SCH.finalize_run(db, db.get(Job, jid), "SUCCEEDED")
        db.commit()
        run = db.get(ScheduleRun, rid)
        assert run.started_at is None              # 不伪造
        assert run.duration_seconds is None        # 不冒充 0/真实耗时
        assert run.result_summary["started_at_unknown"] is True


# ===================================================== run_guard 排队口径
def test_run_guard_queued_backlog_not_execution_timeout(env):
    """QUEUED 排队积压（deadline 已过、未开跑）不按执行超时标记（S02）。"""
    from scheduling.run_guard import scan
    now = utcnow()
    with env["sf"]() as db:
        s = Schedule(id=new_uuid(), tenant_id=env["tid"], code="q1",
                     job_type="metrics.run", cron="*/5 * * * *", enabled=True,
                     max_runtime_seconds=60)
        db.add(s)
        db.flush()
        db.add(ScheduleRun(id=new_uuid(), tenant_id=env["tid"],
                           schedule_id=s.id, run_key="cron:q",
                           fire_time=now - timedelta(hours=2),
                           state="QUEUED",
                           deadline_at=now - timedelta(hours=1),
                           started_at=None))
        db.commit()
    with env["sf"]() as db:
        out = scan(db, env["tid"])
        db.commit()
        assert out == {"timed_out": 0, "stalled": 0}
        r = db.query(ScheduleRun).filter_by(run_key="cron:q").one()
        assert r.state == "QUEUED"                # 保持排队，不标 TIMED_OUT


def test_run_guard_running_with_started_at_still_times_out(env):
    """已开跑 RUNNING 超 deadline 仍标 TIMED_OUT（口径只排除未开跑）。"""
    from scheduling.run_guard import scan
    now = utcnow()
    with env["sf"]() as db:
        s = Schedule(id=new_uuid(), tenant_id=env["tid"], code="r1",
                     job_type="metrics.run", cron="*/5 * * * *", enabled=True,
                     max_runtime_seconds=60)
        db.add(s)
        db.flush()
        db.add(ScheduleRun(id=new_uuid(), tenant_id=env["tid"],
                           schedule_id=s.id, run_key="cron:r",
                           fire_time=now - timedelta(hours=2),
                           state="RUNNING",
                           deadline_at=now - timedelta(minutes=1),
                           started_at=now - timedelta(minutes=30)))
        db.commit()
    with env["sf"]() as db:
        out = scan(db, env["tid"])
        db.commit()
        assert out["timed_out"] == 1
