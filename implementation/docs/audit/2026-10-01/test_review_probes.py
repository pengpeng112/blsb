"""Independent review probes, deliberately outside tests/.

Assertions express v0.4 requirements. Failures are review evidence, NOT passing
acceptance tests. Uses temporary databases and synthetic data only. Run from
implementation/: python -m pytest docs/audit/2026-10-01/test_review_probes.py -q
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

import _bootstrap
import plugin_registry
from tests.harness import harness  # noqa: F401 -- local fixture
from contracts import new_uuid
from contracts.api import digest_of, hash_token
from core_db.models import (AuthSubject, ConnectorInstance, DataPackage, Destination,
                            DownloadEvidence, Job, PackageRevision, Publication,
                            ReceiptEvent, SourceReconciliationRun, SourceSystem,
                            StorageVolume, Submission, Tenant, UploadSession)
from workflow_core.jobs import enqueue_job
from destinations.view_publication.exchange_db import ExchangeDb
from sources.db_view import SyntheticWarehouse


@pytest.fixture(autouse=True)
def isolated_source(monkeypatch):
    import domains.pathology.handlers as _ph
    monkeypatch.setattr(_ph, "WAREHOUSE_SINGLETON", SyntheticWarehouse())


def package(db, h, payload=None):
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


def submission(db, h):
    pr = package(db, h)
    dest = db.query(Destination).filter_by(tenant_id=h.tenant_id, mode="PUSH").first()
    s = Submission(id=new_uuid(), tenant_id=h.tenant_id, package_revision_id=pr.id,
                   destination_id=dest.id, snapshot_digest=pr.digest,
                   idempotency_key=new_uuid(), state="ACCEPTED", submission_revision=1)
    db.add(s)
    db.commit()
    return s


def other_subject(h, *, roles=None):
    with h.sf() as db:
        tid = new_uuid()
        db.add(Tenant(id=tid, code=f"review-{tid}", name="Synthetic tenant B"))
        db.flush()
        db.add(SourceSystem(id=new_uuid(), tenant_id=tid, code="pis", kind="database",
                            identifier_namespace="synthetic-B"))
        tok = f"review-{new_uuid()}"
        db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind="collector",
                           subject_code="review-B", token_hash=hash_token(tok),
                           roles=roles or ["service"]))
        db.commit()
    return tid, {"Authorization": f"Bearer {tok}"}


def uploaded(h, name):
    from tests.fault.test_storage_protocol import _register_and_upload, _put_chunks
    acq, asset, sess, data, sha = _register_and_upload(h, b"review-bytes", scan_id=name)
    for _, r in _put_chunks(h, sess, data):
        assert r.status_code == 204
    r = h.storage.post(f"/api/v1/upload-sessions/{sess['session_id']}/complete",
                       headers=h.agent_headers())
    assert r.status_code == 202
    h.worker.tick(max_jobs=1, max_steps=0)
    return acq, asset, sess, data, sha


def test_R01_accepted_receipt_is_not_confirmation(harness):
    with harness.sf() as db:
        s = submission(db, harness)
    r = harness.core.post(f"/api/v1/receipts/{s.destination_id}", json={
        "receipt_id": "review-accepted", "submission_key": s.idempotency_key,
        "result": "ACCEPTED"}, headers=harness.admin_headers())
    assert r.status_code == 200
    with harness.sf() as db:
        assert db.get(Submission, s.id).state == "ACCEPTED"


def test_R02_publication_receipt_must_match_publication(harness):
    with harness.sf() as db:
        pr = package(db, harness)
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
    assert r.json()["data"]["business_confirmed"] is False


def test_R03_exchange_preserves_other_cases(harness):
    ex = ExchangeDb(os.environ["BLYB_EXCHANGE_DB"])
    try:
        for key in ("case-A", "case-B"):
            b = ex.begin_batch(key, key, "review-scope", key)
            ex.upsert_projection(b, "chk_info", key, {"case": key})
            ex.publish_batch(b)
        rows, _ = ex.view_rows("chk_info", "review-scope")
        assert {r["external_key"] for r in rows} == {"case-A", "case-B"}
    finally:
        ex.close()


def test_R04_exchange_datasets_are_tenant_scoped(harness):
    ex = ExchangeDb(os.environ["BLYB_EXCHANGE_DB"])
    b = ex.begin_batch("tenant-A-publication", "review-tenant-A", "private-A", "d")
    ex.upsert_projection(b, "chk_info", "private-A-case", {"synthetic": "A"})
    ex.publish_batch(b)
    ex.close()
    _, headers = other_subject(harness)
    r = harness.exchange.get("/exchange/v1/datasets/chk_info?scope=private-A", headers=headers)
    assert r.status_code in (403, 404) or r.json()["data"]["rows"] == []


def test_R05_missing_frozen_asset_prevents_publication(harness):
    payload = {"case": {"source_case_id": "synthetic-case", "pathology_no_raw": "synthetic"},
               "slides": [{"barcode_raw": "synthetic", "scans": [{"effective": True,
                   "assets": [{"asset_id": "missing-required-asset"}]}]}]}
    with harness.sf() as db:
        pr = package(db, harness, payload)
        ctx = plugin_registry.HandlerCtx(db, harness.tenant_id,
              SimpleNamespace(context={"package_revision_id": pr.id}),
              SimpleNamespace(input_refs={"package_revision_id": pr.id}), {})
        result = plugin_registry.h_deliver_publish(ctx)
        assert result.state != "SUCCEEDED", "Missing asset was silently omitted and published"


def test_R06_superseded_url_remains_available(harness, tmp_path):
    ex = ExchangeDb(os.environ["BLYB_EXCHANGE_DB"])
    path = tmp_path / "fixed-synthetic-bytes"
    path.write_bytes(b"old-version")
    b1 = ex.begin_batch("p1", "g1", "review-retain", "d1")
    route = ex.add_asset_route(b1, new_uuid(), str(path), 11, "a" * 64, "application/octet-stream")
    ex.mark_asset_reachable(route)
    ex.publish_batch(b1)
    b2 = ex.begin_batch("p2", "g2", "review-retain", "d2")
    ex.publish_batch(b2)
    assert ex.asset_route_by_id(route) is not None


def test_R07_route_id_stable_across_process_restart(tmp_path):
    code = "from destinations.view_publication.exchange_db import ExchangeDb; import sys; e=ExchangeDb(sys.argv[1]); b=e.begin_batch('p','g','s','d'); print(e.add_asset_route(b,'fixed-asset-uuid','path',1,'a'*64,'application/octet-stream')); e.close()"
    results = []
    for seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        r = subprocess.run([sys.executable, "-c", "import _bootstrap; " + code,
                            str(tmp_path / "routes.sqlite3")], env=env,
                           capture_output=True, text=True, timeout=15, check=True)
        results.append(r.stdout.strip())
    assert results[0] == results[1], results


def test_R08_later_case_update_requeues_work(harness):
    with harness.sf() as db:
        j1 = enqueue_job(db, harness.tenant_id, "pathology.freeze_attempt", "case:c", {"case_id": "c"})
        j1.state = "SUCCEEDED"
        db.commit()
        j2 = enqueue_job(db, harness.tenant_id, "pathology.freeze_attempt", "case:c",
                         {"case_id": "c", "reason": "report corrected"})
        assert j2.state == "PENDING", "Later correction returns the completed old job"


def test_R09_new_package_revision_launches_publish_run(harness):
    with harness.sf() as db:
        r1 = harness.worker._launch(db, harness.tenant_id, "pathology.mock_publish",
                                    {"case_id": "same-case", "package_revision_id": "revision-1"})
        r1.state = "SUCCEEDED"
        db.commit()
        r2 = harness.worker._launch(db, harness.tenant_id, "pathology.mock_publish",
                                    {"case_id": "same-case", "package_revision_id": "revision-2"})
        assert r2.id != r1.id


def test_R10_partition_over_one_page_is_not_complete(harness):
    wh = plugin_registry.synthetic_warehouse()
    base = copy.deepcopy(wh.rows["Pitaya"][0])
    wh.rows["Pitaya"] = [{**base, "PathologyID": f"{i:07d}"} for i in range(205)]
    with harness.sf() as db:
        ci = db.query(ConnectorInstance).filter_by(plugin_id="pis.pitaya.sqlserver").first()
        job = Job(id=new_uuid(), tenant_id=harness.tenant_id,
                  job_type="pis.partition_reconcile", dedup_key=new_uuid(),
                  payload={"connector_id": ci.id, "key_from": "0", "key_to": "99999999"})
        harness.worker._j_pis_partition(db, job)
        run = db.query(SourceReconciliationRun).order_by(SourceReconciliationRun.started_at.desc()).first()
        assert not run.complete_coverage or run.summary["rows"] == 205, run.summary


def test_R11_pis_cursor_covers_same_timestamp(harness):
    wh = plugin_registry.synthetic_warehouse()
    base = copy.deepcopy(wh.rows["Pitaya"][0])
    wh.rows["Pitaya"] = [{**base, "PathologyID": f"{i:07d}",
                          "CreateTime": "2026-09-30T08:00:00"} for i in range(60)]
    with harness.sf() as db:
        rt = plugin_registry.build_runtime(db, harness.tenant_id)
        cp = {"since": "2026-09-30T08:00:00", "until": "2026-10-01T00:00:00"}
        seen = set()
        for _ in range(3):
            rows = rt.pis.poll_changes(rt.scope(), "cases", cp, 50)
            seen.update(r["source_key"] for r in rows)
            cp["since"] = max(r["create_time"] for r in rows)
        assert len(seen) == 60, len(seen)


def test_R12_manual_binding_requires_if_match(harness):
    acq = harness.register_scan("990000")["acquisition_id"]
    harness.resolve(acq)
    r = harness.core.post("/api/v1/manual-decisions", json={
        "entity_ref": f"acquisition:{acq}", "action": "bind_slide",
        "after": {"source_slide_id": "4", "barcode_raw": "990000"},
        "reason": "synthetic review"}, headers=harness.admin_headers())
    assert r.status_code == 428


def test_R13_manual_binding_rejects_other_tenant_acquisition(harness):
    acq = harness.register_scan("900001")["acquisition_id"]
    _, headers = other_subject(harness, roles=["business_reviewer"])
    r = harness.core.post("/api/v1/manual-decisions", json={
        "entity_ref": f"acquisition:{acq}", "action": "bind_slide",
        "after": {"source_slide_id": "1", "barcode_raw": "900001"},
        "reason": "synthetic cross tenant review"}, headers={**headers, "If-Match": "1"})
    assert r.status_code == 404, r.text


def test_R14_complete_repeated_while_verifying_is_idempotent(harness):
    from tests.fault.test_storage_protocol import _register_and_upload, _put_chunks
    _, _, sess, data, _ = _register_and_upload(harness, b"review", scan_id="review-complete")
    for _, r in _put_chunks(harness, sess, data):
        assert r.status_code == 204
    path = f"/api/v1/upload-sessions/{sess['session_id']}/complete"
    r1 = harness.storage.post(path, headers=harness.agent_headers())
    assert r1.status_code == 202
    # HTTP boundary may re-raise the database exception through TestClient.
    try:
        r2 = harness.storage.post(path, headers=harness.agent_headers())
    except Exception as exc:
        pytest.fail(f"Repeated complete raised {type(exc).__name__}: {str(exc)[:180]}")
    assert r2.status_code == 202
    assert r2.json()["data"]["operation_id"] == r1.json()["data"]["operation_id"]


def test_R15_cancel_repeated_does_not_release_others_reservation(harness):
    from tests.fault.test_storage_protocol import _register_and_upload
    _, _, a, _, _ = _register_and_upload(harness, b"a" * 10, scan_id="reserve-A")
    _, _, b, _, _ = _register_and_upload(harness, b"b" * 10, scan_id="reserve-B")
    url = f"/api/v1/upload-sessions/{a['session_id']}"
    for _ in range(2):
        assert harness.storage.delete(url, headers=harness.agent_headers()).status_code == 202
    with harness.sf() as db:
        assert db.get(StorageVolume, "vol-t").reserved_bytes == 20


def test_R16_available_asset_session_replay_succeeds(harness):
    acq, asset, _, data, sha = uploaded(harness, "review-resume")
    r = harness.storage.post("/api/v1/upload-sessions", json={
        "acquisition_id": acq, "asset_id": asset, "size_bytes": len(data),
        "sha256": sha, "chunk_size": 1024}, headers={**harness.agent_headers(),
        "Idempotency-Key": "idem-review-resume"})
    assert r.status_code == 200 and r.json()["data"]["state"] == "COMPLETED", r.text


def test_R17_unsupported_command_is_not_acknowledged_success(tmp_path, monkeypatch):
    from apps.edge_agent.service import AgentService
    agent = AgentService(watch_root=str(tmp_path / "watch"), core_url="http://unused",
                         storage_url="http://unused", token="synthetic", agent_id="synthetic",
                         local_db_path=str(tmp_path / "queue.sqlite3"))
    posted = []
    monkeypatch.setattr("apps.edge_agent.service.httpx.post", lambda *a, **k: posted.append(k))
    agent._handle_command({"command_id": "pause-review", "type": "pause", "payload": {}})
    assert posted[0]["json"]["success"] is False or getattr(agent, "paused", False)


def test_R18_branch_target_does_not_run_without_branch(harness):
    from workflow_core.dag import create_run, materialize_steps, runnable_steps
    from core_db.models import StepRun, WorkflowRevision
    dsl = {"nodes": [{"id": "resolve", "type": "resolve"}, {"id": "review", "type": "notify"}],
           "edges": [{"from": "resolve", "to": "review", "on": "needs_review"}]}
    with harness.sf() as db:
        wf = db.query(WorkflowRevision).first()
        run = create_run(db, harness.tenant_id, wf.id, trigger="synthetic", context={})
        materialize_steps(db, run, dsl)
        ready, _ = runnable_steps(db, run, dsl)
        assert "review" not in {s.node_id for s in ready}


def test_R19_unknown_plugin_does_not_fallback_to_other_handler():
    assert plugin_registry.handler_for("deliver.push", "uninstalled.vendor.plugin") is None


def test_R20_regex_execution_is_bounded():
    from mapping_engine import validate_mapping
    mapping = {"ops": [{"op": "regex_replace", "source_field": "x", "target": "y",
                         "pattern": "(a|aa)+$", "replacement": ""}]}
    if validate_mapping(mapping):
        return
    code = "import _bootstrap; from mapping_engine import apply_mapping; " + \
           f"apply_mapping({mapping!r}, {{'x': 'a'*40+'b'}})"
    try:
        subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=2, check=True)
    except subprocess.TimeoutExpired:
        pytest.fail("Accepted regex exceeded 2s deadline in isolated subprocess")


def test_R21_real_sqlserver_connector_passes_object_scope():
    from connector_sdk import SourceScope
    from sources.db_view import PitayaPisConnector
    captured = []
    class Spy:
        def fetch_all(self, template_id, params, limit, *, database="", schema=""):
            captured.append((database, schema))
            return []
    c = PitayaPisConnector(Spy(), database="PITAYA", schema="dbo")
    c.get_slide_by_barcode(SourceScope(tenant_id="synthetic", source_system_code="pis",
                                      database="PITAYA", schema="dbo"), "900001")
    assert captured == [("PITAYA", "dbo")], captured


def test_R22_oracle_can_initialize_with_configured_driver_and_dsn(monkeypatch):
    from sources.db_view.warehouse import OracleWarehouse
    monkeypatch.setenv("BLYB_ORACLE_DSN", "synthetic-only-no-network")
    monkeypatch.setitem(sys.modules, "oracledb", SimpleNamespace(connect=lambda *a, **k: None))
    try:
        OracleWarehouse()
    except Exception as exc:
        pytest.fail(f"Driver and DSN configured but constructor fails: {type(exc).__name__}: {exc}")


def test_R23_large_lengths_use_bigint_for_postgresql():
    from sqlalchemy.dialects import postgresql
    from core_db.models import Asset, Blob
    assert str(Asset.__table__.c.expected_size.type.compile(dialect=postgresql.dialect())) == "BIGINT"
    assert str(Blob.__table__.c.size_bytes.type.compile(dialect=postgresql.dialect())) == "BIGINT"


def test_R24_if_range_mismatch_returns_full_body(harness):
    _, asset, _, data, _ = uploaded(harness, "review-if-range")
    r = harness.storage.get(f"/api/v1/assets/{asset}/content", headers={
        **harness.admin_headers(), "Range": "bytes=0-2", "If-Range": '"old-etag"'})
    assert r.status_code == 200 and r.content == data


def test_R25_multirange_is_ignored_as_designed(harness):
    _, asset, _, data, _ = uploaded(harness, "review-multi-range")
    r = harness.storage.get(f"/api/v1/assets/{asset}/content", headers={
        **harness.admin_headers(), "Range": "bytes=0-1,3-4"})
    assert r.status_code == 200 and r.content == data


def test_R26_download_evidence_not_complete_before_stream(harness):
    from starlette.requests import Request
    from contracts.api import Principal, RequestContext
    _, asset, _, _, _ = uploaded(harness, "review-stream-evidence")
    req = Request({"type": "http", "method": "GET", "headers": []})
    ctx = RequestContext(req)
    ctx.principal = Principal("synthetic", harness.tenant_id, "user", "review", ["operator"])
    endpoint = next(r.endpoint for r in harness.storage.app.routes
                    if getattr(r, "path", "") == "/api/v1/assets/{asset_id}/content")

    async def _receive():
        return {"type": "http.request", "more_body": False}

    req2 = Request({"type": "http", "method": "GET", "headers": []}, receive=_receive)
    import asyncio

    async def _call():
        resp = await endpoint(asset, req2, ctx, None)
        assert resp.body_iterator is not None
        async for _chunk in resp.body_iterator:
            pass

    asyncio.run(_call())
    with harness.sf() as db:
        assert db.query(DownloadEvidence).filter_by(asset_id=asset, result="COMPLETE").count() == 1


def test_R27_enrollment_rotation_changes_secret(harness):
    body = {"enroll_token": "dev-enroll-token", "device_code": "review-device"}
    a = harness.core.post("/api/v1/agents/enroll", json=body).json()["data"]
    b = harness.core.post("/api/v1/agents/enroll", json=body).json()["data"]
    assert a["token"] != b["token"], "Token is a deterministic function of agent_id"


def test_R28_barcode_operation_finishes(harness):
    acq = harness.register_scan("900001")["acquisition_id"]
    r = harness.core.post("/api/v1/pathology/barcode-resolutions", json={"acquisition_id": acq},
                         headers={**harness.admin_headers(), "Idempotency-Key": "resolve-review"})
    assert r.status_code == 202
    op = r.json()["data"]["operation_id"]
    harness.worker.tick(max_jobs=1, max_steps=0)
    r = harness.core.get(f"/api/v1/operations/{op}", headers=harness.admin_headers())
    assert r.json()["data"]["state"] in ("SUCCEEDED", "FAILED"), r.text


def test_R29_seed_encounter_dictionary_matches_index_spec():
    from apps.seed import APPROVED_DICTIONARIES
    items = next(items for ns, _, _, items in APPROVED_DICTIONARIES if ns == "encounter_kind")
    mapping = {r["code"]: r["target"] for r in items}
    assert mapping == {"门诊": "1", "住院": "2"}, mapping


def test_R30_unique_visit_without_bridge_remains_waiting(harness):
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
        assert result["encounter"]["state"] != "CONFIRMED", result["encounter"]


def test_R31_expected_slides_beyond_page_limit_not_hidden(harness):
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
        assert not result["expected_known"] or result["expected_count"] == 25, result


def test_R32_index_uses_reporting_org_instead_of_requesting_org(harness):
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
        rt.dictionaries["report_org_insurance_code@demo-v1"]["synthetic-report-org"] = "H9999999999X"
        idx = pipeline.build_index(db, harness.tenant_id, db.get(type(case), case.id), rt)
        assert idx is not None
        assert "H9999999999X" in idx.serialized_value, idx.display_value


def test_R33_identity_type_with_missing_number_not_confirmed():
    from domains.pathology.identity import decide_identity
    p = SimpleNamespace(id_type_raw="01", id_no_full_ref=None, _id_no_plain=None)
    outcome = decide_identity(pis_identity_card=None, his_patients=[p],
              approved_type_map={"01": "111"}, policy={"fallback_enabled": False},
              evaluated_at="2026-10-01T00:00:00Z")
    assert outcome.patient_state != "CONFIRMED"


def test_R34_dry_run_does_not_publish_via_outbox(harness):
    from apps.seed import HOSPITAL_PACK_APPROVED
    harness.publish_pack(HOSPITAL_PACK_APPROVED)
    acq = harness.register_scan("090001")["acquisition_id"]
    with harness.sf() as db:
        ctx = plugin_registry.HandlerCtx(db, harness.tenant_id,
              SimpleNamespace(context={"acquisition_id": acq}),
              SimpleNamespace(input_refs={"acquisition_id": acq}), {}, dry_run=True)
        plugin_registry.h_pathology_resolve(ctx)
    harness.tick(8)
    with harness.sf() as db:
        assert db.query(Publication).count() == 0, "Dry-run emitted ordinary events and caused publication"


def test_R35_hosted_frontend_assets_are_reachable(harness):
    import re
    r = harness.core.get("/ui/")
    assert r.status_code == 200
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', r.text)
    assert scripts
    r = harness.core.get(scripts[0])
    assert r.status_code == 200, f"Built script {scripts[0]} returned {r.status_code}"


def test_R36_storage_browser_preflight_supported(harness):
    r = harness.storage.options("/api/v1/storage/volumes", headers={
        "Origin": "http://127.0.0.1:8000",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code in (200, 204)
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:8000"


def test_R37_unapproved_dictionary_is_not_loaded_by_pack(harness):
    from apps.seed import HOSPITAL_PACK_APPROVED
    from core_db.models import DictionaryRevision
    with harness.sf() as db:
        db.add(DictionaryRevision(id=new_uuid(), tenant_id=harness.tenant_id,
               namespace="id_type_ga", version="unapproved-review", source="target",
               items=[{"code": "01", "target": "111"}], approved_by=""))
        db.commit()
    pack = copy.deepcopy(HOSPITAL_PACK_APPROVED)
    pack["approved_dictionaries"]["id_type_ga"] = "unapproved-review"
    harness.publish_pack(pack)
    with harness.sf() as db:
        rt = plugin_registry.build_runtime(db, harness.tenant_id)
        assert rt.dict_map("id_type_ga") is None


def test_R38_production_workflow_is_guarded(harness):
    from apps.seed import pathology_push_workflow
    dsl = pathology_push_workflow()
    dsl["workflow_key"] = "review-production-guard"
    dsl["production_enabled"] = True
    r = harness.core.post("/api/v1/workflows", json={"dsl": dsl, "publish": True},
                         headers=harness.admin_headers())
    assert r.status_code == 409, r.text


def test_R39_old_session_cannot_complete_reclaimed_job(harness):
    from datetime import timedelta
    from core_db.engine import utcnow
    from workflow_core.jobs import claim_job, finish_job
    with harness.sf() as db:
        enqueue_job(db, harness.tenant_id, "review.fencing", "one", {})
        db.commit()
    with harness.sf() as old_db:
        old_lease = claim_job(old_db, "old", ["review.fencing"])
        with harness.sf() as new_db:
            j = new_db.get(Job, old_lease.job.id)
            j.lease_until = utcnow() - timedelta(seconds=1)
            new_db.commit()
            new_lease = claim_job(new_db, "new", ["review.fencing"])
            assert new_lease.epoch > old_lease.epoch
        assert finish_job(old_db, old_lease, success=True) is False


def test_R40_commit_intent_is_durable_before_rename(harness, monkeypatch):
    from tests.fault.test_storage_protocol import _register_and_upload, _put_chunks
    from apps.storage_service import service
    from core_db.models import StorageCommitIntent
    _, _, sess, data, _ = _register_and_upload(harness, b"intent", scan_id="review-intent")
    for _, r in _put_chunks(harness, sess, data):
        assert r.status_code == 204
    harness.storage.post(f"/api/v1/upload-sessions/{sess['session_id']}/complete",
                         headers=harness.agent_headers())
    import storage_backend
    original = storage_backend.commit_object
    visible = []
    def inspect_then_rename(*args, **kw):
        with harness.sf() as observer:
            visible.append(observer.query(StorageCommitIntent).filter_by(session_id=sess["session_id"]).count())
        return original(*args, **kw)
    monkeypatch.setattr(storage_backend, "commit_object", inspect_then_rename)
    with harness.sf() as db:
        service.verify_session(db, db.get(UploadSession, sess["session_id"]))
    assert visible and visible[0] > 0, "Independent DB reader sees no committed intent before rename"


def test_R41_content_dedup_leaves_no_assembling_file(harness):
    uploaded(harness, "review-dedup-A")
    uploaded(harness, "review-dedup-B")
    with harness.sf() as db:
        root = db.get(StorageVolume, "vol-t").root_ref
    assert os.listdir(os.path.join(root, "assembling")) == []


def test_R42_quarantined_blob_cannot_be_downloaded_by_other_asset(harness):
    from core_db.models import Asset, Blob
    _, a, _, _, _ = uploaded(harness, "review-blob-A")
    _, b, _, _, _ = uploaded(harness, "review-blob-B")
    with harness.sf() as db:
        aa, bb = db.get(Asset, a), db.get(Asset, b)
        assert aa.blob_id == bb.blob_id
        db.get(Blob, aa.blob_id).state = "QUARANTINED"
        db.commit()
    r = harness.storage.get(f"/api/v1/assets/{b}/content", headers=harness.admin_headers())
    assert r.status_code != 200


def test_R43_default_startup_has_only_one_worker(harness, monkeypatch):
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
