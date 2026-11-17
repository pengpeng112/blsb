#!/usr/bin/env python3
"""DMZ 专用 HTTPS 转发器 v2（DTR-FINALIZE-20261020 FIN-01/02/03 重写）。

目标环境（2026-10-20 实测，evidence/dmz-version-inventory-20261020.json）：
Anolis OS 7.9 / systemd 219 / glibc 2.17 / 系统 OpenSSL 1.0.2k / **无系统 python3**。
因此：解释器来自随包私有运行时（python-build-standalone cpython-3.11.9，
自带 OpenSSL 3.0.13，静态链接，已在 centos:7 glibc2.17 容器实测 ssl/SNI/CA）；
仅用 stdlib，不用 219 之后才存在的 systemd 指令。

监听模式（FIN-01）：
- systemd：443 由 blyb-dmz-forwarder.socket 持有，本进程以非 root 用户接收
  systemd 传入的监听 FD。验证 LISTEN_PID / FD 数量与类型（SO_ACCEPTCONN、
  AF_INET、实际 bind 地址端口 == 配置），任何不符立即退出，绝不静默降级。
- explicit：本地测试/独立部署自己 bind（配置 listen_host:listen_port）。
- auto（默认）：有 LISTEN_FDS 则按 systemd 处理，否则 explicit。

固定语义（不可配置为开放代理；FIN-02 全量保留）：
- 本地路径唯一 POST /alihealth/router/rest → 上游 https://eco.taobao.com/router/rest
  （Host/SNI 固定；请求 Host 头不转发上游；不读代理环境变量）。
- TOP method 白名单默认 {uploadretail, query.billstatus}；uploadinoutbill 仅在
  配置 enable_inout_upload=true 时加入（本期零售口径）。
- 内网侧全链路 mTLS（客户端证书必须由 client_ca 签发）+ 业务 POST 源 IP 白名单；
  GET /health 同样要求 mTLS（运维探针证书），只报本地进程状态，不冒称上游正常。
- 字节保持：请求/响应 body 原样转发（只读解析 method，不重编码/不重签/不缓存）；
  每请求至多一次上游调用，无重试；上游 3xx 视为错误不跟随。
- 预算（FIN-A08/A09）：TLS 握手 / 请求头 / 请求体 / 上游连接 / 上游响应头+体 /
  客户端回写各有独立预算，且总时限覆盖全部阶段；慢 TLS/慢头/慢体在握手前连接
  限额与各阶段超时内释放线程，不阻塞 accept。
- 日志脱敏（FIN-A10）：只记随机关联 rid / 方法枚举 / 字节数 / 状态 / 耗时 /
  错误分类；不记请求行、路径、查询串、header、body、签名、session、追溯码，
  不记上游 Location。

退出码：--check-config / --diagnose 任何必需层失败 → 非零。
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import logging
import os
import secrets
import socket
import ssl
import sys
import threading
import time
from datetime import datetime, timezone
from http.client import HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

UPSTREAM_HOST = "eco.taobao.com"
UPSTREAM_PORT = 443
UPSTREAM_PATH = "/router/rest"
LOCAL_PATH = "/alihealth/router/rest"
USER_AGENT = "blyb-dmz-forwarder/2.0"

# TOP method 白名单全集（与 adapter 协议一致；实际允许集来自配置）
KNOWN_TOP_METHODS = frozenset({
    "alibaba.alihealth.drugtrace.top.yljg.uploadinoutbill",
    "alibaba.alihealth.drugtrace.top.yljg.uploadretail",
    "alibaba.alihealth.drugtrace.top.yljg.query.billstatus",
})
DEFAULT_TOP_METHODS = frozenset({
    "alibaba.alihealth.drugtrace.top.yljg.uploadretail",
    "alibaba.alihealth.drugtrace.top.yljg.query.billstatus",
})
ALLOWED_CONTENT_TYPE = "application/x-www-form-urlencoded"

REQUIRED_CONFIG = ("listen_port", "tls_cert", "tls_key", "client_ca",
                   "allow_ips", "upstream_ca")

log = logging.getLogger("blyb.dmz")


class ConfigError(Exception):
    """配置/材料校验失败（--check-config 与 --diagnose 的非零退出依据）。"""


# ---------------------------------------------------------------- 证书检查
def _decode_cert(path: str) -> dict:
    """stdlib 解码证书元数据（SAN/有效期；私有但 3.7—3.14 稳定可用）。"""
    try:
        return ssl._ssl._test_decode_cert(path)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"cannot parse certificate {path}: {exc}") from exc


def _cert_eku(path: str) -> set[str]:
    """证书 EKU OID 存在性检查（字节扫描，不替代链校验）。

    serverAuth=1.3.6.1.5.5.7.3.1 clientAuth=...3.2。扫描 DER/PEM 中的编码序列；
    目的是拒绝“拿 clientAuth 专用证书当服务端证书”这类明显错配（FIN-A28），
    完整 X.509 校验仍由 ssl 上下文加载与真实握手完成。
    """
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        raise ConfigError(f"cannot read certificate {path}: {exc}") from exc
    if raw.lstrip().startswith(b"-----BEGIN"):
        # PEM：逐块取 base64 解码
        import base64
        found: set[str] = set()
        pem = raw.decode("ascii", "replace")
        lines, buf = pem.splitlines(), []
        for ln in lines:
            if ln.startswith("-----BEGIN CERTIFICATE"):
                buf = []
            elif ln.startswith("-----END CERTIFICATE"):
                try:
                    der = base64.b64decode("".join(buf))
                    found |= _eku_oids_der(der)
                except Exception:  # noqa: BLE001
                    pass
                buf = []
            elif buf is not None and ln:
                buf.append(ln)
        return found
    return _eku_oids_der(raw)


def _eku_oids_der(der: bytes) -> set[str]:
    out: set[str] = set()
    if b"\x2b\x06\x01\x05\x05\x07\x03\x01" in der:   # 1.3.6.1.5.5.7.3.1
        out.add("serverAuth")
    if b"\x2b\x06\x01\x05\x05\x07\x03\x02" in der:   # ...3.2
        out.add("clientAuth")
    return out


def _san_covers(san_list, host: str) -> bool:
    """SAN（[('IP Address','x')]/[('DNS','y')]）是否覆盖 listen 地址。"""
    try:
        want_ip = ipaddress.ip_address(host)
    except ValueError:
        want_ip = None
    for kind, value in san_list or []:
        if kind == "IP Address" and want_ip is not None:
            try:
                if ipaddress.ip_address(value) == want_ip:
                    return True
            except ValueError:
                continue
        if kind == "DNS" and want_ip is None and value == host:
            return True
    return False


def _cert_expiry(path: str) -> datetime | None:
    try:
        return datetime.strptime(_decode_cert(path)["notAfter"],
                                 "%b %d %H:%M:%S %Y %Z").replace(
                                             tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 配置
def apply_defaults(cfg: dict) -> dict:
    """缺省值填充（make_server 直连配置也走同一默认，不依赖 load_config）。"""
    cfg.setdefault("listen_host", "0.0.0.0")
    cfg.setdefault("max_body_bytes", 2 * 1024 * 1024)
    cfg.setdefault("max_response_bytes", 8 * 1024 * 1024)
    cfg.setdefault("rate_per_minute", 60)
    cfg.setdefault("rate_burst", 10)
    cfg.setdefault("max_concurrency", 8)
    cfg.setdefault("max_pending_handshakes", 16)
    cfg.setdefault("upstream_connect_timeout", 5.0)
    cfg.setdefault("upstream_read_timeout", 20.0)
    cfg.setdefault("upstream_total_deadline", 60.0)
    cfg.setdefault("tls_handshake_timeout", 10.0)
    cfg.setdefault("header_timeout", 15.0)
    cfg.setdefault("body_read_timeout", 30.0)
    cfg.setdefault("write_timeout", 30.0)
    cfg.setdefault("total_request_deadline", 120.0)
    cfg.setdefault("backup_listen_port", 0)
    if not cfg.get("allowed_top_methods"):
        cfg["allowed_top_methods"] = frozenset(DEFAULT_TOP_METHODS)
    return cfg


def load_config(path: str, *, strict_tls: bool = True) -> dict:
    """加载并完整校验配置（--check-config 不能只看文件存在，FIN-02-4）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"config unreadable: {exc}") from exc
    if not isinstance(cfg, dict):
        raise ConfigError("config must be a JSON object")
    missing = [k for k in REQUIRED_CONFIG if k not in cfg]
    if missing:
        raise ConfigError(f"config missing keys: {missing}")

    port = cfg["listen_port"]
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise ConfigError("listen_port must be int in 1..65535")
    backup = cfg.get("backup_listen_port", 0)
    if backup not in (0,) and (not isinstance(backup, int)
                               or not (1 <= backup <= 65535)):
        raise ConfigError("backup_listen_port must be 0 or int in 1..65535")
    host = cfg.setdefault("listen_host", "0.0.0.0")
    try:
        ipaddress.ip_address(str(host))
    except ValueError as exc:
        raise ConfigError(f"listen_host must be a literal IP: {host}") from exc
    if not isinstance(cfg["allow_ips"], list) or not cfg["allow_ips"]:
        raise ConfigError("allow_ips must be a non-empty list")
    for ip in cfg["allow_ips"]:
        try:
            ipaddress.ip_address(str(ip))
        except ValueError as exc:
            raise ConfigError(f"allow_ips entry not a literal IP: {ip}") from exc

    # 数值预算：默认值先行，再统一校验（缺省键不得被误判为非法）
    apply_defaults(cfg)
    num_limits = {
        "max_body_bytes": (int, 1024), "max_response_bytes": (int, 1024),
        "rate_per_minute": (int, 1), "rate_burst": (int, 1),
        "max_concurrency": (int, 1), "max_pending_handshakes": (int, 1),
        "upstream_connect_timeout": (float, 0.05),
        "upstream_read_timeout": (float, 0.05),
        "upstream_total_deadline": (float, 0.05),
        "tls_handshake_timeout": (float, 0.05),
        "header_timeout": (float, 0.05),
        "body_read_timeout": (float, 0.05),
        "write_timeout": (float, 0.05),
        "total_request_deadline": (float, 0.05),
    }
    for key, (kind, minimum) in num_limits.items():
        v = cfg.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or v < minimum:
            raise ConfigError(f"{key} must be a number >= {minimum}")

    # TOP method 白名单：默认 retail+query；inout 需显式启用
    methods = set(cfg.get("allowed_top_methods")
                  or DEFAULT_TOP_METHODS)
    unknown = methods - KNOWN_TOP_METHODS
    if unknown:
        raise ConfigError(f"allowed_top_methods has unknown values: "
                          f"{sorted(unknown)}")
    if "alibaba.alihealth.drugtrace.top.yljg.uploadinoutbill" in methods \
            and not cfg.get("enable_inout_upload"):
        raise ConfigError("uploadinoutbill requires enable_inout_upload=true "
                          "(explicit opt-in; retail is the active profile)")
    cfg["allowed_top_methods"] = frozenset(methods)

    for k in ("tls_cert", "tls_key", "client_ca", "upstream_ca"):
        if not os.path.isfile(cfg[k]):
            raise ConfigError(f"file not found for {k}: {cfg[k]}")

    if strict_tls:
        _validate_tls(cfg)
    return cfg


def _validate_tls(cfg: dict) -> None:
    """TLS 材料完整校验：配对/CA 可加载/SAN 覆盖监听地址/EKU/有效期。"""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        ctx.load_cert_chain(cfg["tls_cert"], cfg["tls_key"])
    except ssl.SSLError as exc:
        raise ConfigError(f"tls cert/key load failed (mismatched pair?): "
                          f"{type(exc).__name__}") from exc
    try:
        ctx.load_verify_locations(cafile=cfg["client_ca"])
    except ssl.SSLError as exc:
        raise ConfigError(f"client_ca not loadable: {type(exc).__name__}"
                          ) from exc
    try:
        ssl.create_default_context(cafile=cfg["upstream_ca"])
    except ssl.SSLError as exc:
        raise ConfigError(f"upstream_ca not loadable: {type(exc).__name__}"
                          ) from exc
    cert = _decode_cert(cfg["tls_cert"])
    san = cert.get("subjectAltName") or []
    if not san:
        raise ConfigError("tls_cert has no subjectAltName; modern clients "
                          "will reject it")
    if not _san_covers(san, cfg["listen_host"]):
        raise ConfigError(f"tls_cert SAN does not cover listen address "
                          f"{cfg['listen_host']} (SAN={san})")
    eku = _cert_eku(cfg["tls_cert"])
    if eku and "serverAuth" not in eku:
        raise ConfigError(f"tls_cert EKU lacks serverAuth (found {sorted(eku)})")
    expiry = _cert_expiry(cfg["tls_cert"])
    if expiry is not None and expiry <= datetime.now(timezone.utc):
        raise ConfigError(f"tls_cert expired at {expiry.isoformat()}")


# ---------------------------------------------------------------- systemd FD
def receive_systemd_socket(cfg: dict, *, fd_base: int = 3) -> socket.socket:
    """按 systemd sd_listen_fds 语义接收监听 FD（FIN-A03 严格验证，不降级）。

    fd_base 生产恒为 3（sd_listen_fds 约定）；参数仅供测试注入。
    """
    pid = os.environ.get("LISTEN_PID")
    fds = os.environ.get("LISTEN_FDS")
    names = os.environ.get("LISTEN_FDNAMES", "")
    if pid is None or fds is None:
        raise ConfigError("systemd listen mode requires LISTEN_PID/LISTEN_FDS")
    try:
        if int(pid) != os.getpid():
            raise ConfigError(f"LISTEN_PID {pid} != pid {os.getpid()}")
        n = int(fds)
    except ValueError as exc:
        raise ConfigError(f"invalid LISTEN_* values: {exc}") from exc
    if n < 1:
        raise ConfigError("LISTEN_FDS=0: no listening socket passed")
    first = fd_base
    # 关闭多余 FD（不用即关；不留悬空句柄）
    for extra in range(first + 1, first + n):
        os.close(extra)
    try:
        sock = socket.socket(fileno=first)
    except OSError as exc:
        raise ConfigError(f"cannot adopt FD {first}: {exc}") from exc
    try:
        if sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 0:
            raise ConfigError("passed FD is not a listening socket")
        if sock.family != socket.AF_INET:
            raise ConfigError("passed FD is not AF_INET")
        actual = sock.getsockname()
        want = (cfg["listen_host"], cfg["listen_port"])
        if actual[0] not in (want[0], "0.0.0.0") or actual[1] != want[1]:
            raise ConfigError(f"passed FD listens on {actual}, config expects "
                              f"{want} (mismatch refuses to serve)")
    except ConfigError:
        try:
            sock.close()
        except OSError:
            pass
        raise
    log.info("adopted systemd listening fd (fd=3, addr=%s, names=%r)",
             sock.getsockname(), names)
    return sock


# ---------------------------------------------------------------- 限流/并发
class TokenBucket:
    def __init__(self, rate_per_min: int, burst: int):
        self.rate = rate_per_min / 60.0
        self.burst = float(burst)
        self.tokens = float(burst)
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def allow(self) -> bool:
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.burst,
                              self.tokens + (now - self.updated) * self.rate)
            self.updated = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False


class State:
    """运行态：per-IP 令牌桶 + 握手前连接限额 + 上游并发限额。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.buckets: dict[str, TokenBucket] = {}
        self.bucket_lock = threading.Lock()
        self.active = 0              # 上游转发在途
        self.pending_handshakes = 0  # 已接受未完成 TLS 握手（FIN-A08）
        self.handshake_rejected = 0
        self.handshake_failed = 0
        self.lock = threading.Lock()

    def bucket_for(self, ip: str) -> TokenBucket:
        with self.bucket_lock:
            if ip not in self.buckets:
                self.buckets[ip] = TokenBucket(self.cfg["rate_per_minute"],
                                               self.cfg["rate_burst"])
            return self.buckets[ip]

    def enter_handshake(self) -> bool:
        with self.lock:
            if self.pending_handshakes >= self.cfg["max_pending_handshakes"]:
                self.handshake_rejected += 1
                return False
            self.pending_handshakes += 1
            return True

    def leave_handshake(self) -> None:
        with self.lock:
            self.pending_handshakes -= 1

    def enter(self) -> bool:
        with self.lock:
            if self.active >= self.cfg["max_concurrency"]:
                return False
            self.active += 1
            return True

    def leave(self) -> None:
        with self.lock:
            self.active -= 1


class Deadline:
    """阶段预算 + 总时限（FIN-02-1）。"""

    def __init__(self, total: float):
        self.started = time.monotonic()
        self.total = total

    def remaining(self) -> float:
        return self.total - (time.monotonic() - self.started)

    def budget(self, stage: float) -> float:
        return max(0.0, min(stage, self.remaining()))

    def expired(self) -> bool:
        return self.remaining() <= 0


# ---------------------------------------------------------------- 服务器
class MTLSServer(ThreadingHTTPServer):
    """TLS 握手在 worker 线程完成；accept 循环只做准入控制。

    慢 TLS 握手/空连接不再阻塞 accept（FIN-A08）：主线程 accept 原始 socket
    → 握手前限额检查（超限立即 close）→ 线程内带超时握手 → 失败计数并关闭。
    """

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(self, cfg: dict, raw_sock: socket.socket,
                 handler_class=None):
        cfg = apply_defaults(cfg)
        self.state = State(cfg)
        self.tls_context = self._build_context(cfg)
        super().__init__((cfg["listen_host"], cfg["listen_port"]),
                         handler_class or ForwarderHandler,
                         bind_and_activate=False)
        self.socket.close()          # 关闭 __init__ 自建的未绑定 socket
        self.socket = raw_sock
        host, port = raw_sock.getsockname()[:2]
        self.server_address = (host, port)   # bind_and_activate=False 不会更新
        self.server_name = str(host)   # 不做 DNS 反查（避免阻塞启动）
        self.server_port = port

    @staticmethod
    def _build_context(cfg: dict) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cfg["tls_cert"], cfg["tls_key"])
        ctx.verify_mode = ssl.CERT_REQUIRED    # 全链路 mTLS（含 /health）
        ctx.load_verify_locations(cafile=cfg["client_ca"])
        return ctx

    # ---- 准入：主线程 accept 之后立刻执行（快速、不阻塞）
    def process_request(self, request, client_address):
        if not self.state.enter_handshake():
            log.warning("handshake_over_limit ip=%s pending=%d",
                        client_address[0], self.state.pending_handshakes)
            try:
                request.close()
            except OSError:
                pass
            return
        try:
            super().process_request(request, client_address)
        except Exception:  # noqa: BLE001 —— 线程创建失败也要释放名额
            self.state.leave_handshake()
            raise

    # ---- worker 线程：握手 + 处理 + 收尾
    def process_request_thread(self, request, client_address):
        try:
            try:
                request.settimeout(self.state.cfg["tls_handshake_timeout"])
                tls_sock = self.tls_context.wrap_socket(
                    request, server_side=True)
            except (ssl.SSLError, OSError) as exc:
                self.state.handshake_failed += 1
                log.info("handshake_failed ip=%s err=%s",
                         client_address[0], type(exc).__name__)
                return
            try:
                super().process_request_thread(tls_sock, client_address)
            finally:
                try:
                    tls_sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    tls_sock.close()
                except OSError:
                    pass
        finally:
            self.state.leave_handshake()

    def handle_error(self, request, client_address):
        # 默认 handle_error 会打印完整异常栈（可能带 header/body 内容）
        log.warning("handler_error ip=%s err=%s", client_address[0],
                    sys.exc_info()[0].__name__ if sys.exc_info()[0] else "?")


class ForwarderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "blyb-dmz"
    sys_version = ""
    # 请求头读取超时（StreamRequestHandler.setup 应用到 socket）
    timeout = None  # 每连接在 setup 阶段按配置设置

    # ------------------------------------------------------------ 日志脱敏
    def log_message(self, fmt, *args):  # 覆写：不含请求行/路径
        log.info("conn ip=%s: " + fmt, self.client_address[0], *args)

    def log_request(self, code="-", size="-"):  # 覆写：绝不含 requestline
        log.info("req rid=%s method=%s status=%s size=%s",
                 getattr(self, "rid", "-"), self.command, code, size)

    def log_error(self, fmt, *args):
        log.warning("err rid=%s ip=%s: " + fmt,
                    getattr(self, "rid", "-"), self.client_address[0], *args)

    # ------------------------------------------------------------ 基础
    def setup(self):
        cfg = self.server.state.cfg
        self.timeout = cfg["header_timeout"]
        super().setup()

    def handle_one_request(self):
        # 每个请求（含 keep-alive 第二请求）重置 rid 与总时限
        cfg = self.server.state.cfg
        self.rid = secrets.token_hex(6)
        self.deadline = Deadline(cfg["total_request_deadline"])
        try:
            self.connection.settimeout(
                self.deadline.budget(cfg["header_timeout"]))
        except OSError:
            pass
        return super().handle_one_request()

    def _reject(self, status: int, reason: str) -> None:
        body = json.dumps({"forwarder_error": reason},
                          ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass
        # 只记 rid/方法枚举/状态/原因；不记 path/查询串/header/body
        log.warning("reject rid=%s ip=%s method=%s status=%d reason=%s",
                    getattr(self, "rid", "-"), self.client_address[0],
                    self.command, status, reason)

    def _source_allowed(self) -> bool:
        return self.client_address[0] in self.server.state.cfg["allow_ips"]

    def _write_timeout(self) -> None:
        cfg = self.server.state.cfg
        try:
            self.connection.settimeout(self.deadline.budget(cfg["write_timeout"]))
        except OSError:
            pass

    # ------------------------------------------------------------ 入口
    def do_GET(self):
        # /health 仅本地进程状态：上游不可达不能变成“平台正常”（FIN-02-6）
        if self.path.split("?")[0] == "/health":
            st = self.server.state
            body = json.dumps(
                {"status": "UP", "service": "blyb-dmz-forwarder",
                 "upstream": "not_checked_by_health",
                 "active_upstream": st.active,
                 "pending_handshakes": st.pending_handshakes,
                 "rid": self.rid}, ensure_ascii=False).encode("utf-8")
            self._write_timeout()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass
            return
        self._reject(404, "not_found")

    def do_POST(self):
        if self.path.split("?")[0] != LOCAL_PATH:
            self._reject(404, "not_found")
            return
        if not self._source_allowed():
            self._reject(403, "source_ip_not_allowed")
            return
        st = self.server.state
        if not st.bucket_for(self.client_address[0]).allow():
            self._reject(429, "rate_limited")
            return
        if not st.enter():
            self._reject(503, "too_busy")
            return
        try:
            self._forward()
        finally:
            st.leave()

    def do_CONNECT(self):  # 显式拒绝隧道（不得变成开放代理）
        self._reject(405, "connect_not_allowed")

    def do_PUT(self):
        self._reject(405, "method_not_allowed")

    do_DELETE = do_PUT
    do_HEAD = do_PUT
    do_OPTIONS = do_PUT
    do_TRACE = do_PUT
    do_PATCH = do_PUT

    # ------------------------------------------------------------ 请求解析
    def _read_body(self) -> bytes | None:
        cfg = self.server.state.cfg
        if self.headers.get("Transfer-Encoding"):
            self._reject(400, "transfer_encoding_not_allowed")
            return None
        cls_ = self.headers.get_all("Content-Length") or []
        if len(cls_) > 1:
            self._reject(400, "ambiguous_content_length")
            return None
        raw = (cls_[0] if cls_ else self.headers.get("Content-Length", "0")
               ) or "0"
        try:
            length = int(raw.strip())
        except (ValueError, AttributeError):
            self._reject(400, "bad_content_length")
            return None
        cap = cfg["max_body_bytes"]
        if length < 0 or length > cap:
            self._reject(413, "body_too_large")
            return None
        if length == 0:
            return b""
        # 慢 body 预算：总时限内按 body_read_timeout 收满
        chunks = []
        received = 0
        try:
            self.connection.settimeout(
                self.deadline.budget(cfg["body_read_timeout"]))
        except OSError:
            pass
        while received < length:
            if self.deadline.expired():
                self._reject(408, "request_deadline_exceeded")
                return None
            try:
                chunk = self.rfile.read(min(65536, length - received))
            except (socket.timeout, TimeoutError, OSError):
                self._reject(408, "body_read_timeout")
                return None
            if not chunk:
                self._reject(400, "incomplete_body")
                return None
            chunks.append(chunk)
            received += len(chunk)
        return b"".join(chunks)

    def _checked_method(self, body: bytes) -> str | None:
        """表单 method 白名单校验（UTF-8 严格、重复/未知拒绝）。"""
        try:
            text = body.decode("utf-8")     # errors=strict：畸形 UTF-8 拒绝
        except UnicodeDecodeError:
            self._reject(400, "malformed_utf8")
            return None
        try:
            methods = parse_qs(text, keep_blank_values=True,
                               strict_parsing=False, separator="&").get(
                                   "method") or []
        except ValueError:
            self._reject(400, "malformed_form")
            return None
        if len(methods) != 1 or not methods[0] or methods[0] not in \
                self.server.state.cfg["allowed_top_methods"]:
            self._reject(400, "top_method_not_allowed")
            return None
        return methods[0]

    # ------------------------------------------------------------ 转发
    def _forward(self) -> None:
        cfg = self.server.state.cfg
        body = self._read_body()
        if body is None:
            return
        content_type = self.headers.get("Content-Type", "") or \
            "application/x-www-form-urlencoded;charset=utf-8"
        if not content_type.split(";")[0].strip().lower() == \
                ALLOWED_CONTENT_TYPE:
            self._reject(415, "content_type_not_allowed")
            return
        if self._checked_method(body) is None:
            return
        if self.deadline.expired():
            self._reject(408, "request_deadline_exceeded")
            return
        digest = hashlib.sha256(body).hexdigest()[:12]   # 只记哈希指纹
        upstream_deadline = Deadline(cfg["upstream_total_deadline"])
        conn = None
        try:
            ctx = ssl.create_default_context(cafile=cfg["upstream_ca"])
            # 分阶段超时：connect → headers；body 逐块用剩余预算收紧
            conn = HTTPSConnection(
                UPSTREAM_HOST, UPSTREAM_PORT, context=ctx,
                timeout=cfg["upstream_connect_timeout"])
            conn.request("POST", UPSTREAM_PATH, body=body, headers={
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
                "Accept": "*/*",
                "User-Agent": USER_AGENT,
            })
            sock = conn.sock   # close 语义下 getresponse 释放 sock——先持引用
            remaining = upstream_deadline.budget(cfg["upstream_read_timeout"])
            if sock is not None and remaining > 0:
                sock.settimeout(remaining)
            resp = conn.getresponse()
            status = resp.status
            if 300 <= status < 400:
                # 上游重定向 = 不可信语义；Location 可能带签名查询串——不记录
                log.warning("upstream_redirect rid=%s status=%d",
                            self.rid, status)
                self._reject(502, "upstream_redirect_not_allowed")
                return
            resp_cap = cfg["max_response_bytes"]
            resp_parts = []
            received = 0
            while True:
                remaining = upstream_deadline.budget(
                    cfg["upstream_read_timeout"])
                if remaining <= 0:
                    log.error("upstream_deadline rid=%s digest=%s",
                              self.rid, digest)
                    self._reject(504, "upstream_total_deadline_exceeded")
                    return
                if sock is not None:
                    try:
                        sock.settimeout(remaining)
                    except OSError:
                        pass
                chunk = resp.read1(65536)
                if not chunk:
                    break
                resp_parts.append(chunk)
                received += len(chunk)
                if received > resp_cap:
                    self._reject(502, "upstream_response_too_large")
                    return
            resp_body = b"".join(resp_parts)
            resp_type = resp.getheader("Content-Type",
                                       "application/octet-stream")
        except (socket.timeout, TimeoutError):
            log.error("upstream_timeout rid=%s digest=%s size=%d",
                      self.rid, digest, len(body))
            self._reject(504, "upstream_timeout_result_unknown")
            return
        except ConnectionRefusedError:
            log.error("upstream_refused rid=%s", self.rid)
            self._reject(502, "upstream_tcp_refused")
            return
        except socket.gaierror:
            log.error("upstream_dns_failure rid=%s", self.rid)
            self._reject(502, "upstream_dns_failure")
            return
        except ssl.SSLError as exc:
            log.error("upstream_tls_failure rid=%s err=%s",
                      self.rid, type(exc).__name__)
            self._reject(502, "upstream_tls_failure")
            return
        except OSError as exc:
            log.error("upstream_failure rid=%s err=%s digest=%s size=%d",
                      self.rid, type(exc).__name__, digest, len(body))
            self._reject(502, "upstream_failure_result_unknown")
            return
        except Exception as exc:  # noqa: BLE001 —— 其余按未知网关错误
            log.error("upstream_failure rid=%s err=%s",
                      self.rid, type(exc).__name__)
            self._reject(502, "upstream_failure_result_unknown")
            return
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        took_ms = int((time.monotonic() - upstream_deadline.started) * 1000)
        log.info("forward rid=%s ip=%s size=%d digest=%s status=%d "
                 "resp_size=%d elapsed_ms=%d",
                 self.rid, self.client_address[0], len(body), digest,
                 status, len(resp_body), took_ms)
        self._write_timeout()
        try:
            self.send_response(status)
            self.send_header("Content-Type", resp_type)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        except OSError:
            log.warning("write_failed rid=%s status=%d", self.rid, status)


# ---------------------------------------------------------------- 服务器构建
def make_server(cfg: dict, *, mode: str) -> MTLSServer:
    if mode == "systemd":
        raw = receive_systemd_socket(cfg)
    elif mode == "explicit":
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        raw.bind((cfg["listen_host"], cfg["listen_port"]))
        raw.listen(128)
    else:
        raise ConfigError(f"unknown listen mode {mode!r}")
    return MTLSServer(cfg, raw)


# ---------------------------------------------------------------- 诊断 CLI
def diagnose(cfg_path: str, *, with_upstream: bool) -> int:
    """分层诊断：runtime/config/tls[/upstream]，脱敏 JSON；失败层 → 非零。"""
    out: dict = {"runtime": {
        "python": sys.version.split()[0],
        "openssl": ssl.OPENSSL_VERSION,
        "pid": os.getpid()}}
    ok = True
    try:
        cfg = load_config(cfg_path, strict_tls=True)
        cert = _decode_cert(cfg["tls_cert"])
        expiry = _cert_expiry(cfg["tls_cert"])
        days = ((expiry - datetime.now(timezone.utc)).days
                if expiry else None)
        out["config"] = {"ok": True, "listen": f"{cfg['listen_host']}:"
                        f"{cfg['listen_port']}",
                         "allow_ips": cfg["allow_ips"],
                         "allowed_top_methods": sorted(
                             cfg["allowed_top_methods"])}
        out["tls"] = {"ok": True, "san": cert.get("subjectAltName"),
                      "eku": sorted(_cert_eku(cfg["tls_cert"])),
                      "expires_in_days": days,
                      "client_ca": os.path.basename(cfg["client_ca"]),
                      "upstream_ca": os.path.basename(cfg["upstream_ca"])}
    except ConfigError as exc:
        ok = False
        out["config"] = {"ok": False, "error": str(exc)[:300]}
    if with_upstream:
        layer = {"host": UPSTREAM_HOST, "port": UPSTREAM_PORT}
        try:
            infos = socket.getaddrinfo(UPSTREAM_HOST, UPSTREAM_PORT,
                                       proto=socket.IPPROTO_TCP)
            layer["dns"] = {"ok": True,
                            "addresses": sorted({i[4][0] for i in infos})}
        except socket.gaierror as exc:
            ok = False
            layer["dns"] = {"ok": False, "error": type(exc).__name__}
            infos = []
        addr = None
        if infos:
            addr = infos[0][4]
            try:
                s = socket.create_connection(addr, timeout=5)
                s.close()
                layer["tcp"] = {"ok": True}
            except OSError as exc:
                ok = False
                layer["tcp"] = {"ok": False, "error": type(exc).__name__}
            if layer.get("tcp", {}).get("ok") and "config" in out:
                try:
                    ctx = ssl.create_default_context(
                        cafile=cfg["upstream_ca"])
                    with socket.create_connection(addr, timeout=5) as s2:
                        with ctx.wrap_socket(
                                s2, server_hostname=UPSTREAM_HOST) as ss:
                            layer["tls"] = {"ok": True, "proto": ss.version()}
                except Exception as exc:  # noqa: BLE001
                    ok = False
                    layer["tls"] = {"ok": False,
                                    "error": type(exc).__name__}
        out["upstream"] = layer
    out["overall"] = "OK" if ok else "FAILED"
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if ok else 1


# ---------------------------------------------------------------- 入口
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--check-config", action="store_true")
    ap.add_argument("--diagnose", action="store_true")
    ap.add_argument("--diagnose-upstream", action="store_true",
                    help="诊断包含上游 DNS/TCP/TLS 探测（只握手不发请求）")
    ap.add_argument("--listen-mode", choices=("auto", "systemd", "explicit"),
                    default="auto")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.diagnose:
        return diagnose(args.config, with_upstream=args.diagnose_upstream)
    try:
        cfg = load_config(args.config)
    except (ConfigError, OSError, ValueError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    if args.check_config:
        print("config OK")
        return 0
    mode = args.listen_mode
    if mode == "auto":
        mode = "systemd" if os.environ.get("LISTEN_FDS") else "explicit"
    try:
        server = make_server(cfg, mode=mode)
    except (ConfigError, OSError) as exc:
        print(f"listen error: {exc}", file=sys.stderr)
        return 3
    log.info("dmz forwarder v2 mode=%s listening on %s:%d -> https://%s%s",
             mode, cfg["listen_host"], cfg["listen_port"],
             UPSTREAM_HOST, UPSTREAM_PATH)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
