"""W08正式回归：真实 uvicorn + 真实 socket 的流中断证据。

形态：storage service 以独立进程运行（真实uvicorn/真实TCP），测试以
raw socket / httpx 流式客户端操作，断言：
- 读部分后断开 → DownloadEvidence 记录实际bytes + ABORTED（非COMPLETE）；
- 206 Range（字节精确）/ HEAD（无body）/ If-Range 匹配与错配；
- 复制中 kill 服务进程 → 无 COMPLETE 伪证据；重启后资产可读且hash一致；
- 旧URL窗口：交换库 SUPERSEDED 批次路由在真实HTTP下仍可读（线程uvicorn）。
"""
from __future__ import annotations

import hashlib
import os
import re
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest

IMPL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, IMPL)
import _bootstrap  # noqa: F401,E402

ADMIN = {"Authorization": "Bearer dev-admin-token"}


class StreamServer:
    def __init__(self, tmp_path, file_mb=24):
        self.tmp = str(tmp_path / "srv")
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join("tests", "fault", "_stream_server_proc.py"),
             self.tmp, str(file_mb)],
            cwd=IMPL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        info = {}
        for _ in range(20):
            line = self.proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                info[k] = v
            if k == "PORT":
                break
        assert "PORT" in info, info
        self.port = int(info["PORT"])
        self.asset_id = info["ASSET_ID"]
        self.sha = info["SHA256"]
        self.total = int(info["TOTAL"])
        self.db_url = info["DB_URL"]
        self.vol_root = info["VOL_ROOT"]
        self.base = f"http://127.0.0.1:{self.port}"
        for _ in range(60):
            try:
                httpx.get(f"{self.base}/health", timeout=1)
                break
            except Exception:  # noqa: BLE001
                if self.proc.poll() is not None:
                    raise RuntimeError("server died: " + self.proc.stderr.read())
                time.sleep(0.25)

    def evidence_rows(self):
        from sqlalchemy import create_engine, text
        eng = create_engine(self.db_url)
        try:
            with eng.connect() as conn:
                rows = conn.execute(text(
                    "SELECT bytes_sent, result, range_header FROM download_evidence "
                    "WHERE asset_id = :a ORDER BY rowid"), {"a": self.asset_id}).fetchall()
            return [(r[0], r[1], r[2] or "") for r in rows]
        finally:
            eng.dispose()

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            self.proc.kill()


@pytest.fixture()
def server(tmp_path):
    srv = StreamServer(tmp_path)
    yield srv
    srv.close()


def _raw_request(port: str, req: str, read_bytes: int | None = None,
                 close_after: int | None = None, timeout: float = 10.0):
    """raw socket 发送请求；read_bytes=读到多少字节即返回（含响应头+部分body）。"""
    sock = socket.create_connection(("127.0.0.1", int(port)), timeout=timeout)
    sock.sendall(req.encode("ascii"))
    got = b""
    target = read_bytes if read_bytes is not None else close_after
    while len(got) < (target or 1):
        b = sock.recv(min(65536, (target or 65536) - len(got)))
        if not b:
            break
        got += b
    sock.close()
    return got


def test_w08_partial_read_disconnect_records_aborted(server):
    """读部分后断开：证据为 ABORTED 且 bytes 在 (0,total) 区间（R26真实socket版）。"""
    req = (f"GET /api/v1/assets/{server.asset_id}/content HTTP/1.1\r\n"
           f"Host: 127.0.0.1:{server.port}\r\n"
           "Authorization: Bearer dev-admin-token\r\n"
           "Connection: close\r\n\r\n")
    _raw_request(server.port, req, read_bytes=64 * 1024)
    deadline = time.time() + 20
    row = None
    while time.time() < deadline:
        rows = [r for r in server.evidence_rows() if r[1] == "ABORTED"]
        if rows:
            row = rows[-1]
            break
        time.sleep(0.4)
    assert row is not None, server.evidence_rows()
    sent, result, _ = row
    assert result == "ABORTED"
    assert 0 < sent < server.total, (sent, server.total)


def test_w08_range_206_head_and_ifrange_raw_socket(server):
    """raw socket：206字节精确、HEAD无body带Content-Length、If-Range匹配/错配。"""
    req206 = (f"GET /api/v1/assets/{server.asset_id}/content HTTP/1.1\r\n"
              f"Host: h\r\nAuthorization: Bearer dev-admin-token\r\n"
              "Range: bytes=100-199\r\nConnection: close\r\n\r\n")
    raw = _raw_request(server.port, req206, read_bytes=10 * 1024 * 1024)
    head, _, body = raw.partition(b"\r\n\r\n")
    head_l = head.lower()
    assert b" 206 " in head.split(b"\r\n")[0], head[:200]
    assert b"content-range: bytes 100-199/" in head_l
    assert len(body) == 100, len(body)
    # 第一个4MB块的确定性内容：block[i] = (i*131)%251
    expect = bytes((((100 + j) * 131) % 251) for j in range(100))
    assert body == expect

    reqhead = (f"HEAD /api/v1/assets/{server.asset_id}/content HTTP/1.1\r\n"
               f"Host: h\r\nAuthorization: Bearer dev-admin-token\r\n"
               "Connection: close\r\n\r\n")
    raw = _raw_request(server.port, reqhead, read_bytes=64 * 1024)
    head, _, rest = raw.partition(b"\r\n\r\n")
    assert b" 200 " in head.split(b"\r\n")[0]
    assert f"content-length: {server.total}".encode() in head.lower()
    assert rest == b""  # HEAD 无 body

    etag = f'"{server.sha}"'
    req_ir_match = (f"GET /api/v1/assets/{server.asset_id}/content HTTP/1.1\r\n"
                    f"Host: h\r\nAuthorization: Bearer dev-admin-token\r\n"
                    f"Range: bytes=0-99\r\nIf-Range: {etag}\r\n"
                    "Connection: close\r\n\r\n")
    raw = _raw_request(server.port, req_ir_match, read_bytes=64 * 1024)
    head, _, body = raw.partition(b"\r\n\r\n")
    assert b" 206 " in head.split(b"\r\n")[0]  # ETag匹配 → 保留Range

    req_ir_mismatch = (f"GET /api/v1/assets/{server.asset_id}/content HTTP/1.1\r\n"
                       f"Host: h\r\nAuthorization: Bearer dev-admin-token\r\n"
                       "Range: bytes=0-99\r\nIf-Range: \"stale-etag\"\r\n"
                       "Connection: close\r\n\r\n")
    raw = _raw_request(server.port, req_ir_mismatch, read_bytes=1024 * 1024)
    head, _, _ = raw.partition(b"\r\n\r\n")
    assert b" 200 " in head.split(b"\r\n")[0]  # 错配 → 完整200（R24）


def test_w08_kill_mid_download_no_false_complete(tmp_path):
    """复制中kill服务进程：被中断下载不得出现 COMPLETE；重启后字节/hash一致。"""
    srv = StreamServer(tmp_path / "kill", file_mb=24)
    try:
        before = srv.evidence_rows()
        with httpx.Client(base_url=srv.base, headers=ADMIN, timeout=30) as c:
            with c.stream("GET", f"/api/v1/assets/{srv.asset_id}/content") as resp:
                it = resp.iter_bytes(1024 * 1024)
                read = 0
                for b in it:
                    read += len(b)
                    if read >= 4 * 1024 * 1024:
                        break
        srv.proc.kill()
        srv.proc.wait(timeout=10)
        after = srv.evidence_rows()
        completes = [r for r in after if r[1] == "COMPLETE"]
        assert not completes, (before, after)  # 未完成下载不写COMPLETE
    finally:
        if srv.proc.poll() is None:
            srv.proc.kill()
    # 重启同库同卷的新实例（模拟进程重启）
    srv2 = _restart_stream_server(srv)
    try:
        with httpx.Client(base_url=srv2.base, headers=ADMIN, timeout=60) as c:
            h = hashlib.sha256()
            n = 0
            with c.stream("GET", f"/api/v1/assets/{srv2.asset_id}/content") as resp:
                assert resp.status_code == 200
                for b in resp.iter_bytes(1024 * 1024):
                    h.update(b)
                    n += len(b)
            assert n == srv2.total
            assert h.hexdigest() == srv2.sha  # 对象未被中断破坏
            rows = srv2.evidence_rows()
            assert any(r[1] == "COMPLETE" and r[0] == srv2.total for r in rows), rows
    finally:
        srv2.close()


def _restart_stream_server(old: StreamServer) -> StreamServer:
    """同一 tmp 上再起一个实例（复用DB/卷），模拟进程重启。"""
    new = object.__new__(StreamServer)
    new.tmp = old.tmp
    new.proc = subprocess.Popen(
        [sys.executable, os.path.join("tests", "fault", "_stream_server_proc.py"),
         old.tmp, "24"],
        cwd=IMPL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    info = {}
    k = ""
    for _ in range(20):
        line = new.proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            info[k] = v
        if k == "PORT":
            break
    assert "PORT" in info, info
    new.port = int(info["PORT"])
    new.asset_id = old.asset_id
    new.sha = old.sha
    new.total = old.total
    new.db_url = old.db_url
    new.vol_root = old.vol_root
    new.base = f"http://127.0.0.1:{new.port}"
    for _ in range(60):
        try:
            httpx.get(f"{new.base}/health", timeout=1)
            break
        except Exception:  # noqa: BLE001
            if new.proc.poll() is not None:
                raise RuntimeError("restart died: " + new.proc.stderr.read())
            time.sleep(0.25)
    return new


def test_w08_exchange_old_url_window_real_http(tmp_path):
    """旧URL窗口（线程uvicorn真实HTTP）：SUPERSEDED批次路由在保留窗口仍可读。"""
    import uvicorn
    from apps.exchange_gateway.app import create_app as create_exchange
    from core_db import make_engine, make_session_factory, run_migrations
    import plugin_registry
    from apps.seed import seed
    from core_db.models import (Acquisition, Asset, Blob, SourceSystem, StorageVolume,
                                Tenant)

    db_url = f"sqlite:///{tmp_path / 'core.sqlite3'}"
    os.environ["BLYB_DATABASE_URL"] = db_url
    engine = make_engine(db_url)
    run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
    sf = make_session_factory(engine)
    seed(engine, sf, with_volume=False)
    ex_path = str(tmp_path / "ex.sqlite3")
    # 核心库登记真实 Asset/Blob（R42健康检查走核心库）
    obj = str(tmp_path / "obj.bin")
    payload = os.urandom(4096)
    with open(obj, "wb") as f:
        f.write(payload)
    obj_sha = hashlib.sha256(payload).hexdigest()
    with sf() as db:
        t = db.query(Tenant).filter_by(code="first-hospital").first()
        src = db.query(SourceSystem).filter_by(tenant_id=t.id,
                                               code="scanner-simulator").first()
        acq = Acquisition(id="acq-w8x", tenant_id=t.id, source_system_id=src.id,
                          external_event_id="ev-w8x", source_revision="1",
                          source_scan_id="scan-w8x", manifest={}, manifest_digest="d",
                          completion_evidence={}, state="FILES_COMPLETE")
        db.add(acq)
        blob = Blob(id="blob-w8x", tenant_id=t.id, size_bytes=len(payload),
                    sha256=obj_sha, state="AVAILABLE", verified_at=None)
        db.add(blob)
        db.flush()
        asset = Asset(id="asset-X", tenant_id=t.id, acquisition_id=acq.id,
                      client_asset_id="c", role="primary", original_name="o.bin",
                      relative_path="w8x/o.bin", media_type="application/octet-stream",
                      format_hint="", expected_size=len(payload), expected_sha256=obj_sha,
                      blob_id=blob.id, state="AVAILABLE")
        db.add(asset)
        db.commit()
        tenant_id = t.id

    app = create_exchange(session_factory=sf, engine=engine, exchange_path=ex_path)
    # 直接构造两批发布：batch1(asset X) 发布后被 batch2 supersede
    from destinations.view_publication.exchange_db import ExchangeDb
    exdb = ExchangeDb(ex_path)
    scope = "first-hospital::dest-mock::pathology"
    b1 = exdb.begin_batch("pub-1", "gen-1", scope, manifest_digest="d1", tenant_id="t1")
    exdb.upsert_projection(b1, "pathology", "K1", {"k": "v1"})
    r1 = exdb.add_asset_route(b1, "asset-X", obj, media_type="application/octet-stream",
                              size_bytes=len(payload), sha256=obj_sha)
    exdb.mark_asset_reachable(r1)
    exdb.publish_batch(b1)
    b2 = exdb.begin_batch("pub-2", "gen-2", scope, manifest_digest="d2", tenant_id="t1")
    exdb.upsert_projection(b2, "pathology", "K2", {"k": "v2"})
    exdb.publish_batch(b2)  # b1 → SUPERSEDED
    exdb.close()

    from tests.harness import free_port
    port = free_port()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    try:
        headers = {"Authorization": "Bearer dev-collector-token"}
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=headers,
                          timeout=10) as c:
            r = c.get(f"/exchange/v1/files/{r1}")
            assert r.status_code == 200, r.text
            assert r.content == payload  # 旧批次字节在保留窗口可读
            assert r.headers.get("ETag") == f'"{obj_sha}"'
    finally:
        server.should_exit = True
        engine.dispose()
