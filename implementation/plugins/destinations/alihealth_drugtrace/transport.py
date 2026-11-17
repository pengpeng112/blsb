"""传输层（W05/W06 前置 → FIX2-02/R2-11 修订）：HTTPS 经 DMZ 专用转发。

- 固定路径 /alihealth/router/rest → 官方 eco.taobao.com/router/rest 由 DMZ 组件
  映射；本层只指向配置的 DMZ 地址，不携带任意 URL；主机名必须命中可选
  allow_hosts 白名单（配置了白名单时）。
- 禁止重定向：任何 3xx 响应按错误处理（不跟随）；禁止自动重试：一次 POST 一次
  结果；超时/断连一律 UNKNOWN（上层先查询）。
- TLS：校验链与主机名（DMZ 证书须覆盖配置主机名；无 -k 语义）；mTLS 客户端
  证书/私钥只读挂载（cert_file/key_file → load_cert_chain，R2-11 修订）。
- 超时分层（真实实现，非两数相加冒充）：http.client 连接阶段用 connect_timeout
  建连；建连后 socket 切换 read_timeout 控制每次读；deadline 为总时限硬顶。
"""
from __future__ import annotations

import http.client
import math
import socket
import ssl
import threading
import time
import urllib.parse

from connector_sdk import RequestDeferred
from .rate_control import rate_limit_info, shared_pacer


class RequestPacer:
    """One budget for uploads and every query page to the same DMZ origin.

    Shared across short-lived transport instances in the single production
    worker. The DMZ retains its per-IP enforcement across processes. Waiting
    precedes the network deadline; no failed POST is retried here.
    """

    def __init__(self, interval=1.5, *, clock=None, sleep=None):
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError('request interval must be positive and finite')
        self.interval = interval
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.lock = threading.Lock()
        self.next_start = 0.0

    def wait(self):
        with self.lock:
            remaining = self.next_start - self.clock()
            while remaining > 0:
                self.sleep(remaining)
                remaining = self.next_start - self.clock()
            self.next_start = self.clock() + self.interval

    def cool_down(self, seconds=60.0):
        with self.lock:
            self.next_start = max(self.next_start, self.clock() + seconds)


_PACERS = {}
_PACERS_LOCK = threading.Lock()


def _origin_pacer(host, port):
    with _PACERS_LOCK:
        return _PACERS.setdefault((host.lower(), port), RequestPacer())


class TransportResult:
    __slots__ = ("http_status", "body", "error", "timeout", "rate_limit")

    def __init__(self, *, http_status=None, body=b"", error=None, timeout=False, rate_limit=None):
        self.http_status = http_status
        self.body = body
        self.error = error or {}
        self.timeout = timeout
        self.rate_limit = rate_limit


class RealTransport:
    """真实传输（经 DMZ 转发）。不重试、不缓存；mTLS 客户端证书可选挂载。

    FIX3-06 修订（G3-19）：total_deadline 是**硬顶**——读阶段按剩余预算分块
    读取（每块 socket 超时取 min(read_timeout, 剩余预算)），慢速持续流在预算
    耗尽时立即中断（旧实现 resp.read() 完成后才检查 deadline，涓流可无限占用
    连接）。响应大小设上限（默认 16MiB），超限即断。
    """

    MAX_RESPONSE_BYTES = 16 * 1024 * 1024

    def __init__(self, base_url: str, *, ca_file: str = "",
                 cert_file: str = "", key_file: str = "",
                 connect_timeout: float = 5.0, read_timeout: float = 20.0,
                 total_deadline: float = 60.0, allow_hosts: tuple = (),
                 max_response_bytes: int = MAX_RESPONSE_BYTES):
        if not base_url.startswith("https://"):
            raise ValueError("DMZ forward base_url must be https:// (TLS required)")
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("DMZ forward base_url must not carry path/query")
        self.host = parsed.hostname or ""
        if not self.host:
            raise ValueError("DMZ forward base_url missing host")
        if parsed.port:
            self.port = parsed.port
        else:
            self.port = 443
        if allow_hosts and self.host not in allow_hosts:
            raise ValueError(f"DMZ host {self.host!r} not in allow_hosts")
        self.base_url = base_url.rstrip("/")
        self._pacer = _origin_pacer(self.host, self.port)
        self._shared_pacer = shared_pacer(base_url)
        self.path = "/alihealth/router/rest"
        self.connect_timeout = float(connect_timeout)
        self.read_timeout = float(read_timeout)
        self.total_deadline = float(total_deadline)
        self.max_response_bytes = int(max_response_bytes)
        self._cert_file = cert_file
        self._key_file = key_file
        self._ssl_ctx = ssl.create_default_context(cafile=ca_file or None)
        if cert_file or key_file:
            # mTLS 客户端身份：文件须同时提供且可读（构造期校验，失败拒绝启动）
            if not (cert_file and key_file):
                raise ValueError("mTLS requires both cert_file and key_file")
            with open(cert_file, "rb"):
                pass
            with open(key_file, "rb"):
                pass
            self._ssl_ctx.load_cert_chain(cert_file, key_file)
        # 不关闭证书校验；无 SNI 覆盖（按主机名正常发送）

    def _read_body_with_deadline(self, resp, sock, started) -> tuple[bytes | None,
                                                                     TransportResult | None]:
        """分块读取响应体：剩余预算硬顶 + 单块 read_timeout + 大小上限。

        - 用 read1（单次底层 recv 返回）而非 read（amt）——read 会内部循环
          读满 amt/EOF，涓流响应每次都有字节到达时永不触发 socket 超时；
          read1 保证每块之间都有机会检查剩余预算。
        - getresponse() 在 Connection: close 下会置 conn.sock=None，socket
          引用必须在 request 之后、getresponse 之前捕获（由调用方传入）。
        返回 (body, None) 成功；或 (None, TransportResult) 失败（预算耗尽/
        响应过大/读超时——涓流慢响应不再能占用连接）。"""
        chunks: list[bytes] = []
        received = 0
        while True:
            remaining = self.total_deadline - (time.monotonic() - started)
            if remaining <= 0:
                return None, TransportResult(
                    error={"reason": "total-deadline-exceeded"}, timeout=True)
            if sock is not None:
                try:
                    sock.settimeout(min(self.read_timeout, remaining))
                except OSError:
                    pass
            budget_capped = remaining <= self.read_timeout
            try:
                chunk = resp.read1(65536)
            except (socket.timeout, TimeoutError) as exc:
                # 预算封顶的读超时即硬顶中断（涓流不再延长占用）
                reason = ("total-deadline-exceeded" if budget_capped
                          else f"timeout: {exc}"[:200])
                return None, TransportResult(
                    error={"reason": reason}, timeout=True)
            if not chunk:
                return b"".join(chunks), None
            chunks.append(chunk)
            received += len(chunk)
            if received > self.max_response_bytes:
                return None, TransportResult(
                    error={"reason": "response-size-limit-exceeded",
                           "limit": self.max_response_bytes})

    def post(self, body: bytes, content_type: str) -> TransportResult:
        form = urllib.parse.parse_qs(body.decode('utf8'), keep_blank_values=True)
        app_key = (form.get('app_key') or [''])[0]
        method = (form.get('method') or [''])[0]
        if self._shared_pacer:
            self._shared_pacer.acquire(app_key, method)
        else:
            self._pacer.wait()
        started = time.monotonic()
        conn = None
        try:
            # 连接阶段（TCP+TLS 握手）受 connect_timeout 约束
            conn = http.client.HTTPSConnection(
                self.host, self.port, context=self._ssl_ctx,
                timeout=self.connect_timeout)
            conn.request("POST", self.path, body=body, headers={
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
                "Accept": "*/*",
                "Connection": "close"})
            # close 语义下 getresponse 会释放 conn.sock——先捕获引用用于读超时
            sock = conn.sock
            resp = conn.getresponse()
            status = resp.status
            limit = rate_limit_info(status, retry_after=resp.getheader('Retry-After'))
            if limit:
                self._apply_cooldown(limit, app_key, method)
            if 300 <= status < 400:
                # 重定向禁止跟随：明确报错（不猜测新上游）
                return TransportResult(
                    http_status=status, body=b"",
                    error={"reason": "redirect-forbidden",
                           "location": resp.getheader("Location", "")[:200]})
            data, failure = self._read_body_with_deadline(resp, sock, started)
            if failure is not None:
                failure.rate_limit = limit
                return failure
            if not limit:
                limit = rate_limit_info(status, data, resp.getheader('Retry-After'))
                if limit:
                    self._apply_cooldown(limit, app_key, method)
            return TransportResult(http_status=status, body=data, rate_limit=limit)
        except (socket.timeout, TimeoutError) as exc:
            return TransportResult(error={"reason": f"timeout: {exc}"[:200]},
                                   timeout=True)
        except (http.client.HTTPException, ssl.SSLError, OSError) as exc:
            return TransportResult(error={"reason": str(exc)[:200]})
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 —— 释放连接尽力而为
                    pass

    def _apply_cooldown(self, info, app_key, method):
        seconds = info.get('retry_after') or (60 if info.get('source') == 'http429' else 180)
        if self._shared_pacer:
            try:
                seconds = self._shared_pacer.cool_down(info, app_key, method)
            except (RequestDeferred, OSError):
                # The request HAS happened: never turn this into a pre-send
                # deferral or retry an upload because persistence failed.
                info['shared_state_unavailable'] = True
                self._pacer.cool_down(seconds)
        else:
            self._pacer.cool_down(seconds)
        info['retry_after'] = max(1, math.ceil(seconds))


class MockTransport:
    """离线测试传输：脚本化响应（由测试注入 http_status/body/body_fn）。

    Mock 结果不得作为正式签名/平台确认通过证据（DTR-B03）。未验签阶段的签名
    豁免以本类型为强绑定（protocol.assert_signing_allowed），与密钥值无关。
    """

    def __init__(self, *, http_status=200, body=b'{"_mock": true}',
                 body_fn=None):
        self.http_status = http_status
        self.body = body
        self.body_fn = body_fn
        self.calls: list[bytes] = []

    def post(self, body: bytes, content_type: str) -> TransportResult:
        self.calls.append(body)
        if self.body_fn is not None:
            status, payload = self.body_fn(body)
            return TransportResult(http_status=status, body=payload)
        return TransportResult(http_status=self.http_status, body=self.body)
