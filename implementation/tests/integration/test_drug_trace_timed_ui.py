"""定时上报可视化测试（DTR-TIMED-UI-20261024；TUI-01—04）。

- T-A01：迁移 012 SQLite 与隔离 PG（Docker 可用时）up→幂等→down→up 全程；
  009—011 表不受 down 影响。
- T-A02：daily-reports upsert 幂等/角色/负载校验（深度、64KB）；upload-stats
  与手写 SQL 对照（含非药品域排除、最近尝试分桶）；send-scope 反映配额递增、
  多目的地显式空；submission-outcomes only=problem 过滤。
- T-A03：驱动 v2 —— push_daily_report 契约/失败安全；隔离 PG 上端到端跑
  main()：成功推送、推送失败不改退出码、fatal 路径推 FAILED 日报且退出码不变。

全部使用隔离 SQLite/PG 与假 HTTP 层，不连生产、不发真实平台请求。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import time
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import (AuthSubject, ConnectorInstance, DataPackage,
                            DeliveryAttempt, Destination, PackageRevision,
                            Submission, Tenant)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DRIVER_PATH = os.path.join(_REPO_ROOT, "docs", "development",
                           "drug-trace-timed-report-20261024",
                           "daily_upload_driver.py")
DRIVER_CONNECTOR_ID = "7d51abd7-3f32-4c8e-b2fa-9b652a511808"
DRIVER_DEST_CODE = "alihealth-drugtrace-retail"


# ================================================================ API fixture
def _build_api(db_url: str):
    """构造隔离 API 栈（SQLite 或 PG URL；FIX-07：api/api_pg 共用）。"""
    os.environ["BLYB_DATABASE_URL"] = db_url
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
        # 共享 PG 容器上 tenant.code 有唯一约束：每次构造用唯一 code
        t = Tenant(id=new_uuid(),
                   code=f"first-hospital-{uuid.uuid4().hex[:8]}", name="首院")
        db.add(t)
        db.flush()
        tid = t.id
        subjects = {}
        for role in ("operator", "reviewer", "admin", "auditor"):
            token = f"tok-{role}"
            db.add(AuthSubject(
                id=new_uuid(), tenant_id=tid, kind="user",
                subject_code=f"user-{role}",
                roles={"operator": ["operator"],
                       "reviewer": ["business_reviewer"],
                       "admin": ["platform_admin"],
                       "auditor": ["auditor"]}[role],
                token_hash=__import__("contracts.api",
                                      fromlist=["hash_token"]).hash_token(token),
                status="ACTIVE"))
            subjects[role] = token
        db.add(ConnectorInstance(id=new_uuid(), tenant_id=tid,
                                 direction="source",
                                 plugin_id="sources.db_view.drug_trace",
                                 name="his", status="ENABLED"))
        db.add(Destination(id=new_uuid(), tenant_id=tid, code="ali-mock",
                           profile_revision="alihealth_yljg_retail_v1",
                           mode="PUSH", capabilities=["upload"],
                           is_mock=True, status="ENABLED"))
        db.commit()
        dest_id = db.query(Destination).first().id
        conn_id = db.query(ConnectorInstance).first().id
    return {"client": client, "sf": sf, "tenant_id": tid,
            "subjects": subjects, "connector_id": conn_id, "dest_id": dest_id,
            "engine": engine}


@pytest.fixture()
def api(tmp_path):
    out = _build_api(f"sqlite:///{tmp_path}/api.sqlite3")
    try:
        yield out
    finally:
        out["engine"].dispose()
        os.environ.pop("BLYB_DATABASE_URL", None)


def H(api, role):
    return {"Authorization": f"Bearer {api['subjects'][role]}"}


_REPORT = {
    "report_date": "2026-10-25", "source": "timer",
    "window_begin": "2026-10-24T00:00:00", "window_end": "2026-10-25T00:00:00",
    "candidate_count": 12,
    "collection": {"id": "run-1", "state": "DONE",
                   "summary": {"bills_seen": 30, "revisions_created": 28,
                               "valid": 12, "waiting": 16}},
    "send": {"sent": 11, "prepared_new": 3, "resumed": 0,
             "skipped_terminal": 1,
             "errors": [{"submission": "s1", "step": "send",
                         "res": {"error_code": "PROFILE_UNAPPROVED"}}],
             "aborted_reason": ""},
    "query_stats": {"queried": 4, "errors": []},
    "outcome": "PARTIAL_FAILURE", "elapsed_seconds": 4480.2,
}


# ================================================================ T-A02 日报 API
def test_compact_error_sample_keeps_total_in_dashboard(api):
    body={**_REPORT,'send':{**_REPORT['send'],'errors_total':815,'errors_truncated':True},
          'query_stats':{'queried':1,'errors_total':247,'errors_truncated':True,
                         'errors':[{'res':{'error_code':'VERSION_CONFLICT'}}],'skipped_exhausted':206}}
    c=api['client']
    assert c.post('/api/v1/drug-trace/daily-reports',headers=H(api,'operator'),json=body).status_code==200
    rows=c.get('/api/v1/drug-trace/daily-reports',headers=H(api,'auditor')).json()['data']
    assert rows[0]['errors_count']==815
    assert rows[0]['query_errors_count']==247
    assert rows[0]['query_skipped_exhausted']==206


def _queue_submission(api,db,key,state='PREPARED'):
    package=DataPackage(id=new_uuid(),tenant_id=api['tenant_id'],domain='drug_trace',business_key=key)
    db.add(package);db.flush()
    rev=PackageRevision(id=new_uuid(),tenant_id=api['tenant_id'],package_id=package.id,revision=1,
        schema_ref={'profile':'alihealth_yljg_retail_v1'},state='FROZEN',payload={},provenance={})
    db.add(rev);db.flush()
    sub=Submission(id=new_uuid(),tenant_id=api['tenant_id'],destination_id=api['dest_id'],package_revision_id=rev.id,state=state,idempotency_key=key)
    db.add(sub);db.flush()
    return sub


def test_approved_send_and_query_precede_1352_prepare_jobs_without_resetting_budget(api):
    from core_db.models import Job
    from workflow_core.jobs import claim_job,finish_job,enqueue_job
    from domains.drug_trace import service as S
    c=api['client']
    keys=['EXT-03','EXT-04','EXT-05','EXT-06','EXT-07','EXT-08','EXT-10','EXT-12']
    result=c.post('/api/v1/drug-trace/evidence',headers=H(api,'admin'),json={
        'profile':'alihealth_yljg_retail_v1','capability':'send','destination_id':api['dest_id'],
        'items':[{'key':k,'evidence_ref':'synthetic:'+k} for k in keys],
        'valid_until':'2026-11-23T00:00:00',
        'scope':{'window_begin':'2026-10-24T00:00:00','window_end':'2026-11-23T00:00:00','pharmacies':['050101'],'max_bills':100}})
    assert result.status_code==200,result.text
    with api['sf']() as db:
        sub=_queue_submission(api,db,'queue-synthetic');sid=sub.id
        for i in range(1352):
            db.add(Job(id=new_uuid(),tenant_id=api['tenant_id'],job_type='drug_trace.prepare',dedup_key='prepare-'+str(i),payload={}))
        db.commit()
    response=c.post('/api/v1/drug-trace/submissions/'+sid+'/send',headers=H(api,'operator'),json={'confirm':True})
    assert response.status_code==200,response.text
    with api['sf']() as db:
        query=S._enqueue_query_job(db,api['tenant_id'],sid)
        assert query.priority==60
        lease=claim_job(db,'test-worker',['drug_trace.prepare','drug_trace.send','drug_trace.query_status'])
        assert lease.job.id==response.json()['data']['job_id']
        assert lease.job.priority==50 and lease.epoch==1
        assert finish_job(db,lease,success=True)
        next_lease=claim_job(db,'test-worker',['drug_trace.prepare','drug_trace.send','drug_trace.query_status'])
        assert next_lease.job.id==query.id
        assert db.query(Job).filter_by(job_type='drug_trace.prepare',state='PENDING').count()==1352


def test_driver_query_filter_sql_on_real_pg(pg):
    from core_db.models import Job
    import psycopg
    from psycopg import sql
    url=pg[0]
    database='queue_filter_'+uuid.uuid4().hex[:10]
    admin=psycopg.connect(url.replace('+psycopg',''),autocommit=True)
    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    api=None
    try:
        api=_build_api(url.rsplit('/',1)[0]+'/'+database)
        mod=_load_driver()
        with api['sf']() as db:
            exhausted=_queue_submission(api,db,'budget-old','UNKNOWN')
            active=_queue_submission(api,db,'budget-active','ACCEPTED')
            fresh=_queue_submission(api,db,'budget-fresh','ACCEPTED')
            for i in range(12):db.add(Job(id=new_uuid(),tenant_id=api['tenant_id'],job_type='drug_trace.query_status',
                dedup_key=f'q:{exhausted.id}:{i}#test',state='SUCCEEDED',payload={'submission_id':exhausted.id}))
            db.add(Job(id=new_uuid(),tenant_id=api['tenant_id'],job_type='drug_trace.query_status',dedup_key=f'q:{active.id}:1#test',
                state='PENDING',payload={'submission_id':active.id}))
            db.commit()
            assert mod.eligible_queries(db,api['tenant_id'],api['dest_id'])==[fresh.id]
            assert mod.receipt['query']['skipped_exhausted']==1
            assert mod.receipt['query']['skipped_inflight']==1
    finally:
        if api is not None:api['engine'].dispose()
        os.environ.pop('BLYB_DATABASE_URL',None)
        admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))
        admin.close()


def test_priority_burst_reserves_background_slots(api):
    from types import SimpleNamespace
    from core_db.models import Job
    from apps.worker.engine import WorkerEngine
    observed=[]
    def handle(db,job):observed.append(job.job_type)
    registry=SimpleNamespace(domain_job_handlers=lambda:{
        'drug_trace.send':handle,'drug_trace.query_status':handle,'drug_trace.prepare':handle})
    with api['sf']() as db:
        for i in range(12):db.add(Job(id=new_uuid(),tenant_id=api['tenant_id'],job_type='drug_trace.query_status',
            dedup_key='urgent-'+str(i),payload={},priority=60))
        for i in range(20):db.add(Job(id=new_uuid(),tenant_id=api['tenant_id'],job_type='drug_trace.prepare',
            dedup_key='background-'+str(i),payload={},priority=100))
        db.commit()
        worker=WorkerEngine(api['sf'],registry=registry)
        assert worker._run_due_jobs(db,10)==10
    assert observed[4]==observed[9]=='drug_trace.prepare'
    assert observed[:4]==['drug_trace.query_status']*4


def test_daily_report_upsert_idempotent_and_fetch(api):
    c = api["client"]
    r = c.post("/api/v1/drug-trace/daily-reports", headers=H(api, "operator"),
               json=_REPORT)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["created"] is True
    # 二次 POST：整体更新不重复（幂等 upsert）
    again = {**_REPORT, "outcome": "OK", "send": {**_REPORT["send"], "sent": 12,
                                                  "errors": []}}
    r = c.post("/api/v1/drug-trace/daily-reports", headers=H(api, "admin"),
               json=again)
    assert r.status_code == 200 and r.json()["data"]["created"] is False
    rows = c.get("/api/v1/drug-trace/daily-reports", headers=H(api, "auditor")
                 ).json()["data"]
    assert len(rows) == 1
    row = rows[0]
    assert row["report_date"] == "2026-10-25" and row["source"] == "timer"
    assert row["outcome"] == "OK" and row["sent"] == 12
    assert row["errors_count"] == 0 and row["candidate_count"] == 12
    assert row["elapsed_seconds"] == 4480.2
    # 单日详情：含 errors 明细与窗口
    detail = c.get("/api/v1/drug-trace/daily-reports/2026-10-25",
                   headers=H(api, "auditor")).json()["data"]
    assert len(detail) == 1
    assert detail[0]["send"]["sent"] == 12
    assert detail[0]["window"] == ["2026-10-24T00:00:00",
                                   "2026-10-25T00:00:00"]
    # 同日不同 source 各一行（manual 补跑与 timer 并存）
    r = c.post("/api/v1/drug-trace/daily-reports", headers=H(api, "operator"),
               json={**_REPORT, "source": "manual", "outcome": "OK",
                     "send": {"sent": 1, "prepared_new": 0, "resumed": 0,
                              "skipped_terminal": 0, "errors": [],
                              "aborted_reason": ""}})
    assert r.status_code == 200
    assert len(c.get("/api/v1/drug-trace/daily-reports",
                     headers=H(api, "auditor")).json()["data"]) == 2
    # 审计留痕
    with api["sf"]() as db:
        from core_db.models import AuditEvent
        assert db.query(AuditEvent).filter(
            AuditEvent.action == "drug_trace.daily_report.ingest").count() >= 3


def test_daily_report_roles_and_validation(api):
    c = api["client"]
    # reviewer/auditor 无写权 → 403；未登录 → 401
    assert c.post("/api/v1/drug-trace/daily-reports",
                  headers=H(api, "reviewer"), json=_REPORT).status_code == 403
    assert c.post("/api/v1/drug-trace/daily-reports",
                  headers=H(api, "auditor"), json=_REPORT).status_code == 403
    assert c.post("/api/v1/drug-trace/daily-reports",
                  json=_REPORT).status_code == 401
    # GET 任何已登录主体可看；空列表 200
    r = c.get("/api/v1/drug-trace/daily-reports", headers=H(api, "auditor"))
    assert r.status_code == 200 and r.json()["data"] == []
    # 校验：非 ISO 日期 / 非法 outcome / 非法 source / 深度>4
    bad = c.post("/api/v1/drug-trace/daily-reports", headers=H(api, "operator"),
                 json={**_REPORT, "report_date": "2026/10/25"})
    assert bad.status_code == 422
    bad = c.post("/api/v1/drug-trace/daily-reports", headers=H(api, "operator"),
                 json={**_REPORT, "outcome": "FINE"})
    assert bad.status_code == 422
    bad = c.post("/api/v1/drug-trace/daily-reports", headers=H(api, "operator"),
                 json={**_REPORT, "source": "cron"})
    assert bad.status_code == 422
    bad = c.post("/api/v1/drug-trace/daily-reports", headers=H(api, "operator"),
                 json={**_REPORT, "send": {"errors": [{"res": {
                     "deep": {"deeper": {"deepest": {"x": 1}}}}}]}})
    assert bad.status_code == 422
    # 64KB 上限：三个 JSON 字段合计超限 → 413
    big = "x" * 70000
    bad = c.post("/api/v1/drug-trace/daily-reports", headers=H(api, "operator"),
                 json={**_REPORT, "send": {"note": big}})
    assert bad.status_code == 413
    # 单日详情：不存在的日期 → 404；坏日期 → 422
    assert c.get("/api/v1/drug-trace/daily-reports/2030-01-01",
                 headers=H(api, "auditor")).status_code == 404
    assert c.get("/api/v1/drug-trace/daily-reports/not-a-date",
                 headers=H(api, "auditor")).status_code == 422


def _seed_submission(api, *, state, attempts=(), domain="drug_trace",
                     bill=None, created_at=None):
    """直写 drug_trace（或对照域）提交 + 尝试记录（绕过采集/发送）。"""
    pkg = DataPackage(id=new_uuid(), tenant_id=api["tenant_id"], domain=domain,
                      business_key=f"{domain}|{uuid.uuid4().hex[:8]}")
    pr = PackageRevision(id=new_uuid(), tenant_id=api["tenant_id"],
                         package_id=pkg.id, revision=1,
                         schema_ref={"profile": "alihealth_yljg_retail_v1"},
                         payload={"bill_code": "DTRT-TEST"} if bill is None
                         else {"bill_code": bill["bill_code"]},
                         provenance={"bill_id": bill["id"]} if bill else {},
                         digest="d" * 8, state="FROZEN")
    sub = Submission(id=new_uuid(), tenant_id=api["tenant_id"],
                     package_revision_id=pr.id,
                     destination_id=api["dest_id"], state=state,
                     idempotency_key=f"k-{uuid.uuid4().hex[:10]}",
                     created_at=created_at)
    with api["sf"]() as db:
        db.add_all([pkg, pr, sub])
        db.flush()                      # 先落父行，再加尝试（FK 顺序）
        for i, (outcome, http_status) in enumerate(attempts, start=1):
            db.add(DeliveryAttempt(
                id=new_uuid(), tenant_id=api["tenant_id"],
                submission_id=sub.id, attempt_no=i, outcome=outcome,
                http_status=http_status, started_at=created_at,
                ended_at=created_at))
        db.commit()
    return sub.id


def test_upload_stats_matches_handwritten_sql(api):
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    s1 = _seed_submission(api, state="CONFIRMED",
                          attempts=[("ACCEPTED", 200)], created_at=now)
    _seed_submission(api, state="UNKNOWN",
                     attempts=[("UNKNOWN", 500)], created_at=now)
    _seed_submission(api, state="PREPARED", attempts=[],
                     created_at=now - timedelta(days=1))
    _seed_submission(api, state="REJECTED",
                     attempts=[("UNKNOWN", 500), ("REJECTED", 200)],
                     created_at=now - timedelta(days=1))
    # 非药品域不计入
    _seed_submission(api, state="CONFIRMED", attempts=[("ACCEPTED", 200)],
                     domain="pathology", created_at=now)
    r = api["client"].get("/api/v1/drug-trace/upload-stats?days=14",
                          headers=H(api, "auditor"))
    assert r.status_code == 200
    days = {d["date"]: d for d in r.json()["data"]["days"]}
    # 手写 SQL 对照（by_state / total）
    with api["engine"].connect() as conn:
        from sqlalchemy import text
        raw = conn.execute(text(
            "SELECT date(s.created_at) d, s.state, count(*) FROM submission s "
            "JOIN package_revision pr ON pr.id=s.package_revision_id "
            "JOIN data_package p ON p.id=pr.package_id "
            "WHERE p.domain='drug_trace' AND s.created_at >= :since "
            "GROUP BY 1,2"),
            {"since": (now - timedelta(days=14)).strftime(
                "%Y-%m-%d %H:%M:%S.%f")}).fetchall()
    sql_by_day = {}
    for d, state, n in raw:
        sql_by_day.setdefault(str(d), {}).__setitem__(state, n)
    api_by_day = {k: v["by_state"] for k, v in days.items()}
    assert api_by_day == sql_by_day
    today = days[str((now).date())]
    assert today["total"] == 2          # pathology 排除
    assert today["attempts"] == {"accepted": 1, "http_error": 1, "other": 0}
    yday = days[str((now - timedelta(days=1)).date())]
    # REJECTED 提交两次尝试取最近一次（REJECTED/200 → other）
    assert yday["attempts"] == {"accepted": 0, "http_error": 0, "other": 1}
    assert s1  # noqa: F841 —— 保持引用清晰
    # days 上限 90
    r = api["client"].get("/api/v1/drug-trace/upload-stats?days=500",
                          headers=H(api, "auditor"))
    assert r.status_code == 200


def test_send_scope_reflects_quota_and_ambiguity(api):
    c = api["client"]
    # 未登记：MISSING、字段显式空
    r = c.get("/api/v1/drug-trace/send-scope", headers=H(api, "auditor"))
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["evidence"]["status"] == "MISSING"
    assert data["scope"] is None and data["quota"] is None
    assert data["destination"]["id"] == api["dest_id"]
    # admin 批准 send 证据（完整 EXT-03..12 + scope）
    keys = ["EXT-03", "EXT-04", "EXT-05", "EXT-06", "EXT-07", "EXT-08",
            "EXT-10", "EXT-12"]
    r = c.post("/api/v1/drug-trace/evidence", headers=H(api, "admin"),
               json={"profile": "alihealth_yljg_retail_v1",
                     "capability": "send", "destination_id": api["dest_id"],
                     "items": [{"key": k, "evidence_ref": f"doc:{k}"}
                               for k in keys],
                     "valid_until": "2026-11-23T00:00:00",
                     "scope": {"window_begin": "2026-10-24T00:00:00",
                               "window_end": "2026-11-23T00:00:00",
                               "pharmacies": ["050121", "050102"],
                               "max_bills": 80000}})
    assert r.status_code == 200, r.text
    r = c.get("/api/v1/drug-trace/send-scope", headers=H(api, "auditor"))
    data = r.json()["data"]
    assert data["evidence"]["status"] == "APPROVED"
    assert data["scope"]["pharmacies"] == ["050121", "050102"]
    assert data["quota"] == {"used": 0, "max_bills": 80000}
    # 配额 used 递增后实时反映
    with api["sf"]() as db:
        from domains.drug_trace.models import DrugTraceSendQuota
        quota = db.query(DrugTraceSendQuota).first()
        quota.used = 5
        db.commit()
    data = c.get("/api/v1/drug-trace/send-scope",
                 headers=H(api, "auditor")).json()["data"]
    assert data["quota"] == {"used": 5, "max_bills": 80000}
    # 多目的地：显式空（不猜）
    with api["sf"]() as db:
        db.add(Destination(id=new_uuid(), tenant_id=api["tenant_id"],
                           code="ali-second", profile_revision=
                           "alihealth_yljg_retail_v1", mode="PUSH",
                           capabilities=["upload"], is_mock=True,
                           status="DRAFT"))
        db.commit()
    data = c.get("/api/v1/drug-trace/send-scope",
                 headers=H(api, "auditor")).json()["data"]
    assert data["ambiguous"] is True
    assert data["destination"] is None and data["evidence"]["evidence_id"] is None


def test_submission_outcomes_problem_filter(api):
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    # 异常：UNKNOWN（尝试 UNKNOWN/500）
    _seed_submission(api, state="UNKNOWN", attempts=[("UNKNOWN", 500)],
                     created_at=now)
    # 正常：CONFIRMED（最近尝试 ACCEPTED）
    _seed_submission(api, state="CONFIRMED", attempts=[("ACCEPTED", 200)],
                     created_at=now)
    # 从未发送（PREPARED 无尝试）：不算异常
    _seed_submission(api, state="PREPARED", attempts=[], created_at=now)
    # 状态异常：REJECTED
    _seed_submission(api, state="REJECTED", attempts=[("REJECTED", 200)],
                     created_at=now)
    c = api["client"]
    all_rows = c.get("/api/v1/drug-trace/submission-outcomes?days=7",
                     headers=H(api, "auditor")).json()["data"]
    assert len(all_rows) == 4
    assert all(r["source_bill_no"] == "" for r in all_rows)  # 无 bill 关联时为空
    problems = c.get("/api/v1/drug-trace/submission-outcomes"
                     "?days=7&only=problem", headers=H(api, "auditor")
                     ).json()["data"]
    assert {r["state"] for r in problems} == {"UNKNOWN", "REJECTED"}
    unk = next(r for r in problems if r["state"] == "UNKNOWN")
    assert unk["attempt_outcome"] == "UNKNOWN"
    assert unk["attempt_http_status"] == 500
    # 带 bill 关联的行回带 source_bill_no
    with api["sf"]() as db:
        from domains.drug_trace.models import DrugTraceBill
        bill = DrugTraceBill(
            id=new_uuid(), tenant_id=api["tenant_id"],
            connector_id=api["connector_id"], source_system="HIS",
            pharmacy_code="050121", business_kind="PATIENT_DISPENSE",
            source_bill_id="RX-9", source_bill_no="RX-9")
        db.add(bill)
        db.commit()
        bill_id, api2 = bill.id, api
    _seed_submission(api2, state="UNKNOWN", attempts=[("UNKNOWN", None)],
                     bill={"id": bill_id, "bill_code": "DTRT-X1"},
                     created_at=now)
    rows = c.get("/api/v1/drug-trace/submission-outcomes?days=7&only=problem",
                 headers=H(api, "auditor")).json()["data"]
    assert any(r["source_bill_no"] == "RX-9" and r["bill_code"] == "DTRT-X1"
               for r in rows)


# ================================================================ FIX-07 新增测试
def test_upload_stats_scale_35k(api):
    """G1 回归锁：≥35,000 提交（旧实现 IN 列表超 SQLite 32766 变量上限必 500）。

    Core executemany 批量插入（勿逐 ORM add）；分布 88 天确保全在 days=90 窗口。
    """
    import time as _time
    from datetime import datetime, timedelta
    from sqlalchemy import insert

    tid, dest_id = api["tenant_id"], api["dest_id"]
    total = 35_000
    states = ["CONFIRMED", "ACCEPTED", "PARTIAL", "REJECTED", "UNKNOWN",
              "PREPARED"]
    now = datetime.utcnow()
    base = now - timedelta(days=88)
    span_min = 88 * 24 * 60
    expected: dict = {}
    exp_state: dict = {}
    n_accepted = n_http_error = 0
    t0 = _time.time()
    with api["engine"].begin() as conn:
        pkg_rows: list = []
        pr_rows: list = []
        sub_rows: list = []
        att_rows: list = []

        def _flush():
            if pkg_rows:
                conn.execute(insert(DataPackage), pkg_rows)
                conn.execute(insert(PackageRevision), pr_rows)
                conn.execute(insert(Submission), sub_rows)
                if att_rows:
                    conn.execute(insert(DeliveryAttempt), att_rows)
            pkg_rows.clear(); pr_rows.clear(); sub_rows.clear()
            att_rows.clear()

        for i in range(total):
            dt = base + timedelta(minutes=(i * span_min) // total)
            sid, pid, rid = new_uuid(), new_uuid(), new_uuid()
            pkg_rows.append({"id": pid, "tenant_id": tid,
                             "domain": "drug_trace",
                             "business_key": f"drug_trace|scale|{i}"})
            pr_rows.append({"id": rid, "tenant_id": tid, "package_id": pid,
                            "revision": 1})
            state = states[i % len(states)]
            sub_rows.append({"id": sid, "tenant_id": tid,
                             "package_revision_id": rid,
                             "destination_id": dest_id, "state": state,
                             "idempotency_key": f"scale-{i}",
                             "created_at": dt})
            dstr = str(dt.date())
            expected[dstr] = expected.get(dstr, 0) + 1
            exp_state.setdefault(dstr, {})
            exp_state[dstr][state] = exp_state[dstr].get(state, 0) + 1
            if i % 3 == 0:                       # ~1/3 带尝试
                att_rows.append({"id": new_uuid(), "tenant_id": tid,
                                 "submission_id": sid, "attempt_no": 1,
                                 "outcome": "UNKNOWN", "http_status": 500,
                                 "started_at": dt, "ended_at": dt})
                n_http_error += 1
                if i % 15 == 0:                  # 少量多次尝试：最新为 ACCEPTED
                    att_rows.append({"id": new_uuid(), "tenant_id": tid,
                                     "submission_id": sid, "attempt_no": 2,
                                     "outcome": "ACCEPTED",
                                     "http_status": 200,
                                     "started_at": dt, "ended_at": dt})
                    n_accepted += 1
                    n_http_error -= 1
            if len(sub_rows) >= 1000:
                _flush()
        _flush()
    elapsed = _time.time() - t0
    assert elapsed < 60, f"seeding too slow: {elapsed:.1f}s"

    r = api["client"].get("/api/v1/drug-trace/upload-stats?days=90",
                          headers=H(api, "auditor"))
    assert r.status_code == 200, r.text
    days = r.json()["data"]["days"]
    assert sum(d["total"] for d in days) == total
    got = {d["date"]: d for d in days}
    assert got.keys() == expected.keys()
    for dstr, n in expected.items():
        assert got[dstr]["total"] == n
        assert got[dstr]["by_state"] == exp_state[dstr]
    # 最近尝试分桶：attempt_no=2（ACCEPTED/200）覆盖 1 号 UNKNOWN/500
    assert sum(d["attempts"]["accepted"] for d in days) == n_accepted
    assert sum(d["attempts"]["http_error"] for d in days) == n_http_error
    assert sum(d["attempts"]["other"] for d in days) == 0


def test_upload_stats_days_clamped_to_90(api):
    """days 参数收紧锁：days=500 实际按 90 生效，100 天前的提交不计入。"""
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    _seed_submission(api, state="CONFIRMED", attempts=[("ACCEPTED", 200)],
                     created_at=now - timedelta(days=5))
    _seed_submission(api, state="CONFIRMED", attempts=[("ACCEPTED", 200)],
                     created_at=now - timedelta(days=100))
    r = api["client"].get("/api/v1/drug-trace/upload-stats?days=500",
                          headers=H(api, "auditor"))
    assert r.status_code == 200
    days = r.json()["data"]["days"]
    assert sum(d["total"] for d in days) == 1
    assert days[0]["date"] == str((now - timedelta(days=5)).date())


def test_daily_report_window_order_rejected(api):
    """FIX-03：window_begin >= window_end → 422；合法序不受影响。"""
    c = api["client"]
    bad = c.post("/api/v1/drug-trace/daily-reports",
                 headers=H(api, "operator"),
                 json={**_REPORT, "window_begin": "2026-10-25T00:00:00",
                       "window_end": "2026-10-24T00:00:00"})
    assert bad.status_code == 422
    bad = c.post("/api/v1/drug-trace/daily-reports",
                 headers=H(api, "operator"),
                 json={**_REPORT, "window_begin": "2026-10-24T00:00:00",
                       "window_end": "2026-10-24T00:00:00"})
    assert bad.status_code == 422
    # 不加 report_date 新近度限制：历史日补录（manual、远早于今日）合法
    good = c.post("/api/v1/drug-trace/daily-reports",
                  headers=H(api, "operator"),
                  json={**_REPORT, "report_date": "2026-07-01",
                        "source": "manual", "outcome": "OK"})
    assert good.status_code == 200


# ================================================================ T-A01 迁移 012
def _migration_cycle(engine):
    """012 up→幂等→down→up 全程；返回每阶段结果。"""
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    from sqlalchemy import inspect as sa_inspect, text
    r1 = run_migrations(engine, extra_migrations=reg.migrations)
    applied = {n: res for _, n, res in r1}
    assert applied["012_drug_trace_daily_report"] == "APPLIED"
    r2 = run_migrations(engine, extra_migrations=reg.migrations)
    assert {n: res for _, n, res in r2}["012_drug_trace_daily_report"] \
        == "ALREADY_APPLIED"
    insp = sa_inspect(engine)
    assert insp.has_table("drug_trace_daily_report")
    assert insp.has_table("drug_trace_bill")           # 009 不受影响
    assert insp.has_table("drug_trace_retry_bill")     # 010 不受影响
    assert insp.has_table("drug_trace_send_quota")     # 011 不受影响
    from domains.drug_trace.models import migration_fix4_downgrade
    migration_fix4_downgrade(engine)
    assert not sa_inspect(engine).has_table("drug_trace_daily_report")
    # 009—011 表在 down 后仍存在
    insp = sa_inspect(engine)
    for tbl in ("drug_trace_bill", "drug_trace_retry_bill",
                "drug_trace_send_quota", "drug_trace_production_evidence"):
        assert insp.has_table(tbl), tbl
    # down 后重跑（清除迁移登记行）→ 012 重新应用
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM schema_migrations WHERE step=12"))
    r3 = run_migrations(engine, extra_migrations=reg.migrations)
    assert {n: res for _, n, res in r3}["012_drug_trace_daily_report"] \
        == "APPLIED"
    assert sa_inspect(engine).has_table("drug_trace_daily_report")


def test_migration_012_sqlite_cycle(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/m012.sqlite3"
    engine = make_engine()
    _migration_cycle(engine)
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


# ================================================================ 隔离 PG（Docker）
DOCKER = shutil.which("docker")
pg_mark = pytest.mark.skipif(DOCKER is None, reason="docker 不可用")
_PG_PORT = 55445
_PG_PASSWORD = "dtr-tui-pg"


def _docker_available() -> bool:
    try:
        subprocess.run([DOCKER, "version", "--format", "{{.Server.Version}}"],
                       capture_output=True, timeout=30, check=True)
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def pg():
    if not _docker_available():
        pytest.skip("docker daemon 不可用")
    name = f"blyb-dtr-tui-pg-{uuid.uuid4().hex[:8]}"
    subprocess.run([DOCKER, "run", "-d", "--name", name,
                    "-e", f"POSTGRES_PASSWORD={_PG_PASSWORD}",
                    "-e", "POSTGRES_USER=blyb", "-e", "POSTGRES_DB=blyb",
                    "-p", f"127.0.0.1:{_PG_PORT}:5432", "postgres:16"],
                   capture_output=True, check=True, timeout=300)
    url = f"postgresql+psycopg://blyb:{_PG_PASSWORD}@127.0.0.1:{_PG_PORT}/blyb"
    import psycopg
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            with psycopg.connect(url.replace("+psycopg", ""),
                                 connect_timeout=3) as c:
                with c.cursor() as cur:
                    cur.execute("SELECT 1")
            break
        except Exception:  # noqa: BLE001
            time.sleep(2)
    else:
        subprocess.run([DOCKER, "rm", "-f", name], capture_output=True)
        pytest.fail("隔离 PG 未就绪")
    yield url, name
    subprocess.run([DOCKER, "rm", "-f", name], capture_output=True, timeout=120)


@pg_mark
def test_migration_012_pg_cycle(pg):
    url, _ = pg
    os.environ["BLYB_DATABASE_URL"] = url
    engine = make_engine()
    try:
        _migration_cycle(engine)
    finally:
        engine.dispose()
        os.environ.pop("BLYB_DATABASE_URL", None)


@pytest.fixture()
def api_pg(pg):
    """FIX-07：PG 方言端到端（func.date / ROW_NUMBER 窗口函数 / LEFT JOIN）。"""
    url, _ = pg
    out = _build_api(url)
    try:
        yield out
    finally:
        out["engine"].dispose()
        os.environ.pop("BLYB_DATABASE_URL", None)


@pg_mark
def test_upload_stats_and_outcomes_on_pg(api_pg):
    """PG 上与 SQLite 相同期望：stats 分桶取最新尝试；outcomes 逐行回带。"""
    from datetime import datetime
    api = api_pg
    now = datetime.utcnow()
    _seed_submission(api, state="CONFIRMED",
                     attempts=[("ACCEPTED", 200)], created_at=now)
    # 两次尝试取最新：attempt 2 REJECTED/200 → other 桶（非 http_error）
    _seed_submission(api, state="UNKNOWN",
                     attempts=[("UNKNOWN", 500), ("REJECTED", 200)],
                     created_at=now)
    _seed_submission(api, state="PREPARED", attempts=[], created_at=now)
    c = api["client"]
    r = c.get("/api/v1/drug-trace/upload-stats?days=7", headers=H(api, "auditor"))
    assert r.status_code == 200, r.text
    days = r.json()["data"]["days"]
    assert len(days) == 1
    d = days[0]
    assert d["by_state"] == {"CONFIRMED": 1, "UNKNOWN": 1, "PREPARED": 1}
    assert d["total"] == 3
    assert d["attempts"] == {"accepted": 1, "http_error": 0, "other": 1}
    rows = c.get("/api/v1/drug-trace/submission-outcomes?days=7",
                 headers=H(api, "auditor")).json()["data"]
    assert len(rows) == 3
    by_state = {r["state"]: r for r in rows}
    assert by_state["CONFIRMED"]["attempt_outcome"] == "ACCEPTED"
    assert by_state["CONFIRMED"]["attempt_http_status"] == 200
    assert by_state["UNKNOWN"]["attempt_outcome"] == "REJECTED"
    assert by_state["UNKNOWN"]["attempt_http_status"] == 200
    assert by_state["PREPARED"]["attempt_outcome"] is None
    assert by_state["PREPARED"]["attempt_http_status"] is None
    problems = c.get("/api/v1/drug-trace/submission-outcomes?days=7&only=problem",
                     headers=H(api, "auditor")).json()["data"]
    assert {r["state"] for r in problems} == {"UNKNOWN"}


# ================================================================ T-A03 驱动 v2
def _load_driver():
    """加载驱动 v2 模块（stub stdin 提供 token）。"""
    assert os.path.exists(DRIVER_PATH), DRIVER_PATH
    old_stdin = sys.stdin
    sys.stdin = io.StringIO("tok-driver\n")
    try:
        spec = importlib.util.spec_from_file_location(
            f"daily_upload_driver_test_{uuid.uuid4().hex[:6]}", DRIVER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.stdin = old_stdin


def _reset_receipt(mod):
    mod.receipt = {'window': ['2026-10-24T00:00:00',
                              '2026-10-25T00:00:00'],
                   'collection': {'id': 'run-1', 'state': 'DONE',
                                  'summary': {'valid': 2}},
                   'send': {'sent': 5, 'skipped_terminal': 1, 'resumed': 0,
                            'prepared_new': 4, 'errors': [], 'aborted_reason': ''},
                   'query': {'queried': 2, 'errors': []},
                   'candidate_count': 6, 'elapsed_seconds': 120.5,
                   'outcome': 'OK'}


def test_driver_push_contract_and_failure_safety():
    mod = _load_driver()
    from datetime import date
    captured = {}

    def fake_call(method, path, body=None, timeout=90):
        captured['method'], captured['path'], captured['body'] = \
            method, path, body
        return 200, {'created': True}

    mod.call = fake_call
    _reset_receipt(mod)
    assert mod.push_daily_report(date(2026, 10, 25), 'OK') is True
    assert mod.receipt['report_push'] == {'ok': True}
    # 契约字段（§5.1）：report_date/source/窗口/计数/三 JSON/outcome/耗时
    body = captured['body']
    assert captured['method'] == 'POST'
    assert captured['path'] == '/drug-trace/daily-reports'
    assert body['report_date'] == '2026-10-25'
    assert body['source'] == 'timer'
    assert body['window_begin'] == '2026-10-24T00:00:00'
    assert body['window_end'] == '2026-10-25T00:00:00'
    assert body['candidate_count'] == 6
    assert body['collection'] == {'id': 'run-1', 'state': 'DONE',
                                  'summary': {'valid': 2}}
    assert body['send']['sent'] == 5
    assert body['query_stats'] == {'queried': 2, 'errors': []}
    assert body['outcome'] == 'OK' and body['elapsed_seconds'] == 120.5
    # 推送失败（HTTP 500）：ok=False、不抛异常、receipt 保持
    mod.call = lambda *a, **k: (500, {'error_code': 'X'})
    _reset_receipt(mod)
    assert mod.push_daily_report(date(2026, 10, 25), 'PARTIAL_FAILURE') is False
    assert mod.receipt['report_push']['ok'] is False
    assert mod.receipt['report_push']['status'] == 500
    assert mod.receipt['send']['sent'] == 5
    # 推送异常（网络炸）：同样安全
    def boom(*a, **k):
        raise OSError("connection refused")
    mod.call = boom
    _reset_receipt(mod)
    assert mod.push_daily_report(date(2026, 10, 25), 'OK') is False
    assert mod.receipt['report_push'] == {'ok': False,
                                          'error': 'connection refused'}


@pg_mark
def test_driver_v2_end_to_end_on_pg(pg, capsys, monkeypatch):
    """隔离 PG + 假 HTTP：完整跑 main() 验证回执/退出码/日报入库。"""
    url, _ = pg
    os.environ["BLYB_DATABASE_URL"] = url
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="first-hospital", name="首院")
        db.add(t)
        db.flush()
        db.add(ConnectorInstance(
            id=DRIVER_CONNECTOR_ID, tenant_id=t.id, direction="source",
            plugin_id="sources.db_view.drug_trace", name="his",
            status="ENABLED"))
        db.add(Destination(
            id=new_uuid(), tenant_id=t.id, code=DRIVER_DEST_CODE,
            profile_revision="alihealth_yljg_retail_v1", mode="PUSH",
            capabilities=["upload"], is_mock=False, status="ENABLED"))
        db.commit()
    mod = _load_driver()
    pushed = []

    def fake_call(method, path, body=None, timeout=90):
        if method == 'GET' and path.startswith('/drug-trace/collection-runs'):
            return 200, [{'id': 'run-e2e', 'state': 'DONE',
                          'window': mod.receipt['window'],
                          'summary': {'valid': 0}}]
        if method == 'POST' and path == '/drug-trace/daily-reports':
            pushed.append(body)
            return push_status[0], {'created': True}
        return 200, {'collection_run_id': 'run-e2e'}

    push_status = [200]
    mod.call = fake_call
    # ---- 成功路径：退出码 0，日报入库 outcome=OK
    rc = mod.main()
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 0 and out['outcome'] == 'OK'
    assert out['report_push'] == {'ok': True}
    assert out['send']['sent'] == 0
    assert pushed[-1]['source'] == 'timer'
    assert pushed[-1]['outcome'] == 'OK'
    assert pushed[-1]['candidate_count'] == 0
    from datetime import datetime, timedelta, timezone as tz
    yesterday = (datetime.now(tz(timedelta(hours=8))).date() - timedelta(days=1))
    assert pushed[-1]['report_date'] == yesterday.isoformat()
    with sf() as db:
        from sqlalchemy import text
        row = db.execute(text(
            "SELECT outcome, source FROM drug_trace_daily_report "
            "WHERE report_date=:d"), {'d': yesterday}).fetchone()
        assert row is None  # 假 HTTP 不真正入库；仅断言请求负载正确（上面已验）
    # ---- 推送失败：退出码仍 0（发送无错误），回执 report_push.ok=False
    push_status[0] = 500
    rc = mod.main()
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 0
    assert out['report_push']['ok'] is False
    assert out['report_push']['status'] == 500
    # ---- fatal 路径：采集创建失败 → 退出码 2 不变，日报 outcome=FAILED
    def fatal_call(method, path, body=None, timeout=90):
        if method == 'POST' and path == '/drug-trace/collection-runs':
            return 500, {'error_code': 'INTERNAL'}
        if method == 'POST' and path == '/drug-trace/daily-reports':
            pushed.append(body)
            return 200, {'created': True}
        return 200, []
    mod.call = fatal_call
    rc = mod.main()
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 2 and out['fatal'] == 'collect_create_failed'
    assert out['report_push'] == {'ok': True}
    assert pushed[-1]['outcome'] == 'FAILED'
    assert pushed[-1]['report_date'] == yesterday.isoformat()
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)
