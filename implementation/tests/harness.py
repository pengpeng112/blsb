"""测试装配：全新DB+种子+TestClient应用+真实mock平台（供F用例/集成/故障测试复用）。"""
from __future__ import annotations

import json
import os
import socket
import threading
import time

import httpx
import pytest
import uvicorn

import _bootstrap  # noqa: F401
from core_db import make_engine, make_session_factory, run_migrations
import plugin_registry
from apps.seed import seed
from contracts import new_uuid
from contracts.api import digest_of
from core_db.models import Acquisition, SourceSystem, StorageVolume, Tenant


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Harness:
    def __init__(self, sf, tenant_id, apps, mock_port, tmp, worker, agent_id=""):
        self.sf = sf
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self.core, self.storage, self.exchange = apps
        self.mock_port = mock_port
        self.mock = httpx.Client(base_url=f"http://127.0.0.1:{mock_port}")
        self.tmp = tmp
        self.worker = worker

    # ------------------------------------------------------------ 工具
    def admin_headers(self):
        return {"Authorization": "Bearer dev-admin-token"}

    def agent_headers(self):
        return {"Authorization": "Bearer dev-agent-token"}

    def register_scan(self, barcode: str, *, scan_id: str | None = None,
                      revision: str = "1", pathology_hint: str | None = None,
                      file_bytes: bytes = b"file-bytes", role: str = "primary",
                      completion: str = "vendor_manifest") -> dict:
        """直接在DB登记采集（绕过HTTP以聚焦关联逻辑）。"""
        import hashlib
        sha = hashlib.sha256(file_bytes).hexdigest()
        manifest = {
            "schema_version": "1.0", "event_id": new_uuid(), "agent_id": "agent",
            "source_system_code": "scanner-simulator", "root_id": "test",
            "captured_at": "2026-09-30T08:00:00+08:00",
            "source_scan_id": scan_id or f"scan-{new_uuid()[:8]}",
            "source_revision": revision,
            "identity_hints": {"barcode_raw": barcode, **(
                {"pathology_no": pathology_hint} if pathology_hint else {})},
            "completion_evidence": {
                "method": completion,
                "confirmed_at": "2026-09-30T08:00:00+08:00",
                "evidence_ref": f"test:{scan_id}", "source_immutable_assertion": True},
            "files": [{"client_asset_id": new_uuid(),
                       "relative_path": f"{scan_id or 's'}/{barcode}.wsi", "role": role,
                       "size_bytes": len(file_bytes), "sha256": sha}]}
        with self.sf() as db:
            src = (db.query(SourceSystem)
                   .filter_by(tenant_id=self.tenant_id,
                              code="scanner-simulator").first())
            acq = Acquisition(
                id=new_uuid(), tenant_id=self.tenant_id,
                source_system_id=src.id, external_event_id=manifest["event_id"],
                source_revision=revision, source_scan_id=manifest["source_scan_id"],
                manifest=manifest, manifest_digest=digest_of(manifest),
                completion_evidence=manifest["completion_evidence"],
                state="FILES_COMPLETE")
            db.add(acq)
            db.commit()
            return {"acquisition_id": acq.id, "manifest": manifest}

    def resolve(self, acquisition_id: str) -> dict:
        with self.sf() as db:
            acq = db.get(Acquisition, acquisition_id)
            rt = plugin_registry.build_runtime(db, self.tenant_id)
            from domains.pathology import resolver
            result = resolver.resolve_acquisition(rt, acq)
            db.commit()
            return result

    def tick(self, times: int = 8, **kw):
        for _ in range(times):
            self.worker.tick(max_jobs=10, max_steps=12, retry_wait_seconds=0.2, **kw)
            time.sleep(0.02)

    def case_by_no(self, pathology_no: str):
        from domains.pathology.models import PathologyCase
        with self.sf() as db:
            return (db.query(PathologyCase)
                    .filter_by(tenant_id=self.tenant_id,
                               pathology_no_raw=pathology_no).first())

    def open_issues(self, entity_like: str = "") -> list:
        from core_db.models import QualityIssue
        with self.sf() as db:
            q = (db.query(QualityIssue)
                 .filter_by(tenant_id=self.tenant_id, state="OPEN"))
            if entity_like:
                q = q.filter(QualityIssue.entity_ref.contains(entity_like))
            return q.all()

    def publish_pack(self, content: dict):
        with self.sf() as db:
            from core_db.models import ConfigRevision
            last = (db.query(ConfigRevision)
                    .filter_by(tenant_id=self.tenant_id, config_type="hospital_pack")
                    .order_by(ConfigRevision.revision.desc()).first())
            for r in db.query(ConfigRevision).filter_by(
                    tenant_id=self.tenant_id, config_type="hospital_pack",
                    state="PUBLISHED").all():
                r.state = "RETIRED"
            db.add(ConfigRevision(
                id=new_uuid(), tenant_id=self.tenant_id, config_type="hospital_pack",
                owner_id="first-hospital", revision=last.revision + 1,
                content_json=content, digest=digest_of(content),
                state="PUBLISHED", created_by="test"))
            db.commit()


@pytest.fixture()
def harness(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["BLYB_EXCHANGE_DB"] = str(tmp_path / "exchange.sqlite3")
    engine = make_engine()
    run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
    sf = make_session_factory(engine)
    seed(engine, sf, with_volume=False)
    vol_root = tmp_path / "vol1"
    vol_root.mkdir()
    from storage_backend import VolumeHandle
    VolumeHandle("vol-t", str(vol_root), 32 * 1024 * 1024).ensure_sentinel()
    with sf() as db:
        t = db.query(Tenant).filter_by(code="first-hospital").first()
        db.add(StorageVolume(id="vol-t", tenant_id=t.id, backend="filesystem",
                             name="vol1", root_ref=str(vol_root),
                             safety_margin_bytes=32 * 1024 * 1024, state="ACTIVE",
                             health="OK"))
        db.commit()
        tenant_id = t.id
        from core_db.models import Agent
        agent_id = db.query(Agent).filter_by(device_code="SCAN-WS-01").first().id
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app as create_core
    from apps.storage_service.app import create_app as create_storage
    from apps.exchange_gateway.app import create_app as create_exchange
    # 关闭 storage 内嵌worker线程对测试DB的并发访问：独立无worker实例
    # RT-06 F05：E2E 需要完整默认插件集（domain.routes/台账钩子/分类统计）——
    # 显式注入 load_registry()（非旧 pathology+metrics 兼容层；生产 BLYB_PLUGINS
    # 语义不受影响——测试进程内显式传入）。
    import app_runtime as _ar
    _full_registry = _ar.load_registry()
    apps = (TestClient(create_core(session_factory=sf, engine=engine,
                                   registry=_full_registry)),
            TestClient(create_storage(session_factory=sf, engine=engine,
                                      embedded_worker=False)),
            TestClient(create_exchange(session_factory=sf, engine=engine,
                                       exchange_path=str(tmp_path / "exchange.sqlite3"))))
    mock_port = free_port()
    from destinations.mock_http.server import create_app as create_mock
    mock_app = create_mock()
    mock_app.state.storage_worker_disabled = True
    cfg = uvicorn.Config(mock_app, host="127.0.0.1", port=mock_port, log_level="error")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{mock_port}/health", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    os.environ["BLYB_MOCK_PLATFORM_URL"] = f"http://127.0.0.1:{mock_port}"
    # 禁用内嵌 storage worker：直接替换启动函数（TestClient不触发 lifespan 前已启动?）
    from apps.worker.engine import WorkerEngine
    worker = WorkerEngine(sf)
    h = Harness(sf, tenant_id, apps, mock_port, str(tmp_path), worker, agent_id)
    yield h
    server.should_exit = True
