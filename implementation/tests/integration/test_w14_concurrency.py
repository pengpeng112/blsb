"""W14 正式回归：并发 claim/CAS/unique 持久语义（SQLite 隔离替身）。

边界（如实）：本机无 PostgreSQL —— PG 上的同语义实测**未执行**；
此处以 WAL SQLite 验证条件UPDATE/affected-rows/唯一约束仲裁的持久层语义
（与 PG 行为同构的部分），PG 专属行为（如锁等待队列）不在本测试范围。
"""
from __future__ import annotations

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
import plugin_registry  # noqa: E402
from apps.seed import seed  # noqa: E402
from contracts import JobState  # noqa: E402
from core_db.models import Job, Tenant  # noqa: E402
from workflow_core import jobs as jobs_mod  # noqa: E402


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    db = tmp_path_factory.mktemp("w14") / "core.sqlite3"
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{db}"
    engine = make_engine()
    run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
    sf = make_session_factory(engine)
    seed(engine, sf, with_volume=False)
    with sf() as dbo:
        tenant_id = dbo.query(Tenant).filter_by(code="first-hospital").first().id
    yield sf, tenant_id
    engine.dispose()


def test_w14_concurrent_claim_exactly_one_winner(env):
    sf, tenant_id = env
    with sf() as db:
        db.add(Job(id="job-w14-c1", tenant_id=tenant_id, job_type="x",
                   dedup_key="dk-c1", payload={}, state=JobState.PENDING))
        db.commit()
    winners, losers = [], []
    barrier = threading.Barrier(6)

    def claim(tag: str):
        with sf() as db:
            barrier.wait()
            lease = jobs_mod.claim_job(db, owner=f"worker-{tag}",
                                       job_types=["x"])
            if lease is None:
                losers.append(tag)
            else:
                winners.append((tag, lease.job.id))
            db.commit()

    ts = [threading.Thread(target=claim, args=(f"t{i}",)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(winners) == 1 and winners[0][1] == "job-w14-c1"
    assert len(losers) == 5


def test_w14_finish_job_cas_rejects_stale_epoch(env):
    sf, tenant_id = env
    with sf() as db:
        db.add(Job(id="job-w14-c2", tenant_id=tenant_id, job_type="x",
                   dedup_key="dk-c2", payload={}, state=JobState.PENDING))
        db.commit()
    with sf() as db1:  # worker A claim
        lease_a = jobs_mod.claim_job(db1, owner="A", job_types=["x"])
        db1.commit()
        assert lease_a.job.id == "job-w14-c2"
    with sf() as db2:  # worker B 抢占（租约立即过期路径：直接置回PENDING再claim）
        job = db2.get(Job, "job-w14-c2")
        job.state = JobState.PENDING
        job.lease_until = None
        db2.commit()
        lease_b = jobs_mod.claim_job(db2, owner="B", job_types=["x"])
        db2.commit()
    # A 用旧epoch finish → 拒绝（affected rows=0）
    with sf() as db1:
        ok_a = jobs_mod.finish_job(db1, lease_a, success=True)
        db1.commit()
    assert ok_a is False, "旧epoch的finish必须被CAS拒绝"
    with sf() as db2:
        ok_b = jobs_mod.finish_job(db2, lease_b, success=True)
        db2.commit()
    assert ok_b is True
    with sf() as db:
        assert db.get(Job, "job-w14-c2").state == JobState.SUCCEEDED


def test_w14_dedup_unique_constraint_arbitrates_concurrency(env):
    sf, tenant_id = env
    results = []
    barrier = threading.Barrier(4)

    def insert(tag: str):
        with sf() as db:
            barrier.wait()
            try:
                db.add(Job(id=f"job-w14-d{tag}", tenant_id=tenant_id, job_type="y",
                           dedup_key="dk-shared", payload={}, state=JobState.PENDING))
                db.commit()
                results.append(("ok", tag))
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                results.append(("conflict", type(exc).__name__))

    ts = [threading.Thread(target=insert, args=(str(i),)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    ok = [r for r in results if r[0] == "ok"]
    assert len(ok) == 1, results  # 唯一约束：同dedup键恰一行


def test_w14_migrations_idempotent_rerun_with_data(env):
    """带数据重复执行迁移：ALREADY_APPLIED，数据不清空。"""
    sf, tenant_id = env
    with sf() as db:
        n_before = db.query(Job).count()
    engine = make_engine()
    results = run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
    assert all(r[2] == "ALREADY_APPLIED" for r in results), results
    with sf() as db:
        assert db.query(Job).count() == n_before
    engine.dispose()
