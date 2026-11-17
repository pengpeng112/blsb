"""LT-01 report_targets 服务层测试：CRUD/乐观锁/审计/闸门/种子幂等。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import AuditEvent  # noqa: E402
from plugins import report_targets as RT  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/rt.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["metrics"])          # 轻量插件即可（迁移含核心 014）
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        from core_db.models import Tenant
        t = Tenant(id=new_uuid(), code="rt-tenant", name="目录测试租户")
        db.add(t)
        db.commit()
        tid = t.id
    yield {"sf": sf, "engine": engine, "tid": tid}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


BASE = {"code": "t1", "name": "测试业务", "category": "其他",
        "mode": "PUSH", "lifecycle": "PLANNED",
        "runtime_kind": "PLATFORM", "sole_pusher": "UNCONFIRMED"}


# ---------------------------------------------------------------- CRUD/校验
def test_upsert_create_and_get(env):
    with env["sf"]() as db:
        t = RT.upsert_target(db, env["tid"], dict(BASE), actor="admin")
        db.commit()
        got = RT.get_target(db, env["tid"], "t1")
        assert got.id == t.id and got.revision == 1
        assert got.updated_by == "admin"


def test_upsert_create_requires_name(env):
    with env["sf"]() as db:
        with pytest.raises(RT.ApiError) as ei:
            RT.upsert_target(db, env["tid"], {"code": "x"}, actor="a")
        assert ei.value.code == "VALIDATION_FAILED"


def test_upsert_invalid_enums(env):
    with env["sf"]() as db:
        for field, bad in (("lifecycle", "FOO"), ("runtime_kind", "ALIEN"),
                           ("mode", "SIDEWAYS"), ("sole_pusher", "MAYBE")):
            data = dict(BASE)
            data[field] = bad
            with pytest.raises(RT.ApiError) as ei:
                RT.upsert_target(db, env["tid"], data, actor="a")
            assert ei.value.code == "VALIDATION_FAILED", field


def test_upsert_revision_conflict(env):
    with env["sf"]() as db:
        RT.upsert_target(db, env["tid"], dict(BASE), actor="a")
        db.commit()
        with pytest.raises(RT.ApiError) as ei:
            RT.upsert_target(db, env["tid"],
                             {**BASE, "name": "改", "revision": 99},
                             actor="a", expected_revision=99)
        assert ei.value.code == "CONFLICT"
        # 正确 revision → 更新成功且 revision+1
        t = RT.upsert_target(db, env["tid"], {**BASE, "name": "改2"},
                             actor="a", expected_revision=1)
        db.commit()
        assert t.revision == 2 and t.name == "改2"


def test_upsert_writes_audit_diff(env):
    """RT-05 U02：lifecycle 升级必须带理由（写语义修复——无理由拒绝进审计）。"""
    with env["sf"]() as db:
        RT.upsert_target(db, env["tid"], dict(BASE), actor="admin")
        db.commit()
        # 反例（RT-05 新契约）：无理由升级 PILOT → 拒绝
        from contracts import ApiError
        with pytest.raises(ApiError) as ei:
            RT.upsert_target(db, env["tid"],
                             {**BASE, "lifecycle": "PILOT"},
                             actor="editor", expected_revision=1)
        assert ei.value.code == "VALIDATION_FAILED"
        RT.upsert_target(db, env["tid"],
                         {**BASE, "name": "新名", "lifecycle": "PILOT",
                          "reason": "试点上线完成评审，升级生命周期"},
                         actor="editor", expected_revision=1)
        db.commit()
        evs = (db.query(AuditEvent)
               .filter_by(tenant_id=env["tid"],
                          action="report_target.upsert")
               .order_by(AuditEvent.at).all())
        assert len(evs) == 2
        diff = (evs[-1].new_ref or {}).get("diff", {})
        assert diff.get("name", {}).get("to") == "新名"
        # 理由进审计（new_ref.reason）
        assert (evs[-1].new_ref or {}).get("reason") ==             "试点上线完成评审，升级生命周期"
        assert diff.get("lifecycle", {}).get("to") == "PILOT"


def test_get_target_not_found(env):
    with env["sf"]() as db:
        with pytest.raises(RT.ApiError) as ei:
            RT.get_target(db, env["tid"], "nope")
        assert ei.value.code == "NOT_FOUND"


def test_list_targets_sorted(env):
    with env["sf"]() as db:
        for c in ("zz", "aa", "mm"):
            RT.upsert_target(db, env["tid"], {**BASE, "code": c}, actor="a")
        db.commit()
        codes = [t.code for t in RT.list_targets(db, env["tid"])]
        assert codes == sorted(codes)


# ---------------------------------------------------------------- 闸门
def _mk(env, code, lifecycle, runtime, sole):
    with env["sf"]() as db:
        RT.upsert_target(db, env["tid"],
                         {**BASE, "code": code, "lifecycle": lifecycle,
                          "runtime_kind": runtime, "sole_pusher": sole},
                         actor="a")
        db.commit()


def test_gate_pilot_platform_confirmed_ok(env):
    _mk(env, "g1", "PILOT", "PLATFORM", "CONFIRMED")
    with env["sf"]() as db:
        RT.assert_target_can_send_real(db, env["tid"], "g1")   # 不抛即过


def test_gate_production_platform_unconfirmed_blocked(env):
    """LT-03-R1/R02：UNCONFIRMED 不再放行（旧实现只拒 CONFLICT）——唯一
    推送方未确认即真实发送=双上报风险。"""
    _mk(env, "g2", "PRODUCTION", "PLATFORM", "UNCONFIRMED")
    with env["sf"]() as db:
        with pytest.raises(RT.ApiError) as ei:
            RT.assert_target_can_send_real(db, env["tid"], "g2")
        assert ei.value.code == "PROFILE_UNAPPROVED"
        assert "UNCONFIRMED" in ei.value.message


def test_gate_production_platform_confirmed_ok(env):
    _mk(env, "g2c", "PRODUCTION", "PLATFORM", "CONFIRMED")
    with env["sf"]() as db:
        RT.assert_target_can_send_real(db, env["tid"], "g2c")   # 不抛即过


def test_gate_planned_blocked(env):
    _mk(env, "g3", "PLANNED", "PLATFORM", "CONFIRMED")
    with env["sf"]() as db:
        with pytest.raises(RT.ApiError) as ei:
            RT.assert_target_can_send_real(db, env["tid"], "g3")
        assert ei.value.code == "PROFILE_UNAPPROVED"


def test_gate_mock_only_blocked(env):
    _mk(env, "g4", "MOCK_ONLY", "PLATFORM", "CONFIRMED")
    with env["sf"]() as db:
        with pytest.raises(RT.ApiError):
            RT.assert_target_can_send_real(db, env["tid"], "g4")


def test_gate_external_blocked(env):
    _mk(env, "g5", "PRODUCTION", "EXTERNAL", "CONFIRMED")
    with env["sf"]() as db:
        with pytest.raises(RT.ApiError):
            RT.assert_target_can_send_real(db, env["tid"], "g5")


def test_gate_conflict_blocked(env):
    _mk(env, "g6", "PRODUCTION", "PLATFORM", "CONFLICT")
    with env["sf"]() as db:
        with pytest.raises(RT.ApiError) as ei:
            RT.assert_target_can_send_real(db, env["tid"], "g6")
        assert "CONFLICT" in ei.value.message


def test_gate_not_found(env):
    with env["sf"]() as db:
        with pytest.raises(RT.ApiError) as ei:
            RT.assert_target_can_send_real(db, env["tid"], "ghost")
        assert ei.value.code == "NOT_FOUND"


# ---------------------------------------------------------------- 种子
def test_seed_idempotent_five_rows(env):
    with env["sf"]() as db:
        n1 = RT.seed_default_targets(db, env["tid"])
        db.commit()
        n2 = RT.seed_default_targets(db, env["tid"])
        db.commit()
    assert n1 == 5 and n2 == 0


def test_seed_fields_match_spec(env):
    """种子字段照任务书表格逐项断言（含用户指令：影像云 readme 路径）。"""
    with env["sf"]() as db:
        RT.seed_default_targets(db, env["tid"])
        db.commit()
        by = {t.code: t for t in RT.list_targets(db, env["tid"])}
    d = by["drug_trace_alihealth"]
    assert d.name == "码上放心药品追溯" and d.category == "药品追溯"
    assert d.authority == "阿里健康码上放心平台" and d.plugin_key == "drug_trace"
    assert d.mode == "PUSH" and d.lifecycle == "PRODUCTION"
    assert d.runtime_kind == "PLATFORM" and d.sole_pusher == "CONFLICT"
    assert d.readme_path == "数据上报/1、码上放心平台/README.md"
    assert "原厂家程序" in d.sole_pusher_note
    i = by["inf_cdc_frontend"]
    assert i.runtime_kind == "EXTERNAL" and i.lifecycle == "PRODUCTION"
    assert i.sole_pusher == "UNCONFIRMED"
    assert "crbsb" in i.sole_pusher_note
    # LT-07：联调台交付 → DEVELOPING（真实联调仍外部阻断）
    assert by["medins_emr_sd"].lifecycle == "DEVELOPING"
    assert by["pathology_cloud"].lifecycle == "MOCK_ONLY"
    assert by["pathology_cloud"].mode == "HYBRID"
    img = by["medins_imaging_jn"]
    assert img.readme_path == "数据上报/5、医保影像云上报/README.md"
    # LT-09：脚手架补填待定 plugin_key（其余字段不动）
    assert img.plugin_key == "medins_imaging"


def test_seed_does_not_override_existing(env):
    with env["sf"]() as db:
        RT.upsert_target(db, env["tid"],
                         {"code": "drug_trace_alihealth", "name": "已登记",
                          "lifecycle": "PRODUCTION"}, actor="seed-user")
        db.commit()
        n = RT.seed_default_targets(db, env["tid"])
        db.commit()
        t = RT.get_target(db, env["tid"], "drug_trace_alihealth")
    assert t.name == "已登记"          # 已存在的不覆盖
    assert n == 4                      # 只补缺
