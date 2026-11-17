"""第2轮整改正式回归（对应 docs/audit/2026-10-01 探针 R01—R09/R18/R19/R20/R26—R34/R37—R39/R42/R43）。

迁移原则：复核探针断言期望行为，本文件将其纳入正式回归并按验收矩阵 G01—G10/G19 扩大：
- 交换/确认/资产可见性（W05/W06）
- 作业、条件DAG、dry-run、fencing（W03）
- 租户隔离、人工CAS、注册凭据（W02）
- 正式索引编码、就诊桥接、玻片分页、报告机构、身份（W04）
- 字典审批闭包与生产守卫骨架（W10）
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

import plugin_registry
from tests.harness import Harness, harness  # noqa: F401 -- fixture
from contracts import new_uuid
from contracts.api import digest_of, hash_token
from core_db.models import (Asset, AuthSubject, Blob, ConfigRevision, ConnectorInstance,
                            DataPackage, Destination, DeliveryAttempt, DownloadEvidence,
                            Job, ManualDecision, PackageRevision, Publication,
                            PublicationItem, ReceiptEvent, SourceSystem, StepRun,
                            Submission, Tenant, UploadSession, WorkflowRevision)
from workflow_core.jobs import claim_job, enqueue_job, finish_job
from destinations.view_publication.exchange_db import ExchangeDb
from sources.db_view import SyntheticWarehouse


@pytest.fixture(autouse=True)
def isolated_source(monkeypatch):
    import domains.pathology.handlers as _ph
    monkeypatch.setattr(_ph, "WAREHOUSE_SINGLETON", SyntheticWarehouse())


def _drain_response(response) -> bytes:
    import asyncio

    async def _run():
        out = b""
        async for chunk in response.body_iterator:
            out += chunk if isinstance(chunk, bytes) else chunk.encode()
        return out
    return asyncio.run(_run())


# ---------------------------------------------------------------- 辅助
def _package(db, h: Harness, payload=None) -> PackageRevision:
    p = DataPackage(id=new_uuid(), tenant_id=h.tenant_id, domain="pathology",
                    business_key=new_uuid())
    db.add(p)
    db.flush()
    r = PackageRevision(id=new_uuid(), tenant_id=h.tenant_id, package_id=p.id,
                        revision=1, schema_ref={}, payload=payload or {},
                        digest=digest_of(payload or {}), provenance={}, state="FROZEN")
    db.add(r)
    db.flush()
    return r


def _submission(db, h: Harness) -> Submission:
    pr = _package(db, h)
    dest = db.query(Destination).filter_by(tenant_id=h.tenant_id, mode="PUSH").first()
    s = Submission(id=new_uuid(), tenant_id=h.tenant_id, package_revision_id=pr.id,
                   destination_id=dest.id, snapshot_digest=pr.digest,
                   idempotency_key=new_uuid(), state="ACCEPTED", submission_revision=1)
    db.add(s)
    db.commit()
    return s


def _other_subject(h: Harness, *, roles=None):
    """B租户主体：独立租户+collector令牌（跨租户否定矩阵用）。"""
    with h.sf() as db:
        tid = new_uuid()
        db.add(Tenant(id=tid, code=f"round2-{tid}", name="Synthetic tenant B"))
        db.flush()
        db.add(SourceSystem(id=new_uuid(), tenant_id=tid, code="pis", kind="database",
                            identifier_namespace="synthetic-B"))
        tok = f"round2-{new_uuid()}"
        db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind="collector",
                           subject_code="round2-B", token_hash=hash_token(tok),
                           roles=roles or ["service"]))
        db.commit()
    return tid, {"Authorization": f"Bearer {tok}"}


def _uploaded(h: Harness, name: str):
    from tests.fault.test_storage_protocol import _register_and_upload, _put_chunks
    acq, asset, sess, data, sha = _register_and_upload(h, b"round2-bytes", scan_id=name)
    for _, r in _put_chunks(h, sess, data):
        assert r.status_code == 204
    r = h.storage.post(f"/api/v1/upload-sessions/{sess['session_id']}/complete",
                       headers=h.agent_headers())
    assert r.status_code == 202
    h.worker.tick(max_jobs=1, max_steps=0)
    return acq, asset, sess, data, sha


# ================================================================ W06 回执
def test_R01_accepted_receipt_is_not_confirmation(harness: Harness):
    """ACCEPTED=传输受理；Submission 不推进 CONFIRMED。"""
    with harness.sf() as db:
        s = _submission(db, harness)
    r = harness.core.post(f"/api/v1/receipts/{s.destination_id}", json={
        "receipt_id": "round2-accepted", "submission_key": s.idempotency_key,
        "result": "ACCEPTED"}, headers=harness.admin_headers())
    assert r.status_code == 200
    with harness.sf() as db:
        assert db.get(Submission, s.id).state == "ACCEPTED"
    # CONFIRMED 才推进业务确认
    r2 = harness.core.post(f"/api/v1/receipts/{s.destination_id}", json={
        "receipt_id": "round2-confirmed", "submission_key": s.idempotency_key,
        "result": "CONFIRMED"}, headers=harness.admin_headers())
    assert r2.status_code == 200
    with harness.sf() as db:
        assert db.get(Submission, s.id).state == "CONFIRMED"


def test_R02_publication_receipt_must_match_publication(harness: Harness):
    """其他发布/其他提交的回执不得串成本发布的业务确认证据。"""
    with harness.sf() as db:
        pr = _package(db, harness)
        dest = db.query(Destination).filter_by(mode="PULL_PUBLICATION").first()
        pub = Publication(id=new_uuid(), tenant_id=harness.tenant_id,
                          package_revision_id=pr.id, destination_id=dest.id,
                          generation_id=new_uuid(), state="PUBLISHED")
        db.add(pub)
        db.add(ReceiptEvent(id=new_uuid(), tenant_id=harness.tenant_id,
                            destination_id=dest.id, external_receipt_id=new_uuid(),
                            submission_ref="another-publication",
                            raw_payload={"publication_id": "another-publication"},
                            parsed_result={"result": "CONFIRMED"}))
        db.commit()
        pid = pub.id
    r = harness.core.get(f"/api/v1/publications/{pid}/evidence",
                         headers=harness.admin_headers())
    body = r.json()["data"]
    assert body["business_confirmed"] is False
    assert body["receipts"] == []
    # 反例：精确指向本发布的 CONFIRMED 回执 → business_confirmed=True
    with harness.sf() as db:
        db.add(ReceiptEvent(id=new_uuid(), tenant_id=harness.tenant_id,
                            destination_id=dest.id, external_receipt_id="rcpt-mine",
                            submission_ref=None, publication_ref=pid,
                            raw_payload={"publication_id": pid},
                            parsed_result={"result": "CONFIRMED"}))
        db.commit()
    r2 = harness.core.get(f"/api/v1/publications/{pid}/evidence",
                          headers=harness.admin_headers())
    assert r2.json()["data"]["business_confirmed"] is True


def test_R26_download_evidence_written_after_stream(harness: Harness):
    """完整/Range 下载的证据在实际流结束后才写 COMPLETE/PARTIAL（R26）。

    W08改造后端点为async生成器+断开轮询；直接调用需提供receive通道并
    在同一事件循环内await+drain（行为不变量不变：响应返回前无证据，
    流结束才有COMPLETE与实际bytes）。
    """
    from starlette.requests import Request
    from contracts.api import Principal, RequestContext
    _, asset, _, data, _ = _uploaded(harness, "round2-stream-evidence")

    async def _receive():  # 无断开消息：is_disconnected 恒 False
        return {"type": "http.request", "more_body": False}

    req = Request({"type": "http", "method": "GET", "headers": []},
                  receive=_receive)
    ctx = RequestContext(req)
    ctx.principal = Principal("synthetic", harness.tenant_id, "user", "round2",
                              ["operator"])
    endpoint = next(r.endpoint for r in harness.storage.app.routes
                    if getattr(r, "path", "") == "/api/v1/assets/{asset_id}/content")
    import asyncio

    async def _call_and_drain():
        resp = await endpoint(asset, req, ctx, None)
        assert resp.body_iterator is not None
        with harness.sf() as db:
            assert db.query(DownloadEvidence).filter_by(asset_id=asset).count() == 0,                 "响应返回前不得有任何下载证据"
        out = b""
        async for chunk in resp.body_iterator:
            out += chunk if isinstance(chunk, bytes) else chunk.encode()
        return out

    body = asyncio.run(_call_and_drain())
    assert body == data
    with harness.sf() as db:
        ev = db.query(DownloadEvidence).filter_by(asset_id=asset).all()
        assert len(ev) == 1 and ev[0].result == "COMPLETE"
        assert ev[0].bytes_sent == len(data)


# ================================================================ W05 交换
def test_R03_exchange_preserves_other_cases_and_correction_pointer(harness: Harness):
    """G09子集：A/B/C连续发布均保留；更正A只替换A当前指针。"""
    ex = ExchangeDb(os.environ["BLYB_EXCHANGE_DB"])
    try:
        for key in ("case-A", "case-B", "case-C"):
            b = ex.begin_batch(key, key, "round2-scope", key)
            ex.upsert_projection(b, "chk_info", key, {"case": key, "v": 1})
            ex.publish_batch(b)
        rows, _ = ex.view_rows("chk_info", "round2-scope")
        assert {r["external_key"] for r in rows} == {"case-A", "case-B", "case-C"}
        # 更正 A：新批次同键 v2 → 仅 A 指针变化
        b2 = ex.begin_batch("case-A-r2", "case-A-r2", "round2-scope", "d2")
        ex.upsert_projection(b2, "chk_info", "case-A", {"case": "case-A", "v": 2})
        ex.publish_batch(b2)
        rows2, _ = ex.view_rows("chk_info", "round2-scope")
        by_key = {r["external_key"]: r["payload"] for r in rows2}
        assert set(by_key) == {"case-A", "case-B", "case-C"}
        assert by_key["case-A"]["v"] == 2
        assert by_key["case-B"]["v"] == 1 and by_key["case-C"]["v"] == 1
    finally:
        ex.close()


def test_R04_exchange_datasets_are_tenant_scoped(harness: Harness):
    """G01子集：B租户collector不能读A的结构化行（404，不泄露存在性）。"""
    ex = ExchangeDb(os.environ["BLYB_EXCHANGE_DB"])
    b = ex.begin_batch("tenant-A-pub", "round2-tenant-A", "private-A", "d",
                       tenant_id=harness.tenant_id)
    ex.upsert_projection(b, "chk_info", "private-A-case", {"synthetic": "A"})
    ex.publish_batch(b)
    ex.close()
    _, headers = _other_subject(harness)
    r = harness.exchange.get("/exchange/v1/datasets/chk_info?scope=private-A",
                             headers=headers)
    assert r.status_code == 404, r.text
    r2 = harness.exchange.get(
        "/exchange/v1/generations/current?scope=private-A", headers=headers)
    assert r2.status_code == 404


def test_R05_missing_frozen_asset_prevents_publication(harness: Harness):
    """G10子集：冻结包必需成员缺Asset → 发布阻断且交换区不可见。"""
    payload = {"case": {"source_case_id": "synthetic-case", "pathology_no_raw": "synthetic"},
               "slides": [{"barcode_raw": "synthetic", "scans": [{"effective": True,
                   "assets": [{"asset_id": "missing-required-asset", "sha256": "x" * 64}]}]}]}
    with harness.sf() as db:
        pr = _package(db, harness, payload)
        ctx = plugin_registry.HandlerCtx(db, harness.tenant_id,
              SimpleNamespace(context={"package_revision_id": pr.id}),
              SimpleNamespace(input_refs={"package_revision_id": pr.id}), {})
        result = plugin_registry.h_deliver_publish(ctx)
        assert result.state != "SUCCEEDED"
        assert result.error.get("members"), "阻断时必须列出缺失成员"
        # 交换区不可见：无该键的当前指针
        ex = ExchangeDb(os.environ["BLYB_EXCHANGE_DB"])
        try:
            rows, _ = ex.view_rows("chk_info", "first-hospital-exchange",
                                   tenant_id=harness.tenant_id)
            assert all(r["external_key"] != "synthetic-case" for r in rows)
        finally:
            ex.close()


def test_R06_superseded_url_remains_available(harness: Harness, tmp_path):
    ex = ExchangeDb(os.environ["BLYB_EXCHANGE_DB"])
    path = tmp_path / "fixed-synthetic-bytes"
    path.write_bytes(b"old-version")
    b1 = ex.begin_batch("p1", "g1", "round2-retain", "d1")
    route = ex.add_asset_route(b1, new_uuid(), str(path), 11, "a" * 64,
                               "application/octet-stream")
    ex.mark_asset_reachable(route)
    ex.publish_batch(b1)
    b2 = ex.begin_batch("p2", "g2", "round2-retain", "d2")
    ex.publish_batch(b2)
    assert ex.asset_route_by_id(route) is not None


def test_R07_route_id_stable_across_process_restart(tmp_path):
    code = ("from destinations.view_publication.exchange_db import ExchangeDb; "
            "import sys; e=ExchangeDb(sys.argv[1]); "
            "b=e.begin_batch('p','g','s','d'); "
            "print(e.add_asset_route(b,'fixed-asset-uuid','path',1,'a'*64,"
            "'application/octet-stream')); e.close()")
    results = []
    for seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        r = subprocess.run([sys.executable, "-c", "import _bootstrap; " + code,
                            str(tmp_path / "routes.sqlite3")], env=env,
                           capture_output=True, text=True, timeout=15, check=True)
        results.append(r.stdout.strip())
    assert results[0] == results[1], results


def test_R42_quarantined_blob_blocks_all_referencing_assets(harness: Harness):
    from core_db.models import Asset, Blob
    _, a, _, _, _ = _uploaded(harness, "round2-blob-A")
    _, b, _, _, _ = _uploaded(harness, "round2-blob-B")
    with harness.sf() as db:
        aa, bb = db.get(Asset, a), db.get(Asset, b)
        assert aa.blob_id == bb.blob_id, "同内容应共享Blob"
        db.get(Blob, aa.blob_id).state = "QUARANTINED"
        db.commit()
    ra = harness.storage.get(f"/api/v1/assets/{a}/content",
                             headers=harness.admin_headers())
    rb = harness.storage.get(f"/api/v1/assets/{b}/content",
                             headers=harness.admin_headers())
    assert ra.status_code != 200 and rb.status_code != 200


# ================================================================ W03 作业/DAG
def test_R08_same_event_idempotent_new_generation_requeues(harness: Harness):
    """G04子集：同事件payload幂等复用；新代次（更正）必须有可执行新任务。"""
    with harness.sf() as db:
        j1 = enqueue_job(db, harness.tenant_id, "pathology.freeze_attempt", "case:c",
                         {"case_id": "c"})
        j1.state = "SUCCEEDED"
        db.commit()
        j1b = enqueue_job(db, harness.tenant_id, "pathology.freeze_attempt", "case:c",
                          {"case_id": "c"})
        assert j1b.id == j1.id, "同payload重复摄入应幂等复用"
        j2 = enqueue_job(db, harness.tenant_id, "pathology.freeze_attempt", "case:c",
                         {"case_id": "c", "reason": "report corrected"})
        assert j2.id != j1.id and j2.state == "PENDING"


def test_R09_new_package_revision_launches_publish_run(harness: Harness):
    with harness.sf() as db:
        r1 = harness.worker._launch(db, harness.tenant_id, "pathology.mock_publish",
                                    {"case_id": "same-case",
                                     "package_revision_id": "revision-1"})
        r1.state = "SUCCEEDED"
        db.commit()
        # 同包重复 → 幂等复用
        r1b = harness.worker._launch(db, harness.tenant_id, "pathology.mock_publish",
                                     {"case_id": "same-case",
                                      "package_revision_id": "revision-1"})
        assert r1b.id == r1.id
        r2 = harness.worker._launch(db, harness.tenant_id, "pathology.mock_publish",
                                    {"case_id": "same-case",
                                     "package_revision_id": "revision-2"})
        assert r2.id != r1.id


def test_R18_branch_edges_participate_in_dependency_model(harness: Harness):
    """G06子集：条件边参与依赖；未激活分支SKIPPED；WAITING不使run假结束。"""
    from workflow_core.dag import create_run, materialize_steps, runnable_steps
    from core_db.models import StepRun, WorkflowRevision
    dsl = {"nodes": [{"id": "resolve", "type": "resolve"},
                     {"id": "review", "type": "notify"}],
           "edges": [{"from": "resolve", "to": "review", "on": "needs_review"}]}
    with harness.sf() as db:
        wf = db.query(WorkflowRevision).first()
        run = create_run(db, harness.tenant_id, wf.id, trigger="synthetic", context={})
        materialize_steps(db, run, dsl)
        ready, _ = runnable_steps(db, run, dsl)
        assert "review" not in {s.node_id for s in ready}, \
            "分支目标不得因success入边缺失被当根节点"
        # resolve 成功 → 未选分支 SKIPPED，run 正常终态
        rs = db.query(StepRun).filter_by(run_id=run.id, node_id="resolve").first()
        rs.state = "SUCCEEDED"
        db.commit()
        ready2, _ = runnable_steps(db, run, dsl)
        assert ready2 == []
        rv = db.query(StepRun).filter_by(run_id=run.id, node_id="review").first()
        assert rv.state == "SKIPPED"
        from workflow_core.dag import run_finished
        assert run_finished(db, run, dsl) == "SUCCEEDED"


def test_R19_unknown_plugin_has_no_fallback():
    assert plugin_registry.handler_for("deliver.push", "uninstalled.vendor.plugin") is None


def test_R20_regex_catastrophe_rejected_in_validation():
    from mapping_engine import validate_mapping
    for pattern in ("(a|aa)+$", "(a|a)+$", "(ab|a)+$", "(a+|b)+c", "(a*)+b"):
        mapping = {"ops": [{"op": "regex_replace", "source_field": "x", "target": "y",
                            "pattern": pattern, "replacement": ""}]}
        assert validate_mapping(mapping), f"pattern {pattern!r} must be rejected"


def test_R28_barcode_resolution_operation_reaches_terminal_state(harness: Harness):
    acq = harness.register_scan("900001")["acquisition_id"]
    r = harness.core.post("/api/v1/pathology/barcode-resolutions",
                          json={"acquisition_id": acq},
                          headers={**harness.admin_headers(),
                                   "Idempotency-Key": "round2-resolve"})
    assert r.status_code == 202
    op = r.json()["data"]["operation_id"]
    harness.worker.tick(max_jobs=1, max_steps=0)
    r2 = harness.core.get(f"/api/v1/operations/{op}", headers=harness.admin_headers())
    assert r2.json()["data"]["state"] in ("SUCCEEDED", "FAILED"), r2.text
    assert r2.json()["data"]["result"].get("resolve", {}).get("state") is not None


def test_R34_dry_run_has_zero_real_effects(harness: Harness):
    """G07：dry-run 跑到队列静止，真实 Publication/Submission/Attempt/命令=0，
    且模拟链路只发 dry.* 事件（普通消费者不可吸收）。"""
    from apps.seed import HOSPITAL_PACK_APPROVED
    from core_db.models import AgentCommand, OutboxEvent
    harness.publish_pack(HOSPITAL_PACK_APPROVED)
    acq = harness.register_scan("090001")["acquisition_id"]
    with harness.sf() as db:
        ctx = plugin_registry.HandlerCtx(db, harness.tenant_id,
              SimpleNamespace(context={"acquisition_id": acq}),
              SimpleNamespace(input_refs={"acquisition_id": acq}), {}, dry_run=True)
        plugin_registry.h_pathology_resolve(ctx)
    harness.tick(8)
    with harness.sf() as db:
        assert db.query(Publication).count() == 0, "dry-run不得产生真实发布"
        assert db.query(Submission).count() == 0
        assert db.query(DeliveryAttempt).count() == 0
        assert db.query(AgentCommand).count() == 0
        normal_case_events = (db.query(OutboxEvent)
                              .filter_by(event_type="pathology.case.updated").all())
        assert normal_case_events == [], "dry-run不得发出普通 pathology.case.updated"
        dry_events = (db.query(OutboxEvent)
                      .filter(OutboxEvent.event_type.like("dry.%")).all())
        assert dry_events, "模拟结果应以隔离事件名空间可查"


def test_R39_reclaimed_job_rejects_old_owner(harness: Harness):
    """G05子集：租约被抢占后旧Session的finish被拒（条件UPDATE fencing）。"""
    from datetime import timedelta
    from core_db.engine import utcnow
    with harness.sf() as db:
        enqueue_job(db, harness.tenant_id, "round2.fencing", "one", {})
        db.commit()
    with harness.sf() as old_db:
        old_lease = claim_job(old_db, "old", ["round2.fencing"])
        with harness.sf() as new_db:
            j = new_db.get(Job, old_lease.job.id)
            j.lease_until = utcnow() - timedelta(seconds=1)
            new_db.commit()
            new_lease = claim_job(new_db, "new", ["round2.fencing"])
            assert new_lease.epoch > old_lease.epoch
        assert finish_job(old_db, old_lease, success=True) is False
        # 新owner可正常完成
        assert finish_job(new_db, new_lease, success=True) is True
    # 并发领取：第二个领取者拿不到同一作业
    with harness.sf() as db:
        enqueue_job(db, harness.tenant_id, "round2.fencing", "two", {})
        db.commit()
    s1 = harness.sf()
    s2 = harness.sf()
    try:
        l1 = claim_job(s1, "w1", ["round2.fencing"])
        l2 = claim_job(s2, "w2", ["round2.fencing"])
        assert l1 is not None and l2 is None
    finally:
        s1.close()
        s2.close()


def test_R43_dev_startup_single_executor(harness, monkeypatch):
    import run_dev
    from apps.seed import seed
    names = []

    class CapturedThread:
        def __init__(self, target=None, daemon=None, name=None, **kw):
            self.target, self.name = target, name

        def start(self):
            names.append(self.name)
    monkeypatch.setattr(run_dev.threading, "Thread", CapturedThread)
    monkeypatch.setattr(run_dev, "seed", lambda eng, sf: seed(eng, sf, with_volume=False))

    def fake_sleep(seconds):
        if seconds == 3600:
            raise KeyboardInterrupt()
    monkeypatch.setattr(run_dev.time, "sleep", fake_sleep)
    run_dev.start_all(demo=False)
    assert sum(n in {"storage-verify-worker", "worker"} for n in names) == 1, names


# ================================================================ W02 租户/CAS
def test_R12_manual_binding_requires_if_match_and_cas(harness: Harness):
    """G02子集：缺If-Match 428；并发同版本仅一个成功；旧版本412且无副作用。"""
    acq = harness.register_scan("990000")["acquisition_id"]
    harness.resolve(acq)
    body = {"entity_ref": f"acquisition:{acq}", "action": "bind_slide",
            "after": {"source_slide_id": "4", "barcode_raw": "990000"},
            "reason": "synthetic review"}
    r428 = harness.core.post("/api/v1/manual-decisions", json=body,
                             headers=harness.admin_headers())
    assert r428.status_code == 428
    cand = harness.core.get(
        f"/api/v1/pathology/acquisitions/{acq}/association-candidates",
        headers=harness.admin_headers()).json()["data"]
    v = cand["decision_version"]
    hdr = {**harness.admin_headers(), "If-Match": str(v)}
    r_ok = harness.core.post("/api/v1/manual-decisions", json=body, headers=hdr)
    assert r_ok.status_code == 200, r_ok.text
    # 第二个操作者持同版本重放 → 412，且不新增决定
    r_stale = harness.core.post("/api/v1/manual-decisions", json={
        "entity_ref": f"acquisition:{acq}", "action": "bind_slide",
        "after": {"source_slide_id": "5", "barcode_raw": "990000"},
        "reason": "competing reviewer"}, headers=hdr)
    assert r_stale.status_code == 412
    with harness.sf() as db:
        n = db.query(ManualDecision).filter_by(
            entity_ref=f"acquisition:{acq}").count()
        assert n == 1, "竞争失败方不得产生副作用"


def test_R13_cross_tenant_acquisition_binding_rejected(harness: Harness):
    """G01子集：B租户reviewer绑A租户acquisition → 404 且零副作用。"""
    acq = harness.register_scan("900001")["acquisition_id"]
    _, headers = _other_subject(harness, roles=["business_reviewer"])
    with harness.sf() as db:
        before = (db.query(ManualDecision).count(),
                  db.query(Publication).count())
    r = harness.core.post("/api/v1/manual-decisions", json={
        "entity_ref": f"acquisition:{acq}", "action": "bind_slide",
        "after": {"source_slide_id": "1", "barcode_raw": "900001"},
        "reason": "cross tenant attempt"}, headers={**headers, "If-Match": "0"})
    assert r.status_code == 404, r.text
    with harness.sf() as db:
        after = (db.query(ManualDecision).count(),
                 db.query(Publication).count())
        assert before == after


def test_R27_enrollment_issues_random_rotating_credentials(harness: Harness):
    """G03子集：同设备重注册→新高熵令牌；旧令牌立即失效；新令牌可认证。"""
    body = {"enroll_token": "dev-enroll-token", "device_code": "round2-device"}
    a = harness.core.post("/api/v1/agents/enroll", json=body).json()["data"]
    b = harness.core.post("/api/v1/agents/enroll", json=body).json()["data"]
    assert a["token"] != b["token"]
    assert not a["token"].startswith("agent-token-"), "不得由agent_id推导"
    assert len(a["token"]) >= 32
    # 旧令牌失效，新令牌可用于心跳
    r_old = harness.core.post(f"/api/v1/agents/{a['agent_id']}/heartbeat", json={},
                              headers={"Authorization": f"Bearer {a['token']}"})
    assert r_old.status_code == 401
    r_new = harness.core.post(f"/api/v1/agents/{b['agent_id']}/heartbeat", json={},
                              headers={"Authorization": f"Bearer {b['token']}"})
    assert r_new.status_code == 200


# ================================================================ W04 病理/索引
def test_R29_encounter_dictionary_matches_s03():
    from apps.seed import APPROVED_DICTIONARIES
    items = next(items for ns, _, _, items in APPROVED_DICTIONARIES
                 if ns == "encounter_kind")
    mapping = {r["code"]: r["target"] for r in items}
    assert mapping == {"门诊": "1", "住院": "2"}, mapping
    # 新版本被当前Pack引用
    from apps.seed import HOSPITAL_PACK_APPROVED
    assert HOSPITAL_PACK_APPROVED["approved_dictionaries"]["encounter_kind"] == "demo-v2"


def test_R29b_existing_db_pack_upgraded_on_reseed(harness: Harness):
    """历史影响：已发布pack引用旧编码（demo-v1）时，重跑seed产生更正修订并发布；
    旧revision退役不删除（冻结历史保留）。"""
    from core_db.models import ConfigRevision
    from apps.seed import HOSPITAL_PACK_APPROVED, seed
    old_pack = copy.deepcopy(HOSPITAL_PACK_APPROVED)
    old_pack["approved_dictionaries"]["encounter_kind"] = "demo-v1"
    harness.publish_pack(old_pack)
    with harness.sf() as db:
        retired = (db.query(ConfigRevision)
                   .filter_by(tenant_id=harness.tenant_id,
                              config_type="hospital_pack", state="PUBLISHED")
                   .order_by(ConfigRevision.revision.desc()).first())
        old_rev, old_digest = retired.revision, retired.digest
    seed()  # 同库重跑（env BLYB_DATABASE_URL 指向 harness 的临时库）
    with harness.sf() as db:
        published = (db.query(ConfigRevision)
                     .filter_by(tenant_id=harness.tenant_id,
                                config_type="hospital_pack", state="PUBLISHED")
                     .order_by(ConfigRevision.revision.desc()).first())
        assert published.content_json["approved_dictionaries"][
            "encounter_kind"] == "demo-v2"
        assert published.revision > old_rev
        prev = db.query(ConfigRevision).filter_by(
            tenant_id=harness.tenant_id, config_type="hospital_pack",
            revision=old_rev).first()
        assert prev.state == "RETIRED" and prev.digest == old_digest,             "旧revision内容不可变"


def test_R30_unique_visit_without_bridge_stays_waiting(harness: Harness):
    from apps.seed import HOSPITAL_PACK_APPROVED
    from domains.pathology import resolver
    pack = copy.deepcopy(HOSPITAL_PACK_APPROVED)
    pack.pop("pis_visit_bridge", None)
    harness.publish_pack(pack)
    wh = plugin_registry.synthetic_warehouse()
    wh.rows["PAT_VISIT"] = [r for r in wh.rows["PAT_VISIT"]
                            if r["PATIENT_ID"] != "P0001" or r["VISIT_ID"] == "1"]
    with harness.sf() as db:
        rt = plugin_registry.build_runtime(db, harness.tenant_id)
        cr = rt.pis.get_case(rt.scope(), "9000001")
        c = resolver.upsert_case(rt, cr)
        result = resolver.resolve_patient_and_encounter(rt, c, cr, rt.his)
        assert result["encounter"]["state"] != "CONFIRMED"
        assert result["encounter"]["wait_reason"] == "VISIT_BRIDGE_UNCONFIRMED"


def test_R30b_approved_bridge_confirms_bridged_visit(harness: Harness):
    from domains.pathology import resolver
    with harness.sf() as db:
        rt = plugin_registry.build_runtime(db, harness.tenant_id)
        cr = rt.pis.get_case(rt.scope(), "9000001")
        c = resolver.upsert_case(rt, cr)
        result = resolver.resolve_patient_and_encounter(rt, c, cr, rt.his)
        assert result["encounter"]["state"] == "CONFIRMED"


def test_R31_expected_slides_full_pagination(harness: Harness):
    from apps.seed import HOSPITAL_PACK_APPROVED
    from domains.pathology import resolver
    harness.publish_pack(HOSPITAL_PACK_APPROVED)
    wh = plugin_registry.synthetic_warehouse()
    wh.rows["Pitaya_Slice"] = [{"Id": i, "qptmh": f"s{i}", "PathologyID": "9000001",
                                "lkh": None} for i in range(1, 26)]
    with harness.sf() as db:
        rt = plugin_registry.build_runtime(db, harness.tenant_id)
        cr = rt.pis.get_case(rt.scope(), "9000001")
        c = resolver.upsert_case(rt, cr)
        result = resolver.evaluate_completeness(rt, c, rt.pis)
        assert result["expected_count"] == 25, result
        assert result["pages_read"] >= 2
        assert result["last_page_proven"] is True
        # 新增5片未到 → 缺片等待，不因单页截断假完整
        assert len(result["missing"]) == 25


def test_R32_index_uses_reporting_org(harness: Harness):
    from apps.seed import HOSPITAL_PACK_APPROVED
    from domains.pathology import pipeline
    from domains.pathology.models import PathologyReportVersion
    harness.publish_pack(HOSPITAL_PACK_APPROVED)
    acq = harness.register_scan("090001")["acquisition_id"]
    harness.resolve(acq)
    case = harness.case_by_no("0900001")
    with harness.sf() as db:
        for r in db.query(PathologyReportVersion).filter_by(case_id=case.id):
            r.report_org_raw = "synthetic-report-org"
        db.flush()
        rt = plugin_registry.build_runtime(db, harness.tenant_id)
        rt.dictionaries["report_org_insurance_code@demo-v1"][
            "synthetic-report-org"] = "H9999999999X"
        idx = pipeline.build_index(db, harness.tenant_id, db.get(type(case), case.id), rt)
        assert idx is not None
        assert "H9999999999X" in idx.serialized_value, idx.display_value
        # 申请机构（YLV21051）不得出现在索引串中
        assert "YLV21051" not in idx.serialized_value


def test_R33_identity_requires_number():
    from domains.pathology.identity import decide_identity
    p = SimpleNamespace(id_type_raw="01", id_no_full_ref=None, _id_no_plain=None)
    outcome = decide_identity(pis_identity_card=None, his_patients=[p],
                              approved_type_map={"01": "111"},
                              policy={"fallback_enabled": False},
                              evaluated_at="2026-10-01T00:00:00Z")
    assert outcome.patient_state != "CONFIRMED"
    assert outcome.wait_reason == "ID_NUMBER_MISSING"
    # 有号码 → 可确认
    p2 = SimpleNamespace(id_type_raw="01", id_no_full_ref="ref:1",
                         _id_no_plain="110101195001011239")
    ok = decide_identity(pis_identity_card="110101195001011239", his_patients=[p2],
                         approved_type_map={"01": "111"},
                         policy={"fallback_enabled": False},
                         evaluated_at="2026-10-01T00:00:00Z")
    assert ok.patient_state == "CONFIRMED"


# ================================================================ W10 骨架
def test_R37_unapproved_dictionary_not_loaded_by_pack(harness: Harness):
    from apps.seed import HOSPITAL_PACK_APPROVED
    from core_db.models import DictionaryRevision
    with harness.sf() as db:
        db.add(DictionaryRevision(id=new_uuid(), tenant_id=harness.tenant_id,
               namespace="id_type_ga", version="unapproved-round2", source="target",
               items=[{"code": "01", "target": "111"}], approved_by=""))
        db.commit()
    pack = copy.deepcopy(HOSPITAL_PACK_APPROVED)
    pack["approved_dictionaries"]["id_type_ga"] = "unapproved-round2"
    harness.publish_pack(pack)
    with harness.sf() as db:
        rt = plugin_registry.build_runtime(db, harness.tenant_id)
        assert rt.dict_map("id_type_ga") is None


def test_R38_production_workflow_publish_guarded(harness: Harness):
    from apps.seed import pathology_push_workflow
    dsl = pathology_push_workflow()
    dsl["workflow_key"] = "round2-production-guard"
    dsl["production_enabled"] = True
    r = harness.core.post("/api/v1/workflows", json={"dsl": dsl, "publish": True},
                          headers=harness.admin_headers())
    assert r.status_code == 409, r.text
    # 非生产启用不受守卫影响
    dsl2 = pathology_push_workflow()
    dsl2["workflow_key"] = "round2-normal"
    dsl2["production_enabled"] = False
    r2 = harness.core.post("/api/v1/workflows", json={"dsl": dsl2, "publish": True},
                           headers=harness.admin_headers())
    assert r2.status_code == 200, r2.text


def test_W10_real_destination_send_blocked_by_open_blockers(harness: Harness):
    """W10骨架：真实目的地（is_mock=False）在OPEN阻断下拒绝发送；Mock不受影响。"""
    from core_db.models import Destination as Dest, ExternalBlocker, StepRun, WorkflowRevision
    from workflow_core.dag import create_run, materialize_steps
    payload = {"submission_key": "k", "domain": "pathology",
               "index": {"serialized": "x"}, "case": {"source_case_id": "x"},
               "slides": [], "provenance": {}}
    with harness.sf() as db:
        assert db.query(ExternalBlocker).filter_by(
            tenant_id=harness.tenant_id, status="OPEN").count() > 0
        real = Dest(id=new_uuid(), tenant_id=harness.tenant_id, code="real-mi",
                    mode="PUSH", capabilities=["submit"], is_mock=False)
        db.add(real)
        pr = _package(db, harness, payload)
        db.commit()
        real_id = real.id
    dsl = {"nodes": [{"id": "send", "type": "deliver.push",
                      "plugin_ref": {"key": "destination.mock_http"}}],
           "edges": []}
    with harness.sf() as db:
        wf = db.query(WorkflowRevision).first()
        run = create_run(db, harness.tenant_id, wf.id, trigger="test",
                         context={"package_revision_id": pr.id})
        materialize_steps(db, run, dsl)
        step = db.query(StepRun).filter_by(run_id=run.id, node_id="send").first()
        mock = db.query(Dest).filter_by(tenant_id=harness.tenant_id,
                                        code="mock-push").first()
        mock.status = "DISABLED"
        db.commit()
        ctx = plugin_registry.HandlerCtx(db, harness.tenant_id, run, step, {})
        outcome = plugin_registry.h_deliver_push(ctx)
        assert outcome.state == "FAILED"
        assert outcome.error["code"] == "PROFILE_UNAPPROVED"
        # 未对真实目的地产生任何提交/尝试副作用
        assert db.query(Submission).filter_by(destination_id=real_id).count() == 0
        assert db.query(DeliveryAttempt).count() == 0


# ================================================================ 第2轮提前完成项（原W07—W14范围）
def test_R10_partition_pages_until_last_page_proven(harness: Harness):
    """G17子集：205条>单页200，翻页读完且末页证明才可声明完整覆盖。"""
    import copy as _copy
    from core_db.models import SourceReconciliationRun
    wh = plugin_registry.synthetic_warehouse()
    base = _copy.deepcopy(wh.rows["Pitaya"][0])
    wh.rows["Pitaya"] = [{**base, "PathologyID": f"{i:07d}"} for i in range(205)]
    with harness.sf() as db:
        ci = db.query(ConnectorInstance).filter_by(
            plugin_id="pis.pitaya.sqlserver").first()
        job = Job(id=new_uuid(), tenant_id=harness.tenant_id,
                  job_type="pis.partition_reconcile", dedup_key=new_uuid(),
                  payload={"connector_id": ci.id, "key_from": "0", "key_to": "99999999"})
        harness.worker._j_pis_partition(db, job)
        run = (db.query(SourceReconciliationRun)
               .order_by(SourceReconciliationRun.started_at.desc()).first())
        assert run.summary["rows"] == 205, run.summary
        assert run.summary["pages"] >= 2
        assert run.summary["last_page_proven"] is True
        assert run.complete_coverage is True
        # 单页截断不可声明完整：95条>单页由页限500伪造的场景由 last_page_proven 保证
        assert run.summary["changes"] == 205  # 全部为新发现


def test_R11_poll_changes_tie_cursor_covers_all(harness: Harness):
    """G17子集：60条同CreateTime，(CreateTime,PathologyID)复合游标不丢。"""
    import copy as _copy
    wh = plugin_registry.synthetic_warehouse()
    base = _copy.deepcopy(wh.rows["Pitaya"][0])
    wh.rows["Pitaya"] = [{**base, "PathologyID": f"{i:07d}",
                          "CreateTime": "2026-09-30T08:00:00"} for i in range(60)]
    with harness.sf() as db:
        rt = plugin_registry.build_runtime(db, harness.tenant_id)
        cp = {"since": "2026-09-30T08:00:00", "until": "2026-10-01T00:00:00"}
        seen = set()
        for _ in range(3):
            rows = rt.pis.poll_changes(rt.scope(), "cases", cp, 50)
            assert rows, "同刻窗口未耗尽时不得返回空页（至少一次语义）"
            seen.update(r["source_key"] for r in rows)
            cp["since"] = max(r["create_time"] for r in rows)
        assert len(seen) == 60, len(seen)
        assert cp.get("since_key"), "复合游标必须记录页末键"


def test_R14_complete_replay_returns_same_operation(harness: Harness):
    from tests.fault.test_storage_protocol import _register_and_upload, _put_chunks
    _, _, sess, data, _ = _register_and_upload(harness, b"round2-complete",
                                               scan_id="round2-complete")
    for _, r in _put_chunks(harness, sess, data):
        assert r.status_code == 204
    path = f"/api/v1/upload-sessions/{sess['session_id']}/complete"
    r1 = harness.storage.post(path, headers=harness.agent_headers())
    assert r1.status_code == 202
    r2 = harness.storage.post(path, headers=harness.agent_headers())
    assert r2.status_code == 202, r2.text
    assert r2.json()["data"]["operation_id"] == r1.json()["data"]["operation_id"]
    # 不产生重复 verify job
    from core_db.models import Job as _Job
    with harness.sf() as db:
        n = [_j for _j in db.query(_Job).filter_by(
            job_type="storage.verify_session").all()
            if _j.payload.get("session_id") == sess["session_id"]]
        assert len(n) == 1


def test_R15_repeated_cancel_releases_once(harness: Harness):
    from core_db.models import StorageVolume
    from tests.fault.test_storage_protocol import _register_and_upload
    _, _, a, _, _ = _register_and_upload(harness, b"a" * 10, scan_id="reserve-A2")
    _, _, b, _, _ = _register_and_upload(harness, b"b" * 10, scan_id="reserve-B2")
    url = f"/api/v1/upload-sessions/{a['session_id']}"
    for _ in range(2):
        assert harness.storage.delete(url, headers=harness.agent_headers()).status_code == 202
    with harness.sf() as db:
        assert db.get(StorageVolume, "vol-t").reserved_bytes == 20, \
            "重复取消不得把B会话的预留也扣掉"


def test_R16_available_asset_replay_returns_terminal_proof(harness: Harness):
    acq, asset, _, data, sha = _uploaded(harness, "round2-resume")
    r = harness.storage.post("/api/v1/upload-sessions", json={
        "acquisition_id": acq, "asset_id": asset, "size_bytes": len(data),
        "sha256": sha, "chunk_size": 1024}, headers={**harness.agent_headers(),
        "Idempotency-Key": "idem-round2-resume"})
    assert r.status_code == 200 and r.json()["data"]["state"] == "COMPLETED", r.text
    assert r.json()["data"]["created"] is False


def test_R17_unimplemented_command_rejected_not_acked_success(tmp_path, monkeypatch):
    from apps.edge_agent.service import AgentService
    agent = AgentService(watch_root=str(tmp_path / "watch"), core_url="http://unused",
                         storage_url="http://unused", token="synthetic", agent_id="synthetic",
                         local_db_path=str(tmp_path / "queue.sqlite3"))
    posted = []
    monkeypatch.setattr("apps.edge_agent.service.httpx.post",
                        lambda *a, **k: posted.append(k))
    agent._handle_command({"command_id": "pause-r2", "type": "pause", "payload": {}})
    assert getattr(agent, "paused", False) is True
    agent._handle_command({"command_id": "rot-r2", "type": "rotate_keys",
                           "payload": {}})
    assert posted[-1]["json"]["success"] is False, "未实现命令不得ACK成功"
    assert posted[-1]["json"]["result"]["code"] == "CAPABILITY_UNSUPPORTED"
    # apply_config（W13已实现）：非法载荷同样不得ACK成功
    agent._handle_command({"command_id": "cfg-r2", "type": "apply_config",
                           "payload": {"x": 1}})
    assert posted[-1]["json"]["success"] is False
    assert posted[-1]["json"]["result"]["code"] in (
        "VALIDATION_FAILED", "CAPABILITY_UNSUPPORTED")


def test_R21_connector_passes_object_scope_to_warehouse():
    from connector_sdk import SourceScope
    from sources.db_view import PitayaPisConnector
    captured = []

    class Spy:
        def fetch_all(self, template_id, params, limit, *, database="", schema=""):
            captured.append((template_id, database, schema))
            return []
    c = PitayaPisConnector(Spy(), database="PITAYA", schema="dbo")
    scope = SourceScope(tenant_id="synthetic", source_system_code="pis",
                        database="PITAYA", schema="dbo")
    c.get_slide_by_barcode(scope, "900001")
    c.get_case(scope, "9000001")
    c.list_slides(scope, "9000001")
    assert captured and all(t[1] == "PITAYA" and t[2] == "dbo" for t in captured), captured


def test_R22_oracle_constructor_respects_dsn_and_driver(monkeypatch):
    from sources.db_view.warehouse import OracleWarehouse
    monkeypatch.setenv("BLYB_ORACLE_DSN", "synthetic-only-no-network")
    # DPL-P01：构造函数现先完成 Thick 一次初始化——fake 驱动须提供
    # init_oracle_client/clientversion（与真实 oracledb 契约一致）
    monkeypatch.setitem(sys.modules, "oracledb",
                        SimpleNamespace(connect=lambda *a, **k: None,
                                        init_oracle_client=lambda **k: None,
                                        clientversion=lambda: (19, 32, 0, 0, 0)))
    try:
        OracleWarehouse()
    except Exception as exc:
        pytest.fail(f"Driver and DSN configured but constructor fails: {exc}")
    # 无DSN仍显式失败（不伪成功）
    monkeypatch.delenv("BLYB_ORACLE_DSN")
    from connector_sdk import CapabilityError
    with pytest.raises(CapabilityError):
        OracleWarehouse()


def test_R23_bigint_columns():
    from sqlalchemy.dialects import postgresql
    from core_db.models import Asset, Blob, StorageVolume, UploadSession
    for col in (Asset.__table__.c.expected_size, Blob.__table__.c.size_bytes,
                StorageVolume.__table__.c.capacity_bytes,
                StorageVolume.__table__.c.reserved_bytes,
                UploadSession.__table__.c.expected_size):
        assert str(col.type.compile(dialect=postgresql.dialect())) == "BIGINT"


def test_R24_if_range_mismatch_serves_full_body(harness: Harness):
    _, asset, _, data, _ = _uploaded(harness, "round2-if-range")
    r = harness.storage.get(f"/api/v1/assets/{asset}/content", headers={
        **harness.admin_headers(), "Range": "bytes=0-2", "If-Range": '"old-etag"'})
    assert r.status_code == 200 and r.content == data
