"""深度核查轮专项断言（测试计划 2026-09-30 A表）。

对应文档：docs/测试计划与执行-2026-09-30-深度核查.md
覆盖：H-01—H-08、H-11—H-13 与 B表 C-21（跨租户隔离）。
H-09 退避断言在 tests/fault/test_delivery_faults.py；H-10 在 tests/fault/test_agent_faults.py。
全部使用合成数据；Mock平台为非真实平台。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402
from tests.harness import Harness  # noqa: E402
import plugin_registry  # noqa: E402


# ---------------------------------------------------------------- H-01 映射引擎
def test_H01_mapping_regex_date_decimal():
    """regex_replace/date_format/decimal_scale 全部正确输出（修复前 regex TypeError）。"""
    from mapping_engine import apply_mapping, validate_mapping
    mapping = {"ops": [
        {"op": "regex_replace", "target": "phone_masked", "source_field": "phone",
         "pattern": r"(\d{3})\d{4}(\d{4})", "replacement": r"\1****\2"},
        {"op": "date_format", "target": "apply_date", "source_field": "applied_at",
         "from_format": "%Y-%m-%d", "to_format": "%Y%m%d"},
        {"op": "decimal_scale", "target": "amount_2f", "source_field": "amount",
         "places": 2},
        {"op": "regex_replace", "target": "code_clean", "source_field": "code",
         "pattern": r"[^0-9A-Z]", "replacement": ""},
    ]}
    assert validate_mapping(mapping) == []
    out = apply_mapping(mapping, {
        "phone": "13812345678", "applied_at": "2026-09-30", "amount": "123.4",
        "code": "a-b_9"})
    assert out["phone_masked"] == "138****5678"
    assert out["apply_date"] == "20260930"
    # 修复前实现先乘 10^-places 再 quantize，123.4 会变 1.23（数值被缩小）
    assert out["amount_2f"] == "123.40"
    assert out["code_clean"] == "A9" or out["code_clean"] == "9"  # 小写a被剔除
    # None 输入的语义
    out2 = apply_mapping(mapping, {"applied_at": None, "amount": None})
    assert out2["apply_date"] is None and out2["amount_2f"] is None


# ---------------------------------------------------------------- 存储侧公共夹具
def _mk_session(harness: Harness, data: bytes, *, chunk_size=1024, scan_id="scan-h"):
    """经 storage HTTP 登记采集+建会话，返回 (acq, asset, sess, data, sha)。"""
    sha = hashlib.sha256(data).hexdigest()
    manifest = {
        "schema_version": "1.0", "event_id": f"ev-{scan_id}",
        "agent_id": harness.agent_id, "source_system_code": "scanner-simulator",
        "root_id": "t", "captured_at": "2026-09-30T08:00:00+08:00",
        "source_scan_id": scan_id, "source_revision": "1",
        "identity_hints": {"barcode_raw": "900001"},
        "completion_evidence": {"method": "vendor_manifest",
                                "confirmed_at": "2026-09-30T08:00:00+08:00",
                                "evidence_ref": "t", "source_immutable_assertion": True},
        "files": [{"client_asset_id": "c1", "relative_path": f"{scan_id}/f.wsi",
                   "role": "primary", "size_bytes": len(data), "sha256": sha}]}
    r = harness.storage.post("/api/v1/acquisitions", json=manifest,
                             headers=harness.agent_headers())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    s = harness.storage.post("/api/v1/upload-sessions",
                             json={"acquisition_id": d["acquisition_id"],
                                   "asset_id": d["assets"][0]["asset_id"],
                                   "size_bytes": len(data), "sha256": sha,
                                   "chunk_size": chunk_size},
                             headers={**harness.agent_headers(),
                                      "Idempotency-Key": f"idem-{scan_id}"})
    assert s.status_code == 200, s.text
    return d["acquisition_id"], d["assets"][0]["asset_id"], s.json()["data"], data, sha


def _put_chunk(harness: Harness, sess, data, index: int):
    cs = sess["chunk_size"]
    chunk = data[index * cs:(index + 1) * cs]
    return harness.storage.put(
        f"/api/v1/upload-sessions/{sess['session_id']}/chunks/{index}",
        content=chunk,
        headers={**harness.agent_headers(), "Content-Length": str(len(chunk)),
                 "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
                 "X-Session-Epoch": str(sess["epoch"])})


def _reserved(harness: Harness) -> int:
    from core_db.models import StorageVolume
    with harness.sf() as db:
        vol = (db.query(StorageVolume)
               .filter_by(tenant_id=harness.tenant_id).first())
        return vol.reserved_bytes


def _session_dir(harness: Harness, session_id: str):
    from core_db.models import StorageVolume
    from storage_backend import VolumeHandle
    with harness.sf() as db:
        vol = (db.query(StorageVolume)
               .filter_by(tenant_id=harness.tenant_id).first())
        return VolumeHandle(vol.id, vol.root_ref, vol.safety_margin_bytes) \
            .session_dir(session_id)


# ---------------------------------------------------------------- H-02 预留泄漏
def test_H02_verify_failure_releases_reservation(harness: Harness):
    """合并校验失败也必须释放卷预留（修复前失败路径泄漏 reserved_bytes）。"""
    base = _reserved(harness)
    data = os.urandom(4096)
    acq, asset, sess, data, sha = _mk_session(harness, data, chunk_size=1024,
                                              scan_id="scan-h02")
    assert _reserved(harness) == base + 2 * len(data), "建会话应预留 2×size"
    for i in range(sess["chunk_count"]):
        assert _put_chunk(harness, sess, data, i).status_code == 204
    # 篡改会话期望hash → 合并校验必失败（模拟存储位翻转）
    from core_db.models import UploadSession
    with harness.sf() as db:
        row = db.get(UploadSession, sess["session_id"])
        row.expected_sha256 = hashlib.sha256(b"tampered").hexdigest()
        db.commit()
    comp = harness.storage.post(
        f"/api/v1/upload-sessions/{sess['session_id']}/complete",
        headers={**harness.agent_headers(), "X-Session-Epoch": str(sess["epoch"])})
    assert comp.status_code == 202
    harness.worker.tick(max_jobs=10, max_steps=2)
    op = harness.storage.get(
        f"/api/v1/operations/{comp.json()['data']['operation_id']}",
        headers=harness.agent_headers()).json()["data"]
    assert op["state"] == "FAILED", "篡改hash后校验必须失败"
    assert op["error"]["code"] == "FILE_HASH_MISMATCH"
    assert _reserved(harness) == base, "失败路径也必须释放预留"
    with harness.sf() as db:
        row = db.get(UploadSession, sess["session_id"])
        assert row.state == "FAILED"


# ---------------------------------------------------------------- H-03 孤儿分片恢复
def test_H03_orphan_chunk_recovery(harness: Harness):
    """已落盘未登记的分片：recover_orphans 补登记入进度；坏尺寸进隔离区。"""
    from core_db.models import StorageVolume
    from storage_backend import VolumeHandle
    data = os.urandom(4096)
    acq, asset, sess, data, sha = _mk_session(harness, data, chunk_size=1024,
                                              scan_id="scan-h03")
    assert _put_chunk(harness, sess, data, 0).status_code == 204
    # 模拟崩溃点1：分片1/2直接落盘（发布到最终路径）但DB未登记；分片3写错误尺寸
    with harness.sf() as db:
        vol_row = (db.query(StorageVolume)
                   .filter_by(tenant_id=harness.tenant_id).first())
        vol = VolumeHandle(vol_row.id, vol_row.root_ref, vol_row.safety_margin_bytes)
    for index, blob in ((1, data[1024:2048]), (2, data[2048:3072]),
                        (3, b"wrong-size")):
        path = vol.chunk_path(sess["session_id"], index)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(blob)
    from apps.storage_service import service as storage_service
    recovered = storage_service.recover_orphans(harness.sf)
    assert recovered >= 2, "两个好分片应被补登记"
    prog = harness.storage.get(
        f"/api/v1/upload-sessions/{sess['session_id']}",
        headers=harness.agent_headers()).json()["data"]
    assert 1 in prog["confirmed_chunk_indexes"]
    assert 2 in prog["confirmed_chunk_indexes"]
    assert 3 not in prog["confirmed_chunk_indexes"], "错误尺寸不得登记"
    quarantined = os.listdir(os.path.join(vol.root, "quarantine"))
    assert any(sess["session_id"] in q and q.endswith("-3") for q in quarantined), \
        "坏分片应进隔离区"


# ---------------------------------------------------------------- H-04 会话过期
def test_H04_expired_session_410(harness: Harness):
    """expires_at 生效：PUT分片/complete 均 410 SESSION_EXPIRED 且预留释放。"""
    from core_db.models import UploadSession
    data = os.urandom(2048)
    acq, asset, sess, data, sha = _mk_session(harness, data, chunk_size=1024,
                                              scan_id="scan-h04")
    assert _put_chunk(harness, sess, data, 0).status_code == 204
    base = _reserved(harness) - 2 * len(data)  # 此刻预留应为基线+2×size
    with harness.sf() as db:
        row = db.get(UploadSession, sess["session_id"])
        from datetime import timedelta
        from core_db.engine import utcnow
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    r_put = _put_chunk(harness, sess, data, 1)
    assert r_put.status_code == 410, r_put.text
    assert r_put.json()["error"]["code"] == "SESSION_EXPIRED"
    r_comp = harness.storage.post(
        f"/api/v1/upload-sessions/{sess['session_id']}/complete",
        headers={**harness.agent_headers(), "X-Session-Epoch": str(sess["epoch"])})
    assert r_comp.status_code == 410
    assert r_comp.json()["error"]["code"] == "SESSION_EXPIRED"
    assert _reserved(harness) == base, "过期释放预留"
    assert not os.path.exists(_session_dir(harness, sess["session_id"])), \
        "过期应清理暂存目录"
    with harness.sf() as db:
        assert db.get(UploadSession, sess["session_id"]).state == "EXPIRED"


# ---------------------------------------------------------------- H-05 取消会话
def test_H05_cancel_session(harness: Harness):
    """DELETE取消：ABORTED、预留归零、staging目录删除、后续分片被拒。"""
    data = os.urandom(2048)
    acq, asset, sess, data, sha = _mk_session(harness, data, chunk_size=1024,
                                              scan_id="scan-h05")
    assert _put_chunk(harness, sess, data, 0).status_code == 204
    assert os.path.exists(_session_dir(harness, sess["session_id"]))
    r = harness.storage.delete(
        f"/api/v1/upload-sessions/{sess['session_id']}?reason=test-cancel",
        headers=harness.agent_headers())
    assert r.status_code == 202
    assert r.json()["data"]["state"] == "ABORTED"
    assert _reserved(harness) == 0, "取消应释放全部预留"
    assert not os.path.exists(_session_dir(harness, sess["session_id"]))
    r2 = _put_chunk(harness, sess, data, 1)
    assert r2.status_code == 409, "ABORTED 会话拒收分片"


# ---------------------------------------------------------------- H-06 人工绑定快速通道
def test_H06_manual_bind_fast_path(harness: Harness):
    """歧义条码人工绑定后重跑resolve：维持ASSOCIATED，不再新开SOURCE_AMBIGUOUS。"""
    reg = harness.register_scan("990000")
    r1 = harness.resolve(reg["acquisition_id"])
    assert r1["state"] == "WAITING" and r1["wait_reason"] == "SOURCE_AMBIGUOUS"
    with harness.sf() as db:
        from core_db.models import QualityIssue
        ambig_before = (db.query(QualityIssue)
                        .filter_by(tenant_id=harness.tenant_id,
                                   entity_ref=f"acquisition:{reg['acquisition_id']}")
                        .all())
    # 业务复核员人工选择候选 Id=4（病例9000002）。
    # R12 契约：先读当前 decision_version，再带 If-Match 提交（缺头428）。
    r428 = harness.core.post("/api/v1/manual-decisions", json={
        "entity_ref": f"acquisition:{reg['acquisition_id']}",
        "action": "bind_slide",
        "after": {"barcode_raw": "990000", "source_slide_id": "4"},
        "reason": "缺If-Match应428"}, headers={
            "Authorization": "Bearer dev-reviewer-token"})
    assert r428.status_code == 428, r428.text
    cand = harness.core.get(
        f"/api/v1/pathology/acquisitions/{reg['acquisition_id']}"
        f"/association-candidates", headers={
            "Authorization": "Bearer dev-reviewer-token"}).json()["data"]
    version = cand["decision_version"]
    r = harness.core.post("/api/v1/manual-decisions", json={
        "entity_ref": f"acquisition:{reg['acquisition_id']}",
        "action": "bind_slide",
        "after": {"barcode_raw": "990000", "source_slide_id": "4"},
        "reason": "复核确认：蜡块标签核对，选Id=4"}, headers={
            "Authorization": "Bearer dev-reviewer-token",
            "If-Match": str(version)})
    assert r.status_code == 200, r.text
    # PIS事件唤醒等价路径：同一采集重跑 resolve → 快速通道维持绑定
    r2 = harness.resolve(reg["acquisition_id"])
    assert r2["state"] == "ASSOCIATED", r2
    case = harness.case_by_no("9000002")
    assert case is not None and case.slides_linked
    assert case.state == "ASSOCIATED"
    with harness.sf() as db:
        from core_db.models import QualityIssue
        ambig_after = (db.query(QualityIssue)
                       .filter_by(tenant_id=harness.tenant_id,
                                  entity_ref=f"acquisition:{reg['acquisition_id']}")
                       .all())
        assert len(ambig_after) == len(ambig_before), \
            "重跑不得新开问题（含SOURCE_AMBIGUOUS）"
        assert all(q.state == "RESOLVED" for q in ambig_after)


# ---------------------------------------------------------------- H-07 理由必填
def test_H07_manual_decision_reason_required(harness: Harness):
    """空reason提交manual-decision → 422（设计10§5 理由必填）。"""
    r = harness.core.post("/api/v1/manual-decisions", json={
        "entity_ref": "pathology_case:x", "action": "confirm_visit",
        "after": {}, "reason": "   "},
        headers={"Authorization": "Bearer dev-reviewer-token"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_FAILED"
    r2 = harness.core.post("/api/v1/manual-decisions", json={
        "entity_ref": "pathology_case:x", "action": "confirm_visit",
        "after": {}, "reason": ""},
        headers={"Authorization": "Bearer dev-admin-token"})
    assert r2.status_code == 422


# ---------------------------------------------------------------- H-08 回执→病例确认
def _frozen_submission(harness: Harness, scan_id="scan-h08"):
    """批准pack齐套路径：3片到位、真实上传、冻结并推送，返回Submission。"""
    from apps.seed import HOSPITAL_PACK_APPROVED
    harness.publish_pack(HOSPITAL_PACK_APPROVED)
    reg = None
    for bc in ("900002", "900003"):
        reg = harness.register_scan(bc, scan_id=f"{scan_id}-{bc}")
        harness.resolve(reg["acquisition_id"])
    data = b"h08-fixture-file"
    sha = hashlib.sha256(data).hexdigest()
    manifest = {
        "schema_version": "1.0", "event_id": f"ev-{scan_id}",
        "agent_id": harness.agent_id, "source_system_code": "scanner-simulator",
        "root_id": "t", "captured_at": "2026-09-30T08:00:00+08:00",
        "source_scan_id": scan_id, "source_revision": "1",
        "identity_hints": {"barcode_raw": "900001"},
        "completion_evidence": {"method": "vendor_manifest",
                                "confirmed_at": "2026-09-30T08:00:00+08:00",
                                "evidence_ref": "t", "source_immutable_assertion": True},
        "files": [{"client_asset_id": "c1", "relative_path": f"{scan_id}/f.wsi",
                   "role": "primary", "size_bytes": len(data), "sha256": sha}]}
    d = harness.storage.post("/api/v1/acquisitions", json=manifest,
                             headers=harness.agent_headers()).json()["data"]
    sess = harness.storage.post(
        "/api/v1/upload-sessions",
        json={"acquisition_id": d["acquisition_id"],
              "asset_id": d["assets"][0]["asset_id"],
              "size_bytes": len(data), "sha256": sha, "chunk_size": 8},
        headers={**harness.agent_headers(), "Idempotency-Key": scan_id}
    ).json()["data"]
    cs = sess["chunk_size"]
    for i in range(sess["chunk_count"]):
        chunk = data[i * cs:(i + 1) * cs]
        assert harness.storage.put(
            f"/api/v1/upload-sessions/{sess['session_id']}/chunks/{i}",
            content=chunk,
            headers={**harness.agent_headers(), "Content-Length": str(len(chunk)),
                     "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
                     "X-Session-Epoch": str(sess["epoch"])}).status_code == 204
    assert harness.storage.post(
        f"/api/v1/upload-sessions/{sess['session_id']}/complete",
        headers={**harness.agent_headers(),
                 "X-Session-Epoch": str(sess["epoch"])}).status_code == 202
    harness.worker.tick(max_jobs=10, max_steps=2)
    harness.resolve(d["acquisition_id"])
    harness.mock.post("/exchange-mock/admin/reset")
    with harness.sf() as db:
        harness.worker._launch(db, harness.tenant_id, "pathology.mock_push",
                               {"acquisition_id": d["acquisition_id"]})
        db.commit()
    harness.tick(40)
    from core_db.models import Submission
    with harness.sf() as db:
        return (db.query(Submission).filter_by(tenant_id=harness.tenant_id)
                .order_by(Submission.created_at.desc()).first())


def test_H08_confirmed_receipt_sets_target_confirmed(harness: Harness):
    """Mock平台CONFIRMED回执 → 病例 target_confirmed=True（分列状态，非文件状态）。"""
    sub = _frozen_submission(harness)
    assert sub is not None and sub.state in ("ACCEPTED", "CONFIRMED"), sub.state
    from domains.pathology.models import PathologyCase
    with harness.sf() as db:
        from core_db.models import PackageRevision
        case_id = (db.get(PackageRevision, sub.package_revision_id)
                   .provenance or {}).get("case_id")
        assert case_id, "提交包provenance应携带case_id"
        before = db.get(PathologyCase, case_id)
        assert before.target_confirmed is False
    # Mock平台（非真实）补发 CONFIRMED 回执 → 轮询任务取回
    harness.mock.post("/exchange-mock/admin/receipts/emit", params={
        "submission_key": sub.idempotency_key, "result": "CONFIRMED"})
    with harness.sf() as db:
        job = plugin_registry.enqueue(db, harness.tenant_id,
                                      "destination.poll_receipts",
                                      f"{sub.id}#manual-h08",
                                      {"submission_id": sub.id})
        db.commit()
        jid = job.id
    deadline = time.time() + 20
    while time.time() < deadline:
        harness.tick(3)
        with harness.sf() as db:
            from core_db.models import Job
            from contracts import JobState
            j = db.get(Job, jid)
            if j.state in (JobState.SUCCEEDED, JobState.FAILED):
                break
        time.sleep(0.05)
    with harness.sf() as db:
        case = db.get(PathologyCase, case_id)
        assert case.target_confirmed is True, "CONFIRMED回执必须置病例目标确认"
        assert db.get(type(sub), sub.id).state == "CONFIRMED"


# ---------------------------------------------------------------- H-11 下载审计
def test_H11_download_audited(harness: Harness):
    """operator 下载资产 → audit_event 出现 asset.download（E01）。"""
    data = os.urandom(2048)
    acq, asset, sess, data, sha = _mk_session(harness, data, chunk_size=1024,
                                              scan_id="scan-h11")
    for i in range(sess["chunk_count"]):
        assert _put_chunk(harness, sess, data, i).status_code == 204
    comp = harness.storage.post(
        f"/api/v1/upload-sessions/{sess['session_id']}/complete",
        headers={**harness.agent_headers(), "X-Session-Epoch": str(sess["epoch"])})
    assert comp.status_code == 202
    harness.worker.tick(max_jobs=10, max_steps=2)
    r = harness.storage.get(f"/api/v1/assets/{asset}/content",
                            headers={"Authorization": "Bearer dev-operator-token"})
    assert r.status_code == 200
    audits = harness.core.get("/api/v1/audit?action=asset.download",
                              headers=harness.admin_headers()).json()["data"]
    assert any(f"asset:{asset}" == a["target"] for a in audits), \
        "operator 下载必须留审计痕"


# ---------------------------------------------------------------- H-12 生产启用守卫
def test_H12_production_enabled_blocked(harness: Harness):
    """阻断清单OPEN时发布 production_enabled:true 的pack → 409 PROFILE_UNAPPROVED。"""
    blockers = harness.core.get("/api/v1/external-blockers",
                                headers=harness.admin_headers()).json()["data"]
    assert any(b["status"] == "OPEN" for b in blockers), "种子应含OPEN阻断"
    r = harness.core.post("/api/v1/hospital-pack", json={
        "content": {"production_enabled": True}, "publish": True},
        headers={"Authorization": "Bearer dev-configer-token"})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "PROFILE_UNAPPROVED"
    # 对照：非生产发布不受阻断限制
    r2 = harness.core.post("/api/v1/hospital-pack", json={
        "content": {"production_enabled": False}, "publish": True},
        headers={"Authorization": "Bearer dev-configer-token"})
    assert r2.status_code == 200


# ---------------------------------------------------------------- H-13 manifest幂等冲突
def test_H13_manifest_idempotency_conflict(harness: Harness):
    """同事件同修订不同manifest内容重复登记 → 409 IDEMPOTENCY_CONFLICT。"""
    data = b"h13-bytes"
    sha = hashlib.sha256(data).hexdigest()
    def manifest(sha_hex, size):
        return {
            "schema_version": "1.0", "event_id": "ev-h13",
            "agent_id": harness.agent_id, "source_system_code": "scanner-simulator",
            "root_id": "t", "captured_at": "2026-09-30T08:00:00+08:00",
            "source_scan_id": "scan-h13", "source_revision": "1",
            "identity_hints": {"barcode_raw": "900001"},
            "completion_evidence": {"method": "vendor_manifest",
                                    "confirmed_at": "2026-09-30T08:00:00+08:00",
                                    "evidence_ref": "t",
                                    "source_immutable_assertion": True},
            "files": [{"client_asset_id": "c1", "relative_path": "scan-h13/f.wsi",
                       "role": "primary", "size_bytes": size, "sha256": sha_hex}]}
    r1 = harness.storage.post("/api/v1/acquisitions", json=manifest(sha, len(data)),
                              headers=harness.agent_headers())
    assert r1.status_code == 200
    # 同事件同修订、不同内容（hash不同）→ 409
    r2 = harness.storage.post(
        "/api/v1/acquisitions",
        json=manifest(hashlib.sha256(b"other").hexdigest(), len(data)),
        headers=harness.agent_headers())
    assert r2.status_code == 409, r2.text
    assert r2.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    # 同内容重放 → 幂等返回同一资源
    r3 = harness.storage.post("/api/v1/acquisitions", json=manifest(sha, len(data)),
                              headers=harness.agent_headers())
    assert r3.status_code == 200
    assert r3.json()["data"]["acquisition_id"] == r1.json()["data"]["acquisition_id"]
    assert r3.json()["data"]["created"] is False


# ---------------------------------------------------------------- C-26 预期未关联玻片
def test_C26_expected_unlinked_slide_visible(harness: Harness):
    """预期清单中未关联玻片必须出现在病例详情（版本树不缺行）。"""
    reg = harness.register_scan("900006")  # 唯一命中病例9000002
    result = harness.resolve(reg["acquisition_id"])
    assert result["state"] == "ASSOCIATED"
    case = harness.case_by_no("9000002")
    assert case is not None
    detail = harness.core.get(f"/api/v1/pathology/cases/{case.id}",
                              headers=harness.admin_headers()).json()["data"]
    slides = detail["slides"]
    unlinked = [s for s in slides if s.get("expected_unlinked")]
    assert any(s["barcode_raw"] == "990000" for s in unlinked), \
        "预期但未关联的玻片990000必须以空扫描出现在详情（C-26）"
    linked = [s for s in slides if not s.get("expected_unlinked")]
    assert any(s["barcode_raw"] == "900006" and len(s["scans"]) == 1
               for s in linked)


# ---------------------------------------------------------------- C-21 跨租户隔离
def test_C21_tenant_isolation(harness: Harness):
    """第二租户读不到A租户的资产/会话/采集列表。"""
    from contracts import new_uuid
    from contracts.api import hash_token
    from core_db.models import AuthSubject, Tenant
    with harness.sf() as db:
        t2 = Tenant(id=new_uuid(), code="second-hospital", name="第二医院（合成）",
                    timezone="Asia/Shanghai")
        db.add(t2)
        db.flush()
        db.add(AuthSubject(id=new_uuid(), tenant_id=t2.id, kind="user",
                           subject_code="admin2", display_name="租户B管理员",
                           token_hash=hash_token("tenant-b-token"),
                           roles=["platform_admin", "operator"]))
        db.commit()
        tenant_b_headers = {"Authorization": "Bearer tenant-b-token"}

        data = b"c21-bytes"
        acq, asset, sess, data, sha = _mk_session(harness, data, chunk_size=1024,
                                                  scan_id="scan-c21")
        H = tenant_b_headers
        # 列表为空
        assets_b = harness.core.get("/api/v1/assets", headers=H).json()["data"]
        assert all(a["id"] != asset for a in assets_b), "租户B资产列表不得含A资产"
        acqs_b = harness.core.get("/api/v1/acquisitions", headers=H).json()["data"]
        assert acqs_b == []
        # 直取A租户资源 → 404
        r1 = harness.storage.get(f"/api/v1/assets/{asset}/content", headers=H)
        assert r1.status_code == 404
        r2 = harness.storage.get(f"/api/v1/upload-sessions/{sess['session_id']}",
                                 headers=H)
        assert r2.status_code == 404
        # 对照：A租户自身可见
        r3 = harness.storage.get(f"/api/v1/upload-sessions/{sess['session_id']}",
                                 headers=harness.agent_headers())
        assert r3.status_code == 200
