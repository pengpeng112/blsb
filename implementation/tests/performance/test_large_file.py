"""A01 性能：600MB+文件流式上传，独立服务进程RSS平稳；50GB需显式开关。

- 服务端在子进程中运行（真实uvicorn+HTTP），监控线程每秒采样其RSS。
- 结论记录于 docs/测试执行记录.md。合成大文件只证明传输/存储，不证明图像可用。
- 已知事实：httpx客户端侧的大对象循环引用在长传输中依赖GC回收，
  Agent端在每文件传输后主动gc（见 edge_agent/service.py）。
"""
from __future__ import annotations

import gc
import hashlib
import os
import subprocess
import sys
import threading
import time

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402

MB = 1024 * 1024
HERE = os.path.dirname(os.path.abspath(__file__))


def _proc_rss_mb(pid: int) -> float:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Process -Id {pid}).WorkingSet64"],
            capture_output=True, text=True, timeout=10).stdout.strip()
        return int(out) / MB
    except Exception:  # noqa: BLE001
        return -1


class ServerProc:
    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "_server_proc.py")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.port = None
        self.agent_id = None
        for _ in range(120):
            line = self.proc.stdout.readline()
            if line.startswith("PERF_SERVER_PORT="):
                self.port = int(line.split("=")[1].strip())
            if line.startswith("PERF_AGENT_ID="):
                self.agent_id = line.split("=", 1)[1].strip()
            if self.port and self.agent_id:
                break
        assert self.port and self.agent_id, "server did not report port/agent"
        for _ in range(50):
            try:
                httpx.get(f"http://127.0.0.1:{self.port}/health", timeout=2)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        self.peak_rss = 0.0
        self.stop = threading.Event()

        def monitor():
            while not self.stop.is_set():
                rss = _proc_rss_mb(self.proc.pid)
                if rss > 0:
                    self.peak_rss = max(self.peak_rss, rss)
                time.sleep(1.0)

        self.monitor = threading.Thread(target=monitor, daemon=True)
        self.monitor.start()

    def close(self):
        self.stop.set()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def _write_synthetic(path: str, size_mb: int, block: bytes) -> str:
    h = hashlib.sha256()
    with open(path, "wb") as f:
        for _ in range(size_mb):
            f.write(block)
            h.update(block)
    return h.hexdigest()


def _upload(server: ServerProc, tmp: str, path: str, size: int, sha: str,
            chunk_mb: int):
    import uuid as _u
    headers = {"Authorization": "Bearer dev-agent-token"}
    base = f"http://127.0.0.1:{server.port}"
    scan_id = f"perf-{_u.uuid4().hex[:8]}"
    manifest = {
        "schema_version": "1.0", "event_id": str(_u.uuid4()),
        "agent_id": server.agent_id, "source_system_code": "scanner-simulator",
        "root_id": "perf", "captured_at": "2026-09-30T08:00:00+08:00",
        "source_scan_id": scan_id, "source_revision": "1",
        "identity_hints": {"barcode_raw": "900001"},
        "completion_evidence": {"method": "vendor_manifest",
                                "confirmed_at": "2026-09-30T08:00:00+08:00",
                                "evidence_ref": "perf",
                                "source_immutable_assertion": True},
        "files": [{"client_asset_id": "c1", "relative_path": f"{scan_id}/big.wsi",
                   "role": "primary", "size_bytes": size, "sha256": sha,
                   "media_type": "application/octet-stream",
                   "format_hint": "synthetic-large"}]}
    t0 = time.time()
    with httpx.Client(base_url=base, timeout=120) as client:
        rr = client.post("/api/v1/acquisitions", json=manifest, headers=headers)
        assert rr.status_code == 200, rr.text
        d = rr.json()["data"]
        s = client.post("/api/v1/upload-sessions",
                        json={"acquisition_id": d["acquisition_id"],
                              "asset_id": d["assets"][0]["asset_id"],
                              "size_bytes": size, "sha256": sha,
                              "chunk_size": chunk_mb * MB},
                        headers={**headers, "Idempotency-Key": scan_id})
        assert s.status_code == 200, s.text
        sess = s.json()["data"]
        cs = sess["chunk_size"]
        with open(path, "rb") as f:
            for idx in range(sess["chunk_count"]):
                chunk = f.read(cs)
                put = client.put(
                    f"/api/v1/upload-sessions/{sess['session_id']}/chunks/{idx}",
                    content=chunk,
                    headers={**headers, "Content-Length": str(len(chunk)),
                             "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
                             "X-Session-Epoch": str(sess["epoch"])})
                assert put.status_code == 204, (idx, put.status_code, put.text[:200])
                del chunk
                if idx % 16 == 0:
                    gc.collect()  # Agent同款策略：长传输中周期回收客户端循环引用
        gc.collect()
        comp = client.post(
            f"/api/v1/upload-sessions/{sess['session_id']}/complete",
            headers={**headers, "X-Session-Epoch": str(sess["epoch"])})
        assert comp.status_code == 202
        op_id = comp.json()["data"]["operation_id"]
        while True:
            op = client.get(f"/api/v1/operations/{op_id}",
                            headers=headers).json()["data"]
            if op["state"] in ("SUCCEEDED", "FAILED"):
                break
            time.sleep(0.2)
        assert op["state"] == "SUCCEEDED", op
        assert op["result"]["sha256"] == sha and op["result"]["size_bytes"] == size
    return {"seconds": round(time.time() - t0, 1),
            "server_peak_rss_mb": round(server.peak_rss, 1),
            "size_mb": size // MB}


def test_A01_600mb_server_rss(tmp_path):
    server = ServerProc()
    try:
        block = os.urandom(1 * MB)
        big = os.path.join(str(tmp_path), "big600.bin")
        sha = _write_synthetic(big, 600, block)
        stats = _upload(server, str(tmp_path), big, 600 * MB, sha, chunk_mb=8)
        time.sleep(2.5)  # 采样收尾
        stats["server_peak_rss_mb"] = round(server.peak_rss, 1)
        print("A01 600MB:", stats)
        assert 0 < stats["server_peak_rss_mb"] < 300, (
            f"server peak RSS {stats['server_peak_rss_mb']}MB "
            f"(0=采样失效，>=300=疑似整文件缓冲)")
        os.remove(big)
    finally:
        server.close()


@pytest.mark.skipif(os.environ.get("BLYB_RUN_50GB") != "1",
                    reason="50GB压力需专门磁盘窗口；默认未执行（如实记录）")
def test_A01_50gb_optional(tmp_path):
    server = ServerProc()
    try:
        block = os.urandom(4 * MB)
        big = os.path.join(str(tmp_path), "big50g.bin")
        sha = _write_synthetic(big, 50 * 1024, block)
        stats = _upload(server, str(tmp_path), big, 50 * 1024 * MB, sha, chunk_mb=32)
        print("A01 50GB:", stats)
        os.remove(big)
    finally:
        server.close()
