"""E2E：3片+1重扫 → Agent → 存储 → 条码反查 → 质量/冻结 → 双目的地 → 回执。

真实HTTP服务（uvicorn线程、随机端口）；合成数据；Mock平台非真实医保。
对应07 §8 第一版可演示完成定义的核心链路。
"""
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


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Srv:
    def __init__(self, app, port):
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        self.server = uvicorn.Server(config)
        self.port = port
        threading.Thread(target=self.server.run, daemon=True).start()
        for _ in range(50):
            try:
                httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
                return
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        raise RuntimeError("server did not start")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    db_path = tmp / "core.sqlite3"
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["BLYB_EXCHANGE_DB"] = str(tmp / "exchange.sqlite3")
    engine = make_engine()
    run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
    sf = make_session_factory(engine)
    seed(engine, sf, with_volume=False)
    # 注册存储卷（临时）
    from core_db.models import StorageVolume, Tenant
    with sf() as db:
        t = db.query(Tenant).filter_by(code="first-hospital").first()
        vol_root = tmp / "vol1"
        vol_root.mkdir()
        from storage_backend import VolumeHandle
        VolumeHandle("vol-e2e", str(vol_root), 64 * 1024 * 1024).ensure_sentinel()
        db.add(StorageVolume(id="vol-e2e", tenant_id=t.id, backend="filesystem",
                             name="vol1", root_ref=str(vol_root),
                             safety_margin_bytes=64 * 1024 * 1024, state="ACTIVE",
                             health="OK"))
        db.commit()
        tenant_id = t.id

    from apps.core_api.app import create_app as create_core
    from apps.storage_service.app import create_app as create_storage
    from apps.exchange_gateway.app import create_app as create_exchange
    from destinations.mock_http.server import create_app as create_mock
    ports = {name: _free_port() for name in ("core", "storage", "exchange", "mock")}
    servers = {
        "storage": _Srv(create_storage(session_factory=sf, engine=engine,
                                       embedded_worker=False),
                        ports["storage"]),
        "core": _Srv(create_core(session_factory=sf, engine=engine), ports["core"]),
        "exchange": _Srv(create_exchange(session_factory=sf, engine=engine,
                                         exchange_path=str(tmp / "exchange.sqlite3")),
                         ports["exchange"]),
        "mock": _Srv(create_mock(), ports["mock"]),
    }
    os.environ["BLYB_MOCK_PLATFORM_URL"] = f"http://127.0.0.1:{ports['mock']}"
    from apps.worker.engine import WorkerEngine
    worker = WorkerEngine(sf)

    # 单执行器（ADR-DEV-02）：storage 不带内嵌 worker；
    # 后台线程持续驱动同一 WorkerEngine，供 Agent 上传期间的 storage.verify 等任务
    tick_stop = threading.Event()

    def _ticker():
        while not tick_stop.is_set():
            try:
                worker.tick(max_jobs=10, max_steps=12, retry_wait_seconds=0.3)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.2)

    threading.Thread(target=_ticker, daemon=True, name="e2e-worker-ticker").start()

    yield {"sf": sf, "ports": ports, "tenant_id": tenant_id, "tmp": str(tmp),
           "worker": worker}

    tick_stop.set()
    for s in servers.values():
        s.server.should_exit = True


def _wait(cond, timeout=30, interval=0.2, msg="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = cond()
        if v:
            return v
        time.sleep(interval)
    raise AssertionError(f"timeout waiting: {msg}")


def test_full_vertical_slice(env):
    sf, ports, worker = env["sf"], env["ports"], env["worker"]
    tmp = env["tmp"]
    # ---------------- 模拟扫描：3片+1重扫（B03）
    from apps.edge_agent.scanner_simulator import demo_scenario
    watch = os.path.join(tmp, "scanner-output")
    os.makedirs(watch, exist_ok=True)
    manifests = demo_scenario(watch, size_bytes=1 * 1024 * 1024)
    assert len(manifests) == 4

    # ---------------- Agent 采集（断网恢复由故障测试覆盖；此处正常路径）
    from apps.edge_agent.service import AgentService
    agent = AgentService(
        watch_root=watch, core_url=f"http://127.0.0.1:{ports['core']}",
        storage_url=f"http://127.0.0.1:{ports['storage']}",
        token="dev-agent-token", agent_id=_agent_id(sf),
        local_db_path=os.path.join(tmp, "agent-queue.sqlite3"),
        chunk_size=512 * 1024)
    stats = agent.run_once()
    assert stats["discovered"] == 4
    # 传输可能需多轮（每轮限事件数）；跑到全部 UPLOADED
    def all_uploaded():
        rows = agent.db.q("SELECT COUNT(*) c FROM acquisition_event "
                          "WHERE state='UPLOADED'")
        return rows[0]["c"] == 4
    _wait(all_uploaded, timeout=60, msg="4 scans uploaded")

    # ---------------- worker：关联→质量→冻结→推送→发布→回执
    for _ in range(60):
        worker.tick(max_jobs=10, max_steps=12, retry_wait_seconds=0.3)
        time.sleep(0.05)
    # 数据库断言
    from core_db.models import Acquisition, Asset, Submission, Publication
    from domains.pathology.models import PathologyCase, Slide, ScanAcquisition
    with sf() as db:
        tid = env["tenant_id"]
        acqs = db.query(Acquisition).filter_by(tenant_id=tid).all()
        assert all(a.state == "FILES_COMPLETE" for a in acqs), \
            [a.state for a in acqs]
        assets = db.query(Asset).filter_by(tenant_id=tid).all()
        assert all(a.state == "AVAILABLE" for a in assets)
        # B03：3张玻片、4次扫描（900001两次）
        case = (db.query(PathologyCase)
                .filter_by(tenant_id=tid, pathology_no_raw="9000001").first())
        assert case is not None
        slides = db.query(Slide).filter_by(case_id=case.id).all()
        scans = db.query(ScanAcquisition).filter_by(tenant_id=tid).all()
        assert len(slides) == 3, f"expect 3 slides got {len(slides)}"
        assert len([s for s in scans if s.slide_id]) == 4, \
            f"expect 4 scans got {len(scans)}"
        # 索引/冻结/提交（批准的合成规则跑成功路径）
        assert case.clinically_ready is True
        assert case.files_complete is True
        subs = db.query(Submission).filter_by(tenant_id=tid).all()
        assert subs, "expected at least one submission"
        pubs = db.query(Publication).filter_by(tenant_id=tid).all()
        assert pubs and any(p.state == "PUBLISHED" for p in pubs)
        # 回执：mock sync_success 会立刻发 receipt
        def submission_confirmed():
            return [s.state for s in db.query(Submission)
                    .filter_by(tenant_id=tid).all()]
        # PUSH submission 收到回执后 CONFIRMED
        for _ in range(40):
            worker.tick(max_jobs=10, max_steps=12, retry_wait_seconds=0.3)
            time.sleep(0.1)
            with sf() as db2:
                s_states = [s.state for s in db2.query(Submission)
                            .filter_by(tenant_id=tid).all()]
                if s_states and all(x in ("CONFIRMED", "ACCEPTED") for x in s_states):
                    break
        assert all(x in ("CONFIRMED", "ACCEPTED") for x in submission_confirmed()), \
            submission_confirmed()

    # ---------------- 交换网关：视图 + 文件下载（collector token）
    xc = httpx.Client(base_url=f"http://127.0.0.1:{ports['exchange']}",
                      headers={"Authorization": "Bearer dev-collector-token"})
    gen = xc.get("/exchange/v1/generations/current").json()["data"]
    assert gen and gen["row_count"] >= 2
    rows = xc.get("/exchange/v1/datasets/chk_info").json()["data"]["rows"]
    assert any(r["payload"].get("pathologyNo") == "9000001" for r in rows)
    assets_rows = xc.get("/exchange/v1/datasets/asset_info").json()["data"]["rows"]
    assert assets_rows
    route_id = assets_rows[0]["payload"]["routeId"]
    head = xc.head(f"/exchange/v1/files/{route_id}")
    assert head.status_code == 200
    total = int(head.headers["Content-Length"])
    # Range 下载
    r206 = xc.get(f"/exchange/v1/files/{route_id}",
                  headers={"Range": f"bytes=0-{min(1023, total-1)}"})
    assert r206.status_code == 206
    assert r206.headers["Content-Range"].startswith("bytes 0-")
    # 完整下载
    r200 = xc.get(f"/exchange/v1/files/{route_id}")
    assert r200.status_code == 200 and len(r200.content) == total
    # 采集器业务确认回执（区别于下载观察）
    rc = xc.post("/exchange/v1/receipts", json={
        "receipt_id": "RCPT-E2E-001", "result": "CONFIRMED",
        "submission_key": None})
    assert rc.status_code == 200

    # ---------------- 质量队列非空场景：条码歧义（990000 双候选）
    from apps.edge_agent.scanner_simulator import write_scan
    write_scan(watch, "scan-e2e-ambiguous", "990000", size_bytes=64 * 1024)
    agent.run_once()
    _wait(lambda: (lambda r: r[0]["c"] == 5)(agent.db.q(
        "SELECT COUNT(*) c FROM acquisition_event WHERE state='UPLOADED'")),
        timeout=60, msg="ambiguous scan uploaded")
    for _ in range(30):
        worker.tick(max_jobs=10, max_steps=12, retry_wait_seconds=0.3)
        time.sleep(0.05)
    from core_db.models import QualityIssue
    with sf() as db:
        ambig = (db.query(QualityIssue)
                 .filter_by(tenant_id=tid, rule_code="QF-SOURCE-KEY",
                            state="OPEN").all())
        assert any("SOURCE_AMBIGUOUS" in json.dumps(q.context_json) for q in ambig), \
            "expected SOURCE_AMBIGUOUS quality issue for barcode 990000"


def _agent_id(sf):
    with sf() as db:
        from core_db.models import Agent
        a = db.query(Agent).filter_by(device_code="SCAN-WS-01").first()
        return a.id
