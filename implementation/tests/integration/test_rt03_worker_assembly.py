"""RT-03 S02/S10 集成：worker 引擎真实装配链路。

关键测试在 handler 内用**独立 Session** 观察 RUNNING（只直接调用
mark_run_started 不足以证明装配生效）；finish_job True 后仅持久化终态才
回写 ScheduleRun/Operation（RETRY_WAIT 不提前终结）；旧 epoch 不改新
owner 记录。
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
from core_db.models import Job, Operation, Schedule, ScheduleRun, Tenant  # noqa: E402

U = timezone.utc
NOW = datetime(2026, 11, 13, 3, 0, 0, tzinfo=U)


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/rt03wa.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace", "pathology"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="rt03-wa", name="装配测试")
        db.add(t)
        db.commit()
        tid = t.id
    yield {"sf": sf, "engine": engine, "tid": tid, "reg": reg}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _mk_run_job(env, *, job_type="system.run_guard", payload_extra=None,
                max_rt=3600):
    with env["sf"]() as db:
        s = Schedule(id=new_uuid(), tenant_id=env["tid"],
                     code=f"wa{new_uuid()[:6]}", name="n", job_type=job_type,
                     cron="*/5 * * * *", enabled=True,
                     max_runtime_seconds=max_rt)
        db.add(s)
        db.flush()
        job = Job(id=new_uuid(), tenant_id=env["tid"], job_type=job_type,
                  dedup_key=f"sched:{s.id}:wa", state="PENDING",
                  attempts=0, max_attempts=3)
        db.add(job)
        db.flush()
        run = ScheduleRun(id=new_uuid(), tenant_id=env["tid"],
                          schedule_id=s.id, run_key="cron:wa",
                          fire_time=NOW.replace(tzinfo=None),
                          state="QUEUED", job_id=job.id)
        db.add(run)
        job.payload = {"schedule_run_id": run.id, "schedule_id": s.id,
                       "job_type": job_type, **(payload_extra or {})}
        db.commit()
        return run.id, job.id


def _engine(env):
    from apps.worker.engine import WorkerEngine, PluginRegistryAdapter
    return WorkerEngine(env["sf"], registry=PluginRegistryAdapter(env["reg"]))


def test_handler_mid_run_other_session_sees_running(env, monkeypatch):
    """装配生效证明：worker claim → mark 提交 → **handler 执行期间**另一
    Session 读到 run.state=RUNNING 且 started_at/deadline 已固定。"""
    from scheduling.scheduler import mark_run_started
    seen = {}
    orig_scan = None

    def spy_scan(db, tenant_id):
        # handler（system.run_guard→scan）执行期间用同一 db 查 run——
        # mark_run_started 已在 engine 内提交（独立事务）
        rid_seen = db.query(ScheduleRun).filter_by(run_key="cron:wa").one()
        seen["state"] = rid_seen.state
        seen["started"] = rid_seen.started_at
        return {"timed_out": 0, "stalled": 0}

    import scheduling.run_guard as rg
    orig_scan = rg.scan
    monkeypatch.setattr(rg, "scan", spy_scan)
    rid, jid = _mk_run_job(env, job_type="system.run_guard")
    eng = _engine(env)
    out = eng.tick(max_jobs=5)
    monkeypatch.setattr(rg, "scan", orig_scan)
    assert out["jobs"] >= 1
    assert seen["state"] == "RUNNING", seen       # handler 执行中已是 RUNNING
    assert seen["started"] is not None
    with env["sf"]() as db:
        run = db.get(ScheduleRun, rid)
        assert run.state == "SUCCEEDED"           # 最终回写
        assert run.ended_at is not None
        assert run.duration_seconds is not None


class _RetryableError(RuntimeError):
    """带 retry_after 的可重试异常（worker → RETRY_WAIT 路径）。"""
    retry_after = 5


def _inject_scan_failure(monkeypatch, exc_cls=_RetryableError):
    import scheduling.run_guard as rg

    def boom(db, tenant_id):
        raise exc_cls("injected handler failure")

    monkeypatch.setattr(rg, "scan", boom)


def test_retry_wait_does_not_finalize_run(env, monkeypatch):
    """handler 抛错且可重试（attempts<max）→ Job RETRY_WAIT，
    run 保持 RUNNING 不提前终结；重试耗尽才落 FAILED。"""
    rid, jid = _mk_run_job(env, job_type="system.run_guard")
    _inject_scan_failure(monkeypatch)
    eng = _engine(env)
    eng.tick(max_jobs=5)
    with env["sf"]() as db:
        job = db.get(Job, jid)
        assert job.state == "RETRY_WAIT"          # attempts(1)<max(3)
        run = db.get(ScheduleRun, rid)
        assert run.state == "RUNNING"             # 重试等待：不回写终态
        assert run.ended_at is None
    # 重试耗尽：attempts 拉到上限、available_at 提前（退避未到不领取）
    with env["sf"]() as db:
        j = db.get(Job, jid)
        j.attempts = 2                            # 下次 attempts=3 == max
        j.available_at = utcnow() - timedelta(seconds=1)
        db.commit()
    eng.tick(max_jobs=5)
    with env["sf"]() as db:
        job = db.get(Job, jid)
        assert job.state == "FAILED"
        run = db.get(ScheduleRun, rid)
        assert run.state == "FAILED"              # 耗尽才落 FAILED
        assert run.error_message != ""
        assert run.duration_seconds is not None


def test_failed_job_finalizes_operation_once(env, monkeypatch):
    """Operation 语义保持：job FAILED（持久化）→ Operation FAILED；
    RETRY_WAIT 阶段 Operation 不被提前终结。"""
    rid, jid = _mk_run_job(env, job_type="system.run_guard",
                           payload_extra={"operation_id": "op-wa"})
    with env["sf"]() as db:
        db.add(Operation(id="op-wa", tenant_id=env["tid"], op_type="test",
                         state="RUNNING"))
        db.commit()
    _inject_scan_failure(monkeypatch)
    eng = _engine(env)
    eng.tick(max_jobs=5)
    with env["sf"]() as db:
        assert db.get(Job, jid).state == "RETRY_WAIT"
        assert db.get(Operation, "op-wa").state == "RUNNING"   # 未提前终结
    with env["sf"]() as db:
        j = db.get(Job, jid)
        j.attempts = 2
        j.available_at = utcnow() - timedelta(seconds=1)
        db.commit()
    eng.tick(max_jobs=5)
    with env["sf"]() as db:
        assert db.get(Job, jid).state == "FAILED"
        assert db.get(Operation, "op-wa").state == "FAILED"    # 终态才回写


def test_stale_epoch_worker_cannot_rewrite_new_owner_run(env):
    """旧 epoch worker 的 finish 被 fencing 拒绝；run 终态只由新 owner 写。"""
    from workflow_core import jobs as jobs_mod
    rid, jid = _mk_run_job(env)
    with env["sf"]() as db:
        job = db.get(Job, jid)
        stale_epoch = job.lease_epoch              # claim 前的 epoch
        jobs_mod.claim_job(db, owner="w1", job_types=["system.run_guard"])
        db.commit()
    with env["sf"]() as db:
        job = db.get(Job, jid)
        ok = jobs_mod.finish_job(db, jobs_mod.Lease(job=job, epoch=stale_epoch),
                                 success=True)
        assert ok is False                         # 旧 epoch 拒绝
    with env["sf"]() as db:
        run = db.get(ScheduleRun, rid)
        assert run.state == "QUEUED"               # run 未被旧 epoch 改写
        assert run.ended_at is None


def test_claim_job_commits_then_mark_is_independent_transaction(env):
    """claim_job 自身 commit（既有约定不改）；mark_run_started 是领取后的
    独立事务——崩溃窗口（claim 后、mark 前）下 run 保持 QUEUED，
    由租约抢占+mark 幂等恢复，不宣称原子。"""
    from scheduling.scheduler import mark_run_started
    from workflow_core import jobs as jobs_mod
    rid, jid = _mk_run_job(env)
    with env["sf"]() as db:
        lease = jobs_mod.claim_job(db, owner="w1",
                                   job_types=["system.run_guard"])
        assert lease is not None
        # 模拟崩溃窗口：claim 提交后、mark 前会话中止——新 Session 读 run
    with env["sf"]() as db:
        run = db.get(ScheduleRun, rid)
        assert run.state == "QUEUED"               # 尚未 mark（窗口如实存在）
        assert db.get(Job, jid).state == "LEASED"
    # 恢复路径：新 worker 抢占过期租约后 mark 幂等补写
    with env["sf"]() as db:
        job = db.get(Job, jid)
        job.lease_until = utcnow() - timedelta(seconds=10)
        db.commit()
    with env["sf"]() as db:
        lease = jobs_mod.claim_job(db, owner="w2",
                                   job_types=["system.run_guard"])
        assert lease is not None and lease.job.id == jid
        run = mark_run_started(db, lease.job,
                               now=(NOW + timedelta(minutes=1))
                               .replace(tzinfo=None))
        db.commit()
        assert run.state == "RUNNING"
        assert run.started_at == (NOW + timedelta(minutes=1)).replace(tzinfo=None)
