"""D系交换故障（07 §4 D01/D02/D03/D06/D08）+ 断网续传演示。"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402
from tests.harness import Harness  # noqa: E402



def _upload_and_register(harness: Harness, barcode: str, scan_id: str) -> dict:
    """经 storage HTTP 真实登记+上传一个小文件（生成真实asset/blob/路由）。"""
    import hashlib
    data = b"delivery-fixture-" + scan_id.encode()
    sha = hashlib.sha256(data).hexdigest()
    manifest = {
        "schema_version": "1.0", "event_id": f"ev-{scan_id}",
        "agent_id": harness.agent_id, "source_system_code": "scanner-simulator",
        "root_id": "t", "captured_at": "2026-09-30T08:00:00+08:00",
        "source_scan_id": scan_id, "source_revision": "1",
        "identity_hints": {"barcode_raw": barcode},
        "completion_evidence": {"method": "vendor_manifest",
                                "confirmed_at": "2026-09-30T08:00:00+08:00",
                                "evidence_ref": "t",
                                "source_immutable_assertion": True},
        "files": [{"client_asset_id": "c1", "relative_path": f"{scan_id}/f.wsi",
                   "role": "primary", "size_bytes": len(data), "sha256": sha}]}
    core = harness.storage
    H = harness.agent_headers()
    d = core.post("/api/v1/acquisitions", json=manifest, headers=H).json()["data"]
    sess = core.post("/api/v1/upload-sessions",
                     json={"acquisition_id": d["acquisition_id"],
                           "asset_id": d["assets"][0]["asset_id"],
                           "size_bytes": len(data), "sha256": sha,
                           "chunk_size": 8},
                     headers={**H, "Idempotency-Key": scan_id}).json()["data"]
    cs = sess["chunk_size"]
    for i in range(sess["chunk_count"]):
        chunk = data[i * cs:(i + 1) * cs]
        rr = core.put(f"/api/v1/upload-sessions/{sess['session_id']}/chunks/{i}",
                      content=chunk,
                      headers={**H, "Content-Length": str(len(chunk)),
                               "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
                               "X-Session-Epoch": str(sess["epoch"])})
        assert rr.status_code == 204, rr.text
    comp = core.post(f"/api/v1/upload-sessions/{sess['session_id']}/complete",
                     headers={**H, "X-Session-Epoch": str(sess["epoch"])})
    assert comp.status_code == 202, comp.text
    harness.worker.tick(max_jobs=10, max_steps=2)
    return {"acquisition_id": d["acquisition_id"], "manifest": manifest}


def _frozen_package(harness: Harness, *, scan_id="scan-d-ok"):
    """走批准pack成功路径冻结9000001并推送（返回 submission）。

    register_scan 直建采集不走HTTP上传，因此无 asset.available outbox 事件；
    这里按引擎等价路径直接启动 push 工作流。
    """
    from apps.seed import HOSPITAL_PACK_APPROVED
    harness.publish_pack(HOSPITAL_PACK_APPROVED)
    # whole_case 齐套：3片全部到位；900001走真实上传产生真实资产路由
    reg = None
    for bc in ("900002", "900003"):
        reg = harness.register_scan(bc, scan_id=f"{scan_id}-{bc}")
        harness.resolve(reg["acquisition_id"])
    reg1 = _upload_and_register(harness, "900001", f"{scan_id}-900001")
    harness.resolve(reg1["acquisition_id"])
    harness.mock.post("/exchange-mock/admin/reset")
    with harness.sf() as db:
        harness.worker._launch(db, harness.tenant_id, "pathology.mock_push",
                               {"acquisition_id": reg1["acquisition_id"]})
        db.commit()
    harness.tick(40)
    from core_db.models import Submission
    with harness.sf() as db:
        return (db.query(Submission)
                .filter_by(tenant_id=harness.tenant_id).first())


def test_D01_timeout_accepted_no_duplicate(harness: Harness):
    """API超时但对方已受理：UNKNOWN→查询恢复，不新建重复业务提交。"""
    from apps.seed import HOSPITAL_PACK_APPROVED
    harness.publish_pack(HOSPITAL_PACK_APPROVED)
    reg = None
    for bc in ("900001", "900002", "900003"):
        reg = harness.register_scan(bc, scan_id=f"scan-d01-{bc}")
        harness.resolve(reg["acquisition_id"])
    harness.mock.post("/exchange-mock/admin/reset")
    harness.mock.post("/exchange-mock/admin/behavior/timeout_accepted")
    with harness.sf() as db:
        harness.worker._launch(db, harness.tenant_id, "pathology.mock_push",
                               {"acquisition_id": reg["acquisition_id"]})
        db.commit()
    harness.tick(30)  # submit → 客户端超时 → UNKNOWN
    from core_db.models import Submission, DeliveryAttempt
    deadline = time.time() + 30
    states: list[str] = []
    while time.time() < deadline:
        with harness.sf() as db:
            subs = (db.query(Submission)
                    .filter_by(tenant_id=harness.tenant_id).all())
            states = [s.state for s in subs]
        if states and "UNKNOWN" in states:
            break
        time.sleep(0.5)
    # 超时受理：客户端可能短暂UNKNOWN后由回执轮询恢复；也可能已恢复
    # 语义断言：不新建重复业务提交 + 最终经证据达到 ACCEPTED/CONFIRMED
    with harness.sf() as db:
        first = (db.query(Submission)
                 .filter_by(tenant_id=harness.tenant_id).first())
        assert first is not None, "submission must exist after push attempt"
        sid, key = first.id, first.idempotency_key
    # 显式走一次查询恢复路径（幂等）
    with harness.sf() as db:
        harness.worker._j_query_unknown(db, _qjob(harness, sid))
    with harness.sf() as db:
        subs = (db.query(Submission)
                .filter_by(tenant_id=harness.tenant_id).all())
        assert len(subs) == 1 and subs[0].id == sid,             "must not create duplicate business submission"
        assert subs[0].idempotency_key == key
        assert subs[0].state in ("ACCEPTED", "CONFIRMED"), subs[0].state


def test_D02_duplicate_and_late_receipts(harness: Harness):
    """重复/乱序回执：逐项幂等；旧回执不回退新版本。"""
    sub = _frozen_package(harness)
    assert sub is not None
    core = harness.core
    key = sub.idempotency_key
    # 重复回执
    for _ in range(3):
        r = core.post(f"/api/v1/receipts/{sub.destination_id}", json={
            "receipt_id": "RCPT-DUP-001", "result": "CONFIRMED",
            "submission_key": key}, headers=harness.admin_headers())
        assert r.status_code == 200
    from core_db.models import ReceiptEvent
    with harness.sf() as db:
        n = (db.query(ReceiptEvent)
             .filter_by(external_receipt_id="RCPT-DUP-001").count())
        assert n == 1, "duplicate receipt must be idempotent"
    # 更正后旧回执到达：不回退（新submission_revision不变）
    late = core.post(f"/api/v1/receipts/{sub.destination_id}", json={
        "receipt_id": "RCPT-LATE-002", "result": "REJECTED",
        "submission_key": key}, headers=harness.admin_headers())
    assert late.status_code == 200
    with harness.sf() as db:
        s = db.get(type(sub), sub.id)
        assert s.state == "CONFIRMED", "late older receipt must not regress state"


def test_D03_assets_unreachable_not_published(harness: Harness):
    """文件未复制完成：外部视图不可见。"""
    # 直接验证交换库语义：未 publish 的批次行不可见
    from destinations.view_publication.exchange_db import ExchangeDb
    ex = ExchangeDb(os.environ["BLYB_EXCHANGE_DB"])
    batch = ex.begin_batch("pub-x", "gen-x-d03", "scope-d03", "digest")
    ex.upsert_projection(batch, "chk_info", "k1", {"v": 1})
    gen = ex.current_generation("scope-d03")
    rows, _ = ex.view_rows("chk_info", "scope-d03")
    assert gen is None and rows == [], "PREPARING rows must not be visible"
    ex.publish_batch(batch)
    rows2, _ = ex.view_rows("chk_info", "scope-d03")
    assert len(rows2) == 1
    # D04：查证恢复路径重复执行发布 → 0行生效，不产生重复代际/行
    again = ex.publish_batch(batch)
    assert again == 0, "re-published batch must affect 0 rows (generation idempotent)"
    rows3, _ = ex.view_rows("chk_info", "scope-d03")
    assert len(rows3) == 1


def test_D06_full_download_without_receipt(harness: Harness):
    """完整下载但无业务回执：显示已下载/待确认，不显示医保成功。"""
    sub = _frozen_package(harness)
    assert sub is not None
    # collector 完整下载（视图发布产物）
    xc = harness.exchange
    gen = xc.get("/exchange/v1/generations/current",
                 headers={"Authorization": "Bearer dev-collector-token"}
                 ).json()["data"]
    assert gen, "expected published generation"
    rows = xc.get("/exchange/v1/datasets/asset_info",
                  headers={"Authorization": "Bearer dev-collector-token"}
                  ).json()["data"]["rows"]
    assert rows
    route = rows[0]["payload"]["routeId"]
    r = xc.get(f"/exchange/v1/files/{route}",
               headers={"Authorization": "Bearer dev-collector-token"})
    assert r.status_code == 200
    # 无回执 → 业务确认为 False
    core = harness.core
    pubs = core.get("/api/v1/publications",
                    headers=harness.admin_headers()).json()["data"]
    assert pubs
    ev = core.get(f"/api/v1/publications/{pubs[0]['id']}/evidence",
                  headers=harness.admin_headers()).json()["data"]
    assert ev["data_visible"] is True
    assert ev["download_evidence"], "download evidence must be recorded"
    assert ev["business_confirmed"] is False or ev["receipts"], \
        "无回执时 business_confirmed 必须为 False 或明确待确认"


def test_D08_profile_field_violation_blocked(harness: Harness):
    """Profile字段超长/未知格式：阻断，不截断不改扩展名。"""
    from domains.pathology.index_codec import IndexInput, encode
    long_no = "9" * 19  # 证件号码>18
    inp = IndexInput(id_type="111", id_no=long_no, encounter_kind="1",
                     application_no="1000000001", pathology_no="9000001",
                     application_time="20260901080000",
                     org_code="H1234567890X", category="1")
    enc = encode(inp, "pathology-insurance-formal-draft")
    assert enc["status"] == "INVALID"
    assert any("id_no" in e for e in enc["errors"])
    # 未知格式：资产可归集为AVAILABLE但metadata_state默认PENDING/UNSUPPORTED
    # 不自动宣称已解析（形态由存储测试覆盖；此处断言默认状态机）
    from core_db.models import Asset
    assert Asset.state.default.arg == "STAGING"
    assert Asset.metadata_state.default.arg == "PENDING"


def test_断网续传_resume(harness: Harness):
    """传一半中断（部分分片）→ 重启续传：只补缺片，已确认片不重传。"""
    core = harness.storage
    import hashlib
    data = os.urandom(8192)
    sha = hashlib.sha256(data).hexdigest()
    manifest = {
        "schema_version": "1.0", "event_id": "ev-net-cut",
        "agent_id": harness.agent_id, "source_system_code": "scanner-simulator",
        "root_id": "t", "captured_at": "2026-09-30T08:00:00+08:00",
        "source_scan_id": "scan-netcut", "source_revision": "1",
        "identity_hints": {"barcode_raw": "900001"},
        "completion_evidence": {"method": "vendor_manifest",
                                "confirmed_at": "2026-09-30T08:00:00+08:00",
                                "evidence_ref": "t",
                                "source_immutable_assertion": True},
        "files": [{"client_asset_id": "c1", "relative_path": "scan-netcut/f.wsi",
                   "role": "primary", "size_bytes": len(data), "sha256": sha}]}
    r = core.post("/api/v1/acquisitions", json=manifest,
                  headers=harness.agent_headers())
    d = r.json()["data"]
    s = core.post("/api/v1/upload-sessions",
                  json={"acquisition_id": d["acquisition_id"],
                        "asset_id": d["assets"][0]["asset_id"],
                        "size_bytes": len(data), "sha256": sha,
                        "chunk_size": 1024},
                  headers={**harness.agent_headers(),
                           "Idempotency-Key": "netcut"})
    sess = s.json()["data"]
    H = harness.agent_headers()
    # 传前4片后"断网"
    for i in range(4):
        chunk = data[i * 1024:(i + 1) * 1024]
        rr = core.put(f"/api/v1/upload-sessions/{sess['session_id']}/chunks/{i}",
                      content=chunk,
                      headers={**H, "Content-Length": str(len(chunk)),
                               "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
                               "X-Session-Epoch": str(sess["epoch"])})
        assert rr.status_code == 204
    # "重启"后查询会话：只传缺片
    sess2 = core.get(f"/api/v1/upload-sessions/{sess['session_id']}",
                     headers=H).json()["data"]
    assert sess2["confirmed_chunk_indexes"] == [0, 1, 2, 3]
    assert 4 in sess2["missing_chunk_indexes"]
    retransmitted = []
    for i in sess2["missing_chunk_indexes"]:
        chunk = data[i * 1024:(i + 1) * 1024]
        rr = core.put(f"/api/v1/upload-sessions/{sess['session_id']}/chunks/{i}",
                      content=chunk,
                      headers={**H, "Content-Length": str(len(chunk)),
                               "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
                               "X-Session-Epoch": str(sess2["epoch"])})
        retransmitted.append(i)
        assert rr.status_code == 204
    assert set(retransmitted) == set(sess2["missing_chunk_indexes"])
    comp = core.post(f"/api/v1/upload-sessions/{sess['session_id']}/complete",
                     headers={**H, "X-Session-Epoch": str(sess2["epoch"])})
    assert comp.status_code == 202
    harness.worker.tick(max_jobs=10, max_steps=2)
    op = core.get(f"/api/v1/operations/{comp.json()['data']['operation_id']}",
                  headers=H).json()["data"]
    assert op["state"] == "SUCCEEDED" and op["result"]["sha256"] == sha


def test_H09_receipt_poll_exponential_backoff(harness: Harness, monkeypatch):
    """两轮无回执：重排为新作业行，available_at 间隔指数增长且 poll_count 递增。

    修复前缺陷（H-09实现审查发现）：重排复用原dedup键 → enqueue 去重提前返回
    （payload/poll_count 不更新），且原作业随后被 finish_job 置 SUCCEEDED，
    轮询链第一轮后即中断。
    """
    from apps.seed import HOSPITAL_PACK_APPROVED
    from core_db.models import Job, Submission
    from core_db.engine import utcnow
    from contracts import JobState
    # Mock行为经客户端 X-Mock-Behavior 头注入（h_deliver_push 读此环境变量）
    monkeypatch.setenv("BLYB_MOCK_BEHAVIOR", "no_receipt")
    harness.publish_pack(HOSPITAL_PACK_APPROVED)
    reg = None
    for bc in ("900001", "900002", "900003"):
        reg = harness.register_scan(bc, scan_id=f"scan-h09-{bc}")
        harness.resolve(reg["acquisition_id"])
    harness.mock.post("/exchange-mock/admin/reset")
    with harness.sf() as db:
        harness.worker._launch(db, harness.tenant_id, "pathology.mock_push",
                               {"acquisition_id": reg["acquisition_id"]})
        db.commit()
    harness.tick(40)

    def _sub() -> Submission:
        with harness.sf() as db:
            return (db.query(Submission)
                    .filter_by(tenant_id=harness.tenant_id)
                    .order_by(Submission.created_at.desc()).first())

    sub = _sub()
    assert sub is not None and sub.state in ("ACCEPTED", "UNKNOWN"), sub.state

    def _poll_jobs(sid):
        with harness.sf() as db:
            rows = (db.query(Job)
                    .filter_by(tenant_id=harness.tenant_id,
                               job_type="destination.poll_receipts").all())
            mine = [r for r in rows if r.dedup_key.split("#")[0].startswith(sid)]
            by_gen = {}   # 归一代次：{sid}→gen0, {sid}#pollN→pollN
            for r in mine:
                gen = r.dedup_key[len(sid):]
                gen = gen.split("#")[1] if gen.startswith("#") else "poll0"
                by_gen[(gen, r.state)] = r
            return by_gen, rows

    def _run_pending(sid: str, key_prefix: str):
        """把指定dedup前缀的轮询作业改为立即可执行并驱动一轮tick。

        R08 后 dedup_key = 业务键#payload摘要（事件代次），轮询链各代作业
        键形如 `{sid}`、`{sid}#poll1`...（前缀匹配）。返回强制时刻（UTC）——
        重排作业的 available_at 以此为基准断言退避间隔，不受 tick 期间
        h_receipt_wait 对 mock 的真实HTTP轮询耗时影响。
        """
        t0 = utcnow()
        with harness.sf() as db:
            job = (db.query(Job)
                   .filter_by(tenant_id=harness.tenant_id,
                              job_type="destination.poll_receipts",
                              state=JobState.PENDING)
                   .filter(Job.dedup_key.startswith(key_prefix)).first())
            assert job is not None, f"expected pending poll job ~{key_prefix}"
            job.available_at = t0
            db.commit()
        harness.tick(2)
        return t0

    t0 = _run_pending(sub.id, sub.id)
    jobs, _ = _poll_jobs(sub.id)
    j1 = jobs.get(("poll1", JobState.PENDING))
    assert j1 is not None, f"第一轮后应重排 poll1（实际：{list(jobs)}）"
    assert j1.payload["poll_count"] == 1
    d1 = (j1.available_at - t0).total_seconds()
    assert d1 >= 1.0, f"第一轮退避应≥1s，实际{d1}"

    t1 = _run_pending(sub.id, f"{sub.id}#poll1")
    jobs2, _ = _poll_jobs(sub.id)
    j2 = jobs2.get(("poll2", JobState.PENDING))
    assert j2 is not None, f"第二轮后应重排 poll2（实际：{list(jobs2)}）"
    assert j2.payload["poll_count"] == 2, "poll_count 必须逐轮递增"
    d2 = (j2.available_at - t1).total_seconds()
    assert d2 >= 1.9, f"第二轮退避应≥2s，实际{d2}"
    # 历代作业正常完结，链由新行延续
    assert jobs2.get(("poll1", JobState.SUCCEEDED)) is not None
    assert _sub().state != "CONFIRMED", "无回执不得显示医保业务确认"


def _qjob(harness, submission_id):
    from core_db.models import Job
    from contracts import new_uuid
    return Job(id=new_uuid(), tenant_id=harness.tenant_id,
               job_type="destination.query_unknown",
               dedup_key=f"q-{new_uuid()[:6]}",
               payload={"submission_id": submission_id})
