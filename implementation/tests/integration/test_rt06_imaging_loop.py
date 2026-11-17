"""RT-06 F03：影像插件本地示范闭环（域路由/契约浏览/校验/投影→Mock→台账）。

验收：16 清单/单数据集 404、正反预检、至少一个投影→Submission（Mock 回环
真实命中）→summary/ledger/详情可检索 + mock 证据；业务隔离（不并入他业务）。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import (ConfigRevision, Destination,  # noqa: E402
                            ReportTarget, Tenant)


class MockImagingFrontend:
    """本机回环 Mock 前置（generic 装配真实 HTTP 命中证据）。"""
    hits = []

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                MockImagingFrontend.hits.append(
                    {"path": self.path, "body": body})
                payload = json.dumps({"result": True, "errorCode": "00",
                                      "desc": "mock"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self):
        import threading
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/rt06img.sqlite3"
    mock = MockImagingFrontend()
    mock.start()
    import app_runtime
    reg = app_runtime.load_registry(["medins_imaging"])
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    engine = make_engine()
    results = run_migrations(engine, extra_migrations=reg.migrations)
    assert any(r[1] == "001_core_init" for r in results)
    sf = make_session_factory(engine)
    client = TestClient(create_app(session_factory=sf, engine=engine,
                                   registry=reg))
    from contracts.api import hash_token
    with sf() as db:
        t = Tenant(id=new_uuid(), code="rt06-img", name="影像")
        db.add(t)
        db.flush()
        tid = t.id
        from plugins.report_targets import seed_default_targets
        seed_default_targets(db, tid)
        d = Destination(
            id=new_uuid(), tenant_id=tid, code="imaging_mock",
            name="影像Mock", profile_revision="destination.generic_http_json@1",
            mode="PUSH", is_mock=True, status="ENABLED")
        db.add(d)
        db.flush()
        cfg = {"base_url": mock.url, "allowed_hosts": ["127.0.0.1"],
               "allowed_schemes": ["http"], "allowed_ports": [mock.port],
               "timeout_seconds": 5, "max_attempts": 2,
               "backoff_seconds": [0.01, 0.01], "rate_limit_per_minute": 6000,
               "path_template": "/imaging/receive",
               "receipt_rules": [{"json_path": "result", "op": "eq",
                                  "value": True, "outcome": "ACCEPTED"}],
               "default_outcome": "UNKNOWN"}
        db.add(ConfigRevision(
            id=new_uuid(), tenant_id=tid, config_type="destination",
            owner_id=d.id, revision=1, content_json=cfg, digest="d" * 64,
            state="PUBLISHED"))
        db.flush()
        d.config_revision = db.query(ConfigRevision).filter_by(
            tenant_id=tid, owner_id=d.id).first().id
        t_row = db.query(ReportTarget).filter_by(
            tenant_id=tid, code="medins_imaging_jn").one()
        t_row.destination_id = d.id
        db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind="user",
                           subject_code="u-op", roles=["operator"],
                           token_hash=hash_token("tk-img"), status="ACTIVE",
                           target_scopes=["medins_imaging_jn"]))
        db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind="user",
                           subject_code="u-x", roles=["operator"],
                           token_hash=hash_token("tk-imgx"), status="ACTIVE",
                           target_scopes=["drug_trace_alihealth"]))
        db.commit()
    yield {"client": client, "sf": sf, "engine": engine, "tid": tid,
           "mock": mock}
    engine.dispose()
    mock.stop()
    os.environ.pop("BLYB_DATABASE_URL", None)


from core_db.models import AuthSubject  # noqa: E402


def _records(ds_code: str, contract_path: str, n=2):
    sample = json.load(open(contract_path, encoding="utf-8"))
    rec = dict(sample["input"])
    return [dict(rec) for _ in range(n)]


def test_f03_datasets_list_and_detail(env):
    c = env["client"]
    hdr = {"Authorization": "Bearer tk-img"}
    r = c.get("/api/v1/medins-imaging/datasets", headers=hdr)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["count"] == 16
    codes = [x["code"] for x in d["items"]]
    assert codes[:2] == ["APPLY_INFO", "CHK_INFO"]
    apply_row = next(x for x in d["items"] if x["code"] == "APPLY_INFO")
    assert apply_row["field_count"] == 63
    assert apply_row["revision"] == "r2-csv16"
    assert apply_row["pending_count"] > 0
    # 单数据集：字段树+样例；scope 先校验；未知 404
    r2 = c.get("/api/v1/medins-imaging/datasets/APPLY_INFO", headers=hdr)
    assert r2.status_code == 200
    assert len(r2.json()["data"]["nodes"]) == 63
    assert r2.json()["data"]["sample"]["input"]
    # 范围外账号 → 403（先 scope 后存在性）
    r3 = c.get("/api/v1/medins-imaging/datasets",
               headers={"Authorization": "Bearer tk-imgx"})
    assert r3.status_code == 403
    r4 = c.get("/api/v1/medins-imaging/datasets/NO_SUCH_DS", headers=hdr)
    assert r4.status_code == 404


def test_f03_validate_reverse_cases(env):
    c = env["client"]
    hdr = {"Authorization": "Bearer tk-img"}
    contract = json.load(open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(
            __file__)))), "profiles", "medins_imaging_jn", "revisions",
        "r2-csv16", "fields", "APPLY_INFO.json"), encoding="utf-8"))
    sample = json.load(open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(
            __file__)))), "profiles", "medins_imaging_jn", "revisions",
        "r2-csv16", "samples", "APPLY_INFO.json"), encoding="utf-8"))
    good = dict(sample["input"])
    bad = dict(good)
    req_code = [n for n in contract["nodes"] if n["required"]][0]["code"]
    del bad[req_code]
    r = c.post("/api/v1/medins-imaging/validate", json={
        "dataset": "APPLY_INFO", "records": [good, bad]},
        headers=hdr)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["ok_rows"] == 1 and d["rows"][1]["errors"]


def test_f03_project_loop_ledger_summary(env):
    """投影→冻结→Submission→generic Mock 命中→summary/ledger/详情+mock 证据。"""
    c = env["client"]
    hdr = {"Authorization": "Bearer tk-img"}
    sample = json.load(open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(
            __file__)))), "profiles", "medins_imaging_jn", "revisions",
        "r2-csv16", "samples", "CHK_INFO.json"), encoding="utf-8"))
    records = [dict(sample["input"]) for _ in range(2)]
    r = c.post("/api/v1/medins-imaging/project", json={
        "dataset": "CHK_INFO", "records": records}, headers=hdr)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["frozen"] == 2 and d["mock"] is True
    assert d["outcome"] in ("ACCEPTED", "CONFIRMED", "PARTIAL")
    sub_id = d["submission_id"]
    # generic 回环真实 HTTP 命中（本机 Mock 前置收到影像投影报文）
    assert env["mock"].hits, "Mock 前置零命中——回环未发生"
    assert any(h["body"].get("dataset") == "CHK_INFO"
               for h in env["mock"].hits)
    # 幂等：同内容重投影 → 同 submission
    r2 = c.post("/api/v1/medins-imaging/project", json={
        "dataset": "CHK_INFO", "records": records}, headers=hdr)
    assert r2.json()["data"]["idempotent"] is True
    assert r2.json()["data"]["submission_id"] == sub_id
    # 复核修正回归：幂等限定 destination——换目的地同内容应新建而非复用
    with env["sf"]() as db:
        from core_db.models import Destination as D2
        t_row = db.query(ReportTarget).filter_by(
            tenant_id=env["tid"], code="medins_imaging_jn").one()
        d_b = Destination(id=new_uuid(), tenant_id=env["tid"],
                          code="imaging_b", name="影像B",
                          profile_revision="destination.generic_http_json@1",
                          mode="PUSH", is_mock=True, status="ENABLED")
        db.add(d_b)
        db.flush()
        cfg = dict(json.loads(json.dumps({
            "base_url": "http://127.0.0.1:9", "allowed_hosts": ["127.0.0.1"],
            "allowed_schemes": ["http"], "allowed_ports": [9],
            "timeout_seconds": 5, "max_attempts": 2,
            "backoff_seconds": [0.01] * 2, "rate_limit_per_minute": 6000,
            "path_template": "/x", "receipt_rules": [],
            "default_outcome": "UNKNOWN"})))
        db.add(ConfigRevision(id=new_uuid(), tenant_id=env["tid"],
                              config_type="destination", owner_id=d_b.id,
                              revision=1, content_json=cfg, digest="d" * 64,
                              state="PUBLISHED"))
        db.flush()
        d_b.config_revision = db.query(ConfigRevision).filter_by(
            tenant_id=env["tid"], owner_id=d_b.id).first().id
        t_row.destination_id = d_b.id
        db.commit()
    r3 = c.post("/api/v1/medins-imaging/project", json={
        "dataset": "CHK_INFO", "records": records}, headers=hdr)
    d3 = r3.json()["data"]
    assert d3["idempotent"] is False, "跨目的地同内容不得复用旧 Submission"
    assert d3["submission_id"] != sub_id
    # summary：域内 Submission 桶计数 + evidence_kind=mock
    from plugins.domains.medins_imaging import hooks
    with env["sf"]() as db:
        s = hooks.target_summary(db, env["tid"], "medins_imaging_jn", 7)
    assert s is not None and s.data_source == "platform"
    assert s.evidence_kind == "mock"
    assert s.buckets["ACCEPTED"] == 1          # 2 行冻结=1 Submission
    # ledger：域隔离（其他业务 Submission 不入）
    from plugins.domain_hooks import LedgerFilter
    page = hooks.ledger_search(db, env["tid"], "medins_imaging_jn",
                               LedgerFilter(), None, 10)
    assert page is not None and page.total_estimate == 2   # A+B 各一笔
    assert page.items[0].business_key.startswith("imaging:")
    # detail
    detail = hooks.ledger_detail(db, env["tid"], "medins_imaging_jn",
                                 page.items[0].id)
    assert detail and detail["id"] == page.items[0].id
    # 隔离：drug_trace 的 Submission 不进影像台账
    assert hooks.ledger_search(db, env["tid"], "drug_trace_alihealth",
                               LedgerFilter(), None, 10) is None


def test_f03_project_requires_mock_destination(env):
    """非 Mock 目的地绑定 → 拒绝（不伪装正式医保传输）。"""
    c = env["client"]
    hdr = {"Authorization": "Bearer tk-img"}
    with env["sf"]() as db:
        from core_db.models import Destination
        d = db.query(Destination).filter_by(
            tenant_id=env["tid"], code="imaging_mock").one()
        d.is_mock = False
        db.commit()
    sample = json.load(open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(
            __file__)))), "profiles", "medins_imaging_jn", "revisions",
        "r2-csv16", "samples", "APPLY_INFO.json"), encoding="utf-8"))
    r = c.post("/api/v1/medins-imaging/project", json={
        "dataset": "APPLY_INFO", "records": [dict(sample["input"])]},
        headers=hdr)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "PROFILE_UNAPPROVED"
