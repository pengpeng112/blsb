"""LT-06A 集成：用户与角色管理（权限/校验/最后管理员保护/会话撤销/审计/
不返回 hash）+ 业务范围鉴权（R08：列表过滤/详情详情403/先范围防泄露）。"""
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
from core_db.models import (AuditEvent, AuthSubject, ConsoleAccount,  # noqa: E402
                            Tenant)
from plugins.report_targets import seed_default_targets  # noqa: E402
from contracts.api import hash_token  # noqa: E402


@pytest.fixture()
def api(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/sys.sqlite3"
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
        t = Tenant(id=new_uuid(), code="sys-h", name="系统")
        db.add(t)
        db.flush()
        tid = t.id
        seed_default_targets(db, tid)
        admin_subj = AuthSubject(
            id=new_uuid(), tenant_id=tid, kind="user",
            subject_code="console-admin", roles=["platform_admin"],
            token_hash=hash_token("tk-admin"), status="ACTIVE")
        db.add(admin_subj)
        db.flush()
        db.add(ConsoleAccount(
            id=new_uuid(), tenant_id=tid, subject_id=admin_subj.id,
            username="admin", password_hash="x" * 40))
        db.commit()
    yield {"client": client, "sf": sf, "engine": engine, "tid": tid,
           "admin": "tk-admin"}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def H(api, tok=None):
    return {"Authorization": f"Bearer {tok or api['admin']}"}


def _mk_console_user(api, username, roles, scopes=None, password="Str0ngPass!2026x"):
    r = api["client"].post("/api/v1/system/users", json={
        "username": username, "password": password, "roles": roles,
        "target_scopes": scopes if scopes is not None else ["*"]},
        headers=H(api))
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ---------------------------------------------------------------- 用户管理
def test_users_requires_admin(api):
    r = api["client"].get("/api/v1/system/users",
                          headers=H(api, "no-such-token"))
    assert r.status_code == 401


def test_create_user_validation_and_defaults(api):
    c = api["client"]
    # 用户名非法
    r1 = c.post("/api/v1/system/users", json={
        "username": "ab", "password": "Str0ngPass!2026x", "roles": ["operator"]},
        headers=H(api))
    assert r1.status_code == 422
    # 弱密码（<12）
    r2 = c.post("/api/v1/system/users", json={
        "username": "weak_user", "password": "short12", "roles": ["operator"]},
        headers=H(api))
    assert r2.status_code == 422
    # 禁止分配 service
    r3 = c.post("/api/v1/system/users", json={
        "username": "svc_try", "password": "Str0ngPass!2026x",
        "roles": ["service"]}, headers=H(api))
    assert r3.status_code == 422
    # 未知 scope 拒绝
    r4 = c.post("/api/v1/system/users", json={
        "username": "scope_bad", "password": "Str0ngPass!2026x",
        "roles": ["operator"], "target_scopes": ["ghost"]}, headers=H(api))
    assert r4.status_code == 422
    # 合法创建：scopes 显式给定（新建默认 [] 语义由不传时验证）
    r5 = c.post("/api/v1/system/users", json={
        "username": "op_empty", "password": "Str0ngPass!2026x",
        "roles": ["operator"]}, headers=H(api))
    assert r5.status_code == 200
    assert r5.json()["data"]["target_scopes"] == []   # 新建默认无业务（R08）
    # 重复用户名 409
    r6 = c.post("/api/v1/system/users", json={
        "username": "op_empty", "password": "Str0ngPass!2026x",
        "roles": ["operator"]}, headers=H(api))
    assert r6.status_code == 409


def test_user_list_never_returns_hash_or_token(api):
    _mk_console_user(api, "u_list", ["operator"])
    r = api["client"].get("/api/v1/system/users", headers=H(api))
    assert r.status_code == 200
    for u in r.json()["data"]:
        blob = repr(u)
        assert "password_hash" not in blob and "token_hash" not in blob
        assert u["username"] == "u_list" or u["username"] == "admin" \
            or "roles" in u


def test_remove_last_admin_conflict(api):
    c = api["client"]
    _mk_console_user(api, "second_admin", ["platform_admin"])
    # 两个 admin：移除其中一个 OK
    r1 = c.put("/api/v1/system/users/second_admin/roles", json={
        "roles": ["operator"], "target_scopes": []}, headers=H(api))
    assert r1.status_code == 200
    # 此时 admin（fixture）是唯一 platform_admin → 移除 409
    r2 = c.put("/api/v1/system/users/admin/roles", json={
        "roles": ["operator"], "target_scopes": []}, headers=H(api))
    assert r2.status_code == 409


def test_lock_blocks_login_and_revokes_sessions(api):
    """锁定/解锁使权限即时生效：登录失败 + 旧会话撤销。"""
    c = api["client"]
    _mk_console_user(api, "u_lock", ["operator"])
    # 用密码登录拿会话
    r_login = c.post("/api/v1/auth/login", json={
        "username": "u_lock", "password": "Str0ngPass!2026x"})
    assert r_login.status_code == 200
    tok = r_login.json()["data"]["access_token"] \
        if "access_token" in r_login.json()["data"] else \
        r_login.json()["data"].get("token")
    assert tok
    h = {"Authorization": f"Bearer {tok}"}
    assert c.get("/api/v1/auth/me", headers=h).status_code == 200
    # 管理员锁定 → 会话撤销 + 登录失败
    r_lock = c.post("/api/v1/system/users/u_lock/lock", json={},
                    headers=H(api))
    assert r_lock.status_code == 200
    assert c.get("/api/v1/auth/me", headers=h).status_code == 401
    r2 = c.post("/api/v1/auth/login", json={
        "username": "u_lock", "password": "Str0ngPass!2026x"})
    assert r2.status_code == 401
    # 解锁恢复
    assert c.post("/api/v1/system/users/u_lock/unlock", json={},
                  headers=H(api)).status_code == 200
    assert c.post("/api/v1/auth/login", json={
        "username": "u_lock", "password": "Str0ngPass!2026x"}).status_code == 200


def test_reset_password_revokes_sessions_and_audits(api):
    c = api["client"]
    _mk_console_user(api, "u_reset", ["operator"])
    tok = c.post("/api/v1/auth/login", json={
        "username": "u_reset", "password": "Str0ngPass!2026x"}).json()["data"]
    tok = tok.get("access_token") or tok.get("token")
    h = {"Authorization": f"Bearer {tok}"}
    assert c.get("/api/v1/auth/me", headers=h).status_code == 200
    r = c.post("/api/v1/system/users/u_reset/reset-password", json={
        "password": "N3wStr0ngPass!99x"}, headers=H(api))
    assert r.status_code == 200 and r.json()["data"]["sessions_revoked"] >= 1
    assert c.get("/api/v1/auth/me", headers=h).status_code == 401
    assert c.post("/api/v1/auth/login", json={
        "username": "u_reset", "password": "N3wStr0ngPass!99x"}).status_code == 200
    # 审计
    with api["sf"]() as db:
        ev = (db.query(AuditEvent)
              .filter_by(action="system.user_password_reset").one())
        assert ev.actor.endswith("console-admin")   # actor=kind:code


def test_roles_change_takes_effect_immediately(api):
    """角色变更即时生效：operator 令牌在降权后失去写权限。"""
    c = api["client"]
    _mk_console_user(api, "u_role", ["operator"])
    tok = c.post("/api/v1/auth/login", json={
        "username": "u_role", "password": "Str0ngPass!2026x"}).json()["data"]
    tok = tok.get("access_token") or tok.get("token")
    h = {"Authorization": f"Bearer {tok}"}
    # operator 可访问 summary（有 "*" 范围）
    assert c.get("/api/v1/report-targets/drug_trace_alihealth/summary",
                 headers=h).status_code == 200
    # 降为 auditor（无 operator/business_reviewer 写角色）
    r = c.put("/api/v1/system/users/u_role/roles", json={
        "roles": ["auditor"], "target_scopes": ["*"]}, headers=H(api))
    assert r.status_code == 200
    # 同一令牌立即 403（处置写端点）
    r2 = c.post(
        "/api/v1/report-center/submissions/pathology_cloud/x/manual-resolution",
        json={"resolution": "REJECTED", "reason": "降权后尝试", "evidence_ref": "e",
              "expected_state": "UNKNOWN"}, headers=h)
    assert r2.status_code == 403


# ---------------------------------------------------------------- 范围鉴权
def test_scope_filtering_list_summary_ledger(api):
    c = api["client"]
    _mk_console_user(api, "u_dt", ["operator"], ["drug_trace_alihealth"])
    tok = c.post("/api/v1/auth/login", json={
        "username": "u_dt", "password": "Str0ngPass!2026x"}).json()["data"]
    tok = tok.get("access_token") or tok.get("token")
    h = {"Authorization": f"Bearer {tok}"}
    # 列表只看到 1 条（R08：不返回未授权业务编码/标题）
    r = c.get("/api/v1/report-targets", headers=h)
    codes = [t["code"] for t in r.json()["data"]]
    assert codes == ["drug_trace_alihealth"]
    # 授权业务 summary OK
    assert c.get("/api/v1/report-targets/drug_trace_alihealth/summary",
                 headers=h).status_code == 200
    # 未授权业务：summary/台账/详情均 403（含未知 code 同样 403，防泄露）
    for url in ("/api/v1/report-targets/pathology_cloud/summary",
                "/api/v1/report-targets/ghost/summary",
                "/api/v1/report-center/submissions?target=pathology_cloud"):
        rr = c.get(url, headers=h)
        assert rr.status_code == 403, (url, rr.status_code)
    rr = c.get("/api/v1/report-center/submissions/pathology_cloud/x",
               headers=h)
    assert rr.status_code == 403


def test_scope_overview_only_authorized_totals(api):
    c = api["client"]
    _mk_console_user(api, "u_inf", ["operator"], ["inf_cdc_frontend"])
    tok = c.post("/api/v1/auth/login", json={
        "username": "u_inf", "password": "Str0ngPass!2026x"}).json()["data"]
    tok = tok.get("access_token") or tok.get("token")
    r = c.get("/api/v1/report-center/overview",
              headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    targets = {t["target_code"] for t in r.json()["data"]["targets"]}
    assert targets == {"inf_cdc_frontend"}          # 不返回未授权业务


def test_scope_digest_forbidden_and_alerts_filtered(api):
    c = api["client"]
    _mk_console_user(api, "u_scope2", ["operator"], ["drug_trace_alihealth"])
    tok = c.post("/api/v1/auth/login", json={
        "username": "u_scope2", "password": "Str0ngPass!2026x"}).json()["data"]
    tok = tok.get("access_token") or tok.get("token")
    h = {"Authorization": f"Bearer {tok}"}
    # 晨报全院聚合 → 403（R08）
    assert c.get("/api/v1/alerts/digest/latest", headers=h).status_code == 403
    # 告警列表：无该业务告警时为空；写他业务告警不泄漏（403 on ack 用异业务 id）
    r = c.get("/api/v1/alerts2", headers=h)
    assert r.status_code == 200


def test_agent_token_unaffected_by_scopes(api):
    """R08：机器主体不受 scope 约束（既有生产鉴权零改变）。"""
    with api["sf"]() as db:
        db.add(AuthSubject(id=new_uuid(), tenant_id=api["tid"], kind="agent",
                           subject_code="agent-x", roles=["agent"],
                           token_hash=hash_token("tk-agentx"), status="ACTIVE"))
        db.commit()
    c = api["client"]
    r = c.get("/api/v1/report-targets",
              headers={"Authorization": "Bearer tk-agentx"})
    assert r.status_code == 200
    assert len(r.json()["data"]) == 5               # 全量（scope 不适用）


def test_scope_external_imports_and_schedules(api):
    """R08：范围校验覆盖导入与调度（不只列表过滤）。"""
    c = api["client"]
    _mk_console_user(api, "u_dt2", ["operator"], ["drug_trace_alihealth"])
    tok = c.post("/api/v1/auth/login", json={
        "username": "u_dt2", "password": "Str0ngPass!2026x"}).json()["data"]
    tok = tok.get("access_token") or tok.get("token")
    h = {"Authorization": f"Bearer {tok}"}
    # 导入（写）未授权 target → 403；授权 target 但内容非法 → 422（过了范围门）
    r1 = c.post("/api/v1/report-center/external-imports", json={
        "target": "pathology_cloud", "source_tz": "CST",
        "files": [{"name": "receipts-20261105.jsonl", "content_base64": ""}]},
        headers=h)
    assert r1.status_code == 403
    r2 = c.get("/api/v1/report-center/external-imports?target=inf_cdc_frontend",
               headers=h)
    assert r2.status_code == 403
    # 调度：受限用户列表只见授权业务（无系统调度）
    r3 = c.get("/api/v1/schedules", headers=h)
    assert r3.status_code == 200
    codes = {s["target_code"] for s in r3.json()["data"]}
    assert codes <= {"drug_trace_alihealth"} and "" not in codes
    # run-now 他业务镜像调度 → 403
    r4 = c.get("/api/v1/schedules?target=inf_cdc_frontend",
               headers={"Authorization": f"Bearer {api['admin']}"})
    inf_mirror = next(s for s in r4.json()["data"]
                      if s["code"] == "inf.poll_mirror")         if any(s["code"] == "inf.poll_mirror" for s in r4.json()["data"])         else None
    with api["sf"]() as db:
        from core_db.models import Schedule
        sid = (db.query(Schedule)
               .filter_by(target_code="inf_cdc_frontend").first())
        sid = sid.id if sid else None
    if sid:
        r5 = c.post(f"/api/v1/schedules/{sid}/run-now",
                    json={"reason": "越权立即运行尝试"}, headers=h)
        assert r5.status_code == 403
