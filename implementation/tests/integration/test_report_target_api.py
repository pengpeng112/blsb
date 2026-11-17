"""LT-01 集成测试：上报业务目录 6 端点 + overview 钩子化回归。"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import (AuthSubject, ConnectorInstance,
                            DictionaryRevision, Destination, Tenant)  # noqa: E402

from tests.integration.test_drug_trace_api import H  # noqa: E402  (header helper)


@pytest.fixture()
def api(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/rt-api.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    client = TestClient(create_app(session_factory=sf, engine=engine,
                                   registry=reg))
    with sf() as db:
        t = Tenant(id=new_uuid(), code="rt-hospital", name="目录API租户")
        db.add(t)
        db.flush()
        tid = t.id
        subjects = {}
        from contracts.api import hash_token
        for role, roles in (("operator", ["operator"]),
                            ("editor", ["config_editor"]),
                            ("admin", ["platform_admin"]),
                            ("auditor", ["auditor"])):
            token = f"tok-{role}"
            db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind="user",
                               subject_code=f"user-{role}", roles=roles,
                               token_hash=hash_token(token), status="ACTIVE", target_scopes=["*"]))
            subjects[role] = token
        db.commit()
    yield {"client": client, "sf": sf, "engine": engine, "tid": tid,
           "subjects": subjects}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


@pytest.fixture()
def seeded(api):
    from plugins.report_targets import seed_default_targets
    with api["sf"]() as db:
        n = seed_default_targets(db, api["tid"])
        db.commit()
    assert n == 5
    return api


# ---------------------------------------------------------------- 列表/详情
def test_list_targets_any_role(seeded):
    for role in ("operator", "auditor", "admin"):
        r = seeded["client"].get("/api/v1/report-targets", headers=H(seeded, role))
        assert r.status_code == 200, r.text
        codes = [x["code"] for x in r.json()["data"]]
        assert codes == sorted(codes) and len(codes) == 5


def test_get_target_detail(seeded):
    r = seeded["client"].get("/api/v1/report-targets/drug_trace_alihealth",
                             headers=H(seeded, "operator"))
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["lifecycle"] == "PRODUCTION" and d["sole_pusher"] == "CONFLICT"


def test_get_target_404(seeded):
    r = seeded["client"].get("/api/v1/report-targets/ghost",
                             headers=H(seeded, "operator"))
    assert r.status_code == 404


# ---------------------------------------------------------------- 新建
def test_create_by_admin(seeded):
    r = seeded["client"].post(
        "/api/v1/report-targets",
        headers=H(seeded, "admin"),
        json={"code": "new_biz", "name": "新业务", "category": "其他",
              "lifecycle": "PLANNED", "mode": "PUSH",
              "runtime_kind": "PLATFORM"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["revision"] == 1


def test_create_duplicate_409(seeded):
    body = {"code": "pathology_cloud", "name": "重复", "lifecycle": "PLANNED"}
    r = seeded["client"].post("/api/v1/report-targets",
                              headers=H(seeded, "admin"), json=body)
    assert r.status_code == 409


def test_create_by_operator_403(seeded):
    r = seeded["client"].post(
        "/api/v1/report-targets", headers=H(seeded, "operator"),
        json={"code": "x2", "name": "越权", "lifecycle": "PLANNED"})
    assert r.status_code == 403


def test_create_bad_enum_422(seeded):
    # 系统错误码约定 VALIDATION_FAILED→422（contracts.ERROR_CODES；任务书 400 为近似值）
    r = seeded["client"].post(
        "/api/v1/report-targets", headers=H(seeded, "admin"),
        json={"code": "x3", "name": "坏枚举", "lifecycle": "NOT_A_STATE"})
    assert r.status_code == 422


# ---------------------------------------------------------------- 更新（乐观锁）
def test_update_with_revision(seeded):
    r = seeded["client"].put(
        "/api/v1/report-targets/medins_emr_sd", headers=H(seeded, "editor"),
        json={"revision": 1, "owner_dept": "病案科", "contact": "病案科张工"})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["revision"] == 2 and d["owner_dept"] == "病案科"


def test_update_stale_revision_409(seeded):
    r = seeded["client"].put(
        "/api/v1/report-targets/medins_emr_sd", headers=H(seeded, "editor"),
        json={"revision": 99, "owner_dept": "x"})
    assert r.status_code == 409


def test_update_missing_revision_422(seeded):
    r = seeded["client"].put(
        "/api/v1/report-targets/medins_emr_sd", headers=H(seeded, "editor"),
        json={"owner_dept": "x"})
    assert r.status_code == 422


def test_update_by_operator_403(seeded):
    r = seeded["client"].put(
        "/api/v1/report-targets/medins_emr_sd", headers=H(seeded, "operator"),
        json={"revision": 1, "owner_dept": "x"})
    assert r.status_code == 403


# ---------------------------------------------------------------- summary / overview
def test_summary_days_validation(seeded):
    # VALIDATION_FAILED→422（系统约定；任务书 400 为近似值）
    for days in ("0", "91"):
        r = seeded["client"].get(
            f"/api/v1/report-targets/drug_trace_alihealth/summary?days={days}",
            headers=H(seeded, "operator"))
        assert r.status_code == 422, days


def test_summary_no_hook_data_source_none(seeded):
    """medins_emr_sd 无插件钩子认领 → data_source=none，buckets 全 0，不 500。"""
    r = seeded["client"].get(
        "/api/v1/report-targets/medins_emr_sd/summary?days=7",
        headers=H(seeded, "operator"))
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["data_source"] == "none"
    assert all(v == 0 for v in d["buckets"].values())


def test_summary_404_unknown_target(seeded):
    r = seeded["client"].get("/api/v1/report-targets/ghost/summary?days=7",
                             headers=H(seeded, "operator"))
    assert r.status_code == 404


def test_report_center_overview(seeded):
    r = seeded["client"].get("/api/v1/report-center/overview?days=7",
                             headers=H(seeded, "auditor"))
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert len(d["targets"]) == 5
    assert d["totals"]["PENDING"] == sum(
        t["buckets"]["PENDING"] for t in d["targets"])
    codes = {t["target_code"] for t in d["targets"]}
    assert codes == {"drug_trace_alihealth", "inf_cdc_frontend",
                     "medins_emr_sd", "pathology_cloud", "medins_imaging_jn"}
    drug = next(t for t in d["targets"]
                if t["target_code"] == "drug_trace_alihealth")
    assert drug["lifecycle"] == "PRODUCTION" and drug["sole_pusher"] == "CONFLICT"


def test_report_center_days_validation(seeded):
    r = seeded["client"].get("/api/v1/report-center/overview?days=0",
                             headers=H(seeded, "operator"))
    assert r.status_code == 422


def test_report_center_hook_error_isolated(seeded, monkeypatch):
    """单业务钩子抛异常 → 该项带 error 字段，其余照常（局部失败隔离）。"""
    import plugins.domain_hooks as dh
    import app_runtime

    def boom(db, tenant_id, target_code, days):
        if target_code == "drug_trace_alihealth":
            raise RuntimeError("hook exploded")
        return None

    reg = app_runtime.load_registry(["drug_trace"])
    monkeypatch.setattr(reg, "target_summary",
                        lambda db, tid, code, days: boom(db, tid, code, days),
                        raising=False)
    # 直接在闭包里重挂钩子不可行（registry 已建）——用 app 层 monkeypatch：
    from apps.core_api import report_target_routes as RTR

    class FakeReg:
        def has(self, plugin_key):
            return False

        def capability_list(self, key):    # RT-02：domain.routes 通用挂载契约
            return []

        def target_summary(self, db, tid, code, days):
            return boom(db, tid, code, days)

    import apps.core_api.app as app_mod
    orig_create = app_mod.create_app

    client = seeded["client"]
    # 重建一个 app，注入抛异常 registry
    from fastapi.testclient import TestClient
    app2 = orig_create(session_factory=seeded["sf"], engine=seeded["engine"],
                       registry=FakeReg())
    c2 = TestClient(app2)
    r = c2.get("/api/v1/report-center/overview?days=7",
               headers=H(seeded, "auditor"))
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    drug = next(t for t in d["targets"]
                if t["target_code"] == "drug_trace_alihealth")
    assert "error" in drug
    others = [t for t in d["targets"] if t["target_code"] != "drug_trace_alihealth"]
    assert all("error" not in t for t in others)


# ---------------------------------------------------------------- 跨租户
def test_cross_tenant_isolation(tmp_path):
    """同 code 不同租户互不可见。"""
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/rt-cross.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    client = TestClient(create_app(session_factory=sf, engine=engine,
                                   registry=reg))
    from contracts.api import hash_token
    tids = {}
    with sf() as db:
        for code in ("t-a", "t-b"):
            t = Tenant(id=new_uuid(), code=code, name=code)
            db.add(t)
            db.flush()
            db.add(AuthSubject(id=new_uuid(), tenant_id=t.id, kind="user",
                               subject_code=f"user-{code}", roles=["platform_admin"],
                               token_hash=hash_token(f"tok-{code}"),
                               status="ACTIVE", target_scopes=["*"]))
            tids[code] = t.id
        db.commit()
    from plugins.report_targets import seed_default_targets
    with sf() as db:
        seed_default_targets(db, tids["t-a"])
        db.commit()
    r_a = client.get("/api/v1/report-targets",
                     headers={"Authorization": "Bearer tok-t-a"})
    r_b = client.get("/api/v1/report-targets",
                     headers={"Authorization": "Bearer tok-t-b"})
    assert len(r_a.json()["data"]) == 5
    assert len(r_b.json()["data"]) == 0        # B 租户未见 A 的目录
    r404 = client.get("/api/v1/report-targets/drug_trace_alihealth",
                      headers={"Authorization": "Bearer tok-t-b"})
    assert r404.status_code == 404
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


# ---------------------------------------------------------------- overview 钩子化回归
def test_admin_overview_keys_preserved(tmp_path):
    """/admin/overview 键集合与改前一致（病理钩子提供 cases 两键）。"""
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/rt-ov.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["pathology", "drug_trace"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    client = TestClient(create_app(session_factory=sf, engine=engine,
                                   registry=reg))
    with sf() as db:
        t = Tenant(id=new_uuid(), code="ov-tenant", name="OV")
        db.add(t)
        db.flush()
        db.add(AuthSubject(id=new_uuid(), tenant_id=t.id, kind="user",
                           subject_code="user-ov", roles=["auditor"],
                           token_hash=__import__("contracts.api",
                                                 fromlist=["hash_token"]).hash_token("tok-ov"),
                           status="ACTIVE", target_scopes=["*"]))
        db.commit()
    r = client.get("/api/v1/admin/overview",
                   headers={"Authorization": "Bearer tok-ov"})
    assert r.status_code == 200, r.text
    keys = set(r.json()["data"].keys())
    expected = {"acquisitions", "files_available", "bytes_available",
                "cases", "cases_clinically_ready", "quality_open",
                "quality_open_by_rule", "submissions", "submissions_by_state",
                "publications", "agents_total", "agents_online",
                "oldest_pending_job_age_seconds"}
    assert keys == expected, keys ^ expected
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def test_admin_overview_metrics_only_no_error(tmp_path):
    """metrics-only 进程（无病理钩子）调用 overview：不报错、无 cases 键。"""
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/rt-mo.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["metrics"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    client = TestClient(create_app(session_factory=sf, engine=engine,
                                   registry=reg))
    with sf() as db:
        t = Tenant(id=new_uuid(), code="mo-tenant", name="MO")
        db.add(t)
        db.flush()
        db.add(AuthSubject(id=new_uuid(), tenant_id=t.id, kind="user",
                           subject_code="user-a", roles=["platform_admin"],
                           token_hash=__import__("contracts.api",
                                                 fromlist=["hash_token"]).hash_token("tok-a"),
                           status="ACTIVE", target_scopes=["*"]))
        db.commit()
    r = client.get("/api/v1/admin/overview",
                   headers={"Authorization": "Bearer tok-a"})
    assert r.status_code == 200, r.text
    assert "cases" not in r.json()["data"]      # 病理未启用：键缺失是可接受新行为
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)
