"""LT-03 集成：generic_http_json 真实 send（独立 mock server，随机端口）。

覆盖 inf_pusher 语义：00 成功 / 01 重试后成功 / 01 连续→UNKNOWN / 02→REJECTED
attempts=1 / timeout→UNKNOWN / 500→重试 / 429→retry_after / notjson→UNKNOWN /
凭据缺失→UNSUPPORTED。
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

from destinations.generic_http_json import GenericHttpJsonAdapter  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("BLYB_NET_TESTS", "1") != "1",
    reason="网络级测试需 BLYB_NET_TESTS=1（本机默认跑；CI 可关闭）")


class MockHandler(BaseHTTPRequestHandler):
    fail_counts: dict[str, int] = {}

    def log_message(self, *a):  # noqa: 静音
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        code = str(body.get("__mock_code", "00"))
        fail_times = int(body.get("__mock_fail_times", 0) or 0)
        key = self.headers.get("Idempotency-Key", "")
        if fail_times > 0:
            seen = MockHandler.fail_counts.get(key, 0)
            if seen < fail_times:
                MockHandler.fail_counts[key] = seen + 1
                code = "01"
            else:
                code = "00"
        if code == "timeout":
            time.sleep(2)                     # 客户端 timeout=0.3 必超时
            return
        if code == "500":
            self.send_response(500)
            self.end_headers()
            return
        if code == "429":
            self.send_response(429)
            self.send_header("Retry-After", "3")
            self.end_headers()
            return
        if code == "notjson":
            payload = b"this is not json"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        ok = code == "00"
        desc = {"00": "成功", "01": "服务端异常", "99": "服务端异常",
                "02": "必填项缺失"}.get(code, "mock")
        payload = json.dumps({"result": ok, "errorCode": code,
                              "desc": desc,
                              "remote_id": "R-123"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def server_url():
    MockHandler.fail_counts.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()
    srv.server_close()


def _cfg(url, **kw):
    base = {"base_url": url, "allowed_hosts": ["127.0.0.1"],
            "allowed_schemes": ["http"],
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
                 "outcome": "REJECTED"},
            ],
            "default_outcome": "UNKNOWN"}
    base.update(kw)
    return base


def test_send_success_00(server_url):
    ad = GenericHttpJsonAdapter(sleeper=lambda s: None)
    r = ad.send(_cfg(server_url), {"business_type": "admission",
                                   "__mock_code": "00"}, "idem-1")
    assert r.outcome == "ACCEPTED"
    assert r.attempts == 1 and r.http_status == 200


def test_send_retry_then_success(server_url):
    # LT-03-R1：重试仅在已证实幂等契约（idempotency_contract=CONFIRMED）下发生
    ad = GenericHttpJsonAdapter(sleeper=lambda s: None)
    r = ad.send(_cfg(server_url, idempotency_contract="CONFIRMED"),
                {"__mock_fail_times": 2}, "idem-retry-1")
    assert r.outcome == "ACCEPTED"
    assert r.attempts == 3


def test_send_no_contract_retryable_goes_unknown_no_resend(server_url):
    """LT-03-R1/R02：未证实对端幂等——RETRYABLE（服务端异常）第一次即 UNKNOWN，
    不自动再次 POST（对方可能已收到，盲重发=重复上报）。"""
    ad = GenericHttpJsonAdapter(sleeper=lambda s: None)
    r = ad.send(_cfg(server_url, max_attempts=3),
                {"__mock_code": "01"}, "idem-nocontract-1")
    assert r.outcome == "UNKNOWN"
    assert r.attempts == 1
    assert "未证实对端幂等" in (r.error_message or "")


def test_send_no_contract_timeout_single_post(server_url):
    """LT-03-R1/R02：超时（POST 可能已被接收）无契约 → UNKNOWN 且只发一次。"""
    ad = GenericHttpJsonAdapter(sleeper=lambda s: None)
    r = ad.send(_cfg(server_url, timeout_seconds=1, max_attempts=3),
                {"__mock_code": "timeout"}, "idem-nocontract-to")
    assert r.outcome == "UNKNOWN"
    assert r.attempts == 1
    assert "未证实对端幂等" in (r.error_message or "")


def test_send_contract_invalid_value_rejected(server_url):
    ad = GenericHttpJsonAdapter(sleeper=lambda s: None)
    r = ad.send(_cfg(server_url, idempotency_contract="ASSUMED"),
                {"__mock_code": "00"}, "idem-badcontract")
    assert r.outcome == "UNSUPPORTED"
    assert "idempotency_contract" in r.error_message


def test_send_redirect_prohibited(server_url):
    """LT-03-R1/R02：3xx 一律 REJECTED，不跟随重定向（即使 Location 指向
    本机回环另一端口也不跟随）；目标主机收不到任何请求。"""
    received_elsewhere: list[str] = []

    class RedirectTarget(BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: 静音
            pass

        def do_POST(self):  # noqa: N802
            received_elsewhere.append("hit")
            self.send_response(200)
            self.end_headers()

    srv2 = ThreadingHTTPServer(("127.0.0.1", 0), RedirectTarget)
    th2 = threading.Thread(target=srv2.serve_forever, daemon=True)
    th2.start()
    try:
        port2 = srv2.server_address[1]

        class Redirector(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: 静音
                pass

            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{port2}/x")
                self.send_header("Content-Length", "0")
                self.end_headers()

        srv1 = ThreadingHTTPServer(("127.0.0.1", 0), Redirector)
        th1 = threading.Thread(target=srv1.serve_forever, daemon=True)
        th1.start()
        try:
            url1 = f"http://127.0.0.1:{srv1.server_address[1]}"
            ad = GenericHttpJsonAdapter(sleeper=lambda s: None)
            r = ad.send(_cfg(url1), {"__mock_code": "00"}, "idem-redir")
            assert r.outcome == "REJECTED"
            assert r.http_status == 302
            assert "重定向被禁止" in r.error_message
            assert r.attempts == 1
            assert received_elsewhere == []   # 未跟随，目标主机零请求
        finally:
            srv1.shutdown()
            srv1.server_close()
    finally:
        srv2.shutdown()
        srv2.server_close()


def test_send_retry_exhausted_unknown(server_url):
    ad = GenericHttpJsonAdapter(sleeper=lambda s: None)
    r = ad.send(_cfg(server_url, max_attempts=2,
                     idempotency_contract="CONFIRMED"),
                {"__mock_code": "01"}, "idem-unk-1")
    assert r.outcome == "UNKNOWN"
    assert r.error_code == "01" and r.attempts == 2


def test_send_rejected_no_retry(server_url):
    sleeps: list[float] = []
    ad = GenericHttpJsonAdapter(sleeper=lambda s: sleeps.append(s))
    r = ad.send(_cfg(server_url), {"__mock_code": "02"}, "idem-rej")
    assert r.outcome == "REJECTED"
    assert r.error_code == "02" and r.attempts == 1
    assert sleeps == []                      # 业务拒绝不重试


def test_send_timeout_unknown(server_url):
    ad = GenericHttpJsonAdapter(sleeper=lambda s: None)
    # 校验范围 timeout∈[1,120]；mock sleep 2s > 1s 超时（无契约不重发 attempts=1）
    r = ad.send(_cfg(server_url, timeout_seconds=1, max_attempts=1),
                {"__mock_code": "timeout"}, "idem-to")
    assert r.outcome == "UNKNOWN"
    assert r.attempts == 1


def test_send_500_retry_then_unknown(server_url):
    sleeps: list[float] = []
    ad = GenericHttpJsonAdapter(sleeper=lambda s: sleeps.append(s))
    r = ad.send(_cfg(server_url, max_attempts=2,
                     idempotency_contract="CONFIRMED"),
                {"__mock_code": "500"}, "idem-500")
    assert r.outcome == "UNKNOWN"
    assert len(sleeps) >= 1                  # 有退避


def test_send_500_no_contract_single_attempt(server_url):
    sleeps: list[float] = []
    ad = GenericHttpJsonAdapter(sleeper=lambda s: sleeps.append(s))
    r = ad.send(_cfg(server_url, max_attempts=3), {"__mock_code": "500"},
                "idem-500nc")
    assert r.outcome == "UNKNOWN" and r.attempts == 1
    assert sleeps == []                      # 无契约零退避零重发


def test_send_429_retry_after(server_url):
    # 无契约：429(RETRYABLE) 直接 → UNKNOWN（attempts=1），Retry-After 保留传递
    ad = GenericHttpJsonAdapter(sleeper=lambda s: None)
    r = ad.send(_cfg(server_url, max_attempts=1), {"__mock_code": "429"},
                "idem-429")
    assert r.outcome == "UNKNOWN"
    assert r.retry_after_seconds == 3
    # 有契约多次重试路径：退避取 Retry-After 与 backoff 较大者后仍 UNKNOWN
    ad2 = GenericHttpJsonAdapter(sleeper=lambda s: None)
    r2 = ad2.send(_cfg(server_url, max_attempts=2,
                       idempotency_contract="CONFIRMED"),
                  {"__mock_code": "429"}, "idem-429b")
    assert r2.attempts == 2 and r2.outcome == "UNKNOWN"


def test_send_notjson_unknown(server_url):
    ad = GenericHttpJsonAdapter(sleeper=lambda s: None)
    r = ad.send(_cfg(server_url, max_attempts=1), {"__mock_code": "notjson"},
                "idem-nj")
    assert r.outcome == "UNKNOWN"
    assert "JSON" in r.error_message


def test_send_missing_credential_unsupported(server_url):
    os.environ.pop("BLYB_LT03_SECRET", None)
    ad = GenericHttpJsonAdapter(sleeper=lambda s: None)
    r = ad.send(_cfg(server_url, credential_ref="env:BLYB_LT03_SECRET"),
                {"__mock_code": "00"}, "idem-cred")
    assert r.outcome == "UNSUPPORTED"
    assert "凭据未配置" in r.error_message
    assert "BLYB_LT03_SECRET" in r.error_message     # 变量名可见（非值）


def test_send_invalid_config_unsupported():
    ad = GenericHttpJsonAdapter(sleeper=lambda s: None)
    r = ad.send({"base_url": "http://evil.com:1", "allowed_hosts": ["127.0.0.1"],
                 "allowed_ports": [1], "allowed_schemes": ["http"]},
                {}, "idem-bad")
    assert r.outcome == "UNSUPPORTED"


def test_send_path_injection_blocked(server_url):
    ad = GenericHttpJsonAdapter(sleeper=lambda s: None)
    r = ad.send(_cfg(server_url, path_template="/api/{bt}"),
                {"bt": "../../admin"}, "idem-inj")
    assert r.outcome == "UNSUPPORTED"
    assert "非法字符" in r.error_message


def test_send_remote_ref_path(server_url):
    ad = GenericHttpJsonAdapter(sleeper=lambda s: None)
    r = ad.send(_cfg(server_url, remote_ref_path="remote_id"),
                {"__mock_code": "00"}, "idem-rr")
    assert r.outcome == "ACCEPTED" and r.remote_ref == "R-123"


def test_send_on_attempt_records_every_attempt(server_url):
    """LT-03-R1/R02：on_attempt 每次尝试各回调一次（含重试），前序尝试不丢失。"""
    seen: list[tuple[str, int]] = []
    ad = GenericHttpJsonAdapter(sleeper=lambda s: None)
    r = ad.send(_cfg(server_url, idempotency_contract="CONFIRMED"),
                {"__mock_fail_times": 2}, "idem-onattempt",
                on_attempt=lambda res: seen.append((res.outcome, res.attempts)))
    assert r.outcome == "ACCEPTED" and r.attempts == 3
    assert seen == [("RETRYABLE", 1), ("RETRYABLE", 2), ("ACCEPTED", 3)]
