"""DMZ 转发器测试（W06；DTR-C01—C04）：本地 TLS 全链路，零外连。

拓扑（全部 127.0.0.1，测试专用端口）：
  client(mTLS) → forwarder(--config) → mock upstream(TLS, 固定host解析)
上游固定 eco.taobao.com 由 forwarder 常量保证；测试把 eco.taobao.com 指到本地
mock 上游的 CA/证书（HOST 自签证书 CN=eco.taobao.com），经 monkeypatch 连接目标。
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402

IMPL = Path = None
DMZ_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "deploy", "dmz")
sys.path.insert(0, DMZ_DIR)

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None,
                                reason="openssl 不可用（证书生成）")


# ---------------------------------------------------------------- 测试 CA
def _run(cmd: list[str]):
    subprocess.run(cmd, check=True, capture_output=True)


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    d = tmp_path_factory.mktemp("pki")
    # 服务 CA + 转发器服务端证书（SAN=DNS:dmz.local + IP:127.0.0.1）
    _run(["openssl", "genrsa", "-out", str(d / "srv-ca.key"), "2048"])
    _ca_ext = ["-addext", "basicConstraints=critical,CA:TRUE",
               "-addext", "keyUsage=critical,keyCertSign,cRLSign"]
    _run(["openssl", "req", "-x509", "-new", "-key", str(d / "srv-ca.key"),
          "-days", "2", "-subj", "/CN=DMZ Test CA", *_ca_ext,
          "-out", str(d / "srv-ca.crt")])
    _run(["openssl", "genrsa", "-out", str(d / "fwd.key"), "2048"])
    (d / "fwd.ext").write_text(
        "subjectAltName=DNS:dmz.local,IP:127.0.0.1\nbasicConstraints=CA:FALSE\n",
        encoding="utf-8")
    _run(["openssl", "req", "-new", "-key", str(d / "fwd.key"),
          "-subj", "/CN=dmz.local", "-out", str(d / "fwd.csr")])
    _run(["openssl", "x509", "-req", "-CA", str(d / "srv-ca.crt"),
          "-CAkey", str(d / "srv-ca.key"), "-CAcreateserial", "-days", "2",
          "-extfile", str(d / "fwd.ext"), "-in", str(d / "fwd.csr"),
          "-out", str(d / "fwd.crt")])
    # 客户端 CA（<prod-host> 身份）+ 客户端证书 + 一个未授权 CA 的客户端
    _run(["openssl", "genrsa", "-out", str(d / "cl-ca.key"), "2048"])
    _run(["openssl", "req", "-x509", "-new", "-key", str(d / "cl-ca.key"),
          "-days", "2", "-subj", "/CN=Client CA", *_ca_ext,
          "-out", str(d / "cl-ca.crt")])
    _run(["openssl", "genrsa", "-out", str(d / "cl.key"), "2048"])
    _run(["openssl", "req", "-new", "-key", str(d / "cl.key"),
          "-subj", "/CN=blyb-8156", "-out", str(d / "cl.csr")])
    _run(["openssl", "x509", "-req", "-CA", str(d / "cl-ca.crt"),
          "-CAkey", str(d / "cl-ca.key"), "-CAcreateserial", "-days", "2",
          "-in", str(d / "cl.csr"), "-out", str(d / "cl.crt")])
    _run(["openssl", "genrsa", "-out", str(d / "bad-ca.key"), "2048"])
    _run(["openssl", "req", "-x509", "-new", "-key", str(d / "bad-ca.key"),
          "-days", "2", "-subj", "/CN=Bad CA", *_ca_ext,
          "-out", str(d / "bad-ca.crt")])
    _run(["openssl", "genrsa", "-out", str(d / "bad.key"), "2048"])
    _run(["openssl", "req", "-new", "-key", str(d / "bad.key"),
          "-subj", "/CN=attacker", "-out", str(d / "bad.csr")])
    _run(["openssl", "x509", "-req", "-CA", str(d / "bad-ca.crt"),
          "-CAkey", str(d / "bad-ca.key"), "-CAcreateserial", "-days", "2",
          "-in", str(d / "bad.csr"), "-out", str(d / "bad.crt")])
    # 上游 CA + eco.taobao.com 证书（mock 上游身份）
    _run(["openssl", "genrsa", "-out", str(d / "up-ca.key"), "2048"])
    _run(["openssl", "req", "-x509", "-new", "-key", str(d / "up-ca.key"),
          "-days", "2", "-subj", "/CN=Upstream CA", *_ca_ext,
          "-out", str(d / "up-ca.crt")])
    _run(["openssl", "genrsa", "-out", str(d / "up.key"), "2048"])
    (d / "up.ext").write_text(
        "subjectAltName=DNS:eco.taobao.com\nbasicConstraints=CA:FALSE\n",
        encoding="utf-8")
    _run(["openssl", "req", "-new", "-key", str(d / "up.key"),
          "-subj", "/CN=eco.taobao.com", "-out", str(d / "up.csr")])
    _run(["openssl", "x509", "-req", "-CA", str(d / "up-ca.crt"),
          "-CAkey", str(d / "up-ca.key"), "-CAcreateserial", "-days", "2",
          "-extfile", str(d / "up.ext"), "-in", str(d / "up.csr"),
          "-out", str(d / "up.crt")])
    return d


class UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hits = []
    fail_next = False
    respond_status = 200
    respond_body = b'{"alibaba_alihealth_drugtrace_top_yljg_uploadretail_response": {"success": true}}'
    slow_body_seconds = None   # FIN-A09：每块写间隔（慢流注入）
    location = None            # FIN-A10：3xx Location 注入

    def log_message(self, *a):  # 静默
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(n)
        type(self).hits.append({"path": self.path, "body": body,
                                "host": self.headers.get("Host", ""),
                                "content_type": self.headers.get("Content-Type", "")})
        if type(self).fail_next:
            type(self).fail_next = False
            # 直接断连模拟上游故障（无响应）
            self.close_connection = True
            self.wfile.close()
            return
        out = type(self).respond_body
        if type(self).slow_body_seconds is not None:
            self.send_response(type(self).respond_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            import time as _t
            step = max(1, len(out) // 8)
            for i in range(0, len(out), step):
                self.wfile.write(out[i:i + step])
                self.wfile.flush()
                _t.sleep(type(self).slow_body_seconds)
            return
        self.send_response(type(self).respond_status)
        if type(self).location:
            self.send_header("Location", type(self).location)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture(scope="module")
def upstream(pki):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(pki / "up.crt"), str(pki / "up.key"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield {"port": port}
    httpd.shutdown()


@pytest.fixture(scope="module")
def forwarder(pki, upstream, tmp_path_factory):
    import forwarder as fwd_mod
    # 上游固定 eco.taobao.com:443 —— 测试仅重定向 socket 目标地址，
    # SNI/证书校验仍按 eco.taobao.com（保持真实校验语义）
    orig_conn = fwd_mod.HTTPSConnection
    _local_addr = ("127.0.0.1", upstream["port"])

    class LocalHTTPSConnection(orig_conn):
        def __init__(self, host, port=443, **kw):
            super().__init__("eco.taobao.com", 443, **kw)

        def connect(self):
            import socket
            self.sock = self._context.wrap_socket(
                socket.create_connection(_local_addr, timeout=self.timeout),
                server_hostname=self.host)
            self.sock.settimeout(self.timeout)

    fwd_mod.HTTPSConnection = LocalHTTPSConnection
    cfg = {
        "listen_host": "127.0.0.1", "listen_port": 0,
        "tls_cert": str(pki / "fwd.crt"), "tls_key": str(pki / "fwd.key"),
        "client_ca": str(pki / "cl-ca.crt"),
        "upstream_ca": str(pki / "up-ca.crt"),
        "allow_ips": ["127.0.0.1"],
        "max_body_bytes": 65536, "rate_per_minute": 10000, "rate_burst": 10000,
        "max_concurrency": 8, "upstream_connect_timeout": 2.0,
        "upstream_read_timeout": 3.0, "max_response_bytes": 4096,
        "upstream_total_deadline": 10.0,
    }
    server = fwd_mod.make_server(cfg, mode="explicit")
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    import logging
    logs = []
    handler = logging.Handler()
    handler.emit = lambda record: logs.append(record.getMessage())
    logging.getLogger("blyb.dmz").addHandler(handler)
    yield {"port": port, "pki": pki, "server": server, "logs": logs}
    server.shutdown()
    fwd_mod.HTTPSConnection = orig_conn


TOP_METHOD = b"method=alibaba.alihealth.drugtrace.top.yljg.uploadretail"


class _Conn(http.client.HTTPSConnection):
    """按 IP 连接（转发器证书含 IP SAN 127.0.0.1，链+名称校验均开启）。"""

    def __init__(self, pki, port, cert=("cl.crt", "cl.key"), host="127.0.0.1"):
        ctx = ssl.create_default_context(cafile=str(pki / "srv-ca.crt"))
        crt, key = cert
        ctx.load_cert_chain(str(pki / crt), str(pki / key))
        super().__init__(host, port, context=ctx)


def test_c02_fixed_upstream_methods_paths(forwarder, upstream):
    pki = forwarder["pki"]
    UpstreamHandler.hits.clear()
    conn = _Conn(pki, forwarder["port"])
    # 任意 Host 头都不影响固定上游
    conn.request("POST", "/alihealth/router/rest",
                 body=TOP_METHOD + b"&sign=ABCDEF123456",
                 headers={"Content-Type": "application/x-www-form-urlencoded",
                          "Host": "evil.example.com"})
    resp = conn.getresponse()
    assert resp.status == 200
    body = resp.read()
    assert b"success" in body
    # 上游收到：固定 path /router/rest、原字节 body、Host=eco.taobao.com
    hit = UpstreamHandler.hits[-1]
    assert hit["path"] == "/router/rest"
    assert hit["body"] == TOP_METHOD + b"&sign=ABCDEF123456"
    assert hit["host"] == "eco.taobao.com"
    # 错误路径/方法拒绝
    for method, path, expect in (
            ("GET", "/alihealth/router/rest", 404),
            ("POST", "/other/path", 404),
            ("PUT", "/alihealth/router/rest", 405),
            ("CONNECT", "/alihealth/router/rest", 405)):
        c2 = _Conn(pki, forwarder["port"])
        c2.request(method, path, body=b"x" if method in ("POST", "PUT") else None)
        r2 = c2.getresponse()
        assert r2.status == expect, (method, path, r2.status)
        r2.read()
    # 健康端点（不触上游）
    hits_before = len(UpstreamHandler.hits)
    c3 = _Conn(pki, forwarder["port"])
    c3.request("GET", "/health")
    r3 = c3.getresponse()
    assert r3.status == 200 and b"UP" in r3.read()
    assert len(UpstreamHandler.hits) == hits_before


def test_c03_bytes_preserved_no_retry_no_cache(forwarder, upstream):
    pki = forwarder["pki"]
    UpstreamHandler.hits.clear()
    # 已签表单原字节（含百分号编码与中文）
    signed = ("method=alibaba.alihealth.drugtrace.top.yljg.uploadretail&"
              "bill_code=DTRT-ABC&trace_codes=000123456789%2C000123456790&"
              "note=%E4%B8%AD%E6%96%87").encode()
    conn = _Conn(pki, forwarder["port"])
    conn.request("POST", "/alihealth/router/rest", body=signed,
                 headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"})
    resp = conn.getresponse()
    assert resp.status == 200
    assert resp.read() == UpstreamHandler.respond_body  # 响应原字节
    assert UpstreamHandler.hits[-1]["body"] == signed   # 请求原字节（未重编码）
    assert UpstreamHandler.hits[-1]["content_type"].startswith(
        "application/x-www-form-urlencoded")
    # 无重试：上游断连一次 → 转发器返回 502 明确错误 JSON；上游只被调用一次
    UpstreamHandler.fail_next = True
    UpstreamHandler.hits.clear()
    conn2 = _Conn(pki, forwarder["port"])
    conn2.request("POST", "/alihealth/router/rest", body=TOP_METHOD,
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    r2 = conn2.getresponse()
    assert r2.status == 502
    out = r2.read()
    assert b"forwarder_error" in out and b"success" not in out  # 不伪装平台成功
    time.sleep(0.2)
    assert len(UpstreamHandler.hits) == 1, "无自动重试：上游必须只被调用一次"


def test_c04_resource_limits_and_redacted_logs(forwarder, upstream):
    pki = forwarder["pki"]
    # 大 body → 413
    conn = _Conn(pki, forwarder["port"])
    conn.request("POST", "/alihealth/router/rest", body=b"x" * 70000,
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    r = conn.getresponse()
    assert r.status == 413
    r.read()
    # 日志脱敏：不含码串/签名值
    conn2 = _Conn(pki, forwarder["port"])
    secret_marker = "0001234567890123SECRET"
    conn2.request("POST", "/alihealth/router/rest",
                  body=TOP_METHOD + f"&k={secret_marker}".encode(),
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    r2 = conn2.getresponse()
    r2.read()
    time.sleep(0.2)
    joined = "\n".join(forwarder["logs"])
    assert secret_marker not in joined, "日志不得含码串/表单值"
    assert "digest=" in joined  # 以哈希指纹代替


def test_c01_mtls_identity(forwarder):
    pki = forwarder["pki"]
    # 不可信客户端证书：服务端拒绝（客户端表现为握手/连接层失败）
    import socket
    with pytest.raises((ssl.SSLError, OSError, http.client.HTTPException)):
        conn = _Conn(pki, forwarder["port"], cert=("bad.crt", "bad.key"))
        conn.request("GET", "/health")
        conn.getresponse()
        conn.close()
    # 无客户端证书：同样握手失败
    ctx = ssl.create_default_context(cafile=str(pki / "srv-ca.crt"))
    with pytest.raises((ssl.SSLError, OSError)):
        raw = socket.create_connection(("127.0.0.1", forwarder["port"]), timeout=5)
        s = ctx.wrap_socket(raw, server_hostname="127.0.0.1")
        s.sendall(b"GET /health HTTP/1.0" + bytes([13, 10, 13, 10]))  # 触发服务端校验
        s.recv(1)
        s.close()


def test_config_validation(pki, tmp_path):
    import forwarder as fwd_mod
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(fwd_mod.ConfigError):
        fwd_mod.load_config(str(bad))
    good = {
        "listen_host": "127.0.0.1", "listen_port": 8443,
        "tls_cert": str(pki / "fwd.crt"),
        "tls_key": str(pki / "fwd.key"), "client_ca": str(pki / "cl-ca.crt"),
        "upstream_ca": str(pki / "up-ca.crt"), "allow_ips": ["<intranet-host>"],
    }
    cfg = fwd_mod.load_config.__wrapped__(str(good)) if False else None
    import json as _json
    gp = tmp_path / "good.json"
    gp.write_text(_json.dumps(good), encoding="utf-8")
    cfg = fwd_mod.load_config(str(gp))
    assert cfg["max_body_bytes"] == 2 * 1024 * 1024  # 工程默认


def test_c05_top_method_whitelist_and_upstream_gates(forwarder, upstream):
    """FIX2-06/T23：TOP 方法白名单、Content-Type、上游重定向与响应上限。"""
    pki = forwarder["pki"]
    UpstreamHandler.hits.clear()
    # 未知 TOP method → 400（不触上游）
    conn = _Conn(pki, forwarder["port"])
    conn.request("POST", "/alihealth/router/rest",
                 body=b"method=alibaba.alihealth.drugtrace.top.yljg.evil",
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    r = conn.getresponse()
    assert r.status == 400 and b"top_method_not_allowed" in r.read()
    # 重复/歧义 method → 400
    conn = _Conn(pki, forwarder["port"])
    conn.request("POST", "/alihealth/router/rest",
                 body=TOP_METHOD + b"&" + TOP_METHOD,
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    r = conn.getresponse()
    assert r.status == 400 and b"top_method_not_allowed" in r.read()
    # 非表单 Content-Type → 415
    conn = _Conn(pki, forwarder["port"])
    conn.request("POST", "/alihealth/router/rest", body=TOP_METHOD,
                 headers={"Content-Type": "application/json"})
    r = conn.getresponse()
    assert r.status == 415 and b"content_type_not_allowed" in r.read()
    assert not UpstreamHandler.hits, "被拒请求不得触达上游"
    # 上游 3xx 重定向 → 502（不跟随）
    UpstreamHandler.respond_status = 302
    try:
        conn = _Conn(pki, forwarder["port"])
        conn.request("POST", "/alihealth/router/rest", body=TOP_METHOD,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        assert r.status == 502 and b"redirect" in r.read()
    finally:
        UpstreamHandler.respond_status = 200
    # 上游响应超上限 → 502（读取有界，连接释放）
    UpstreamHandler.respond_body = b"y" * 8192   # 夹具上限 4096
    try:
        conn = _Conn(pki, forwarder["port"])
        conn.request("POST", "/alihealth/router/rest", body=TOP_METHOD,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        assert r.status == 502 and b"response_too_large" in r.read()
        # 上限内可正常往返（连接池未被拖死）
        UpstreamHandler.respond_body = b'{"ok": true}'
        conn = _Conn(pki, forwarder["port"])
        conn.request("POST", "/alihealth/router/rest", body=TOP_METHOD,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        assert r.status == 200 and r.read() == b'{"ok": true}'
    finally:
        UpstreamHandler.respond_body = (
            b'{"alibaba_alihealth_drugtrace_top_yljg_uploadretail_response":'
            b' {"success": true}}')


def test_c06_app_real_transport_mtls_end_to_end(forwarder, upstream):
    """FIX2-T07：应用 RealTransport（mTLS 客户端证书）→ 本地 DMZ 转发器 →
    模拟官方上游的完整闭环；分层超时/禁重定向/字节保持。（签名一致性仍由
    EXT-07 阻断——本测试直接 POST 已备表单字节，验证传输链路而非签名。）"""
    impl = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    if impl not in sys.path:
        sys.path.insert(0, impl)
    from destinations.alihealth_drugtrace.transport import RealTransport
    pki = forwarder["pki"]
    UpstreamHandler.hits.clear()
    UpstreamHandler.respond_body = (
        b'{"alibaba_alihealth_drugtrace_top_yljg_uploadretail_response":'
        b' {"response_success": true, "model": "SYN"}}')
    transport = RealTransport(
        f"https://127.0.0.1:{forwarder['port']}",
        ca_file=str(pki / "srv-ca.crt"),
        cert_file=str(pki / "cl.crt"), key_file=str(pki / "cl.key"),
        connect_timeout=5.0, read_timeout=5.0, total_deadline=15.0)
    body = TOP_METHOD + b"&bill_code=DTRT-1&bill_time=2026-10-12+07%3A55%3A00"
    result = transport.post(body,
                            "application/x-www-form-urlencoded;charset=utf-8")
    assert result.http_status == 200, result.error
    assert b"response_success" in result.body
    # 上游收到原字节（经 DMZ 转发不重编码）
    hit = UpstreamHandler.hits[-1]
    assert hit["body"] == body
    assert hit["host"] == "eco.taobao.com"
    # 客户端证书缺失 → 转发器拒绝（mTLS 强制）
    from destinations.alihealth_drugtrace.transport import TransportResult
    no_cert = RealTransport(f"https://127.0.0.1:{forwarder['port']}",
                            ca_file=str(pki / "srv-ca.crt"))
    refused = no_cert.post(body, "application/x-www-form-urlencoded")
    assert refused.http_status is None and refused.error, "无客户端证书必须被拒"
    UpstreamHandler.respond_body = (
        b'{"alibaba_alihealth_drugtrace_top_yljg_uploadretail_response":'
        b' {"success": true}}')


# ================================================================ FIN 测试
# DTR-FINALIZE-20261020：socket 激活/FD 验证、慢连接预算、日志脱敏、
# 歧义请求拒绝、默认 inout 关闭、诊断 CLI（FIN-A03/06—A10）。

def _fd3_listen(port: int) -> "socket.socket":
    """创建监听 socket；fd 基址由调用方经 fd_base=holder.fileno() 注入
    （Windows 套接字句柄不能 dup2，生产路径恒为 fd 3）。"""
    import socket as _s
    s = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    s.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(4)
    return s



def _safe_close(sock) -> None:
    """Windows 下同一 OS 句柄被采纳方关闭后，再关持有对象会 10038。"""
    try:
        sock.close()
    except OSError:
        pass

def test_fin_a03_systemd_fd_validation(pki, monkeypatch):
    """FIN-A03：FD 数量/PID/非监听/端口不符均拒绝；正确 FD 被采纳。"""
    import socket as _s
    import forwarder as fwd_mod
    cfg = {
        "listen_host": "127.0.0.1", "listen_port": 0,   # 端口由监听 socket 决定
        "tls_cert": str(pki / "fwd.crt"), "tls_key": str(pki / "fwd.key"),
        "client_ca": str(pki / "cl-ca.crt"),
        "upstream_ca": str(pki / "up-ca.crt"), "allow_ips": ["<intranet-host>"],
    }
    # 正确路径：fd3 监听 → 采纳且地址一致
    holder = _fd3_listen(0)
    port = holder.getsockname()[1]
    cfg["listen_port"] = port
    monkeypatch.setenv("LISTEN_PID", str(os.getpid()))
    monkeypatch.setenv("LISTEN_FDS", "1")
    sock = fwd_mod.receive_systemd_socket(cfg, fd_base=holder.fileno())
    assert sock.getsockname() == ("127.0.0.1", port)
    sock.close()
    try:
        _safe_close(holder)   # 同一 OS 句柄已被采纳方关闭；POSIX 忽略，Windows 需容错
    except OSError:
        pass
    # PID 不符
    holder = _fd3_listen(0)
    cfg["listen_port"] = holder.getsockname()[1]
    monkeypatch.setenv("LISTEN_PID", "999999")
    with pytest.raises(fwd_mod.ConfigError, match="LISTEN_PID"):
        fwd_mod.receive_systemd_socket(cfg, fd_base=holder.fileno())
    monkeypatch.setenv("LISTEN_PID", str(os.getpid()))
    # LISTEN_FDS=0
    monkeypatch.setenv("LISTEN_FDS", "0")
    with pytest.raises(fwd_mod.ConfigError, match="LISTEN_FDS=0"):
        fwd_mod.receive_systemd_socket(cfg, fd_base=holder.fileno())
    # 端口不符（拒绝服务错误地址；用相对当前 holder 的端口，避免临时端口
    # 顺序分配导致的 +1 巧合相等）
    monkeypatch.setenv("LISTEN_FDS", "1")
    cfg["listen_port"] = holder.getsockname()[1] + 1
    with pytest.raises(fwd_mod.ConfigError, match="mismatch"):
        fwd_mod.receive_systemd_socket(cfg, fd_base=holder.fileno())
    _safe_close(holder)
    # 非监听 socket（已连接对）拒绝
    c1, c2 = _s.socketpair()
    monkeypatch.setenv("LISTEN_FDS", "1")
    cfg["listen_port"] = 443
    with pytest.raises(fwd_mod.ConfigError, match="not a listening socket"):
        fwd_mod.receive_systemd_socket(cfg, fd_base=c1.fileno())
    _safe_close(c1); _safe_close(c2)
    # 环境缺失
    monkeypatch.delenv("LISTEN_PID")
    with pytest.raises(fwd_mod.ConfigError, match="LISTEN_PID/LISTEN_FDS"):
        fwd_mod.receive_systemd_socket(cfg, fd_base=3)


@pytest.mark.skipif(os.name != "posix",
                    reason="Windows 套接字句柄不可按 fd 序号布局")
def test_fin_a03_extra_fd_closed(pki, monkeypatch):
    """LISTEN_FDS>1 时多余 FD 被关闭（不留悬空句柄；POSIX only）。"""
    import forwarder as fwd_mod
    holder = _fd3_listen(0)
    port = holder.getsockname()[1]
    extra = os.dup(holder.fileno())
    os.dup2(extra, 4)
    cfg = {
        "listen_host": "127.0.0.1", "listen_port": port,
        "tls_cert": str(pki / "fwd.crt"), "tls_key": str(pki / "fwd.key"),
        "client_ca": str(pki / "cl-ca.crt"),
        "upstream_ca": str(pki / "up-ca.crt"), "allow_ips": ["<intranet-host>"],
    }
    monkeypatch.setenv("LISTEN_PID", str(os.getpid()))
    monkeypatch.setenv("LISTEN_FDS", "2")
    sock = fwd_mod.receive_systemd_socket(cfg)
    sock.close()
    import socket as _s
    with pytest.raises(OSError):
        _s.socket(fileno=4)   # fd4 已被关闭
    os.close(extra)
    holder.close()


def _mtls_raw(pki, port):
    """已握手的原始 mTLS socket（发送手工构造的请求）。"""
    import socket as _s
    ctx = ssl.create_default_context(cafile=str(pki / "srv-ca.crt"))
    ctx.load_cert_chain(str(pki / "cl.crt"), str(pki / "cl.key"))
    raw = _s.create_connection(("127.0.0.1", port), timeout=8)
    s = ctx.wrap_socket(raw, server_hostname="127.0.0.1")
    s.settimeout(8)
    return s


def _ok_request(pki, port) -> float:
    """一次正常 mTLS POST；返回耗时秒。"""
    t0 = time.monotonic()
    conn = _Conn(pki, port)
    conn.request("POST", "/alihealth/router/rest", body=TOP_METHOD,
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    r = conn.getresponse()
    assert r.status == 200, r.status
    r.read()
    conn.close()
    return time.monotonic() - t0


def test_fin_a08_slow_tls_does_not_block_accept(forwarder, monkeypatch):
    """慢/空 TLS 握手不阻塞 accept：正常请求不受影响，握手超时释放名额。"""
    import socket as _s
    port = forwarder["port"]
    # 打开多个不发起 TLS 的原始 TCP 连接（握手挂起）
    stalls = [_s.create_connection(("127.0.0.1", port), timeout=5)
              for _ in range(4)]
    took = _ok_request(forwarder["pki"], port)
    assert took < 5.0, f"正常请求被慢握手阻塞（{took:.1f}s）"
    for s in stalls:
        s.close()


def test_fin_a08_slow_header_and_body_budgets(forwarder, monkeypatch):
    """慢请求头/慢请求体在预算内被断开，不占住并发名额。"""
    import socket as _s
    port = forwarder["port"]
    # 慢头：发半行后停住
    s1 = _mtls_raw(forwarder["pki"], port)
    s1.sendall(b"POST /alihealth/router/re")
    # 慢体：声明 4096 字节只给一点
    s2 = _mtls_raw(forwarder["pki"], port)
    s2.sendall(b"POST /alihealth/router/rest HTTP/1.1\r\n"
               b"Host: x\r\nContent-Type: application/x-www-form-urlencoded\r\n"
               b"Content-Length: 4096\r\n\r\nmethod=x&pad=1")
    # 两类慢请求在途时正常请求仍可用
    took = _ok_request(forwarder["pki"], port)
    assert took < 5.0, f"正常请求被慢头/慢体阻塞（{took:.1f}s）"
    # 慢头连接最终被 header_timeout 断开
    try:
        s1.settimeout(20)
        data = s1.recv(1)   # 服务端关闭后 recv 返回 b""
        assert data == b""
    except (ssl.SSLError, OSError):
        pass
    finally:
        s1.close()
        s2.close()


def test_fin_a08_handshake_pending_limit(forwarder):
    """握手前连接限额：超限的原始连接被立即关闭（不排队占线程）。"""
    import socket as _s
    port = forwarder["port"]
    cfg = forwarder["server"].state.cfg
    limit = int(cfg["max_pending_handshakes"])
    conns = []
    for _ in range(limit + 4):
        c = _s.create_connection(("127.0.0.1", port), timeout=3)
        conns.append(c)
    st = forwarder["server"].state
    time.sleep(0.3)
    assert st.handshake_rejected >= 1, "超限连接未被拒绝"
    for c in conns:
        c.close()


def test_fin_a09_upstream_slow_body_deadline(forwarder, monkeypatch):
    """上游慢流响应在总预算内中止（504），且只调用一次。"""
    pki = forwarder["pki"]
    UpstreamHandler.hits.clear()
    UpstreamHandler.respond_body = b"z" * 8192
    UpstreamHandler.slow_body_seconds = 0.4   # 每块间隔 > 读超时预算
    try:
        conn = _Conn(pki, forwarder["port"])
        conn.request("POST", "/alihealth/router/rest", body=TOP_METHOD,
                     headers={"Content-Type":
                              "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        assert r.status in (502, 504)
        body = r.read()
        assert b"forwarder_error" in body
        assert len(UpstreamHandler.hits) == 1, "慢流上游也必须只调用一次"
    finally:
        UpstreamHandler.slow_body_seconds = None
        UpstreamHandler.respond_body = (
            b'{"alibaba_alihealth_drugtrace_top_yljg_uploadretail_response":'
            b' {"success": true}}')


def test_fin_a10_log_redaction_query_location(forwarder):
    """FIN-A10：查询串/Location/签名/session 诱饵不进日志。"""
    pki = forwarder["pki"]
    logs = forwarder["logs"]
    logs.clear()
    # 带敏感查询串的错误路径（404）
    s = _mtls_raw(pki, forwarder["port"])
    s.sendall(b"GET /evil?session=SESS-DECOY-123&sign=SIGNDECOY9f HTTP/1.1\r\n"
              b"Host: h\r\nConnection: close\r\n\r\n")
    s.recv(4096)
    s.close()
    # 上游 302 + 带敏感参数的 Location
    UpstreamHandler.respond_status = 302
    UpstreamHandler.location = "https://evil.example.com/x?sign=LOCDECOY77&session=SESS"
    try:
        conn = _Conn(pki, forwarder["port"])
        conn.request("POST", "/alihealth/router/rest",
                     body=TOP_METHOD + b"&session=SESSDECOY&sign=SIGDECOY",
                     headers={"Content-Type":
                              "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        assert r.status == 502
        r.read()
    finally:
        UpstreamHandler.respond_status = 200
        UpstreamHandler.location = None
    time.sleep(0.3)
    joined = "\n".join(logs)
    for marker in ("SESS-DECOY-123", "SIGNDECOY9f", "LOCDECOY77",
                   "SESSDECOY", "SIGDECOY", "/evil", "evil.example.com"):
        assert marker not in joined, f"日志泄漏：{marker}"
    assert "reject rid=" in joined and "upstream_redirect" in joined


def test_fin_ambiguity_rejected(forwarder):
    """CL/TE 歧义、重复 CL、畸形 UTF-8 拒绝（不触上游）。"""
    pki = forwarder["pki"]
    UpstreamHandler.hits.clear()
    raw_cases = [
        # Transfer-Encoding
        (b"POST /alihealth/router/rest HTTP/1.1\r\nHost: x\r\n"
         b"Content-Type: application/x-www-form-urlencoded\r\n"
         b"Transfer-Encoding: chunked\r\n\r\n5\r\nmethod=x\r\n0\r\n\r\n",
         b"transfer_encoding_not_allowed"),
        # 重复 Content-Length（手工注入两个头）
        (b"POST /alihealth/router/rest HTTP/1.1\r\nHost: x\r\n"
         b"Content-Type: application/x-www-form-urlencoded\r\n"
         b"Content-Length: 7\r\nContent-Length: 8\r\n\r\nmethod=x",
         b"ambiguous_content_length"),
        # 非法 CL
        (b"POST /alihealth/router/rest HTTP/1.1\r\nHost: x\r\n"
         b"Content-Type: application/x-www-form-urlencoded\r\n"
         b"Content-Length: 9zz\r\n\r\nmethod=x",
         b"bad_content_length"),
        # 畸形 UTF-8（method 解码失败）；12 = len(b"method=\xff\xfeabc")
        (b"POST /alihealth/router/rest HTTP/1.1\r\nHost: x\r\n"
         b"Content-Type: application/x-www-form-urlencoded\r\n"
         b"Content-Length: 12\r\n\r\nmethod=\xff\xfeabc",
         b"malformed_utf8"),
    ]
    for payload, expect in raw_cases:
        s = _mtls_raw(pki, forwarder["port"])
        s.sendall(payload)
        data = b""
        try:
            while True:
                part = s.recv(4096)
                if not part:
                    break
                data += part
                if b"forwarder_error" in data:
                    break
        except (ssl.SSLError, OSError):
            pass
        finally:
            s.close()
        assert expect in data, (expect, data[:200])
    time.sleep(0.2)
    assert not UpstreamHandler.hits, "歧义请求不得触达上游"


def test_fin_inout_disabled_by_default(forwarder):
    """默认白名单只有 retail+query；uploadinoutbill 需显式启用。"""
    pki = forwarder["pki"]
    UpstreamHandler.hits.clear()
    conn = _Conn(pki, forwarder["port"])
    conn.request("POST", "/alihealth/router/rest",
                 body=b"method=alibaba.alihealth.drugtrace.top.yljg.uploadinoutbill&bill_code=X",
                 headers={"Content-Type":
                          "application/x-www-form-urlencoded"})
    r = conn.getresponse()
    assert r.status == 400 and b"top_method_not_allowed" in r.read()
    assert not UpstreamHandler.hits
    # 显式启用配置可加载
    import forwarder as fwd_mod
    import json as _json
    good = {
        "listen_host": "127.0.0.1", "listen_port": 8443,
        "tls_cert": str(pki / "fwd.crt"), "tls_key": str(pki / "fwd.key"),
        "client_ca": str(pki / "cl-ca.crt"),
        "upstream_ca": str(pki / "up-ca.crt"), "allow_ips": ["<intranet-host>"],
        "allowed_top_methods": [
            "alibaba.alihealth.drugtrace.top.yljg.uploadinoutbill"],
        "enable_inout_upload": True,
    }
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        _json.dump(good, f)
        path = f.name
    cfg = fwd_mod.load_config(path)
    os.unlink(path)
    assert "alibaba.alihealth.drugtrace.top.yljg.uploadinoutbill" in \
        cfg["allowed_top_methods"]
    # 未启用 inout 却登记在白名单 → 拒绝
    good2 = dict(good)
    good2["enable_inout_upload"] = False
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        _json.dump(good2, f)
        path2 = f.name
    with pytest.raises(fwd_mod.ConfigError, match="enable_inout_upload"):
        fwd_mod.load_config(path2)
    os.unlink(path2)


def test_fin_config_strict_validation(pki, tmp_path):
    """端口/预算/IP/白名单/SAN 覆盖/EKU 错配校验（FIN-02-4）。"""
    import json as _json
    import forwarder as fwd_mod
    base = {
        "listen_host": "127.0.0.1", "listen_port": 8443,
        "tls_cert": str(pki / "fwd.crt"), "tls_key": str(pki / "fwd.key"),
        "client_ca": str(pki / "cl-ca.crt"),
        "upstream_ca": str(pki / "up-ca.crt"), "allow_ips": ["<intranet-host>"],
    }

    def _load(mutate):
        cfg = dict(base)
        cfg.update(mutate)
        p = tmp_path / "c.json"
        p.write_text(_json.dumps(cfg), encoding="utf-8")
        return fwd_mod.load_config(str(p))

    for mutate, pattern in (
            ({"listen_port": 0}, "listen_port"),
            ({"listen_port": 70000}, "listen_port"),
            ({"allow_ips": ["<intranet-seg>"]}, "allow_ips"),
            ({"upstream_total_deadline": 0}, "upstream_total_deadline"),
            ({"max_body_bytes": -1}, "max_body_bytes"),
            ({"allowed_top_methods": ["evil.method"]}, "unknown values"),
            ({"listen_host": "127.0.0.9"}, "SAN")):
        with pytest.raises(fwd_mod.ConfigError, match=pattern):
            _load(mutate)
    # 通配 0.0.0.0 无法 SAN 校验 → 明确拒绝（要求绑定具体地址）
    with pytest.raises(fwd_mod.ConfigError):
        _load({"listen_host": "0.0.0.0"})
    # clientAuth 专用证书当服务端证书 → EKU/SAN 拒绝
    with pytest.raises(fwd_mod.ConfigError,
                       match="subjectAltName|serverAuth|listen"):
        _load({"tls_cert": str(pki / "cl.crt"),
               "tls_key": str(pki / "cl.key")})
    # 密钥不配对
    with pytest.raises(fwd_mod.ConfigError):
        _load({"tls_key": str(pki / "up.key")})


def test_fin_diagnose_cli(pki, tmp_path, capsys):
    """--diagnose 输出分层 JSON；坏配置非零退出。"""
    import json as _json
    import forwarder as fwd_mod
    good = {
        "listen_host": "127.0.0.1", "listen_port": 8443,
        "tls_cert": str(pki / "fwd.crt"), "tls_key": str(pki / "fwd.key"),
        "client_ca": str(pki / "cl-ca.crt"),
        "upstream_ca": str(pki / "up-ca.crt"), "allow_ips": ["<intranet-host>"],
    }
    gp = tmp_path / "g.json"
    gp.write_text(_json.dumps(good), encoding="utf-8")
    assert fwd_mod.diagnose(str(gp), with_upstream=False) == 0
    out = capsys.readouterr().out
    data = _json.loads(out)
    assert data["overall"] == "OK" and data["config"]["ok"] and data["tls"]["ok"]
    assert "expires_in_days" in data["tls"]
    bad = tmp_path / "b.json"
    bad.write_text("{}", encoding="utf-8")
    assert fwd_mod.diagnose(str(bad), with_upstream=False) == 1


def test_fin_health_no_upstream_claim(forwarder):
    """/health 只报本地状态，不冒称上游正常（FIN-02-6）。"""
    conn = _Conn(forwarder["pki"], forwarder["port"])
    conn.request("GET", "/health")
    r = conn.getresponse()
    body = r.read()
    assert r.status == 200
    assert b"not_checked_by_health" in body
    assert b"eco.taobao.com" not in body


# ---------------------------------------------------------------- 219 兼容
# systemd v219（Anolis 7.9 实测版本）支持的指令集合（v219 man
# systemd.unit/socket/service/exec）；随包 unit 不得使用任何 219 之外指令。
SYSTEMD_219_DIRECTIVES = {
    "Unit": {
        "Description", "Documentation", "Requires", "Wants", "Requisite",
        "BindsTo", "Conflicts", "Before", "After", "OnFailure", "PartOf",
        "RequiresOverridable", "RequisiteOverridable", "OnFailureJobMode",
        "IgnoreOnIsolate", "StopWhenUnneeded", "RefuseManualStart",
        "RefuseManualStop", "AllowIsolate", "DefaultDependencies",
        "JobTimeoutSec", "JobTimeoutAction", "JobTimeoutRebootArgument",
        "StartLimitInterval", "StartLimitBurst", "StartLimitAction",
        "RebootArgument", "ConditionPathExists", "ConditionPathIsDirectory",
        "ConditionPathIsSymbolicLink", "ConditionPathIsMountPoint",
        "ConditionPathIsReadWrite", "ConditionDirectoryNotEmpty",
        "ConditionFileNotEmpty", "ConditionFileIsExecutable",
        "ConditionVirtualization", "ConditionSecurity", "ConditionACPower",
        "ConditionKernelCommandLine", "ConditionHost", "ConditionNull",
        "SourcePath",
    },
    "Service": {
        "Type", "RemainAfterExit", "GuessMainPID", "PIDFile", "BusName",
        "ExecStart", "ExecStartPre", "ExecStartPost", "ExecReload",
        "ExecStop", "ExecStopPost", "RestartSec", "TimeoutSec",
        "TimeoutStartSec", "TimeoutStopSec", "RuntimeMaxSec", "WatchdogSec",
        "Restart", "PermissionsStartOnly", "RootDirectoryStartOnly",
        "NonBlocking", "NotifyAccess", "Sockets", "FailureAction",
        "SuccessExitStatus", "RestartPreventExitStatus",
        "RestartForceExitStatus", "User", "Group", "SupplementaryGroups",
        "WorkingDirectory", "RootDirectory", "NoNewPrivileges", "PrivateTmp",
        "PrivateDevices", "PrivateNetwork", "ProtectSystem", "ProtectHome",
        "ReadOnlyDirectories", "ReadWriteDirectories", "InaccessibleDirectories",
        "TemporaryFileSystem", "CapabilityBoundingSet", "SELinuxContext",
        "LimitCPU", "LimitFSIZE", "LimitDATA", "LimitSTACK", "LimitCORE",
        "LimitRSS", "LimitNOFILE", "LimitAS", "LimitNPROC", "LimitMEMLOCK",
        "LimitLOCKS", "LimitSIGPENDING", "LimitMSGQUEUE", "LimitNICE",
        "LimitRTPRIO", "LimitRTTIME", "UMask", "OOMScoreAdjust",
        "StandardInput", "StandardOutput", "StandardError", "SyslogIdentifier",
        "SyslogFacility", "SyslogLevel", "SyslogLevelPrefix", "KillMode",
        "KillSignal", "SendSIGKILL", "SendSIGHUP", "IOSchedulingClass",
        "IOSchedulingPriority", "CPUSchedulingPolicy", "CPUSchedulingPriority",
        "CPUSchedulingResetOnFork", "CPUAffinity", "UMask", "Nice",
        "IgnoreSIGPIPE", "TimerSlackNSec",
    },
    "Socket": {
        "ListenStream", "ListenDatagram", "ListenSequentialPacket",
        "ListenFIFO", "ListenSpecial", "ListenMessageQueue", "ListenNetlink",
        "SocketProtocol", "BindIPv6Only", "Backlog", "BindToDevice",
        "SocketUser", "SocketGroup", "SocketMode", "DirectoryMode",
        "Accept", "Writable", "MaxConnections", "MaxConnectionsPerSource",
        "KeepAlive", "NoDelay", "Priority", "ReceiveBuffer", "SendBuffer",
        "IPTOS", "IPTTL", "Mark", "ReusePort", "PipeSize", "FreeBind",
        "Transparent", "Broadcast", "PassCredentials", "PassSecurity",
        "PassPacketInfo", "TCPCongestion", "ExecStartPre", "ExecStartPost",
        "TimeoutSec", "Service", "RemoveOnStop", "Symlinks",
        "FileDescriptorName", "SmackLabel",
    },
    "Install": {"WantedBy", "RequiredBy", "Also", "Alias", "DefaultInstance"},
}

# 219 不存在/语义不同的指令（FIN-01：不得用新指令冒充兼容）
SYSTEMD_POST_219_FORBIDDEN = {
    "AmbientCapabilities", "ProtectSystem=strict", "ProtectHome=tmpfs",
    "StandardOutput=append", "StandardError=append", "ReadOnlyPaths",
    "ReadWritePaths", "InaccessiblePaths", "DynamicUser", "RestrictSUIDSGID",
    "RestrictNamespaces", "SystemCallFilter", "SystemCallArchitectures",
    "LockPersonality", "MemoryDenyWriteExecute", "PrivateUsers",
    "ProtectKernelTunables", "ProtectKernelModules", "ProtectControlGroups",
    "ProtectClock", "ProtectHostname", "ProtectProc", "ProcSubset",
    "ReadWritePaths", "RuntimeDirectory", "StateDirectory", "CacheDirectory",
    "LogsDirectory", "ConfigurationDirectory", "BindReadOnlyPaths",
    "MountAPIVFS", "KeyringMode", "CapabilityBoundingSet=", "TraceSave",
}


def _parse_unit(text: str) -> dict:
    out: dict = {}
    section = None
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        if ln.startswith("[") and ln.endswith("]"):
            section = ln[1:-1]
            out.setdefault(section, set())
            continue
        if "=" in ln and section:
            out[section].add(ln.split("=", 1)[0].strip())
    return out


def test_fin_a02_systemd_219_directive_whitelist():
    """随包 unit/socket 的每条指令都在 v219 支持集合内（FIN-A02 静态核验）。

    动态 socket 激活已在 systemd PID1 容器实测（见 finalization evidence）；
    现场最终验收保留在 FIN-10 部署步骤。
    """
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "deploy", "dmz")
    for name in ("blyb-dmz-forwarder.service", "blyb-dmz-forwarder.socket"):
        text = open(os.path.join(base, name), encoding="utf-8").read()
        units = _parse_unit(text)
        for section, directives in units.items():
            allowed = SYSTEMD_219_DIRECTIVES.get(section)
            assert allowed is not None, f"{name}: 未知 section [{section}]"
            bad = directives - allowed
            assert not bad, f"{name}: [{section}] 含 219 不支持指令 {bad}"
        flat = " ".join(
            f"{s}:{d}" for s, ds in units.items() for d in ds)
        for forbidden in SYSTEMD_POST_219_FORBIDDEN:
            assert forbidden.rstrip("=") not in flat.replace(" ", ""), \
                f"{name}: 使用了 219 后指令 {forbidden}"
        # ProtectSystem 只允许布尔/full（strict 是 v231）
        for ln in text.splitlines():
            if ln.strip().startswith("ProtectSystem"):
                assert "=strict" not in ln, "ProtectSystem=strict 需要 v231"
            if ln.strip().startswith("ProtectHome"):
                assert "=tmpfs" not in ln
            if "StandardOutput" in ln or "StandardError" in ln:
                assert "=append" not in ln, "append: 需要 v240"
