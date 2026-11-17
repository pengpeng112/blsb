"""RT-02 集成测试：安全鉴权闭环 + 运维审计 + 最后管理员并发锁 +
单轨插件装配 + Oracle 合成桩兼容（R22 探针语义）。

覆盖：
1. medins_emr 全部 7 个接口的 target_scopes 越权拦截与放行；
2. PUT/POST /api/v1/report-targets 与 POST/PUT /api/v1/alert-rules 的范围校验；
3. 旧接口 /api/v1/alerts、/api/v1/submissions（含详情）按范围收缩，
   机器 token 与 ["*"] 管理员行为不变；
4. 告警 ack/resolve、规则增改、通道增改 6 类操作全部落 AuditEvent；
5. 最后管理员并发保护（自我降权/双管理员接力/锁定账号不充数/机器锁人）；
6. 单轨装配：_default_registry 与 create_app 始终持有真实 PluginRegistry、
   domain.routes 通用挂载、Compose 四服务 BLYB_PLUGINS 一致；
7. oracle_client 合成桩放行且不污染粘滞状态、真实驱动 Thick 失败仍阻断。
"""
from __future__ import annotations

import os
import sys
import types
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from contracts.api import hash_token  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.engine import utcnow  # noqa: E402
from core_db.models import (AlertEvent, AuditEvent, AuthSubject,  # noqa: E402
                            ConsoleAccount, DataPackage, Destination,
                            PackageRevision, ReportTarget, Submission, Tenant)
from plugins.report_targets import seed_default_targets  # noqa: E402


@pytest.fixture()
def rt02(tmp_path, monkeypatch):
    monkeypatch.setenv("BLYB_DATABASE_URL", f"sqlite:///{tmp_path}/rt02.sqlite3")
    monkeypatch.delenv("BLYB_PLUGINS", raising=False)
    import app_runtime
    reg = app_runtime.load_registry(["medins_emr", "drug_trace"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    client = TestClient(create_app(session_factory=sf, engine=engine,
                                   registry=reg))
    with sf() as db:
        t = Tenant(id=new_uuid(), code="rt02-h", name="RT02")
        db.add(t)
        db.flush()
        tid = t.id
        seed_default_targets(db, tid)
        # 主体矩阵：受限人类账号（各 scope 形态）+ 机器 service 管理员
        users = {
            "admin": (["platform_admin"], ["*"]),      # platform_admin 不受限
            "editor_dt": (["config_editor"], ["drug_trace_alihealth"]),
            "editor_none": (["config_editor"], []),
            "op_medins": (["operator"], ["medins_emr_sd"]),
            "op_none": (["operator"], []),
            "op_dt": (["operator"], ["drug_trace_alihealth"]),
            "view_dt": (["viewer"], ["drug_trace_alihealth"]),
            "view_star": (["viewer"], ["*"]),
        }
        tokens = {}
        for name, (roles, scopes) in users.items():
            tok = f"tk-rt02-{name}"
            db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind="user",
                               subject_code=f"u-{name}", roles=roles,
                               target_scopes=scopes,
                               token_hash=hash_token(tok), status="ACTIVE"))
            tokens[name] = tok
        machine_tok = "tk-rt02-machine"
        db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind="service",
                           subject_code="svc-ops", roles=["platform_admin"],
                           target_scopes=None,
                           token_hash=hash_token(machine_tok), status="ACTIVE"))
        db.commit()
    yield {"client": client, "sf": sf, "engine": engine, "tid": tid,
           "tokens": tokens, "machine": machine_tok}
    engine.dispose()


def H(rt02, who):
    return {"Authorization": f"Bearer {rt02['tokens'][who]}"}


# ================================================================ 1. medins_emr 范围
MEDINS_ENDPOINTS = [
    ("get", "/api/v1/medins-emr/transactions"),
    ("get", "/api/v1/medins-emr/transactions/11101"),
    ("post", "/api/v1/medins-emr/validate",
     {"code": "11101", "message": {}}),
    ("post", "/api/v1/medins-emr/send-mock",
     {"code": "11101", "message": {}}),
    ("post", "/api/v1/medins-emr/export-request",
     {"code": "11101", "message": {}}),
    ("get", "/api/v1/medins-emr/debug-exchanges"),
    ("post", "/api/v1/medins-emr/debug-exchanges/nonexistent/record-result",
     {"success": False, "note": "范围拦截验证用"}),
]


def _call(c, method, path, body=None, headers=None):
    if method == "get":
        return c.get(path, headers=headers)
    return c.post(path, json=body, headers=headers)


@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/v1/medins-emr/transactions", None),
    ("get", "/api/v1/medins-emr/transactions/11101", None),
    ("post", "/api/v1/medins-emr/validate", {"code": "11101", "message": {}}),
    ("post", "/api/v1/medins-emr/send-mock", {"code": "11101", "message": {}}),
    ("post", "/api/v1/medins-emr/export-request",
     {"code": "11101", "message": {}}),
    ("get", "/api/v1/medins-emr/debug-exchanges", None),
    ("post", "/api/v1/medins-emr/debug-exchanges/nonexistent/record-result",
     {"success": False, "note": "范围拦截验证用"}),
])
def test_medins_scope_empty_forbidden(rt02, method, path, body):
    """空范围 operator 访问全部 7 接口 → 403（fail-closed）。"""
    r = _call(rt02["client"], method, path, body, H(rt02, "op_none"))
    assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text}"


def test_medins_scope_wrong_target_forbidden(rt02):
    """持其他业务范围（drug_trace）的 operator 访问 7 接口 → 全部 403。"""
    c = rt02["client"]
    for method, path, *rest in MEDINS_ENDPOINTS:
        r = _call(c, method, path, rest[0] if rest else None, H(rt02, "op_dt"))
        assert r.status_code == 403, f"{method} {path}: {r.status_code}"


def test_medins_scope_editor_wrong_target_forbidden(rt02):
    """受限 config_editor（未授权 medins）同样被拦（角色不能越过范围）。"""
    c = rt02["client"]
    for method, path, *rest in MEDINS_ENDPOINTS[:3]:
        r = _call(c, method, path, rest[0] if rest else None, H(rt02, "editor_dt"))
        assert r.status_code == 403, f"{method} {path}: {r.status_code}"


def test_medins_scope_authorized_pass(rt02):
    """持 medins_emr_sd 范围：读接口放行（200，非 403）。"""
    c = rt02["client"]
    r1 = c.get("/api/v1/medins-emr/transactions", headers=H(rt02, "op_medins"))
    assert r1.status_code == 200 and isinstance(r1.json()["data"], list)
    r2 = c.get("/api/v1/medins-emr/transactions/11101",
               headers=H(rt02, "op_medins"))
    assert r2.status_code == 200 and r2.json()["data"]["code"] == "11101"


def test_medins_scope_star_pass(rt02):
    """["*"] 全局范围放行。"""
    r = rt02["client"].get("/api/v1/medins-emr/transactions",
                           headers=H(rt02, "view_star"))
    assert r.status_code == 200


def test_medins_machine_token_pass(rt02):
    """机器 service 主体不受 scope 约束（行为保持）。"""
    r = rt02["client"].get(
        "/api/v1/medins-emr/transactions",
        headers={"Authorization": f"Bearer {rt02['machine']}"})
    assert r.status_code == 200


# ================================================================ 2. report-targets / alert-rules
def _target_revision(rt02, code):
    with rt02["sf"]() as db:
        t = db.query(ReportTarget).filter_by(
            tenant_id=rt02["tid"], code=code).first()
        return t.revision


def test_put_report_target_wrong_scope_forbidden(rt02):
    """受限 config_editor 改未授权业务（medins_emr_sd）→ 403（防篡改）。"""
    rev = _target_revision(rt02, "medins_emr_sd")
    r = rt02["client"].put("/api/v1/report-targets/medins_emr_sd", json={
        "revision": rev, "schedule_note": "篡改尝试"}, headers=H(rt02, "editor_dt"))
    assert r.status_code == 403
    # 数据未被改动
    assert _target_revision(rt02, "medins_emr_sd") == rev


def test_put_report_target_authorized_editor_ok(rt02):
    """受限 config_editor 改自己授权业务（drug_trace_alihealth）→ 200。"""
    rev = _target_revision(rt02, "drug_trace_alihealth")
    r = rt02["client"].put("/api/v1/report-targets/drug_trace_alihealth", json={
        "revision": rev, "schedule_note": "RT-02 授权内编辑"}, headers=H(rt02, "editor_dt"))
    assert r.status_code == 200, r.text


def test_post_report_target_restricted_forbidden(rt02):
    """非管理员/受限账号新建业务 → 403。"""
    r = rt02["client"].post("/api/v1/report-targets", json={
        "code": "new_biz", "name": "越权新建"}, headers=H(rt02, "editor_dt"))
    assert r.status_code == 403


def test_post_report_target_admin_ok(rt02):
    """platform_admin（全局范围）新建业务 → 200。"""
    r = rt02["client"].post("/api/v1/report-targets", json={
        "code": "rt02_new_biz", "name": "RT-02 新建",
        "category": "测试", "authority": "测试", "plugin_key": "drug_trace",
        "mode": "PUSH", "lifecycle": "PLANNED", "runtime_kind": "PLATFORM"},
        headers=H(rt02, "admin"))
    assert r.status_code == 200, r.text


def _mk_rule(rt02, code, target_code=""):
    from core_db.models import AlertRule
    with rt02["sf"]() as db:
        db.add(AlertRule(id=new_uuid(), tenant_id=rt02["tid"], code=code,
                         name=code, target_code=target_code, kind="BACKLOG_OVER",
                         params={"max": 5}, severity="WARN", enabled=False,
                         cooldown_minutes=60, notify_channels=["file_default"],
                         revision=1, created_at=utcnow(), updated_at=utcnow(),
                         updated_by="u-admin"))
        db.commit()


def _rule_revision(rt02, code):
    from core_db.models import AlertRule
    with rt02["sf"]() as db:
        return db.query(AlertRule).filter_by(
            tenant_id=rt02["tid"], code=code).first().revision


def test_alert_rule_update_other_target_forbidden(rt02):
    """受限 config_editor 改他人业务规则 → 403。"""
    _mk_rule(rt02, "rule_inf", target_code="inf_cdc_frontend")
    r = rt02["client"].put("/api/v1/alert-rules/rule_inf", json={
        "revision": 1, "enabled": True}, headers=H(rt02, "editor_dt"))
    assert r.status_code == 403


def test_alert_rule_update_system_rule_forbidden(rt02):
    """系统级规则（target_code 空）对单业务受限账号 → 403。"""
    _mk_rule(rt02, "rule_sys", target_code="")
    r = rt02["client"].put("/api/v1/alert-rules/rule_sys", json={
        "revision": 1, "enabled": True}, headers=H(rt02, "editor_dt"))
    assert r.status_code == 403


def test_alert_rule_retarget_to_unauthorized_forbidden(rt02):
    """授权内规则试图改挂未授权业务 → 403。"""
    _mk_rule(rt02, "rule_dt", target_code="drug_trace_alihealth")
    r = rt02["client"].put("/api/v1/alert-rules/rule_dt", json={
        "revision": 1, "target_code": "inf_cdc_frontend"}, headers=H(rt02, "editor_dt"))
    assert r.status_code == 403


def test_alert_rule_update_authorized_ok(rt02):
    """授权内规则正常更新 → 200（范围校验不误伤）。"""
    _mk_rule(rt02, "rule_dt2", target_code="drug_trace_alihealth")
    r = rt02["client"].put("/api/v1/alert-rules/rule_dt2", json={
        "revision": 1, "enabled": True}, headers=H(rt02, "editor_dt"))
    assert r.status_code == 200, r.text
    assert _rule_revision(rt02, "rule_dt2") == 2


def test_alert_rule_create_restricted_forbidden(rt02):
    """受限 config_editor 新建规则 → 403（角色门），管理员新建业务规则 OK。"""
    r = rt02["client"].post("/api/v1/alert-rules", json={
        "code": "rule_new", "kind": "BACKLOG_OVER", "target_code": "",
        "params": {"max": 1}}, headers=H(rt02, "editor_dt"))
    assert r.status_code == 403
    r2 = rt02["client"].post("/api/v1/alert-rules", json={
        "code": "rule_new_admin", "kind": "BACKLOG_OVER",
        "target_code": "drug_trace_alihealth", "params": {"max": 1}},
        headers=H(rt02, "admin"))
    assert r2.status_code == 200, r2.text


# ================================================================ 3. 旧接口范围收缩
def _mk_alert(rt02, code_suffix, target_code):
    with rt02["sf"]() as db:
        ev = AlertEvent(id=new_uuid(), tenant_id=rt02["tid"],
                        alert_type="BACKLOG", severity="WARN",
                        message=f"alert-{code_suffix}", target_ref="x",
                        state="OPEN", fingerprint=f"fp-{code_suffix}",
                        rule_code="r", target_code=target_code,
                        created_at=utcnow(), first_seen_at=utcnow(),
                        last_seen_at=utcnow(), occurrences=1)
        db.add(ev)
        db.commit()
        return ev.id


def _mk_submission(rt02, dest_code, target_code):
    """Destination + 既有 ReportTarget 绑定 destination_id + 一条 Submission。"""
    with rt02["sf"]() as db:
        dest = Destination(id=new_uuid(), tenant_id=rt02["tid"], code=dest_code,
                           mode="PUSH", is_mock=True)
        db.add(dest)
        db.flush()
        t = (db.query(ReportTarget)
             .filter_by(tenant_id=rt02["tid"], code=target_code).one())
        t.destination_id = dest.id          # 种子业务：绑定目的地
        pkg = DataPackage(id=new_uuid(), tenant_id=rt02["tid"],
                          domain="drug_trace", business_key=f"k-{dest_code}")
        db.add(pkg)
        db.flush()
        pr = PackageRevision(id=new_uuid(), tenant_id=rt02["tid"],
                             package_id=pkg.id, revision=1, state="FROZEN")
        db.add(pr)
        db.flush()
        s = Submission(id=new_uuid(), tenant_id=rt02["tid"],
                       package_revision_id=pr.id, destination_id=dest.id,
                       idempotency_key=f"idem-{dest_code}",
                       snapshot_digest="0" * 64, state="ACCEPTED")
        db.add(s)
        db.commit()
        return s.id


def test_old_alerts_scope_filtered(rt02):
    """旧 /api/v1/alerts：受限账号只见授权业务（系统级 target_code='' 不见）。"""
    _mk_alert(rt02, "dt", "drug_trace_alihealth")
    _mk_alert(rt02, "inf", "inf_cdc_frontend")
    _mk_alert(rt02, "sys", "")
    r = rt02["client"].get("/api/v1/alerts", headers=H(rt02, "view_dt"))
    assert r.status_code == 200
    msgs = [a["message"] for a in r.json()["data"]]
    assert msgs == ["alert-dt"], msgs


def test_old_alerts_empty_scope_returns_nothing(rt02):
    _mk_alert(rt02, "dt2", "drug_trace_alihealth")
    r = rt02["client"].get("/api/v1/alerts", headers=H(rt02, "op_none"))
    assert r.status_code == 200 and r.json()["data"] == []


def test_old_alerts_machine_and_star_unchanged(rt02):
    """机器 token 与 ["*"] 账号仍见全部（含系统级）——既有行为不变。"""
    _mk_alert(rt02, "dt3", "drug_trace_alihealth")
    _mk_alert(rt02, "sys3", "")
    r1 = rt02["client"].get(
        "/api/v1/alerts", headers={"Authorization": f"Bearer {rt02['machine']}"})
    assert len(r1.json()["data"]) == 2
    r2 = rt02["client"].get("/api/v1/alerts", headers=H(rt02, "view_star"))
    assert len(r2.json()["data"]) == 2


def test_old_submissions_scope_filtered(rt02):
    """旧 /api/v1/submissions：受限账号只返回授权业务（destination 绑定）。"""
    _mk_submission(rt02, "dest-dt", "drug_trace_alihealth")
    _mk_submission(rt02, "dest-inf", "inf_cdc_frontend")
    r = rt02["client"].get("/api/v1/submissions", headers=H(rt02, "view_dt"))
    assert r.status_code == 200
    dests = [s["destination"] for s in r.json()["data"]]
    assert dests == ["dest-dt"], dests


def test_old_submission_detail_hidden_for_restricted(rt02):
    """旧详情接口：受限账号访问未授权业务提交 → 404（不泄露存在性）。"""
    sid_dt = _mk_submission(rt02, "dest-dt2", "drug_trace_alihealth")
    sid_inf = _mk_submission(rt02, "dest-inf2", "inf_cdc_frontend")
    c = rt02["client"]
    assert c.get(f"/api/v1/submissions/{sid_inf}",
                 headers=H(rt02, "view_dt")).status_code == 404
    assert c.get(f"/api/v1/submissions/{sid_dt}",
                 headers=H(rt02, "view_dt")).status_code == 200
    # 管理员不受影响
    assert c.get(f"/api/v1/submissions/{sid_inf}",
                 headers=H(rt02, "admin")).status_code == 200


def test_old_submissions_machine_unchanged(rt02):
    _mk_submission(rt02, "dest-a", "drug_trace_alihealth")
    _mk_submission(rt02, "dest-b", "inf_cdc_frontend")
    r = rt02["client"].get(
        "/api/v1/submissions",
        headers={"Authorization": f"Bearer {rt02['machine']}"})
    assert len(r.json()["data"]) == 2


# ================================================================ 4. 运维审计
def _audits(rt02, action, actor_suffix):
    with rt02["sf"]() as db:
        rows = db.query(AuditEvent).filter_by(
            tenant_id=rt02["tid"], action=action).all()
        return [r for r in rows if r.actor.endswith(actor_suffix)]


def _mk_ackable_alert(rt02, target_code="drug_trace_alihealth",
                      alert_type="BACKLOG"):
    ev_id = _mk_alert(rt02, "ack", target_code)
    with rt02["sf"]() as db:
        ev = db.get(AlertEvent, ev_id)
        ev.alert_type = alert_type
        db.commit()
    return ev_id


def test_audit_alert_ack(rt02):
    ev_id = _mk_ackable_alert(rt02)
    r = rt02["client"].post(f"/api/v1/alerts2/{ev_id}/ack", json={
        "reason": "RT-02 已确认处理"}, headers=H(rt02, "op_dt"))
    assert r.status_code == 200, r.text
    rows = _audits(rt02, "alert.ack", "u-op_dt")
    assert rows and rows[0].target == f"alert_event:{ev_id}"
    assert rows[0].new_ref.get("ack_reason") == "RT-02 已确认处理"
    assert rows[0].trace_id  # 记录了请求追踪


def test_audit_alert_resolve(rt02):
    ev_id = _mk_ackable_alert(rt02, alert_type="DATE_EXPIRY")
    r = rt02["client"].post(f"/api/v1/alerts2/{ev_id}/resolve", json={
        "reason": "RT-02 人工关闭（日期过期类）"}, headers=H(rt02, "admin"))
    assert r.status_code == 200, r.text
    rows = _audits(rt02, "alert.resolve", "u-admin")
    assert rows and rows[0].new_ref.get("alert_type") == "DATE_EXPIRY"


def test_audit_alert_rule_create_and_update(rt02):
    c = rt02["client"]
    r = c.post("/api/v1/alert-rules", json={
        "code": "aud-rule", "kind": "BACKLOG_OVER",
        "target_code": "drug_trace_alihealth", "params": {"max": 3},
        "name": "审计规则", "enabled": False}, headers=H(rt02, "admin"))
    assert r.status_code == 200, r.text
    rows = _audits(rt02, "alert_rule.create", "u-admin")
    assert rows and rows[0].target == "alert_rule:aud-rule"
    r2 = c.put("/api/v1/alert-rules/aud-rule", json={
        "revision": _rule_revision(rt02, "aud-rule"), "enabled": True},
        headers=H(rt02, "admin"))
    assert r2.status_code == 200, r2.text
    rows2 = _audits(rt02, "alert_rule.update", "u-admin")
    assert rows2 and rows2[0].old_ref.get("enabled") is False \
        and rows2[0].new_ref.get("enabled") is True


def test_audit_notification_channel_create_and_update(rt02):
    c = rt02["client"]
    r = c.post("/api/v1/notification-channels", json={
        "code": "aud-file", "type": "file", "config": {"path": "rt02.log"},
        "enabled": False}, headers=H(rt02, "admin"))
    assert r.status_code == 200, r.text
    rows = _audits(rt02, "notification_channel.create", "u-admin")
    assert rows and rows[0].target == "notification_channel:aud-file"
    assert "config" not in rows[0].new_ref   # 配置内容不入审计
    r2 = c.put("/api/v1/notification-channels/aud-file", json={
        "enabled": True}, headers=H(rt02, "admin"))
    assert r2.status_code == 200, r2.text
    rows2 = _audits(rt02, "notification_channel.update", "u-admin")
    assert rows2 and rows2[0].old_ref.get("enabled") is False \
        and rows2[0].new_ref.get("enabled") is True


def test_audit_schedule_enable_already_present(rt02):
    """调度 enable 的审计早已在服务层（schedule.enable）——核对不缺。"""
    c = rt02["client"]
    r = c.post("/api/v1/schedules", json={
        "code": "aud-sched", "name": "审计调度", "job_type": "metrics.run",
        "cron": "0 3 * * *", "target_code": "drug_trace_alihealth"},
        headers=H(rt02, "admin"))
    assert r.status_code == 200, r.text
    sid = r.json()["data"]["id"]
    r2 = c.post(f"/api/v1/schedules/{sid}/enable", json={
        "reason": "RT-02 审计启用"}, headers=H(rt02, "admin"))
    assert r2.status_code == 200, r2.text
    with rt02["sf"]() as db:
        acts = {row.action for row in db.query(AuditEvent).filter_by(
            tenant_id=rt02["tid"]).all()
            if row.target.startswith("schedule:aud-sched")}
    assert {"schedule.upsert", "schedule.enable"} <= acts


# ================================================================ 5. 最后管理员并发保护
def _isolate_admin_world(rt02):
    """把 fixture 的 token-admin（无 ConsoleAccount 的 platform_admin 用户）
    降为 operator——使租户内只剩测试自建的管理员，守护判定可精确断言。
    操作者改用机器 service 管理员（不受人类降权/锁定影响）。"""
    with rt02["sf"]() as db:
        subj = db.query(AuthSubject).filter_by(
            tenant_id=rt02["tid"], subject_code="u-admin").first()
        subj.roles = ["operator"]
        db.commit()
    return {"Authorization": f"Bearer {rt02['machine']}"}


def _mk_admin_with_account(rt02, username, *, locked=False):
    """直插管理员主体 + 控制台账号（密码哈希占位，不走登录路径）。"""
    from contracts.passwords import hash_password
    with rt02["sf"]() as db:
        subj = AuthSubject(id=new_uuid(), tenant_id=rt02["tid"], kind="user",
                           subject_code=f"console-{username}",
                           roles=["platform_admin"], target_scopes=["*"],
                           token_hash=hash_token(f"tk-{username}-rt02"),
                           status="ACTIVE")
        db.add(subj)
        db.flush()
        acc = ConsoleAccount(id=new_uuid(), tenant_id=rt02["tid"],
                             subject_id=subj.id, username=username,
                             password_hash=hash_password("Str0ngPass!2026x"))
        if locked:
            acc.locked_until = utcnow() + timedelta(days=3650)
        db.add(acc)
        db.commit()
        return username


def _demote(rt02, headers, username):
    return rt02["client"].put(
        f"/api/v1/system/users/{username}/roles",
        json={"roles": ["operator"], "target_scopes": []},
        headers=headers)


def _active_admin_count(rt02):
    with rt02["sf"]() as db:
        admins = (db.query(AuthSubject)
                  .filter_by(tenant_id=rt02["tid"], kind="user",
                             status="ACTIVE").all())
        now = utcnow()
        n = 0
        for s in admins:
            if "platform_admin" not in (s.roles or []):
                continue
            acc = db.query(ConsoleAccount).filter_by(subject_id=s.id).first()
            if acc is not None and acc.locked_until is not None \
                    and acc.locked_until > now:
                continue
            n += 1
        return n


def test_last_admin_self_demote_blocked(rt02):
    """唯一活跃管理员自我降权 → 409，管理员数保持 1。"""
    hdr = _isolate_admin_world(rt02)
    name = _mk_admin_with_account(rt02, "only_admin")
    r = _demote(rt02, hdr, name)
    assert r.status_code == 409, r.text
    assert _active_admin_count(rt02) == 1


def test_last_admin_two_admins_second_blocked(rt02):
    """双管理员接力：第一个降权成功，第二个（此时已成最后一名）→ 409。"""
    hdr = _isolate_admin_world(rt02)
    a = _mk_admin_with_account(rt02, "adm_a")
    b = _mk_admin_with_account(rt02, "adm_b")
    r1 = _demote(rt02, hdr, a)
    assert r1.status_code == 200, r1.text
    r2 = _demote(rt02, hdr, b)
    assert r2.status_code == 409
    assert _active_admin_count(rt02) == 1


def test_last_admin_locked_admin_not_counted(rt02):
    """唯一活跃管理员 + 另一名已锁定管理员：降权/锁定活跃者 → 409。"""
    hdr = _isolate_admin_world(rt02)
    _mk_admin_with_account(rt02, "adm_locked", locked=True)
    active = _mk_admin_with_account(rt02, "adm_active")
    assert _active_admin_count(rt02) == 1
    r = _demote(rt02, hdr, active)
    assert r.status_code == 409
    # 锁定路径同样受保护（机器 service 管理员锁定最后一名人类活跃管理员）
    r2 = rt02["client"].post(f"/api/v1/system/users/{active}/lock", json={},
                             headers=hdr)
    assert r2.status_code == 409
    assert _active_admin_count(rt02) == 1


def test_last_admin_dual_session_interleaved(rt02):
    """双 Session 交错（PostgreSQL FOR UPDATE 行锁释放后的第二事务等价）：
    s1 完整提交降权 A 后，s2 的判定必须读取已提交状态 → B 被拦 409。"""
    hdr = _isolate_admin_world(rt02)
    a = _mk_admin_with_account(rt02, "adm_c")
    b = _mk_admin_with_account(rt02, "adm_d")
    sf = rt02["sf"]
    # s1：模拟事务1——与生产守护同形（id 升序 FOR UPDATE 锁全租户活跃集合）
    with sf() as s1:
        rows = (s1.query(AuthSubject)
                .filter_by(tenant_id=rt02["tid"], kind="user",
                           status="ACTIVE")
                .order_by(AuthSubject.id).with_for_update().all())
        subj_a = next(r for r in rows
                      if r.subject_code == "console-adm_c")
        subj_a.roles = ["operator"]
        s1.commit()
    # s2：事务2（新会话）——经 API 判定降权 B：必读已提交状态 → 409
    r = _demote(rt02, hdr, b)
    assert r.status_code == 409
    assert _active_admin_count(rt02) == 1


def test_lock_non_last_admin_ok(rt02):
    """存在其他活跃管理员时锁定/降权正常放行（保护不误伤）。"""
    hdr = _isolate_admin_world(rt02)
    a = _mk_admin_with_account(rt02, "adm_e")
    b = _mk_admin_with_account(rt02, "adm_f")
    r = rt02["client"].post(f"/api/v1/system/users/{a}/lock", json={},
                            headers=hdr)
    assert r.status_code == 200, r.text
    assert _active_admin_count(rt02) == 1  # b 仍在


# ================================================================ 6. 单轨装配
def test_engine_default_registry_single_track(monkeypatch):
    """未设/显式设 BLYB_PLUGINS，engine 默认装配都是真实 PluginRegistry。"""
    from apps.worker.engine import PluginRegistryAdapter, _default_registry
    monkeypatch.delenv("BLYB_PLUGINS", raising=False)
    r1 = _default_registry()
    assert isinstance(r1, PluginRegistryAdapter) and r1.reg is not None
    import plugin_registry
    assert r1.reg is plugin_registry.shared_registry()  # 与兼容层同一实例
    monkeypatch.setenv("BLYB_PLUGINS", "metrics")
    r2 = _default_registry()
    assert isinstance(r2, PluginRegistryAdapter) and r2.reg is not None
    assert r2.reg.enabled == ["metrics"]


def test_plugin_registry_shared_instance_stable():
    import plugin_registry
    assert plugin_registry.shared_registry() is plugin_registry._registry
    assert plugin_registry.shared_registry() is plugin_registry.shared_registry()
    # 兼容层导出面与同一实例的处理器表一致（不产生两套分裂表）
    assert plugin_registry.handler_for(
        "resolve", "pathology.resolve") is plugin_registry.shared_registry(
        ).handler_for("resolve", "pathology.resolve")


def test_app_default_assembly_single_track(rt02, tmp_path, monkeypatch):
    """未设 BLYB_PLUGINS：create_app 持真实 registry——
    medins 路由不挂载（默认集无 medins_emr）、drug_trace job 类型未知；
    病理+metrics 默认集不受影响（旧行为兼容）。"""
    monkeypatch.delenv("BLYB_PLUGINS", raising=False)
    monkeypatch.setenv("BLYB_DATABASE_URL", f"sqlite:///{tmp_path}/def.sqlite3")
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    e = make_engine()
    run_migrations(e)
    sf2 = make_session_factory(e)
    c = TestClient(create_app(session_factory=sf2, engine=e))
    # medins 未启用 → 路由不存在（404 而非 401/403）
    assert c.get("/api/v1/medins-emr/transactions",
                 headers=H(rt02, "admin")).status_code == 404
    # ops 收到真实 registry：默认集（pathology+metrics）不含 drug_trace
    from scheduling.scheduler import known_job_types
    import plugin_registry
    jt = known_job_types(plugin_registry.shared_registry())
    assert "drug_trace.collect" not in jt
    assert "pathology.resolve" in jt
    e.dispose()


def test_app_explicit_plugins_assembly(rt02, tmp_path, monkeypatch):
    """显式 BLYB_PLUGINS：ops_routes 拿到领域 job 类型（单轨闭环）。"""
    monkeypatch.setenv("BLYB_PLUGINS", "pathology,metrics,drug_trace,medins_emr")
    monkeypatch.setenv("BLYB_DATABASE_URL", f"sqlite:///{tmp_path}/expl.sqlite3")
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    e = make_engine()
    import app_runtime
    reg = app_runtime.load_registry()  # 按 BLYB_PLUGINS
    run_migrations(e, extra_migrations=reg.migrations)
    sf2 = make_session_factory(e)
    with sf2() as db:
        t = Tenant(id=new_uuid(), code="expl-h", name="X")
        db.add(t)
        db.flush()
        tid = t.id
        seed_default_targets(db, tid)
        tok = "tk-expl-admin"
        db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind="user",
                           subject_code="u-expl", roles=["platform_admin"],
                           token_hash=hash_token(tok), status="ACTIVE"))
        db.commit()
    c = TestClient(create_app(session_factory=sf2, engine=e))
    hdr = {"Authorization": f"Bearer {tok}"}
    # domain.routes 通用挂载：medins 路由已注册（403 而非 404，未带范围）
    assert c.get("/api/v1/medins-emr/transactions",
                 headers=hdr).status_code == 200
    # 调度校验接受 drug_trace 领域 job（registry 传递到 ops_routes）
    r = c.post("/api/v1/schedules", json={
        "code": "expl-dt", "name": "DT", "job_type": "drug_trace.collect",
        "cron": "0 1 * * *", "target_code": "drug_trace_alihealth"},
        headers=hdr)
    assert r.status_code == 200, r.text
    e.dispose()


def test_compose_plugins_consistency():
    """override 四服务（core/worker/migrate/bootstrap）BLYB_PLUGINS 默认集一致。"""
    import re
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[2] / "deploy" / "compose"
    ov = yaml.safe_load((root / "drug-trace.override.yml").read_text(
        encoding="utf-8"))
    expected_var = "${BLYB_PLUGINS_DRUGTRACE:-pathology,metrics,drug_trace," \
                   "infectious,medins_emr,medins_imaging}"
    vals = {}
    for svc in ("core", "worker", "migrate", "bootstrap"):
        env = ov["services"][svc]["environment"]
        assert "BLYB_PLUGINS" in env, f"override 缺 {svc}.BLYB_PLUGINS"
        vals[svc] = env["BLYB_PLUGINS"]
    assert set(vals.values()) == {expected_var}, vals
    # 基础 compose：migrate 服务（共享 env 锚点）也带 BLYB_PLUGINS
    base = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"^  migrate:.*?BLYB_PLUGINS:", base, re.S | re.M)
    # 两个新插件 key 均真实存在（manifest 可装配）
    import app_runtime
    assert {"infectious", "medins_emr", "medins_imaging",
            "drug_trace"} <= set(app_runtime.PLUGIN_MANIFESTS)


# ================================================================ 7. Oracle 探针兼容
def _reset_oracle_state():
    from sources.db_view import oracle_client
    oracle_client.reset_for_tests()
    return oracle_client


def test_oracle_stub_namespace_allowed_no_sticky(monkeypatch):
    """R22 探针桩（SimpleNamespace(connect=...)）：返回 stub 模式且不写粘滞状态。"""
    oc = _reset_oracle_state()
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules, "oracledb",
                        SimpleNamespace(connect=lambda *a, **k: None))
    mode, ver = oc.ensure_oracle_client()
    assert (mode, ver) == ("stub", None)
    st = oc.oracle_client_status()
    assert st["initialized"] is False and st["blocked"] is None


def test_oracle_real_driver_failure_blocks_and_sticky(monkeypatch):
    """真实形态驱动（init_oracle_client 可调用）失败：严格阻断 + 粘滞，
    Thick 强制语义 100% 不变。"""
    oc = _reset_oracle_state()
    calls = []

    def _init(lib_dir=None):
        calls.append(lib_dir)

        raise RuntimeError("DPY-6000: cannot load the library")

    fake = types.ModuleType("oracledb")
    fake.init_oracle_client = _init
    fake.__version__ = "2.4.1"
    fake.DatabaseError = type("DatabaseError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "oracledb", fake)
    monkeypatch.delenv("BLYB_DRUGTRACE_ORACLE_ALLOW_THIN", raising=False)
    from connector_sdk import CapabilityError
    with pytest.raises(CapabilityError) as ei:
        oc.ensure_oracle_client()
    assert getattr(ei.value, "code", "") == "DRIVER_UNAVAILABLE"
    assert calls == [None]                     # 确实尝试过 Thick 初始化
    # 失败粘滞：同进程再次调用直接阻断（不重试）
    with pytest.raises(CapabilityError):
        oc.ensure_oracle_client()
    assert calls == [None]


def test_oracle_stub_then_real_driver_clean(monkeypatch):
    """桩放行后不污染状态：随后真实驱动失败路径仍按首次语义阻断。"""
    oc = _reset_oracle_state()
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules, "oracledb",
                        SimpleNamespace(connect=lambda *a, **k: None))
    assert oc.ensure_oracle_client() == ("stub", None)
    assert oc.oracle_client_status()["initialized"] is False  # 未被桩置位

    def _init(lib_dir=None):
        raise OSError("lib not found")
    fake = types.ModuleType("oracledb")
    fake.init_oracle_client = _init
    fake.__version__ = "2.4.1"
    fake.DatabaseError = type("DatabaseError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "oracledb", fake)
    from connector_sdk import CapabilityError
    with pytest.raises(CapabilityError):
        oc.ensure_oracle_client()


def test_oracle_warehouse_constructs_with_stub(rt02, monkeypatch):
    """R22 全链路：桩驱动 + DSN 已配置 → OracleWarehouse 构造成功。"""
    from types import SimpleNamespace
    oc = _reset_oracle_state()
    monkeypatch.setitem(sys.modules, "oracledb",
                        SimpleNamespace(connect=lambda *a, **k: None))
    monkeypatch.setenv("BLYB_ORACLE_DSN", "synthetic-only-no-network")
    from sources.db_view.warehouse import OracleWarehouse
    wh = OracleWarehouse()          # 不抛异常即通过（探针 R22 语义）
    assert wh.dialect() == "oracle"
