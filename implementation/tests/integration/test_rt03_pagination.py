"""RT-03 S08/S10：四接口真实 keyset 分页集成测试。

覆盖：每接口 15 条 limit=6 翻三页严格无重无漏；同时间戳 id 决胜；非法
游标 400 INVALID_CURSOR（坏 base64/字段数/时间）；换上下文/换租户 400
CURSOR_CONTEXT_MISMATCH；租户隔离；范围收缩（受限账号不见系统级）；旧
warning 与 NULL last_seen_at 归一/有效时间排序；空页与末页 next_cursor=
null；旧 /api/v1/alerts 数组契约保持。
"""
from __future__ import annotations

import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from contracts.api import hash_token, keyset_cursor_encode  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.engine import utcnow  # noqa: E402
from core_db.models import (AlertEvent, AuthSubject, DebugExchange,  # noqa: E402
                            NotificationLog, Schedule, ScheduleRun, Tenant)
from plugins.report_targets import seed_default_targets  # noqa: E402

TARGET = "medins_emr_sd"


@pytest.fixture()
def api(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/rt03pg.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace", "pathology", "medins_emr"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    client = TestClient(create_app(session_factory=sf, engine=engine,
                                   registry=reg))
    with sf() as db:
        t = Tenant(id=new_uuid(), code="rt03-pg", name="分页测试")
        db.add(t)
        db.flush()
        tid = t.id
        seed_default_targets(db, tid)
        subjects = {
            "admin": ("u-admin", ["platform_admin"], ["*"], "user"),
            "viewer": ("u-viewer", ["viewer"], ["*"], "user"),
            "viewer_dt": ("u-vdt", ["viewer"], ["drug_trace_alihealth"], "user"),
            "viewer_medins": ("u-vm", ["viewer"], [TARGET], "user"),
            "op_none": ("u-none", ["viewer"], [], "user"),
            "machine": ("m-bot", ["service"], None, "service"),   # 机器主体
        }
        for tok, (code, roles, scopes, kind) in subjects.items():
            db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind=kind,
                               subject_code=code, roles=roles,
                               token_hash=hash_token(f"tok-{tok}"),
                               status="ACTIVE", target_scopes=scopes))
        db.commit()
    yield {"client": client, "sf": sf, "engine": engine, "tid": tid}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def H(api, tok):
    return {"Authorization": f"Bearer tok-{tok}"}


def _mk_schedule(api, code="pg.sched"):
    with api["sf"]() as db:
        s = Schedule(id=new_uuid(), tenant_id=api["tid"], code=code,
                     name="n", job_type="metrics.run", cron="*/5 * * * *",
                     enabled=True)
        db.add(s)
        db.commit()
        return s.id


def _mk_runs(api, sched_id, n=15, *, same_ts=False):
    """n 条 run：fire_time 递减 1 分钟（same_ts 时同刻不同 id）。"""
    base = utcnow()
    with api["sf"]() as db:
        for i in range(n):
            ft = base if same_ts else base - __import__("datetime") \
                .timedelta(minutes=i)
            db.add(ScheduleRun(id=new_uuid(), tenant_id=api["tid"],
                               schedule_id=sched_id, run_key=f"cron:r{i}",
                               fire_time=ft, state="SUCCEEDED"))
        db.commit()


def _mk_alerts(api, n=15):
    """n 条告警：混合旧 warning/Warning/WARN 大小写与 NULL last_seen_at。"""
    base = utcnow()
    from datetime import timedelta
    with api["sf"]() as db:
        for i in range(n):
            sev = ("warning", "Warning", "WARN", "warn", "critical", "info")[i % 6]
            db.add(AlertEvent(id=new_uuid(), tenant_id=api["tid"],
                              alert_type="X", severity=sev,
                              message=f"alert-{i}", target_ref="-",
                              state="OPEN", created_at=base - timedelta(minutes=i),
                              rule_code=f"r{i}",
                              target_code="drug_trace_alihealth",
                              last_seen_at=None if i % 3 == 0
                              else base - timedelta(minutes=i)))
        db.commit()


def _mk_logs(api, n=15):
    base = utcnow()
    from datetime import timedelta
    with api["sf"]() as db:
        for i in range(n):
            db.add(NotificationLog(id=new_uuid(), tenant_id=api["tid"],
                                   channel_code="file_default", ok=True,
                                   kind="ALERT", sent_at=base - timedelta(minutes=i)))
        db.commit()


def _mk_exchanges(api, n=15):
    base = utcnow()
    from datetime import timedelta
    with api["sf"]() as db:
        for i in range(n):
            db.add(DebugExchange(id=new_uuid(), tenant_id=api["tid"],
                                 target_code=TARGET, transaction_code="1101",
                                 created_by="u", mode="MOCK",
                                 created_at=base - timedelta(minutes=i),
                                 outcome="PASS"))
        db.commit()


def _walk_pages(client, url, headers, *, total, limit=6, key="items"):
    """翻页直到 next_cursor=null；返回 (all_items, pages)。严格无重无漏。"""
    seen, pages, cursor = [], 0, ""
    while True:
        params: dict = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        r = client.get(url, params=params, headers=headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        items = data[key]
        pages += 1
        seen.extend(items)
        cursor = data.get("next_cursor")
        if cursor is None:
            break
        assert len(items) == limit                # 非末页满页
    ids = [x["id"] for x in seen]
    assert len(ids) == len(set(ids)) == total     # 无重无漏
    return seen, pages


# ===================================================== 1) schedule runs
def test_runs_keyset_three_pages_no_overlap(api):
    sid = _mk_schedule(api)
    _mk_runs(api, sid, 15)
    headers = H(api, "viewer")
    seen, pages = _walk_pages(api["client"],
                              f"/api/v1/schedules/{sid}/runs", headers,
                              total=15)
    assert pages == 3                              # 6+6+3
    # 排序：fire_time DESC
    ts = [x["fire_time"] for x in seen]
    assert ts == sorted(ts, reverse=True)
    # shape 保留 schedule/items + next_cursor
    r = api["client"].get(f"/api/v1/schedules/{sid}/runs",
                          params={"limit": 6}, headers=headers)
    body = r.json()["data"]
    assert body["schedule"] == "pg.sched" and "items" in body \
        and "next_cursor" in body


def test_runs_same_timestamp_id_tiebreak_no_skip(api):
    sid = _mk_schedule(api, code="pg.same")
    _mk_runs(api, sid, 15, same_ts=True)           # 同一 fire_time
    seen, _ = _walk_pages(api["client"],
                          f"/api/v1/schedules/{sid}/runs", H(api, "viewer"),
                          total=15)
    ids = [x["id"] for x in seen]
    assert ids == sorted(ids, reverse=True)        # 同刻内 id DESC 决胜


def test_runs_invalid_cursor_400(api):
    sid = _mk_schedule(api, code="pg.bad")
    _mk_runs(api, sid, 3)
    h = H(api, "viewer")
    # 合法形状游标 + 篡改时间段 → 上下文通过但时间非法 → INVALID_CURSOR
    good = keyset_cursor_encode(resource="schedule_runs", tenant_id=api["tid"],
                                context={"schedule_id": sid, "target": ""},
                                ts=utcnow(), row_id="row1")
    raw = base64.urlsafe_b64decode(good.encode()).decode()
    parts = raw.split("|")
    parts[4] = "not-a-time"
    bad_ts = base64.urlsafe_b64encode("|".join(parts).encode()).decode()
    for bad in ("@@not-base64@@",
                base64.urlsafe_b64encode(b"v1|only|three").decode(),
                bad_ts):
        r = api["client"].get(f"/api/v1/schedules/{sid}/runs",
                              params={"cursor": bad}, headers=h)
        assert r.status_code == 400, bad
        assert r.json()["error"]["code"] == "INVALID_CURSOR", bad


def test_runs_cursor_bound_to_schedule_and_tenant(api):
    s1 = _mk_schedule(api, code="pg.a")
    s2 = _mk_schedule(api, code="pg.b")
    _mk_runs(api, s1, 8)
    h = H(api, "viewer")
    r = api["client"].get(f"/api/v1/schedules/{s1}/runs",
                          params={"limit": 3}, headers=h)
    cur = r.json()["data"]["next_cursor"]
    # 换 schedule → 上下文不匹配
    r2 = api["client"].get(f"/api/v1/schedules/{s2}/runs",
                           params={"cursor": cur}, headers=h)
    assert r2.status_code == 400
    assert r2.json()["error"]["code"] == "CURSOR_CONTEXT_MISMATCH"
    # 换租户编码 → 不匹配
    forged = keyset_cursor_encode(resource="schedule_runs", tenant_id="other",
                                  context={"schedule_id": s1,
                                           "target": ""},
                                  ts=utcnow(), row_id="x")
    r3 = api["client"].get(f"/api/v1/schedules/{s1}/runs",
                           params={"cursor": forged}, headers=h)
    assert r3.status_code == 400
    assert r3.json()["error"]["code"] == "CURSOR_CONTEXT_MISMATCH"
    # 原上下文重放仍成功
    r4 = api["client"].get(f"/api/v1/schedules/{s1}/runs",
                           params={"cursor": cur}, headers=h)
    assert r4.status_code == 200


# ===================================================== 2) alerts2
def test_alerts2_keyset_three_pages_and_normalization(api):
    _mk_alerts(api, 15)
    h = H(api, "admin")
    seen, pages = _walk_pages(api["client"], "/api/v1/alerts2", h, total=15)
    assert pages == 3
    sevs = {x["severity"] for x in seen}
    assert sevs <= {"INFO", "WARN", "CRITICAL"}   # 统一三值
    assert "WARN" in sevs                          # warning/Warning/WARN 归一
    # 有效时间 COALESCE(last_seen_at, created_at) DESC
    def eff(x):
        return x["last_seen_at"] or x["created_at"]
    times = [eff(x) for x in seen]
    assert times == sorted(times, reverse=True)
    # NULL last_seen_at 行按 created_at 参与排序（不丢行）
    assert any(x["last_seen_at"] is None for x in seen)


def test_alerts2_severity_filter_covers_warning_variants(api):
    _mk_alerts(api, 15)
    h = H(api, "admin")
    r = api["client"].get("/api/v1/alerts2",
                          params={"severity": "WARN", "limit": 50},
                          headers=h)
    rows = r.json()["data"]["items"]
    assert rows and {x["severity"] for x in rows} == {"WARN"}
    assert len(rows) == 11                         # 15 条中 warning 系占 11
    # 换筛选后旧游标必须失效
    r1 = api["client"].get("/api/v1/alerts2",
                           params={"limit": 3}, headers=h)
    cur = r1.json()["data"]["next_cursor"]
    r2 = api["client"].get("/api/v1/alerts2",
                           params={"severity": "CRITICAL", "cursor": cur},
                           headers=h)
    assert r2.status_code == 400
    assert r2.json()["error"]["code"] == "CURSOR_CONTEXT_MISMATCH"


def test_alerts2_scope_shrinks_and_system_hidden(api):
    """受限账号只见授权业务；系统级（target_code=''）不可见；空范围恒空。"""
    with api["sf"]() as db:
        db.add(AlertEvent(id=new_uuid(), tenant_id=api["tid"],
                          alert_type="SYS", severity="warn", message="sys",
                          target_ref="-", state="OPEN", target_code="",
                          rule_code="sys.x"))
        db.add(AlertEvent(id=new_uuid(), tenant_id=api["tid"],
                          alert_type="X", severity="warn", message="dt",
                          target_ref="-", state="OPEN",
                          target_code="drug_trace_alihealth", rule_code="r"))
        db.commit()
    r = api["client"].get("/api/v1/alerts2", headers=H(api, "viewer_dt"))
    msgs = [x["message"] for x in r.json()["data"]["items"]]
    assert msgs == ["dt"]                          # 系统级不返回
    r2 = api["client"].get("/api/v1/alerts2", headers=H(api, "op_none"))
    assert r2.json()["data"]["items"] == []        # 空范围恒空
    r3 = api["client"].get("/api/v1/alerts2", headers=H(api, "machine"))
    assert len(r3.json()["data"]["items"]) == 2    # 机器 token 不收缩
    r4 = api["client"].get("/api/v1/alerts2", headers=H(api, "viewer"))
    assert len(r4.json()["data"]["items"]) == 2    # ["*"] 全见


def test_old_alerts_array_contract_kept(api):
    """旧 /api/v1/alerts 数组契约不变（含动态告警插首），severity 归一。"""
    _mk_alerts(api, 3)
    r = api["client"].get("/api/v1/alerts", headers=H(api, "machine"))
    assert r.status_code == 200
    assert isinstance(r.json()["data"], list)      # 数组（未迁移）
    for x in r.json()["data"]:
        assert x["severity"] in ("INFO", "WARN", "CRITICAL")


# ===================================================== 3) notification-logs
def test_notify_logs_keyset_three_pages(api):
    _mk_logs(api, 15)
    h = H(api, "admin")
    seen, pages = _walk_pages(api["client"], "/api/v1/notification-logs", h,
                              total=15)
    assert pages == 3
    ts = [x["sent_at"] for x in seen]
    assert ts == sorted(ts, reverse=True)
    # kind 筛选游标绑定：换 kind 后旧游标失效
    r = api["client"].get("/api/v1/notification-logs",
                          params={"kind": "ALERT", "limit": 3}, headers=h)
    cur = r.json()["data"]["next_cursor"]
    r2 = api["client"].get("/api/v1/notification-logs",
                           params={"cursor": cur}, headers=h)
    assert r2.status_code == 400
    assert r2.json()["error"]["code"] == "CURSOR_CONTEXT_MISMATCH"
    # 角色门保持：viewer 403
    r3 = api["client"].get("/api/v1/notification-logs", headers=H(api, "viewer"))
    assert r3.status_code == 403


# ===================================================== 4) medins exchanges
def test_medins_exchanges_keyset_three_pages(api):
    _mk_exchanges(api, 15)
    h = H(api, "viewer_medins")
    seen, pages = _walk_pages(api["client"],
                              "/api/v1/medins-emr/debug-exchanges", h,
                              total=15)
    assert pages == 3
    ts = [x["created_at"] for x in seen]
    assert ts == sorted(ts, reverse=True)
    # code 筛选游标绑定
    r = api["client"].get("/api/v1/medins-emr/debug-exchanges",
                          params={"code": "1101", "limit": 3}, headers=h)
    cur = r.json()["data"]["next_cursor"]
    r2 = api["client"].get("/api/v1/medins-emr/debug-exchanges",
                           params={"code": "1102", "cursor": cur}, headers=h)
    assert r2.status_code == 400
    # 范围：未授权 medins 业务 → 403（RT-02 防线不变）
    r3 = api["client"].get("/api/v1/medins-emr/debug-exchanges",
                           headers=H(api, "viewer_dt"))
    assert r3.status_code == 403


# ===================================================== 空页/末页
def test_empty_filter_page_and_last_page_null_cursor(api):
    sid = _mk_schedule(api, code="pg.empty")
    h = H(api, "viewer")
    r = api["client"].get(f"/api/v1/schedules/{sid}/runs", headers=h)
    body = r.json()["data"]
    assert body["items"] == [] and body["next_cursor"] is None   # 空页
    _mk_runs(api, sid, 4)
    r2 = api["client"].get(f"/api/v1/schedules/{sid}/runs",
                           params={"limit": 10}, headers=h)
    body2 = r2.json()["data"]
    assert len(body2["items"]) == 4 and body2["next_cursor"] is None  # 末页
    # alerts2 空筛选
    r3 = api["client"].get("/api/v1/alerts2",
                           params={"state": "RESOLVED"}, headers=H(api, "admin"))
    assert r3.json()["data"]["items"] == []
    assert r3.json()["data"]["next_cursor"] is None
