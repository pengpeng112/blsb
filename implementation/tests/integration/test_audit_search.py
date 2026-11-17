"""LT-06A 集成：审计检索扩展（旧调用逐字段兼容 + 各过滤参数 + 游标 +
详情 + 导出 CSV/BOM/公式注入防护/超限 400/导出被审计）。"""
from __future__ import annotations

import csv
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import AuditEvent, Tenant  # noqa: E402
from contracts.api import hash_token  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/audit.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry([])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    client = TestClient(create_app(session_factory=sf, engine=engine,
                                   registry=reg))
    with sf() as db:
        t = Tenant(id=new_uuid(), code="aud-h", name="审计")
        db.add(t)
        db.flush()
        tid = t.id
        # 旧版兼容样本：混合 actor/action/时间
        rows = [
            ("alice", "auth.login", "subject:s1", None, None,
             "2026-11-01T09:00:00"),
            ("alice", "auth.login_failed", "subject:s1", None, None,
             "2026-11-02T09:00:00"),
            ("bob", "publication.revoke", "publication:p1",
             {"state": "PUBLISHED"}, {"reason": "r"}, "2026-11-03T10:00:00"),
            ("eve", "=cmd|' /C calc'!A0", "sheet:1", None, None,
             "2026-11-04T11:00:00"),     # 公式注入样本（防护断言用）
        ]
        from datetime import datetime
        for actor, action, target, old_ref, new_ref, at in rows:
            at = datetime.fromisoformat(at)
            db.add(AuditEvent(
                id=new_uuid(), tenant_id=tid, actor=actor, action=action,
                target=target, old_ref=old_ref, new_ref=new_ref,
                at=at))
        from core_db.models import AuthSubject
        db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind="user",
                           subject_code="console-aud", roles=["auditor"],
                           token_hash=hash_token("tk-aud"), status="ACTIVE"))
        db.commit()
    yield {"client": client, "sf": sf, "engine": engine, "tid": tid}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def H(env):
    return {"Authorization": "Bearer tk-aud"}


def test_old_call_shape_identical(env):
    """不带新参数：与旧版逐字段相同（list + id/at/actor/action/target/reason）。"""
    r = env["client"].get("/api/v1/audit", headers=H(env))
    assert r.status_code == 200
    data = r.json()["data"]
    assert isinstance(data, list) and len(data) == 4
    for item in data:
        assert set(item.keys()) == {"id", "actor", "action", "target",
                                    "reason", "at"}


def test_filters_actor_action_prefix_target_time(env):
    c = env["client"]
    Hh = H(env)
    r1 = c.get("/api/v1/audit?actor=alice", headers=Hh)
    assert {x["actor"] for x in r1.json()["data"]} == {"alice"}
    # action（精确，旧语义）vs action_prefix（新，前缀）
    r2 = c.get("/api/v1/audit?action=auth.login", headers=Hh)
    assert [x["action"] for x in r2.json()["data"]] == ["auth.login"]
    r3 = c.get("/api/v1/audit?action_prefix=auth.login_", headers=Hh)
    assert [x["action"] for x in r3.json()["data"]] == ["auth.login_failed"]
    r4 = c.get("/api/v1/audit?target=publication:", headers=Hh)
    assert [x["target"] for x in r4.json()["data"]] == ["publication:p1"]
    r5 = c.get("/api/v1/audit?at_from=2026-11-02T00:00:00&at_to=2026-11-04T00:00:00",
               headers=Hh)
    assert {x["action"] for x in r5.json()["data"]} == {
        "auth.login_failed", "publication.revoke"}
    # 非法时间 → 422
    r6 = c.get("/api/v1/audit?at_from=not-a-date", headers=Hh)
    assert r6.status_code == 422


def test_cursor_pagination_no_dup_no_loss(env):
    c = env["client"]
    Hh = H(env)
    r1 = c.get("/api/v1/audit?limit=2&cursor=2099-01-01T00:00:00|z", headers=Hh)
    assert r1.status_code == 200
    d = r1.json()["data"]
    assert set(d.keys()) == {"items", "next_cursor"}
    seen = []
    seen.extend(x["id"] for x in d["items"])
    cur = d["next_cursor"]
    while cur:
        url = "/api/v1/audit?limit=2" + f"&cursor={cur}"
        rr = c.get(url, headers=Hh)
        assert rr.status_code == 200
        body = rr.json()["data"]
        items = body if isinstance(body, list) else body["items"]
        seen.extend(x["id"] for x in items)
        cur = None if isinstance(body, list) else body.get("next_cursor")
        if not cur:
            break
    assert len(seen) == 4 and len(set(seen)) == 4     # 无重无漏


def test_audit_detail_returns_refs(env):
    c = env["client"]
    Hh = H(env)
    r0 = c.get("/api/v1/audit?target=publication:", headers=Hh)
    eid = r0.json()["data"][0]["id"]
    r = c.get(f"/api/v1/audit/{eid}", headers=Hh)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["old_ref"] == {"state": "PUBLISHED"}
    assert d["new_ref"] == {"reason": "r"}
    # 列表不含 refs
    assert "old_ref" not in r0.json()["data"][0]


def test_export_csv_bom_escaping_formula_guard(env):
    c = env["client"]
    r = c.get("/api/v1/audit/export", headers=H(env))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.content[:3] == b"\xef\xbb\xbf"           # UTF-8 BOM
    text = r.content[3:].decode("utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["id", "at", "actor", "action", "target", "reason"]
    assert len(rows) == 5                             # 表头+4
    # 公式注入防护：以 = 开头的单元格被前置单引号
    eve_row = next(r for r in rows[1:] if r[2] == "eve")
    assert eve_row[3].startswith("'=")
    # 导出动作本身被审计
    r2 = c.get("/api/v1/audit?action=audit.export", headers=H(env))
    assert len(r2.json()["data"]) == 1


def test_export_over_limit_400(env):
    """>10000 行 → 400（用 SQL 注入生成 10001 行验证上限逻辑）。"""
    with env["sf"]() as db:
        from core_db.engine import utcnow
        base = utcnow()
        rows = [AuditEvent(id=new_uuid(), tenant_id=env["tid"],
                           actor=f"bulk{i}", action="bulk.test",
                           target="x", at=base)
                for i in range(10001)]
        db.add_all(rows)
        db.commit()
    r = env["client"].get("/api/v1/audit/export?actor=bulk", headers=H(env))
    assert r.status_code == 422
    assert "10000" in r.json()["error"]["message"]
    # 缩小范围后可导出
    r2 = env["client"].get("/api/v1/audit/export?actor=bulk0",
                           headers=H(env))
    assert r2.status_code == 200
    body = r2.content[3:].decode("utf-8")
    assert len(body.strip().split("\r\n")) == 2       # 表头+1（仅 bulk0）


def test_export_requires_auditor_role(env):
    with env["sf"]() as db:
        from core_db.models import AuthSubject
        db.add(AuthSubject(id=new_uuid(), tenant_id=env["tid"], kind="user",
                           subject_code="console-op", roles=["operator"],
                           token_hash=hash_token("tk-op"), status="ACTIVE"))
        db.commit()
    r = env["client"].get("/api/v1/audit/export",
                          headers={"Authorization": "Bearer tk-op"})
    assert r.status_code == 403


def test_migration_018_legacy_users_get_star(tmp_path):
    """018：含既有用户的升级库——既有 user 行刷 ["*"]，机器主体不赋；
    重复执行幂等。"""
    from core_db import migrate as mig
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/m018.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry([])
    engine = make_engine()
    old = list(mig._MIGRATIONS)
    try:
        mig._MIGRATIONS = [t for t in old if t[0] <= 14]
        run_migrations(engine, extra_migrations=reg.migrations)
    finally:
        mig._MIGRATIONS = old
    with engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text(
            "INSERT INTO tenant (id, code, name, timezone, status, created_at) VALUES "
            "('t1','legacy-h','升级前租户','UTC+8','ACTIVE','2026-01-01 00:00:00')"))
        # 模拟升级前既有账号（无 target_scopes 列的裸 INSERT）
        conn.execute(text(
            "INSERT INTO auth_subject (id, tenant_id, kind, subject_code, "
            "display_name, roles, token_hash, status, created_at) VALUES "
            "('u1','t1','user','console-old','old','[\"operator\"]','h',"
            "'ACTIVE','2026-01-01 00:00:00'),"
            "('g1','t1','agent','agent-old','old2','[]','h2','ACTIVE',"
            "'2026-01-01 00:00:00')"))
    res = run_migrations(engine, extra_migrations=reg.migrations)
    assert 18 in {s for s, _, r in res if r == "APPLIED"}
    with engine.connect() as conn:
        from sqlalchemy import text
        u = conn.execute(text(
            "SELECT target_scopes FROM auth_subject WHERE id='u1'")).scalar()
        g = conn.execute(text(
            "SELECT target_scopes FROM auth_subject WHERE id='g1'")).scalar()
    import json as _json
    assert _json.loads(u) == ["*"]                    # 既有用户兼容赋全量
    assert g is None                                  # 机器主体不赋
    res2 = run_migrations(engine, extra_migrations=reg.migrations)
    assert all(r == "ALREADY_APPLIED" for _, _, r in res2)   # 幂等
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)
