"""W13 正式回归：Agent 目录候选人工放行完整执行链、命令幂等/超期、
apply_config 原子落地与失败保留、升级包签名验证框架。

形态：真实本地队列（sqlite）+ 真实文件系统 + httpx monkeypatch 模拟
core/storage 响应（不连接真实系统）。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402
from apps.edge_agent.service import AgentService  # noqa: E402


def _make_agent(tmp_path, *, stability_window=0) -> AgentService:
    return AgentService(
        watch_root=str(tmp_path / "watch"), core_url="http://127.0.0.1:9",
        storage_url="http://127.0.0.1:9", token="synthetic", agent_id="agent-w13",
        local_db_path=str(tmp_path / "q" / "agent-queue.sqlite3"),
        stability_window=stability_window)


def _write(root, rel, data: bytes, mtime_age=120):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    os.utime(p, (time.time() - mtime_age, time.time() - mtime_age))
    return p


def test_w13_directory_candidate_manual_release_chain(tmp_path):
    """候选→登记（hash快照）→人工放行（证据保存）→同一归集队列acquisition_event。"""
    agent = _make_agent(tmp_path)
    data = os.urandom(2048)
    _write(agent.watch_root, "day1/slideA.wsi", data)
    cands = agent.scan_directory_candidates()
    assert len(cands) == 1
    c = cands[0]
    assert c["state"] == "CANDIDATE" and c["snapshot_version"] == 1
    assert c["sha256"] == hashlib.sha256(data).hexdigest()
    rel = agent.release_directory_candidate(
        c["candidate_id"], decided_by="reviewer-zhang", reason="玻片确认完整")
    assert rel["ok"] is True, rel
    ev = agent.db.one("SELECT * FROM acquisition_event WHERE event_id=?",
                      (rel["event_id"],))
    assert ev is not None and ev["state"] == "DISCOVERED"
    manifest = json.loads(ev["manifest_json"])
    evd = json.loads(ev["completion_evidence"])
    assert evd["method"] == "manual_release"
    assert evd["decided_by"] == "reviewer-zhang" and evd["reason"]
    assert evd["snapshot_sha256"] == hashlib.sha256(data).hexdigest()
    assert evd["source_immutable_assertion"] is False  # 人工放行不冒充厂商完成断言
    assert manifest["files"][0]["sha256"] == hashlib.sha256(data).hexdigest()
    # 候选已RELEASED；重复放行拒绝
    row = agent.db.one("SELECT state FROM dir_candidate WHERE candidate_id=?",
                       (c["candidate_id"],))
    assert row["state"] == "RELEASED"
    again = agent.release_directory_candidate(
        c["candidate_id"], decided_by="x", reason="dup")
    assert again["ok"] is False and again["code"] == "INVALID_STATE"


def test_w13_candidate_content_changed_at_release_refused(tmp_path):
    """放行时内容变化→CONTENT_CHANGED；重新扫描产生新snapshot_version。"""
    agent = _make_agent(tmp_path)
    _write(agent.watch_root, "day1/x.wsi", b"v1-content")
    c1 = agent.scan_directory_candidates()[0]
    _write(agent.watch_root, "day1/x.wsi", b"v2-content-different", mtime_age=130)
    rel = agent.release_directory_candidate(
        c1["candidate_id"], decided_by="r", reason="test")
    assert rel["ok"] is False and rel["code"] == "CONTENT_CHANGED"
    assert agent.db.one("SELECT state FROM dir_candidate WHERE candidate_id=?",
                        (c1["candidate_id"],))["state"] == "CHANGED"
    c2 = agent.scan_directory_candidates()
    newest = [c for c in c2 if c["snapshot_version"] == 2]
    assert newest and newest[0]["sha256"] == hashlib.sha256(
        b"v2-content-different").hexdigest()


def test_w13_symlink_escape_rejected(tmp_path):
    """junction/symlink 越根：不登记候选，记 issue。"""
    agent = _make_agent(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    _write(str(outside), "secret.wsi", b"secret")
    link = os.path.join(agent.watch_root, "escape.wsi")
    try:
        os.symlink(os.path.join(str(outside), "secret.wsi"), link)
    except (OSError, NotImplementedError):  # Windows 无符号权限时跳过该断言组
        pytest.skip("symlink creation not permitted on this host")
    cands = agent.scan_directory_candidates()
    assert not [c for c in cands if c["relative_path"] == "escape.wsi"]
    assert agent.db.one("SELECT COUNT(*) c FROM local_issue WHERE "
                        "category='path_escape'")["c"] >= 1


def _patch_httpx_noop(monkeypatch):
    class FakeHttpx:
        @staticmethod
        def post(url, **kw):
            class R:
                status_code = 200
                def json(self):
                    return {"data": {}}
                def raise_for_status(self):
                    pass
            return R()
    import apps.edge_agent.service as svc_mod
    monkeypatch.setattr(svc_mod, "httpx", FakeHttpx)


def test_w13_command_idempotent_resend_and_expiry(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path)
    acked = []

    class FakeHttpx:
        @staticmethod
        def post(url, **kw):
            class R:
                status_code = 200
                def json(self):
                    return {"data": {}}
                def raise_for_status(self):
                    pass
            if "/ack" in url:
                acked.append(kw.get("json") or {})
            return R()

    import apps.edge_agent.service as svc_mod
    monkeypatch.setattr(svc_mod, "httpx", FakeHttpx)
    calls = []
    orig_discover = agent.discover
    monkeypatch.setattr(agent, "discover", lambda: calls.append(1))
    cmd = {"command_id": "cmd-1", "type": "rescan"}
    agent._handle_command(dict(cmd))
    agent._handle_command(dict(cmd))  # 重发
    assert len(calls) == 1, "命令重发不得重复执行"
    assert len(acked) == 1
    # 超期命令：EXPIRED，且不执行动作
    expired = {"command_id": "cmd-2", "type": "rescan",
               "expires_at": (datetime.now(timezone.utc)
                              - timedelta(minutes=1)).isoformat()}
    calls.clear()
    agent._handle_command(expired)
    assert not calls
    result = json.loads(agent.db.one(
        "SELECT result FROM command_inbox WHERE command_id='cmd-2'")["result"])
    assert result["code"] == "COMMAND_EXPIRED"


def test_w13_apply_config_digest_and_atomic_rollback(tmp_path):
    agent = _make_agent(tmp_path)
    from contracts.api import digest_of
    good = {"watch_root": agent.watch_root, "chunk_size": 8388608}
    r = agent._apply_config({"config": good, "digest": digest_of(good)})
    assert r["ok"] is True, r
    final = os.path.join(os.path.dirname(agent.db.path), "agent-config-applied.json")
    applied = json.load(open(final, encoding="utf-8"))
    assert applied["config"] == good and applied["digest"] == digest_of(good)
    # digest错配：拒绝且旧配置保留
    r2 = agent._apply_config({"config": {"watch_root": "Z:\\nope"}, "digest": "bad"})
    assert r2["ok"] is False and r2["code"] == "DIGEST_MISMATCH"
    assert json.load(open(final, encoding="utf-8"))["config"] == good
    # watch_root不存在：拒绝
    r3 = agent._apply_config({"config": {"watch_root": "Z:\\missing-dir"},
                              "digest": digest_of({"watch_root": "Z:\\missing-dir"})})
    assert r3["ok"] is False and r3["code"] == "VALIDATION_FAILED"
    # 再次成功应用产生.bak（旧配置保留）
    good2 = {"watch_root": agent.watch_root, "chunk_size": 4194304}
    r4 = agent._apply_config({"config": good2, "digest": digest_of(good2)})
    assert r4["ok"] is True and r4["backup"]
    bak = json.load(open(r4["backup"], encoding="utf-8"))
    assert bak["config"] == good


def test_w13_upgrade_signature_framework(tmp_path, monkeypatch):
    _patch_httpx_noop(monkeypatch)
    from apps.edge_agent.upgrade_verify import (sign_manifest, verify_upgrade_bundle)
    key = tmp_path / "upgrade.key"
    key.write_bytes(b"k" * 32)
    payload = b"upgrade-payload-bytes"
    manifest = {"version": "1.2.3", "created_at": "2026-10-01T00:00:00Z",
                "files": [{"path": "agent.exe", "sha256": hashlib.sha256(
                    payload).hexdigest(), "size_bytes": len(payload)}]}
    pkg = tmp_path / "pkg"
    (pkg / "").mkdir()
    (pkg / "agent.exe").write_bytes(payload)
    sig = sign_manifest(manifest, key.read_bytes())
    ok, detail = verify_upgrade_bundle(manifest, sig, str(key), base_dir=str(pkg))
    assert ok, detail
    # 篡改签名 / 篡改文件 → 拒绝
    ok2, _ = verify_upgrade_bundle(manifest, "0" * 64, str(key), base_dir=str(pkg))
    assert not ok2
    (pkg / "agent.exe").write_bytes(b"tampered")
    ok3, d3 = verify_upgrade_bundle(manifest, sig, str(key), base_dir=str(pkg))
    assert not ok3 and "mismatch" in d3
    # Agent命令层：验签通过仍拒绝安装（不假ACK成功）
    agent = _make_agent(tmp_path)
    r = agent._verify_upgrade_payload({"manifest": manifest, "signature": sig,
                                       "public_key_path": str(key)})
    assert r["ok"] is False and r["code"] == "CAPABILITY_UNSUPPORTED"
    assert r.get("signature_verified") is True
    r2 = agent._verify_upgrade_payload({"manifest": manifest, "signature": "x" * 64,
                                        "public_key_path": str(key)})
    assert r2["ok"] is False and r2["code"] == "SIGNATURE_INVALID"


def test_w13_diagnostic_real_result_not_stub(tmp_path, monkeypatch):
    _patch_httpx_noop(monkeypatch)
    agent = _make_agent(tmp_path)
    r = agent._handle_command({"command_id": "cmd-diag", "type": "diagnostic"})
    result = json.loads(agent.db.one(
        "SELECT result FROM command_inbox WHERE command_id='cmd-diag'")["result"])
    assert result["ok"] is True
    assert result["diagnostics"]["disk_free_bytes"] > 0
    assert result["diagnostics"]["watch_root"] == agent.watch_root
