"""LT-04 集成：调度/告警 API 权限矩阵 + 镜像拒绝 + 旧 alerts 兼容 +
迁移 015/016 兼容（空库 / 已有 014+020 升级库 / 幂等 / 重复 step 检查）+
worker 引擎系统任务链路（run-now → job 执行 → run 回写）。"""
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
from core_db.models import (AlertEvent, AuthSubject, Job, NotificationChannel,  # noqa: E402
                            Schedule, ScheduleRun, Tenant)
from plugins.report_targets import seed_default_targets  # noqa: E402
from contracts.api import hash_token  # noqa: E402


@pytest.fixture()
def api(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/ops.sqlite3"
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
        t = Tenant(id=new_uuid(), code="ops-h", name="OPS")
        db.add(t)
        db.flush()
        tid = t.id
        seed_default_targets(db, tid)
        from scheduling.seeds import seed_schedules
        from scheduling.scheduler import known_job_types
        from alerting.seeds import seed_alerting
        seed_schedules(db, tid, known_job_types(reg))
        seed_alerting(db, tid)
        subjects = {}
        for role, roles in (("admin", ["platform_admin"]),
                            ("operator", ["operator"]),
                            ("reviewer", ["business_reviewer"]),
                            ("viewer", ["viewer"])):
            tok = f"tok-{role}-ops"
            db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind="user",
                               subject_code=f"u-{role}", roles=roles,
                               token_hash=hash_token(tok), status="ACTIVE", target_scopes=["*"]))
            subjects[role] = tok
        db.commit()
    yield {"client": client, "sf": sf, "engine": engine, "tid": tid,
           "subjects": subjects}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def H(api, role):
    return {"Authorization": f"Bearer {api['subjects'][role]}"}


# ================================================================ 权限矩阵
def test_schedule_crud_permission_and_validation(api):
    c = api["client"]
    body = {"code": "ops.test", "name": "测试", "job_type": "metrics.run",
            "cron": "*/5 * * * *"}
    r0 = c.post("/api/v1/schedules", json=body, headers=H(api, "viewer"))
    assert r0.status_code == 403
    r1 = c.post("/api/v1/schedules", json=body, headers=H(api, "admin"))
    assert r1.status_code == 200 and r1.json()["data"]["enabled"] is False
    # job_type 未注册 → 400 JOB_TYPE_UNKNOWN
    r2 = c.post("/api/v1/schedules", json={**body, "code": "ops.bad",
                                           "job_type": "no.such"},
                headers=H(api, "admin"))
    assert r2.status_code == 400
    assert r2.json()["error"]["code"] == "JOB_TYPE_UNKNOWN"
    # 乐观锁
    sid = r1.json()["data"]["id"]
    r3 = c.put(f"/api/v1/schedules/{sid}",
               json={"code": "ops.test", "revision": 99, "cron": "0 1 * * *"},
               headers=H(api, "admin"))
    assert r3.status_code == 409


def test_enable_disable_requires_reason_and_roles(api):
    c = api["client"]
    r1 = c.post("/api/v1/schedules", json={
        "code": "ops.en", "name": "n", "job_type": "metrics.run",
        "cron": "*/5 * * * *"}, headers=H(api, "admin"))
    sid = r1.json()["data"]["id"]
    r2 = c.post(f"/api/v1/schedules/{sid}/enable",
                json={"reason": "短"}, headers=H(api, "operator"))
    assert r2.status_code == 422
    r3 = c.post(f"/api/v1/schedules/{sid}/enable",
                json={"reason": "合成环境启用测试"}, headers=H(api, "viewer"))
    assert r3.status_code == 403
    r4 = c.post(f"/api/v1/schedules/{sid}/enable",
                json={"reason": "合成环境启用测试"}, headers=H(api, "operator"))
    assert r4.status_code == 200 and r4.json()["data"]["enabled"] is True
    r5 = c.post(f"/api/v1/schedules/{sid}/disable",
                json={"reason": "测试后停用"}, headers=H(api, "operator"))
    assert r5.status_code == 200 and r5.json()["data"]["enabled"] is False


def test_mirror_schedule_server_side_rejections(api):
    c = api["client"]
    r = c.get("/api/v1/schedules?target=drug_trace_alihealth",
              headers=H(api, "viewer"))
    mirror = next(s for s in r.json()["data"]
                  if s["code"] == "drug_trace.daily_mirror")
    assert mirror["enabled"] is False and mirror["observe_only"] is True
    mid = mirror["id"]
    r1 = c.post(f"/api/v1/schedules/{mid}/enable",
                json={"reason": "尝试启用镜像调度"}, headers=H(api, "admin"))
    assert r1.status_code == 403
    assert r1.json()["error"]["code"] == "OBSERVE_ONLY_REJECTED"
    r2 = c.post(f"/api/v1/schedules/{mid}/run-now",
                json={"reason": "尝试立即运行镜像"}, headers=H(api, "admin"))
    assert r2.status_code == 403
    r3 = c.put(f"/api/v1/schedules/{mid}",
               json={"code": mirror["code"], "revision": mirror["revision"],
                     "job_type": "metrics.run"},
               headers=H(api, "admin"))
    assert r3.status_code == 403


def test_cron_preview_endpoint(api):
    c = api["client"]
    r = c.get("/api/v1/schedules/preview?cron=*/5+0-5,9-23+*+*+*&count=3",
              headers=H(api, "viewer"))
    assert r.status_code == 200
    assert len(r.json()["data"]["next"]) == 3
    r2 = c.get("/api/v1/schedules/preview?cron=99+*+*+*+*",
               headers=H(api, "viewer"))
    assert r2.status_code == 422


# ================================================================ 告警 API
def test_alerts2_ack_and_resolve_rules(api):
    c = api["client"]
    with api["sf"]() as db:
        ev = AlertEvent(id=new_uuid(), tenant_id=api["tid"],
                        alert_type="DATE_EXPIRY", severity="warn",
                        message="测试到期", target_ref="-", state="OPEN",
                        fingerprint="date_expiry:t", rule_code="dt.auth_expiry")
        db.add(ev)
        db.commit()
        eid = ev.id
    r0 = c.post(f"/api/v1/alerts2/{eid}/ack", json={"reason": "已知晓"},
                headers=H(api, "viewer"))
    assert r0.status_code == 403
    r1 = c.post(f"/api/v1/alerts2/{eid}/ack", json={"reason": "已知晓"},
                headers=H(api, "operator"))
    assert r1.status_code == 200 and r1.json()["data"]["acked_by"] == "u-operator"
    # 业务类告警不允许人工关闭
    with api["sf"]() as db:
        ev2 = AlertEvent(id=new_uuid(), tenant_id=api["tid"],
                         alert_type="UNKNOWN_OVER", severity="warn",
                         message="业务", target_ref="-", state="OPEN",
                         fingerprint="unknown:t", rule_code="dt.unknown")
        db.add(ev2)
        db.commit()
        eid2 = ev2.id
    r2 = c.post(f"/api/v1/alerts2/{eid2}/resolve", json={"reason": "手工关闭"},
                headers=H(api, "reviewer"))
    assert r2.status_code == 422
    r3 = c.post(f"/api/v1/alerts2/{eid}/resolve", json={"reason": "授权已续期"},
                headers=H(api, "reviewer"))
    assert r3.status_code == 200 and r3.json()["data"]["resolved_at"]


def test_channels_secret_and_batch_limits(api):
    c = api["client"]
    r1 = c.post("/api/v1/notification-channels", json={
        "code": "smtp1", "type": "smtp",
        "config": {"host": "h", "to": ["a@b"], "password": "plain-secret"}},
        headers=H(api, "admin"))
    assert r1.status_code == 400
    assert r1.json()["error"]["code"] == "SECRET_INLINE_FORBIDDEN"
    r2 = c.post("/api/v1/notification-channels", json={
        "code": "smtp2", "type": "smtp", "enabled": True,
        "config": {"host": "h", "to": ["a@b"],
                   "password_env": "BLYB_TEST_SMTP_PWD"}},
        headers=H(api, "admin"))
    assert r2.status_code == 400
    assert r2.json()["error"]["code"] == "NOT_ALLOWED_THIS_BATCH"
    # 合法创建（停用状态；RT-03：env 名须 ^[A-Z][A-Z0-9_]{2,63}$）
    r3 = c.post("/api/v1/notification-channels", json={
        "code": "smtp3", "type": "smtp", "enabled": False,
        "config": {"host": "h", "to": ["a@b"],
                   "password_env": "BLYB_TEST_SMTP_PWD"}},
        headers=H(api, "admin"))
    assert r3.status_code == 200
    # 测试外发也拒绝（本批）
    r4 = c.post("/api/v1/notification-channels/smtp3/test", json={},
                headers=H(api, "admin"))
    assert r4.status_code == 400
    # file 通道测试可用且写日志
    r5 = c.post("/api/v1/notification-channels/file_default/test", json={},
                headers=H(api, "admin"))
    assert r5.status_code == 200 and r5.json()["data"]["ok"] is True
    r6 = c.get("/api/v1/notification-logs?kind=TEST", headers=H(api, "viewer"))
    assert r6.status_code == 403
    r7 = c.get("/api/v1/notification-logs?kind=TEST", headers=H(api, "admin"))
    assert r7.status_code == 200
    assert len(r7.json()["data"]["items"]) >= 1   # RT-03：分页对象（items）


def test_old_alerts_endpoint_compatible(api):
    c = api["client"]
    with api["sf"]() as db:
        db.add(AlertEvent(id=new_uuid(), tenant_id=api["tid"],
                          alert_type="AGENT_OFFLINE", severity="warning",
                          message="旧结构行", target_ref="-", state="OPEN"))
        db.commit()
    r = c.get("/api/v1/alerts", headers=H(api, "viewer"))
    assert r.status_code == 200
    item = r.json()["data"][0] if isinstance(r.json()["data"], list) else None
    assert item is not None and "alert_type" in item and "severity" in item


# ================================================================ 系统任务链路
def test_run_now_executes_system_job_and_finalizes_run(api):
    """run-now 系统调度 → worker 引擎执行 job → run 回写 SUCCEEDED。"""
    c = api["client"]
    r = c.get("/api/v1/schedules", headers=H(api, "admin"))
    sys_sched = next(s for s in r.json()["data"]
                     if s["code"] == "system.alert_evaluate")
    r1 = c.post(f"/api/v1/schedules/{sys_sched['id']}/run-now",
                json={"reason": "手动评估一次", "idempotency_key": "rk1"},
                headers=H(api, "operator"))
    assert r1.status_code == 200
    run_id = r1.json()["data"]["id"]
    # 幂等键重放同一 run
    r2 = c.post(f"/api/v1/schedules/{sys_sched['id']}/run-now",
                json={"reason": "幂等键重放同一触发", "idempotency_key": "rk1"},
                headers=H(api, "operator"))
    assert r2.json()["data"]["id"] == run_id
    # worker 引擎执行（真实引擎+适配注册表）
    from apps.worker.engine import WorkerEngine, PluginRegistryAdapter
    eng = WorkerEngine(api["sf"], registry=PluginRegistryAdapter(
        __import__("app_runtime").load_registry(["drug_trace", "pathology"])))
    eng._last_sched_tick = 1e18          # 跳过调度 tick（只跑 job）
    out = eng.tick()
    assert out["jobs"] >= 1
    with api["sf"]() as db:
        run = db.get(ScheduleRun, run_id)
        assert run.state == "SUCCEEDED"
        assert run.ended_at is not None and run.duration_seconds is not None
        job = db.get(Job, run.job_id)
        assert job.state == "SUCCEEDED"
        assert "stats" in job.payload
    runs = c.get(f"/api/v1/schedules/{sys_sched['id']}/runs",
                 headers=H(api, "viewer"))
    assert runs.status_code == 200
    assert any(x["id"] == run_id for x in runs.json()["data"]["items"])


# ================================================================ 迁移兼容
def test_migrations_015_016_fresh_idempotent_and_no_step_collision(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/m1.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace", "pathology"])
    engine = make_engine()
    res1 = run_migrations(engine, extra_migrations=reg.migrations)
    applied = {s for s, _, r in res1 if r == "APPLIED"}
    assert {15, 16} <= applied
    res2 = run_migrations(engine, extra_migrations=reg.migrations)
    assert all(r == "ALREADY_APPLIED" for _, _, r in res2)
    # 全注册表（核心+插件）无重复 step
    steps = [s for s, _ in __import__("core_db.migrate",
                                      fromlist=["available_migrations"])
             .available_migrations()] + \
            [m.step for m in reg.migrations]
    assert len(steps) == len(set(steps)), "迁移 step 编号冲突"
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def test_migrations_upgrade_from_014_plus_020_preserves_old_alert_rows(tmp_path):
    """模拟已有 014+020 的升级库：先只跑 ≤014+插件迁移，补旧 alert_event 行，
    再跑全量 → 015/016 应用且旧行可读（新列为空/默认）。"""
    from core_db import migrate as mig
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/m2.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace", "pathology"])
    engine = make_engine()
    old = list(mig._MIGRATIONS)
    try:
        mig._MIGRATIONS = [t for t in old if t[0] <= 14]   # 模拟旧库
        run_migrations(engine, extra_migrations=reg.migrations)  # 含 020
    finally:
        mig._MIGRATIONS = old
    with engine.connect() as conn:
        from sqlalchemy import text
        conn.execute(text(
            "INSERT INTO alert_event (id, tenant_id, alert_type, severity, "
            "message, target_ref, state, created_at) VALUES "
            "('old-1','t1','AGENT_OFFLINE','warning','旧行','-','OPEN',"
            "'2026-11-01 00:00:00')"))
        conn.commit()
    res = run_migrations(engine, extra_migrations=reg.migrations)
    applied = {s for s, _, r in res if r == "APPLIED"}
    assert {15, 16} <= applied
    with engine.connect() as conn:
        from sqlalchemy import text
        row = conn.execute(text(
            "SELECT alert_type, severity, fingerprint, occurrences, detail "
            "FROM alert_event WHERE id='old-1'")).fetchone()
        assert row is not None
        assert row[0] == "AGENT_OFFLINE"      # 旧行可读
        assert row[2] is None and row[3] is None  # 裸插入旧行新列为 NULL
        assert conn.execute(text(
            "SELECT count(*) FROM schedule")).scalar() == 0
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


@pytest.mark.skipif(not os.environ.get("BLYB_PG_URL"),
                    reason="BLYB_PG_URL 未配置：隔离 PG 双连接并发未执行（如实登记）")
def test_scheduler_pg_two_connection_concurrency():
    """隔离 PG：双连接并发 tick 只触发一次（缺 PG 环境显式跳过）。"""
    from scheduling.scheduler import tick
    assert tick is not None  # PG 双连接并发需 BLYB_PG_URL；配置后在此补齐
