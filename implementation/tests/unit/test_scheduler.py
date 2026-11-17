"""LT-04 调度器单测：抢占事务/并发/崩溃回滚/FORBID/blackout/misfire/
run-now/镜像 OBSERVE_ONLY/Job 回写/种子默认停用。"""
from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.engine import utcnow  # noqa: E402
from core_db.models import Job, Schedule, ScheduleRun, Tenant  # noqa: E402

U = timezone.utc


class _NullReg:
    def capability_merged(self, key):
        return {}

    def capability_list(self, key):
        return []


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/sched.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry([])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="sched-h", name="调度测试")
        db.add(t)
        db.commit()
        tid = t.id
    yield {"sf": sf, "engine": engine, "tid": tid, "reg": reg}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _mk_sched(env, *, cron="*/5 * * * *", enabled=True, job_type="metrics.run",
              next_fire=None, concurrency="FORBID", blackout=None,
              misfire="SKIP", max_rt=3600, code=None, observe_only=False):
    with env["sf"]() as db:
        s = Schedule(id=new_uuid(), tenant_id=env["tid"],
                     code=code or f"s{new_uuid()[:6]}", name="测试调度",
                     job_type=job_type, cron=cron, enabled=enabled,
                     observe_only=observe_only, payload_template={},
                     concurrency=concurrency, blackout=blackout or [],
                     misfire_policy=misfire, max_runtime_seconds=max_rt,
                     next_fire_at=next_fire)
        db.add(s)
        db.commit()
        return s.id


def _now_minus(seconds):
    return (utcnow().replace(tzinfo=U) - timedelta(seconds=seconds))


# ---------------------------------------------------------------- 基础触发
def test_tick_fires_due_schedule_single_run_and_job(env):
    from scheduling.scheduler import tick
    sid = _mk_sched(env, next_fire=_now_minus(10).replace(tzinfo=None))
    out = tick(env["sf"](), _now_minus(5), registry=_NullReg())
    assert out["fired"] == 1
    with env["sf"]() as db:
        runs = db.query(ScheduleRun).filter_by(schedule_id=sid).all()
        assert len(runs) == 1 and runs[0].state == "QUEUED"
        assert runs[0].job_id
        job = db.get(Job, runs[0].job_id)
        assert job.payload["schedule_run_id"] == runs[0].id
        assert job.state in ("PENDING", "LEASED", "SUCCEEDED")


def test_tick_null_next_fire_computed_not_fired(env):
    from scheduling.scheduler import tick
    sid = _mk_sched(env, next_fire=None)
    out = tick(env["sf"](), utcnow().replace(tzinfo=U), registry=_NullReg())
    assert out["fired"] == 0 and out["scheduled"] == 1
    with env["sf"]() as db:
        s = db.get(Schedule, sid)
        assert s.next_fire_at is not None
        assert db.query(ScheduleRun).filter_by(schedule_id=sid).count() == 0


def test_disabled_and_observe_only_never_fire(env):
    from scheduling.scheduler import tick
    s1 = _mk_sched(env, enabled=False,
                   next_fire=_now_minus(60).replace(tzinfo=None))
    s2 = _mk_sched(env, enabled=True, observe_only=True, next_fire=None)
    out = tick(env["sf"](), _now_minus(30), registry=_NullReg())
    assert out["fired"] == 0
    with env["sf"]() as db:
        assert db.query(ScheduleRun).count() == 0
        # disabled：查询层排除，next_fire 保持预置值且不产生 run
        assert db.get(Schedule, s1).last_fire_at is None
        # observe_only：永不为其计算/推进触发指针（服务端只观测）
        assert db.get(Schedule, s2).next_fire_at is None


# ---------------------------------------------------------------- 崩溃/事务
def test_tick_crash_between_advance_and_enqueue_rolls_back(env, monkeypatch):
    """故障注入：入队阶段抛错 → next_fire 推进一并回滚（不丢不重）。"""
    from scheduling import scheduler as SCH
    due = _now_minus(10).replace(tzinfo=None)
    sid = _mk_sched(env, next_fire=due)
    calls = {"n": 0}

    def boom(db, *a, **kw):
        calls["n"] += 1
        raise RuntimeError("injected crash before enqueue")

    monkeypatch.setattr(SCH, "enqueue_job", boom)
    with pytest.raises(RuntimeError):
        SCH.tick(env["sf"](), _now_minus(5), registry=_NullReg())
    assert calls["n"] == 1
    with env["sf"]() as db:
        s = db.get(Schedule, sid)
        assert s.next_fire_at == due                       # 未推进（回滚）
        assert db.query(ScheduleRun).count() == 0                     # 无 run
        assert db.query(Job).count() == 0                             # 无 Job


def test_two_workers_concurrent_tick_single_fire(env):
    """双 worker 真实并发：条件 UPDATE 抢占，只有一个入队（SQLite WAL）。"""
    from scheduling.scheduler import tick
    sid = _mk_sched(env, cron="*/5 * * * *",
                    next_fire=_now_minus(10).replace(tzinfo=None))
    results = []
    errs = []

    def worker():
        try:
            with env["sf"]() as db:
                results.append(tick(db, _now_minus(5), registry=_NullReg()))
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()
    total_fired = sum(r.get("fired", 0) for r in results)
    assert total_fired == 1, (results, errs)
    with env["sf"]() as db:
        assert db.query(ScheduleRun).filter_by(schedule_id=sid).count() == 1
        assert db.query(Job).filter(Job.dedup_key.like(f"sched:{sid}:%")).count() == 1


# ---------------------------------------------------------------- 跳过分支
def test_blackout_skip(env):
    from scheduling.scheduler import tick
    sid = _mk_sched(env, blackout=[{"start": "00:00", "end": "23:59"}],
                    next_fire=_now_minus(10).replace(tzinfo=None))
    tick(env["sf"](), _now_minus(5), registry=_NullReg())
    with env["sf"]() as db:
        r = db.query(ScheduleRun).filter_by(schedule_id=sid).one()
        assert r.state == "SKIPPED_BLACKOUT"


def test_misfire_skip_records_missed(env):
    from scheduling.scheduler import tick
    sid = _mk_sched(env, next_fire=_now_minus(3600).replace(tzinfo=None),
                    misfire="SKIP")
    out = tick(env["sf"](), utcnow().replace(tzinfo=U), registry=_NullReg())
    assert out["skipped"].get("MISSED") == 1
    with env["sf"]() as db:
        r = db.query(ScheduleRun).filter_by(schedule_id=sid).one()
        assert r.state == "MISSED"
        assert db.query(Job).count() == 0


def test_misfire_run_once_fires_with_latest_legal_slot(env):
    """RT-03 S03：RUN_ONCE 补 [旧 next_fire_at, now] 内**最近合法槽位**
    （不再用旧 due 槽位——LT-04 语义按任务书修订；也不拿 now 冒充计划时间）。"""
    from scheduling.scheduler import tick
    from workflow_core.cron import CronExpr
    now = utcnow().replace(tzinfo=U)
    due = (now - timedelta(hours=1)).replace(tzinfo=None)
    sid = _mk_sched(env, next_fire=due, misfire="RUN_ONCE")
    out = tick(env["sf"](), now, registry=_NullReg())
    assert out["fired"] == 1
    with env["sf"]() as db:
        r = db.query(ScheduleRun).filter_by(schedule_id=sid).one()
        expect = CronExpr("*/5 * * * *").previous_at_or_before(now)
        assert r.fire_time == expect.replace(tzinfo=None)   # 最近合法槽位
        assert r.fire_time >= due                           # 在 [due, now] 内
        assert r.fire_time <= now.replace(tzinfo=None)      # 不越过 now


def test_forbid_overlap_checks_real_job_not_observational_state(env):
    """R04：上一轮 Job 处于 PENDING（真实非终态）→ SKIPPED_OVERLAP；
    run 被标 TIMED_OUT（观测态）而 Job 仍 LEASED → 仍不放行。"""
    from scheduling.scheduler import tick
    # 场景1：非终态 Job + QUEUED run（近期到期，不走 misfire 分支）
    sid = _mk_sched(env, next_fire=_now_minus(10).replace(tzinfo=None))
    with env["sf"]() as db:
        s = db.get(Schedule, sid)
        job = Job(id=new_uuid(), tenant_id=env["tid"], job_type="metrics.run",
                  dedup_key=f"sched:{sid}:old", payload={}, state="PENDING")
        db.add(job)
        db.flush()
        db.add(ScheduleRun(id=new_uuid(), tenant_id=env["tid"],
                           schedule_id=sid, run_key="cron:old",
                           fire_time=_now_minus(7200).replace(tzinfo=None),
                           state="QUEUED", job_id=job.id))
        db.commit()
    tick(env["sf"](), utcnow().replace(tzinfo=U), registry=_NullReg())
    with env["sf"]() as db:
        states = [r.state for r in db.query(ScheduleRun)
                  .filter_by(schedule_id=sid).all()]
        assert "SKIPPED_OVERLAP" in states

    # 场景2：TIMED_OUT 观测态 + Job LEASED → FORBID 仍拦（R04）
    sid2 = _mk_sched(env, next_fire=_now_minus(10).replace(tzinfo=None))
    with env["sf"]() as db:
        job2 = Job(id=new_uuid(), tenant_id=env["tid"], job_type="metrics.run",
                   dedup_key=f"sched:{sid2}:old2", payload={}, state="LEASED",
                   lease_until=utcnow() + timedelta(minutes=10))
        db.add(job2)
        db.flush()
        db.add(ScheduleRun(id=new_uuid(), tenant_id=env["tid"],
                           schedule_id=sid2, run_key="cron:old2",
                           fire_time=_now_minus(7200).replace(tzinfo=None),
                           state="TIMED_OUT", job_id=job2.id))
        db.commit()
    tick(env["sf"](), utcnow().replace(tzinfo=U), registry=_NullReg())
    with env["sf"]() as db:
        states2 = [r.state for r in db.query(ScheduleRun)
                   .filter_by(schedule_id=sid2).all()]
        assert "SKIPPED_OVERLAP" in states2
        assert "QUEUED" not in states2


# ---------------------------------------------------------------- run-now/镜像
def test_run_now_respects_concurrency_and_idempotency(env):
    from scheduling.scheduler import run_now
    from contracts import ApiError
    sid = _mk_sched(env, concurrency="FORBID")
    with env["sf"]() as db:
        r1 = run_now(db, env["tid"], sid, actor="op", reason="手动触发测试",
                     idempotency_key="k1")
        db.commit()
        r1_id = r1.id
        # 幂等键重放：返回同一 run
        r2 = run_now(db, env["tid"], sid, actor="op", reason="重放同一键",
                     idempotency_key="k1")
        assert r2.id == r1_id
        # FORBID：在途 run 未结束 → 拒绝（换幂等键）
        with pytest.raises(ApiError) as ei:
            run_now(db, env["tid"], sid, actor="op", reason="并发触发测试",
                    idempotency_key="k2")
        assert ei.value.code == "CONFLICT"


def test_run_now_blackout_rejected_no_bypass(env):
    from scheduling.scheduler import run_now
    from contracts import ApiError
    sid = _mk_sched(env, blackout=[{"start": "00:00", "end": "23:59"}])
    with env["sf"]() as db:
        with pytest.raises(ApiError) as ei:
            run_now(db, env["tid"], sid, actor="op", reason="维护窗口触发")
        assert ei.value.code == "BLACKOUT"


def test_mirror_observe_only_enable_runnow_modify_all_rejected(env):
    from scheduling.scheduler import run_now, set_enabled, upsert_schedule
    from contracts import ApiError
    sid = _mk_sched(env, observe_only=True, enabled=False, code="m1")
    with env["sf"]() as db:
        s = db.get(Schedule, sid)
        with pytest.raises(ApiError) as e1:
            set_enabled(db, env["tid"], sid, enabled=True, actor="a",
                        reason="尝试启用镜像")
        assert e1.value.code == "OBSERVE_ONLY_REJECTED"
        with pytest.raises(ApiError) as e2:
            run_now(db, env["tid"], sid, actor="a", reason="尝试立即运行")
        assert e2.value.code == "OBSERVE_ONLY_REJECTED"
        with pytest.raises(ApiError) as e3:
            upsert_schedule(db, env["tid"], {
                "code": "m1", "revision": s.revision,
                "job_type": "metrics.run", "cron": "*/5 * * * *"},
                actor="a")
        assert e3.value.code == "OBSERVE_ONLY_REJECTED"
        db.rollback()
    # 复制调度（新建同 job_type）虽可行，但 observe_only 只能种子创建——
    # API 新建带 observe_only 被拒：
    with env["sf"]() as db:
        with pytest.raises(ApiError) as e4:
            upsert_schedule(db, env["tid"], {
                "code": "m2", "name": "复制镜像", "job_type": "metrics.run",
                "cron": "*/5 * * * *", "observe_only": True}, actor="a")
        assert e4.value.code == "VALIDATION_FAILED"


# ---------------------------------------------------------------- 回写/校验/种子
def test_finalize_run_late_success_keeps_late_flag(env):
    from scheduling.scheduler import finalize_run
    sid = _mk_sched(env)
    with env["sf"]() as db:
        job = Job(id=new_uuid(), tenant_id=env["tid"], job_type="metrics.run",
                  dedup_key="x", payload={}, state="SUCCEEDED")
        db.add(job)
        db.flush()
        run = ScheduleRun(id=new_uuid(), tenant_id=env["tid"],
                          schedule_id=sid, run_key="cron:t",
                          fire_time=utcnow(), state="TIMED_OUT",
                          job_id=job.id, started_at=utcnow() - timedelta(hours=2))
        db.add(run)
        db.commit()
        job.payload = {"schedule_run_id": run.id}
        finalize_run(db, job, "SUCCEEDED")
        db.commit()
        r = db.get(ScheduleRun, run.id)
        assert r.state == "SUCCEEDED" and r.result_summary.get("late") is True


def test_upsert_validates_job_type_and_cron(env):
    from scheduling.scheduler import upsert_schedule
    from contracts import ApiError
    with env["sf"]() as db:
        with pytest.raises(ApiError) as e1:
            upsert_schedule(db, env["tid"], {"code": "x1", "name": "n",
                                             "job_type": "no.such", "cron": "* * * * *"},
                            actor="a", registry=_NullReg())
        assert e1.value.code == "JOB_TYPE_UNKNOWN"
        with pytest.raises(ApiError) as e2:
            upsert_schedule(db, env["tid"], {"code": "x2", "name": "n",
                                             "job_type": "metrics.run",
                                             "cron": "99 * * * *"},
                            actor="a", registry=_NullReg())
        assert e2.value.code == "VALIDATION_FAILED"


def test_seeds_all_disabled_and_mirror_observe_only(env):
    from scheduling.seeds import seed_schedules
    with env["sf"]() as db:
        n = seed_schedules(db, env["tid"], known_types={"drug_trace.collect",
                                                        "metrics.run",
                                                        "system.alert_evaluate",
                                                        "system.alert_digest",
                                                        "system.run_guard"})
        db.commit()
        assert n == 4      # 3 系统 + drug 镜像（inf.collect 未注册→跳过）
        rows = {s.code: s for s in db.query(Schedule)
                .filter_by(tenant_id=env["tid"]).all()}
        for code in ("system.alert_evaluate", "system.alert_digest",
                     "system.run_guard", "drug_trace.daily_mirror"):
            assert rows[code].enabled is False, code
        assert rows["drug_trace.daily_mirror"].observe_only is True
        assert "inf.poll_mirror" not in rows       # inf.collect 未注册→跳过
        # 幂等：再跑不增
        assert seed_schedules(db, env["tid"], known_types=set()) == 0
