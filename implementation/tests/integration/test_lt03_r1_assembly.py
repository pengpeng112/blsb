"""LT-03-R1 集成：Mock→generic 装配 / 重试零重复 POST / 闸门 CONFIRMED /
Mock 回环强制 / RESEND_ALLOWED 不被自动拾取。

全部测试调用**真实装配路径**（app_runtime.load_registry 注册适配器后经
core_handlers.perform_push 派发），不 monkeypatch 派发逻辑；网络目标一律
为本机 127.0.0.1 随机端口 Mock 服务（离线约束，真实院内地址只在
"必须被触网前拒绝"的反例中出现）。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import (ConfigRevision, DataPackage, DeliveryAttempt,  # noqa: E402
                            Destination, PackageRevision, ReportTarget,
                            Submission, Tenant)

pytestmark = pytest.mark.skipif(
    os.environ.get("BLYB_NET_TESTS", "1") != "1",
    reason="网络级测试需 BLYB_NET_TESTS=1（本机默认跑；CI 可关闭）")


class CountingHandler(BaseHTTPRequestHandler):
    """计数 + inf_pusher 行为档（__mock_code/__mock_fail_times）。"""
    hits: list[dict] = []
    fail_counts: dict[str, int] = {}

    def log_message(self, *a):  # noqa: 静音
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        CountingHandler.hits.append({
            "path": self.path,
            "key": self.headers.get("Idempotency-Key", ""),
            "auth": self.headers.get("Authorization", ""),
            "body": body})
        code = str(body.get("__mock_code", "00"))
        fail_times = int(body.get("__mock_fail_times", 0) or 0)
        key = self.headers.get("Idempotency-Key", "")
        if fail_times > 0:
            seen = CountingHandler.fail_counts.get(key, 0)
            if seen < fail_times:
                CountingHandler.fail_counts[key] = seen + 1
                code = "01"
            else:
                code = "00"
        if code == "timeout":
            time.sleep(2)
            return
        ok = code == "00"
        payload = json.dumps({"result": ok, "errorCode": code,
                              "desc": "mock", "remote_id": "R-1"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def mock_url():
    CountingHandler.hits.clear()
    CountingHandler.fail_counts.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), CountingHandler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/r1.sqlite3"
    import app_runtime
    # 真实装配：加载 generic 适配器注册（perform_push 派发依赖此注册）
    reg = app_runtime.load_registry([])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="r1-hospital", name="R1测试")
        db.add(t)
        db.commit()
        tid = t.id
    yield {"sf": sf, "engine": engine, "tid": tid, "registry": reg}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _dest_cfg(url, **extra):
    cfg = {"base_url": url,
           "allowed_hosts": ["127.0.0.1"], "allowed_schemes": ["http"],
           "allowed_ports": [int(url.rsplit(":", 1)[-1])],
           "timeout_seconds": 1, "max_attempts": 3,
           "backoff_seconds": [0.01, 0.01, 0.01],
           "rate_limit_per_minute": 6000,
           "receipt_rules": [
               {"json_path": "result", "op": "eq", "value": True,
                "outcome": "ACCEPTED"},
               {"json_path": "errorCode", "op": "in", "value": ["01", "99"],
                "outcome": "RETRYABLE"},
               {"json_path": "errorCode", "op": "exists",
                "outcome": "REJECTED"}],
           "default_outcome": "UNKNOWN"}
    cfg.update(extra)
    return cfg


def _mk_dest(env, cfg, *, is_mock=True, profile="destination.generic_http_json@1"):
    with env["sf"]() as db:
        d = Destination(id=new_uuid(), tenant_id=env["tid"], code=f"g{new_uuid()[:6]}",
                        name="generic测试目的地", profile_revision=profile,
                        mode="PUSH", capabilities=[], is_mock=is_mock,
                        status="ENABLED")
        db.add(d)
        db.flush()
        cr = ConfigRevision(
            id=new_uuid(), tenant_id=env["tid"], config_type="destination",
            owner_id=d.id, revision=1, content_json=cfg,
            digest="d" * 64, state="PUBLISHED")
        db.add(cr)
        db.flush()
        d.config_revision = cr.id
        db.commit()
        return d.id


def _mk_submission(env, dest_id, *, state="PREPARED", snapshot_digest=None):
    with env["sf"]() as db:
        pkg = DataPackage(id=new_uuid(), tenant_id=env["tid"],
                          domain="metrics", business_key=f"bk{new_uuid()[:6]}")
        db.add(pkg)
        db.flush()
        pr = PackageRevision(id=new_uuid(), tenant_id=env["tid"],
                             package_id=pkg.id, revision=1, state="FROZEN")
        db.add(pr)
        db.flush()
        s = Submission(id=new_uuid(), tenant_id=env["tid"],
                       package_revision_id=pr.id, destination_id=dest_id,
                       idempotency_key=f"idem-{new_uuid()[:8]}",
                       state=state, snapshot_digest=snapshot_digest)
        db.add(s)
        db.commit()
        return s.id


def _mk_target(env, code, dest_id=None, *, lifecycle="PILOT",
               sole_pusher="CONFIRMED", runtime="PLATFORM"):
    with env["sf"]() as db:
        t = ReportTarget(id=new_uuid(), tenant_id=env["tid"], code=code,
                         name=code, revision=1, lifecycle=lifecycle,
                         runtime_kind=runtime, sole_pusher=sole_pusher,
                         destination_id=dest_id)
        db.add(t)
        db.commit()
        return code


# ================================================================ Mock→generic 装配
def test_mock_generic_destination_hits_generic_adapter(env, mock_url):
    """R02.1：is_mock=True + generic profile → 显式走 generic 适配器与行为档
    （非旧 MockHttpClient）；请求确实到达本机 Mock，审计行 mock=True。"""
    from core_handlers import perform_push
    dest_id = _mk_dest(env, _dest_cfg(mock_url), is_mock=True)
    sid = _mk_submission(env, dest_id)
    with env["sf"]() as db:
        s = db.get(Submission, sid)
        r = perform_push(db, env["tid"], s, {"__mock_code": "00"})
        assert r["outcome"] == "ACCEPTED"
        assert db.get(Submission, sid).state == "ACCEPTED"
        atts = (db.query(DeliveryAttempt)
                .filter_by(submission_id=sid).all())
        assert len(atts) == 1
        assert atts[0].response_snapshot.get("mock") is True
    assert len(CountingHandler.hits) == 1
    assert CountingHandler.hits[0]["body"].get("__mock_code") == "00"


def test_mock_generic_rejected_branch_via_behavior_profile(env, mock_url):
    """行为档 __mock_code=02 → REJECTED（装配路径与 generic 规则一致）。"""
    from core_handlers import perform_push
    dest_id = _mk_dest(env, _dest_cfg(mock_url), is_mock=True)
    sid = _mk_submission(env, dest_id)
    with env["sf"]() as db:
        s = db.get(Submission, sid)
        r = perform_push(db, env["tid"], s, {"__mock_code": "02"})
        assert r["outcome"] == "REJECTED"
        assert db.get(Submission, sid).state == "REJECTED"


def test_mock_generic_non_loopback_rejected_before_network(env):
    """R02：影子/联调 Mock 只允许本机回环——指向院内真实地址（<frontend-host>）
    的配置在触网前即拒绝（该地址绝不能被访问；无超时等待即返回）。"""
    from core_handlers import perform_push
    dest_id = _mk_dest(env, _dest_cfg("http://<frontend-host>",
                                      allowed_hosts=["<frontend-host>"],
                                      allowed_ports=[8881]), is_mock=True)
    sid = _mk_submission(env, dest_id)
    with env["sf"]() as db:
        s = db.get(Submission, sid)
        t0 = time.monotonic()
        r = perform_push(db, env["tid"], s, {"__mock_code": "00"})
        assert time.monotonic() - t0 < 5      # 无网络等待（触网前拒绝）
        assert r["outcome"] == "UNSUPPORTED"
        assert "MOCK_LOOPBACK_REQUIRED" in r["error_message"]
        assert db.get(Submission, sid).state == "REJECTED"
        atts = (db.query(DeliveryAttempt)
                .filter_by(submission_id=sid).all())
        assert len(atts) == 1
        assert atts[0].response_snapshot.get("mock") is True


def test_flip_real_to_mock_cannot_reach_internal_address(env):
    """把真实目的地改标 is_mock=True 也带不进非本机地址（同上拒绝）——
    改 is_mock 字段不是绕过手段。"""
    from core_handlers import perform_push
    dest_id = _mk_dest(env, _dest_cfg("http://<prod-host>:443",
                                      allowed_hosts=["<prod-host>"],
                                      allowed_ports=[443],
                                      allowed_schemes=["https"]), is_mock=True)
    sid = _mk_submission(env, dest_id)
    with env["sf"]() as db:
        s = db.get(Submission, sid)
        r = perform_push(db, env["tid"], s, {})
        assert r["outcome"] == "UNSUPPORTED"
        assert "MOCK_LOOPBACK_REQUIRED" in r["error_message"]


def test_legacy_mock_destination_keeps_mock_client(env, mock_url):
    """旧行为兼容：非 generic profile 的 Mock 目的地仍走 MockHttpClient
    （BLYB_MOCK_PLATFORM_URL 指向本机服务可证），不经 generic。"""
    os.environ["BLYB_MOCK_PLATFORM_URL"] = mock_url
    try:
        from core_handlers import perform_push
        with env["sf"]() as db:
            d = Destination(id=new_uuid(), tenant_id=env["tid"],
                            code=f"m{new_uuid()[:6]}", name="旧Mock",
                            profile_revision="profile.metrics_internal_v1@1",
                            mode="PUSH", is_mock=True, status="ENABLED")
            db.add(d)
            db.commit()
            did = d.id
        CountingHandler.hits.clear()
        sid = _mk_submission(env, did)
        with env["sf"]() as db:
            s = db.get(Submission, sid)
            r = perform_push(db, env["tid"], s, {"__mock_code": "00"})
            assert r["outcome"] == "ACCEPTED"
        assert len(CountingHandler.hits) >= 1    # 旧客户端仍发出
    finally:
        os.environ.pop("BLYB_MOCK_PLATFORM_URL", None)


# ================================================================ 闸门（真实分支）
def test_real_generic_confirmed_target_with_evidence_passes(env, mock_url):
    """R02.4：CONFIRMED sole_pusher + PILOT/PLATFORM + 独立启用证据 → 放行
    并派发到 generic（URL 为本机 Mock 顶替真实对端，属测试替身）。"""
    from core_handlers import perform_push
    cfg = _dest_cfg(mock_url, report_target_code="shadow_ok",
                    real_send_evidence_ref="TICKET-2026-1110-01")
    dest_id = _mk_dest(env, cfg, is_mock=False)
    _mk_target(env, "shadow_ok", sole_pusher="CONFIRMED")
    sid = _mk_submission(env, dest_id)
    with env["sf"]() as db:
        s = db.get(Submission, sid)
        r = perform_push(db, env["tid"], s, {"__mock_code": "00"})
        assert r["outcome"] == "ACCEPTED"
    assert len(CountingHandler.hits) == 1


def test_real_generic_unconfirmed_sole_pusher_blocked(env, mock_url):
    """R02.4：UNCONFIRMED 不再放行（旧实现只拒 CONFLICT）。"""
    from core_handlers import perform_push
    from contracts import ApiError
    cfg = _dest_cfg(mock_url, report_target_code="shadow_unc",
                    real_send_evidence_ref="TICKET-X")
    dest_id = _mk_dest(env, cfg, is_mock=False)
    _mk_target(env, "shadow_unc", sole_pusher="UNCONFIRMED")
    sid = _mk_submission(env, dest_id)
    with env["sf"]() as db:
        s = db.get(Submission, sid)
        with pytest.raises(ApiError) as ei:
            perform_push(db, env["tid"], s, {"__mock_code": "00"})
        assert ei.value.code == "PROFILE_UNAPPROVED"
        assert "UNCONFIRMED" in ei.value.message
    assert CountingHandler.hits == []          # 触网前拦截


def test_real_generic_without_evidence_ref_blocked(env, mock_url):
    """R02.4：缺 real_send_evidence_ref（独立启用证据）→ 拒绝。"""
    from core_handlers import perform_push
    from contracts import ApiError
    cfg = _dest_cfg(mock_url, report_target_code="shadow_noev")
    dest_id = _mk_dest(env, cfg, is_mock=False)
    _mk_target(env, "shadow_noev", sole_pusher="CONFIRMED")
    sid = _mk_submission(env, dest_id)
    with env["sf"]() as db:
        s = db.get(Submission, sid)
        with pytest.raises(ApiError) as ei:
            perform_push(db, env["tid"], s, {"__mock_code": "00"})
        assert ei.value.code == "PROFILE_UNAPPROVED"
        assert "real_send_evidence_ref" in ei.value.message
    assert CountingHandler.hits == []


def test_flip_mock_to_real_still_gated(env, mock_url):
    """把 Mock 目的地改 is_mock=False 不能绕过闸门（无 target_code 即拒）。"""
    from core_handlers import perform_push
    from contracts import ApiError
    dest_id = _mk_dest(env, _dest_cfg(mock_url), is_mock=False)  # 无 target_code
    sid = _mk_submission(env, dest_id)
    with env["sf"]() as db:
        s = db.get(Submission, sid)
        with pytest.raises(ApiError) as ei:
            perform_push(db, env["tid"], s, {"__mock_code": "00"})
        assert ei.value.code == "PROFILE_UNAPPROVED"
    assert CountingHandler.hits == []


# ================================================================ 零重复 POST
def test_timeout_unknown_zero_duplicate_post(env, mock_url):
    """R02.2：超时（不确定结果）+ 无幂等契约 → UNKNOWN，服务器只收到 1 次
    POST（零重复），审计行 attempts=1。"""
    from core_handlers import perform_push
    dest_id = _mk_dest(env, _dest_cfg(mock_url, timeout_seconds=1,
                                      max_attempts=3), is_mock=True)
    sid = _mk_submission(env, dest_id)
    with env["sf"]() as db:
        s = db.get(Submission, sid)
        r = perform_push(db, env["tid"], s, {"__mock_code": "timeout"})
        assert r["outcome"] == "UNKNOWN"
        assert r["attempts"] == 1
        assert db.get(Submission, sid).state == "UNKNOWN"
    assert len(CountingHandler.hits) == 1      # 零重复 POST


def test_contract_retries_record_every_attempt(env, mock_url):
    """R02.2：CONFIRMED 幂等契约下按同冻结请求重试，每次尝试各一行审计。"""
    from core_handlers import perform_push
    dest_id = _mk_dest(env, _dest_cfg(mock_url,
                                      idempotency_contract="CONFIRMED"),
                       is_mock=True)
    sid = _mk_submission(env, dest_id)
    with env["sf"]() as db:
        s = db.get(Submission, sid)
        r = perform_push(db, env["tid"], s, {"__mock_fail_times": 2})
        assert r["outcome"] == "ACCEPTED"
        assert r["attempts"] == 3
        atts = (db.query(DeliveryAttempt)
                .filter_by(submission_id=sid)
                .order_by(DeliveryAttempt.attempt_no).all())
        assert [a.attempt_no for a in atts] == [1, 2, 3]
        assert [a.outcome for a in atts] == ["RETRYABLE", "RETRYABLE",
                                             "ACCEPTED"]
    assert len(CountingHandler.hits) == 3


# ================================================================ RESEND_ALLOWED 链
def test_resend_allowed_not_auto_picked_by_worker(env, mock_url):
    """R02.6：RESEND_ALLOWED 只改回 PREPARED——API→处置→worker tick 全链
    不自动发送（无新尝试、无新 job、状态保持）。"""
    from plugins.report_ledger import manual_resolution
    dest_id = _mk_dest(env, _dest_cfg(mock_url), is_mock=True)
    _mk_target(env, "shadow_resend", dest_id)
    sid = _mk_submission(env, dest_id, state="UNKNOWN",
                         snapshot_digest="ab" * 32)
    with env["sf"]() as db:
        from core_db.engine import utcnow
        db.add(DeliveryAttempt(
            id=new_uuid(), tenant_id=env["tid"], submission_id=sid,
            attempt_no=1, request_digest="d" * 64, started_at=utcnow(),
            ended_at=utcnow(), http_status=None, outcome="UNKNOWN",
            response_snapshot={"mock": True}))
        db.commit()
    CountingHandler.hits.clear()
    with env["sf"]() as db:
        r = manual_resolution(db, env["tid"], "shadow_resend", sid,
                              resolution="RESEND_ALLOWED",
                              reason="平台人工核查后允许重发",
                              evidence_ref="TICKET-R1",
                              expected_state="UNKNOWN", actor="reviewer1")
        assert r["to_state"] == "PREPARED"
        db.commit()
    # worker tick（真实引擎 + 与生产同形的 PluginRegistryAdapter 装配）
    from apps.worker.engine import WorkerEngine, PluginRegistryAdapter
    eng = WorkerEngine(env["sf"],
                       registry=PluginRegistryAdapter(env["registry"]))
    out = eng.tick()
    assert out["jobs"] == 0
    with env["sf"]() as db:
        s = db.get(Submission, sid)
        assert s.state == "PREPARED"
        n = (db.query(DeliveryAttempt)
             .filter_by(submission_id=sid).count())
        assert n == 1                       # 无新发送尝试
        from core_db.models import Job
        jobs = db.query(Job).filter_by(tenant_id=env["tid"]).all()
        assert jobs == []                   # 未入队任何 job
    assert CountingHandler.hits == []       # 未触网


def test_manual_resolution_requires_frozen_digest_and_attempts(env, mock_url):
    """R02.6：无冻结摘要或零尝试的 UNKNOWN 不满足处置前提。"""
    from plugins.report_ledger import manual_resolution
    from contracts import ApiError
    dest_id = _mk_dest(env, _dest_cfg(mock_url), is_mock=True)
    _mk_target(env, "shadow_nodigest", dest_id)
    sid = _mk_submission(env, dest_id, state="UNKNOWN",
                         snapshot_digest=None)
    with env["sf"]() as db:
        with pytest.raises(ApiError) as ei:
            manual_resolution(db, env["tid"], "shadow_nodigest", sid,
                              resolution="REJECTED", reason="复核不通过",
                              evidence_ref="E", expected_state="UNKNOWN",
                              actor="r")
        assert "snapshot_digest" in ei.value.message
