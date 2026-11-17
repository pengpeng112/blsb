"""第2轮整改正式回归·续（提前完成的 W07—W14 范围探针迁移：R10/R11/R14—R17/R21—R25/R35/R36/R40/R41）。"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import plugin_registry
from tests.harness import Harness, harness  # noqa: F401
from contracts import new_uuid
from core_db.models import ConnectorInstance, Job, UploadSession
from sources.db_view import SyntheticWarehouse


@pytest.fixture(autouse=True)
def isolated_source(monkeypatch):
    import domains.pathology.handlers as _ph
    monkeypatch.setattr(_ph, "WAREHOUSE_SINGLETON", SyntheticWarehouse())


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


# ================================================================ 盘点/游标（W09 提前）
def test_R10_partition_pages_until_last_page_proven(harness: Harness):
    """G17子集：205条>单页200，翻页读完且末页证明才可声明完整覆盖。"""
    import copy
    from core_db.models import SourceReconciliationRun
    wh = plugin_registry.synthetic_warehouse()
    base = copy.deepcopy(wh.rows["Pitaya"][0])
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
        assert run.summary["changes"] == 205


def test_R11_poll_changes_tie_cursor_covers_all(harness: Harness):
    """G17子集：60条同CreateTime，(CreateTime,PathologyID)复合游标不丢。"""
    import copy
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
            assert rows, "同刻窗口未耗尽时不得返回空页（至少一次语义）"
            seen.update(r["source_key"] for r in rows)
            cp["since"] = max(r["create_time"] for r in rows)
        assert len(seen) == 60, len(seen)
        assert cp.get("since_key"), "复合游标必须记录页末键"


# ================================================================ 存储幂等/恢复（W07 提前）
def test_R14_complete_replay_returns_same_operation(harness: Harness):
    from tests.fault.test_storage_protocol import _register_and_upload, _put_chunks
    _, _, sess, data, _ = _register_and_upload(harness, b"round2b-complete",
                                               scan_id="round2b-complete")
    for _, r in _put_chunks(harness, sess, data):
        assert r.status_code == 204
    path = f"/api/v1/upload-sessions/{sess['session_id']}/complete"
    r1 = harness.storage.post(path, headers=harness.agent_headers())
    assert r1.status_code == 202
    r2 = harness.storage.post(path, headers=harness.agent_headers())
    assert r2.status_code == 202, r2.text
    assert r2.json()["data"]["operation_id"] == r1.json()["data"]["operation_id"]
    with harness.sf() as db:
        n = [j for j in db.query(Job).filter_by(
            job_type="storage.verify_session").all()
            if j.payload.get("session_id") == sess["session_id"]]
        assert len(n) == 1, "VERIFYING期间重放不得新建verify作业"


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
    acq, asset, _, data, sha = _uploaded(harness, "round2b-resume")
    r = harness.storage.post("/api/v1/upload-sessions", json={
        "acquisition_id": acq, "asset_id": asset, "size_bytes": len(data),
        "sha256": sha, "chunk_size": 1024}, headers={**harness.agent_headers(),
        "Idempotency-Key": "idem-round2b-resume"})
    assert r.status_code == 200 and r.json()["data"]["state"] == "COMPLETED", r.text
    assert r.json()["data"]["created"] is False


def test_R40_commit_intent_durable_before_rename(harness: Harness, monkeypatch):
    from tests.fault.test_storage_protocol import _register_and_upload, _put_chunks
    from apps.storage_service import service
    from core_db.models import StorageCommitIntent
    _, _, sess, data, _ = _register_and_upload(harness, b"intent-r2b",
                                               scan_id="round2b-intent")
    for _, r in _put_chunks(harness, sess, data):
        assert r.status_code == 204
    harness.storage.post(f"/api/v1/upload-sessions/{sess['session_id']}/complete",
                         headers=harness.agent_headers())
    import storage_backend
    original = storage_backend.commit_object
    visible = []

    def inspect_then_rename(*args, **kw):
        with harness.sf() as observer:
            visible.append(observer.query(StorageCommitIntent).filter_by(
                session_id=sess["session_id"]).count())
        return original(*args, **kw)
    monkeypatch.setattr(storage_backend, "commit_object", inspect_then_rename)
    with harness.sf() as db:
        service.verify_session(db, db.get(UploadSession, sess["session_id"]))
    assert visible and visible[0] > 0, \
        "独立会话在rename前必须能看到已提交的意图"


def test_R41_dedup_clears_assembling_temp(harness: Harness):
    import os
    from core_db.models import StorageVolume
    _uploaded(harness, "round2b-dedup-A")
    _uploaded(harness, "round2b-dedup-B")  # 相同内容→共享Blob
    with harness.sf() as db:
        root = db.get(StorageVolume, "vol-t").root_ref
    assert os.listdir(os.path.join(root, "assembling")) == []


# ================================================================ Agent命令（W13 提前）
def test_R17_unimplemented_command_rejected_not_acked_success(tmp_path, monkeypatch):
    from apps.edge_agent.service import AgentService
    agent = AgentService(watch_root=str(tmp_path / "watch"), core_url="http://unused",
                         storage_url="http://unused", token="synthetic", agent_id="synthetic",
                         local_db_path=str(tmp_path / "queue.sqlite3"))
    posted = []
    monkeypatch.setattr("apps.edge_agent.service.httpx.post",
                        lambda *a, **k: posted.append(k))
    agent._handle_command({"command_id": "pause-r2b", "type": "pause", "payload": {}})
    assert getattr(agent, "paused", False) is True, "pause必须真实生效"
    agent._handle_command({"command_id": "rot-r2b", "type": "rotate_keys",
                           "payload": {}})
    last = posted[-1]["json"]
    assert last["success"] is False, "未实现命令不得ACK成功"
    assert last["result"]["code"] == "CAPABILITY_UNSUPPORTED"
    # apply_config（W13已实现）：非法载荷必须 success=False（不假ACK成功）
    agent._handle_command({"command_id": "cfg-r2b", "type": "apply_config",
                           "payload": {"x": 1}})
    last2 = posted[-1]["json"]
    assert last2["success"] is False
    assert last2["result"]["code"] in ("VALIDATION_FAILED", "CAPABILITY_UNSUPPORTED")


# ================================================================ 驱动/模型（W09/W14 提前）
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


# ================================================================ 下载条件请求（W08 提前）
def test_R24_if_range_mismatch_serves_full_body(harness: Harness):
    _, asset, _, data, _ = _uploaded(harness, "round2b-if-range")
    r = harness.storage.get(f"/api/v1/assets/{asset}/content", headers={
        **harness.admin_headers(), "Range": "bytes=0-2", "If-Range": '"old-etag"'})
    assert r.status_code == 200 and r.content == data


def test_R25_multirange_ignored_full_body(harness: Harness):
    _, asset, _, data, _ = _uploaded(harness, "round2b-multi-range")
    r = harness.storage.get(f"/api/v1/assets/{asset}/content", headers={
        **harness.admin_headers(), "Range": "bytes=0-1,3-4"})
    assert r.status_code == 200 and r.content == data
    # suffix超长裁剪为整文件
    r2 = harness.storage.get(f"/api/v1/assets/{asset}/content", headers={
        **harness.admin_headers(), "Range": f"bytes=-{len(data) + 100}"})
    assert r2.status_code == 206 and r2.content == data


# ================================================================ UI托管/CORS（W12 提前）
def test_R35_hosted_ui_assets_reachable(harness: Harness):
    import re
    r = harness.core.get("/ui/")
    assert r.status_code == 200
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', r.text)
    assert scripts, "index.html must reference built assets"
    assert scripts[0].startswith("/ui/"), scripts
    r2 = harness.core.get(scripts[0])
    assert r2.status_code == 200, f"built script {scripts[0]} -> {r2.status_code}"


def test_R36_storage_preflight_allowed_origin(harness: Harness):
    r = harness.storage.options("/api/v1/storage/volumes", headers={
        "Origin": "http://127.0.0.1:8000",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code in (200, 204)
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:8000"
