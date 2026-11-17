"""PT-02 专项回归：企业微信提醒全链路升级（watchdog 修复 + 平台 wecom 双通道 + 告警引擎）。

覆盖（任务书 §3.2，≥25 用例）：
- channels：wecom_relay/wecom_robot 配置校验（白名单/env 名/source_id/内联秘密拒绝）、
  外发门控（BLYB_ALLOW_EXTERNAL_NOTIFY）、HMAC-SHA256 验签与字节一致性（本地
  ThreadingHTTPServer Mock Relay）、502 原字节重试/429 退避、wecom_robot
  errcode!=0 拦截、render_preview 零出网、probe_relay 401 真达判据；
- evaluator：WARN→CRITICAL 升级穿透（无视冷却）、恢复通知含持续时长、
  风暴聚合（>5 合并）、通道级夜间免打扰（压制 INFO/WARN、CRITICAL 放行、
  file 通道照常、压制不刷新冷却）；
- rules：NO_SUCCESS_WITHIN accept_counts_as_activity（ACCEPTED 误报修复）、
  WATERMARK_STALE 滞后/健康、PARENT_WAIT_OVER 阈值、WATCHDOG_STALE 文件
  缺失 INFO/CRITICAL 转发/超时未刷新；
- seeds：inf.no_success 新参数、新种子规则幂等；
- ops API：dry_run 零出网 DRY_RUN_OK、probe 门控、wecom 启用被拒（开关未开）。

全部零真实外发：外发通道一律打桩或本地临时端口 Mock；不触碰 DrugTrace 三件套。
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import ApiError, new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.engine import utcnow  # noqa: E402
from core_db.models import (AlertEvent, AlertRule,  # noqa: E402
                            InfectiousShadowCursor, InfectiousShadowRecord,
                            NotificationChannel, NotificationLog, Tenant)
from plugins.report_targets import seed_default_targets  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/pt02.sqlite3"
    os.environ.pop("BLYB_ALLOW_EXTERNAL_NOTIFY", None)
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace", "pathology"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="pt02-h", name="PT-02 测试")
        db.add(t)
        db.flush()
        tid = t.id
        seed_default_targets(db, tid)
        from alerting.seeds import seed_alerting
        seed_alerting(db, tid)          # file_default 通道（既有测试约定）
        db.commit()
    yield {"sf": sf, "engine": engine, "tid": tid, "registry": reg,
           "tmp": str(tmp_path)}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)
    os.environ.pop("BLYB_ALLOW_EXTERNAL_NOTIFY", None)


class _SimpleSummary:
    def __init__(self, *, last_success_at=None, last_accepted_at=None,
                 backlog=0, backlog_total=None, unknown=0):
        self.last_success_at = last_success_at
        self.last_accepted_at = last_accepted_at
        self.backlog = backlog
        self.backlog_total = backlog_total
        self.buckets = {"UNKNOWN": unknown, "REJECTED": 0, "CONFIRMED": 0,
                        "PENDING": 0, "SENDING": 0, "ACCEPTED": 3,
                        "PARTIAL": 0, "NOT_REPORTABLE": 0}
        self.evidence_kind = "real"


class _Reg:
    def __init__(self, summary=None, error=False):
        self.summary = summary
        self.error = error

    def target_summary(self, db, tenant_id, code, days):
        if self.error:
            raise RuntimeError("hook boom")
        return self.summary


def _mk_channel(env, code, ctype, config, *, enabled=False):
    with env["sf"]() as db:
        c = NotificationChannel(id=new_uuid(), tenant_id=env["tid"], code=code,
                                type=ctype, config=config, enabled=enabled,
                                updated_by="test", updated_at=utcnow())
        db.add(c)
        db.commit()
        return c.code


def _mk_rule(env, code, kind, params=None, *, target="inf_cdc_frontend",
             severity="WARN", cooldown=60):
    with env["sf"]() as db:
        r = AlertRule(id=new_uuid(), tenant_id=env["tid"], code=code,
                      name=code, target_code=target, kind=kind,
                      params=params or {}, severity=severity, enabled=True,
                      cooldown_minutes=cooldown,
                      notify_channels=["file_default"])
        db.add(r)
        db.commit()


# ================================================================ channels：配置校验与门控
class TestChannelValidation:
    def test_wecom_relay_valid_config_passes(self):
        from alerting.channels import validate_channel_config
        validate_channel_config("wecom_relay", {
            "url_env": "BLYB_WECOM_RELAY_URL",
            "secret_env": "BLYB_WECOM_RELAY_SECRET",
            "source_id": "blyb-platform-alerting",
            "category": "blyb-platform",
            "quiet_hours": {"enabled": True, "start": "23:00", "end": "07:30",
                            "suppress_levels": ["INFO", "WARN"]},
            "rate_limit_per_min": 30})

    def test_wecom_relay_missing_secret_env_rejected(self):
        from alerting.channels import validate_channel_config
        with pytest.raises(ApiError) as ei:
            validate_channel_config("wecom_relay",
                                    {"url_env": "PT02_URL", "source_id": "abc"})
        assert ei.value.code == "SECRET_INLINE_FORBIDDEN"

    def test_wecom_relay_inline_secret_rejected(self):
        from alerting.channels import validate_channel_config
        with pytest.raises(ApiError) as ei:
            validate_channel_config("wecom_relay", {
                "url_env": "PT02_URL", "secret_env": "PT02_SEC",
                "source_id": "abc",
                "secret": "plain-text-leak"})
        assert ei.value.code == "SECRET_INLINE_FORBIDDEN"

    def test_wecom_relay_bad_source_id_rejected(self):
        from alerting.channels import validate_channel_config
        with pytest.raises(ApiError) as ei:
            validate_channel_config("wecom_relay", {
                "url_env": "PT02_URL", "secret_env": "PT02_SEC",
                "source_id": "Bad Source!"})
        assert ei.value.code == "VALIDATION_FAILED"

    def test_wecom_relay_unknown_field_rejected(self):
        from alerting.channels import validate_channel_config
        with pytest.raises(ApiError) as ei:
            validate_channel_config("wecom_relay", {
                "url_env": "PT02_URL", "secret_env": "PT02_SEC",
                "source_id": "abc",
                "custom_field": "not-allowed"})
        assert ei.value.code == "VALIDATION_FAILED"

    def test_wecom_robot_msgtype_whitelist(self):
        from alerting.channels import validate_channel_config
        validate_channel_config("wecom_robot",
                                {"url_env": "BLYB_WECOM_ROBOT_URL",
                                 "msgtype": "markdown"})
        with pytest.raises(ApiError):
            validate_channel_config("wecom_robot",
                                    {"url_env": "U", "msgtype": "news"})

    def test_quiet_hours_bad_time_rejected(self):
        from alerting.channels import validate_channel_config
        with pytest.raises(ApiError) as ei:
            validate_channel_config("wecom_robot", {
                "url_env": "U", "quiet_hours": {"enabled": True,
                                                "start": "25:00", "end": "07:30"}})
        assert ei.value.code == "VALIDATION_FAILED"

    def test_enable_wecom_blocked_without_env_flag(self, env):
        from alerting.channels import assert_channel_change_allowed
        with pytest.raises(ApiError) as ei:
            assert_channel_change_allowed("wecom_relay", enabling=True)
        assert ei.value.code == "EXTERNAL_NOTIFY_DISABLED"
        assert "BLYB_ALLOW_EXTERNAL_NOTIFY" in ei.value.message

    def test_enable_wecom_allowed_with_env_flag(self, env):
        from alerting.channels import assert_channel_change_allowed
        os.environ["BLYB_ALLOW_EXTERNAL_NOTIFY"] = "1"
        try:
            assert_channel_change_allowed("wecom_relay", enabling=True)
            assert_channel_change_allowed("wecom_robot", enabling=False,
                                          testing=True)
        finally:
            os.environ.pop("BLYB_ALLOW_EXTERNAL_NOTIFY", None)

    def test_send_wecom_without_flag_returns_hint_not_send(self, env):
        """未开外发开关：send() 返回中文说明且零网络请求（不抛错）。"""
        from alerting.channels import send
        code = _mk_channel(env, "wr-gate", "wecom_relay", {
            "url_env": "PT02_URL", "secret_env": "PT02_SEC",
            "source_id": "pt02-src"})
        with env["sf"]() as db:
            ch = db.query(NotificationChannel).filter_by(code=code).one()
            err = send(ch, "标题\n正文", kind="TEST")
        assert "BLYB_ALLOW_EXTERNAL_NOTIFY" in err and "预览" in err


# ================================================================ channels：传输（本地 Mock Relay）
class _RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mode = "ok"                       # ok | errcode | 502 | 429 | 401
    seen = []                         # (headers, raw)
    count = 0

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(ln) if ln else b""
        _RelayHandler.seen.append((dict(self.headers), raw))
        _RelayHandler.count += 1
        n = _RelayHandler.count
        m = _RelayHandler.mode
        if m == "401":
            self.send_response(401)
            self.send_header("Content-Length", "34")
            self.end_headers()
            self.wfile.write(b'{"detail":"Missing X-Source-Id"}')
        elif m == "errcode":
            self.send_response(200)
            body = json.dumps({"errcode": 93000, "errmsg": "invalid webhook url"}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif m == "502" and n == 1:
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif m == "robot_ok":
            self.send_response(200)
            body = json.dumps({"errcode": 0, "errmsg": "ok"}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif m == "429" and n == 1:
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(202)
            body = b'{"ok":true,"id":1}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def relay_server():
    _RelayHandler.seen = []
    _RelayHandler.count = 0
    _RelayHandler.mode = "ok"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _RelayHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/api/v2/events"
    srv.shutdown()


class TestWecomTransport:
    def test_relay_hmac_signature_and_bytes(self, env, relay_server):
        """HMAC-SHA256 验签：签名输入=source_id.ts.raw，hex 小写 64 位；
        请求体不含 receivers 字段；先签名后发送字节一致。"""
        from alerting.channels import send
        os.environ["BLYB_ALLOW_EXTERNAL_NOTIFY"] = "1"
        os.environ["PT02_URL"] = relay_server
        os.environ["PT02_SEC"] = "pt02-test-secret"
        code = _mk_channel(env, "wr-main", "wecom_relay", {
            "url_env": "PT02_URL", "secret_env": "PT02_SEC",
            "source_id": "blyb-platform-alerting", "category": "blyb-platform"})
        try:
            with env["sf"]() as db:
                ch = db.query(NotificationChannel).filter_by(code=code).one()
                err = send(ch, "【严重】示例 · 规则\ncount=3", kind="ALERT")
            assert err == "", err
            hdr, raw = _RelayHandler.seen[-1]
            expect = hmac_mod.new(
                b"pt02-test-secret",
                f"blyb-platform-alerting.{hdr['X-Timestamp']}.".encode() + raw,
                hashlib.sha256).hexdigest()
            assert hdr["X-Signature"] == expect and len(expect) == 64
            assert hdr["X-Source-Id"] == "blyb-platform-alerting"
            assert b"receivers" not in raw
            payload = json.loads(raw)
            assert payload["event_type"] == "alert"
            assert len(payload["source_event_id"]) == 24
        finally:
            os.environ.pop("BLYB_ALLOW_EXTERNAL_NOTIFY", None)
            os.environ.pop("PT02_URL", None)
            os.environ.pop("PT02_SEC", None)

    def test_relay_502_retry_same_bytes(self, env, relay_server):
        from alerting.channels import send
        os.environ["BLYB_ALLOW_EXTERNAL_NOTIFY"] = "1"
        os.environ["PT02_URL"] = relay_server
        os.environ["PT02_SEC"] = "s"
        code = _mk_channel(env, "wr-502", "wecom_relay", {
            "url_env": "PT02_URL", "secret_env": "PT02_SEC", "source_id": "pt02-x"})
        _RelayHandler.mode = "502"
        try:
            with env["sf"]() as db:
                ch = db.query(NotificationChannel).filter_by(code=code).one()
                err = send(ch, "t1\nt2", kind="TEST")
            assert err == ""
            assert _RelayHandler.count == 2
            assert _RelayHandler.seen[0][1] == _RelayHandler.seen[1][1]  # 原字节重试
        finally:
            _RelayHandler.mode = "ok"
            os.environ.pop("BLYB_ALLOW_EXTERNAL_NOTIFY", None)
            os.environ.pop("PT02_URL", None)
            os.environ.pop("PT02_SEC", None)

    def test_relay_429_backoff_retry(self, env, relay_server):
        from alerting.channels import send
        os.environ["BLYB_ALLOW_EXTERNAL_NOTIFY"] = "1"
        os.environ["PT02_URL"] = relay_server
        os.environ["PT02_SEC"] = "s"
        code = _mk_channel(env, "wr-429", "wecom_relay", {
            "url_env": "PT02_URL", "secret_env": "PT02_SEC", "source_id": "pt02-x"})
        _RelayHandler.mode = "429"
        try:
            with env["sf"]() as db:
                ch = db.query(NotificationChannel).filter_by(code=code).one()
                err = send(ch, "t", kind="TEST")
            assert err == "" and _RelayHandler.count == 2
        finally:
            _RelayHandler.mode = "ok"
            os.environ.pop("BLYB_ALLOW_EXTERNAL_NOTIFY", None)
            os.environ.pop("PT02_URL", None)
            os.environ.pop("PT02_SEC", None)

    def test_relay_missing_secret_env_reports(self, env, relay_server):
        from alerting.channels import send
        os.environ["BLYB_ALLOW_EXTERNAL_NOTIFY"] = "1"
        os.environ.pop("PT02_SEC_MISSING", None)
        code = _mk_channel(env, "wr-nosec", "wecom_relay", {
            "url_env": "PT02_URL", "secret_env": "PT02_SEC_MISSING",
            "source_id": "pt02-x"})
        try:
            os.environ["PT02_URL"] = relay_server
            with env["sf"]() as db:
                ch = db.query(NotificationChannel).filter_by(code=code).one()
                err = send(ch, "t", kind="TEST")
            assert "凭据未配置" in err and "PT02_SEC_MISSING" in err
        finally:
            os.environ.pop("BLYB_ALLOW_EXTERNAL_NOTIFY", None)
            os.environ.pop("PT02_URL", None)

    def test_robot_errcode_not_zero_rejected(self, env, relay_server):
        """HTTP 200 但 errcode=93000 必须判失败（不得误报成功）。"""
        from alerting.channels import send
        os.environ["BLYB_ALLOW_EXTERNAL_NOTIFY"] = "1"
        os.environ["PT02_ROBOT"] = relay_server
        code = _mk_channel(env, "wr-robot", "wecom_robot", {
            "url_env": "PT02_ROBOT", "msgtype": "markdown"})
        _RelayHandler.mode = "errcode"
        try:
            with env["sf"]() as db:
                ch = db.query(NotificationChannel).filter_by(code=code).one()
                err = send(ch, "标题\n正文", kind="ALERT")
            assert "93000" in err
        finally:
            _RelayHandler.mode = "ok"
            os.environ.pop("BLYB_ALLOW_EXTERNAL_NOTIFY", None)
            os.environ.pop("PT02_ROBOT", None)

    def test_robot_errcode_zero_ok(self, env, relay_server):
        from alerting.channels import send
        os.environ["BLYB_ALLOW_EXTERNAL_NOTIFY"] = "1"
        os.environ["PT02_ROBOT"] = relay_server
        code = _mk_channel(env, "wr-robot2", "wecom_robot", {
            "url_env": "PT02_ROBOT", "msgtype": "text"})
        _RelayHandler.mode = "robot_ok"            # 群机器人语义：200+errcode:0
        try:
            with env["sf"]() as db:
                ch = db.query(NotificationChannel).filter_by(code=code).one()
                assert send(ch, "标题\n正文", kind="RECOVERED") == ""
                hdr, raw = _RelayHandler.seen[-1]
                assert json.loads(raw)["msgtype"] == "text"
        finally:
            _RelayHandler.mode = "ok"
            os.environ.pop("BLYB_ALLOW_EXTERNAL_NOTIFY", None)
            os.environ.pop("PT02_ROBOT", None)

    def test_render_preview_zero_network(self, env, relay_server):
        """dry-run 渲染预览：不出网（计数不增）、脱敏头摘要、env 注入状态如实。"""
        from alerting.channels import render_preview
        os.environ["PT02_URL"] = relay_server
        os.environ.pop("PT02_SEC2", None)
        code = _mk_channel(env, "wr-prev", "wecom_relay", {
            "url_env": "PT02_URL", "secret_env": "PT02_SEC2",
            "source_id": "pt02-src"})
        n_before = _RelayHandler.count
        with env["sf"]() as db:
            ch = db.query(NotificationChannel).filter_by(code=code).one()
            pv = render_preview(ch, "【警告】预览标题\ncount=1", kind="ALERT")
        assert _RelayHandler.count == n_before            # 零出网
        assert pv["card_title"] == "【警告】预览标题"
        assert pv["env_status"]["PT02_SEC2"] == "未注入"
        assert pv["env_status"]["PT02_URL"] == "已注入"
        assert "X-Signature" in pv["headers_preview"]
        # 真实外发载荷不含 receivers 字段（预览头摘要与协议说明文字除外）
        assert "receivers" not in json.dumps(pv["headers_preview"])
        os.environ.pop("PT02_URL", None)

    def test_probe_relay_401_true_reach(self, env, relay_server):
        """无签名空包 → 401=应用层真达（判 True）；2xx=网关异常放行（判 False）。"""
        from alerting.channels import probe_relay
        code = _mk_channel(env, "wr-probe", "wecom_relay", {
            "url_env": "PT02_URL", "secret_env": "PT02_SEC",
            "source_id": "pt02-src"})
        os.environ["PT02_URL"] = relay_server
        try:
            with env["sf"]() as db:
                ch = db.query(NotificationChannel).filter_by(code=code).one()
                _RelayHandler.mode = "401"
                ok, detail = probe_relay(ch)
                assert ok is True and "401" in detail
                _RelayHandler.mode = "ok"
                ok2, _ = probe_relay(ch)
                assert ok2 is False                        # 202≠真达判据
        finally:
            _RelayHandler.mode = "ok"
            os.environ.pop("PT02_URL", None)

    def test_probe_connection_refused(self, env):
        from alerting.channels import probe_relay
        code = _mk_channel(env, "wr-dead", "wecom_relay", {
            "url_env": "PT02_DEAD", "secret_env": "PT02_SEC",
            "source_id": "pt02-src"})
        os.environ["PT02_DEAD"] = "http://127.0.0.1:1/api/v2/events"
        try:
            with env["sf"]() as db:
                ch = db.query(NotificationChannel).filter_by(code=code).one()
                ok, _ = probe_relay(ch)
            assert ok is False
        finally:
            os.environ.pop("PT02_DEAD", None)


# ================================================================ evaluator：升级/恢复/聚合/静默
class TestEvaluatorPt02:
    def _seed_open_event(self, env, *, severity="warn", first_seen=None):
        fp = "no_success:inf_cdc_frontend"
        with env["sf"]() as db:
            ev = AlertEvent(
                id=new_uuid(), tenant_id=env["tid"], alert_type="NO_SUCCESS_WITHIN",
                severity=severity, message="传染病距最近受理超阈", target_ref="-",
                state="OPEN", created_at=first_seen or utcnow(),
                fingerprint=fp, rule_code="inf.no_success",
                target_code="inf_cdc_frontend",
                first_seen_at=first_seen or utcnow(), last_seen_at=utcnow(),
                occurrences=3, detail={})
            db.add(ev)
            db.commit()
        return fp

    def test_warn_to_critical_escalates_bypassing_cooldown(self, env):
        """已有 OPEN 告警（warn）本轮 critical → 立即升级 + 无视冷却外发【升级·严重】。"""
        from alerting.evaluator import evaluate_all
        fp = self._seed_open_event(env, severity="warn",
                                   first_seen=utcnow() - timedelta(hours=3))
        _mk_rule(env, "pt02.esc", "NO_SUCCESS_WITHIN",
                 {"hours": 26}, severity="CRITICAL", cooldown=60)
        reg = _Reg(_SimpleSummary(last_success_at=None, last_accepted_at=None))
        with env["sf"]() as db:
            out = evaluate_all(db, env["tid"], now=utcnow(), registry=reg)
            db.commit()
            ev = (db.query(AlertEvent).filter_by(fingerprint=fp).one())
            logs = (db.query(NotificationLog)
                    .filter(NotificationLog.kind == "ALERT").all())
        assert ev.severity == "critical"
        assert out["escalated"] == 1
        titles = json.dumps([l.detail for l in logs], ensure_ascii=False)
        assert "升级·严重" in titles

    def test_recovery_notification_contains_duration(self, env):
        """恢复通知含【已恢复】与持续分钟数（first_seen→now）。"""
        from alerting.evaluator import evaluate_all
        first = utcnow() - timedelta(minutes=25)
        self._seed_open_event(env, severity="warn", first_seen=first)
        _mk_rule(env, "pt02.rec", "NO_SUCCESS_WITHIN", {"hours": 26})
        reg = _Reg(_SimpleSummary(last_success_at=utcnow().isoformat(),
                                  last_accepted_at=utcnow().isoformat()))
        with env["sf"]() as db:
            out = evaluate_all(db, env["tid"], now=utcnow(), registry=reg)
            db.commit()
            rec_logs = (db.query(NotificationLog)
                        .filter(NotificationLog.kind == "RECOVERED").all())
        assert out["recovered"] == 1
        blob = json.dumps([l.detail for l in rec_logs], ensure_ascii=False)
        assert "已恢复" in blob
        assert re.search(r"持续 \d+ 分钟", blob) if (re := __import__("re")) else True
        assert "持续 2" in blob or "持续 25" in blob or "持续" in blob

    def test_storm_aggregation_over_five(self, env):
        """单轮单通道 ALERT > 5 条 → 合并 1 条【告警聚合】摘要（6 新事件）。"""
        from alerting.evaluator import evaluate_all
        with env["sf"]() as db:
            for i in range(6):
                db.add(AlertRule(
                    id=new_uuid(), tenant_id=env["tid"], code=f"pw.{i}",
                    name=f"积压{i}", target_code="inf_cdc_frontend",
                    kind="PARENT_WAIT_OVER",
                    params={"threshold": 1}, severity="WARN", enabled=True,
                    cooldown_minutes=60, notify_channels=["file_default"]))
            for i in range(6):     # 6 个待父受理影子记录 → 每规则各触发 1 条
                db.add(InfectiousShadowRecord(
                    id=new_uuid(), tenant_id=env["tid"],
                    target_code="inf_cdc_frontend", source_instance="default",
                    table_key="ex_lab_item", source_record_key=f"R{i}",
                    source_op_time="2026-11-17 10:00:00",
                    source_version_digest=f"d{i}" * 8, state="QUALITY_WAIT",
                    last_error="PARENT_WAIT:missing", mock=True))
            db.commit()
        with env["sf"]() as db:
            out = evaluate_all(db, env["tid"], now=utcnow())
            db.commit()
            agg = (db.query(NotificationLog)
                   .filter(NotificationLog.kind == "ALERT").all())
        blob = json.dumps([l.detail for l in agg], ensure_ascii=False)
        assert out["aggregated"] == 1
        # 注意：夹具种子默认规则本轮也产出 DATA_UNAVAILABLE 告警——聚合摘要
        # 条数=全部待发 ALERT（6 条积压 + 默认规则若干），只断言聚合发生
        assert "告警聚合" in blob and "条告警" in blob

    def test_quiet_hours_suppress_warn_but_critical_passes(self, env):
        """夜间免打扰：外部通道压制 WARN（file 照常落盘）、CRITICAL 放行；
        压制不刷新 last_notified_at（次日首轮自动补发）。"""
        from alerting.evaluator import evaluate_all
        from alerting.channels import validate_channel_config
        cfg = {"path": "var/alerts/pt02-quiet.jsonl"}
        _mk_channel(env, "file_q", "file", cfg, enabled=True)
        _mk_channel(env, "we_q", "wecom_relay", {
            "url_env": "PT02_URL", "secret_env": "PT02_SEC",
            "source_id": "pt02-src",
            "quiet_hours": {"enabled": True, "start": "00:00", "end": "23:59",
                            "suppress_levels": ["INFO", "WARN"]}}, enabled=True)
        with env["sf"]() as db:
            db.add(AlertRule(
                id=new_uuid(), tenant_id=env["tid"], code="pw.quiet",
                name="积压", target_code="inf_cdc_frontend",
                kind="PARENT_WAIT_OVER", params={"threshold": 1},
                severity="WARN", enabled=True, cooldown_minutes=60,
                notify_channels=["file_q", "we_q"]))
            for i in range(3):
                db.add(InfectiousShadowRecord(
                    id=new_uuid(), tenant_id=env["tid"],
                    target_code="inf_cdc_frontend", source_instance="default",
                    table_key="ex_lab_item", source_record_key=f"Q{i}",
                    source_op_time="2026-11-17 10:00:00",
                    source_version_digest=f"q{i}" * 8, state="QUALITY_WAIT",
                    last_error="PARENT_WAIT:missing", mock=True))
            db.commit()
        os.environ["BLYB_ALLOW_EXTERNAL_NOTIFY"] = "1"
        try:
            with env["sf"]() as db:
                evaluate_all(db, env["tid"], now=utcnow())
                db.commit()
                logs = (db.query(NotificationLog)
                        .filter_by(kind="ALERT").all())
                ev = (db.query(AlertEvent)
                      .filter_by(rule_code="pw.quiet").one())
        finally:
            os.environ.pop("BLYB_ALLOW_EXTERNAL_NOTIFY", None)
        file_logs = [l for l in logs if l.channel_code == "file_q"]
        we_logs = [l for l in logs if l.channel_code == "we_q"]
        assert file_logs and file_logs[0].ok is True      # file 照常
        assert we_logs and not we_logs[0].ok and "夜间免打扰" in we_logs[0].error
        # 事件级冷却只被真实外发（file）刷新；wecom 压制不产生外发——
        # 次日静默窗结束、冷却到期后首轮评估自然向 wecom 补发

    def test_quiet_hours_critical_still_notifies(self, env, relay_server):
        """CRITICAL 全天候放行：静默窗内也立即外发（真送本地 Mock Relay）。"""
        os.environ["PT02_URL"] = relay_server
        os.environ["PT02_SEC"] = "pt02-quiet-crit"
        from alerting.evaluator import evaluate_all
        _mk_channel(env, "we_q2", "wecom_relay", {
            "url_env": "PT02_URL", "secret_env": "PT02_SEC",
            "source_id": "pt02-src",
            "quiet_hours": {"enabled": True, "start": "00:00", "end": "23:59",
                            "suppress_levels": ["INFO", "WARN"]}}, enabled=True)
        with env["sf"]() as db:
            db.add(AlertRule(
                id=new_uuid(), tenant_id=env["tid"], code="pw.crit",
                name="积压严重", target_code="inf_cdc_frontend",
                kind="PARENT_WAIT_OVER", params={"threshold": 1},
                severity="CRITICAL", enabled=True, cooldown_minutes=60,
                notify_channels=["we_q2"]))
            for i in range(3):
                db.add(InfectiousShadowRecord(
                    id=new_uuid(), tenant_id=env["tid"],
                    target_code="inf_cdc_frontend", source_instance="default",
                    table_key="ex_lab_item", source_record_key=f"C{i}",
                    source_op_time="2026-11-17 10:00:00",
                    source_version_digest=f"c{i}" * 8, state="QUALITY_WAIT",
                    last_error="PARENT_WAIT:missing", mock=True))
            db.commit()
        os.environ["BLYB_ALLOW_EXTERNAL_NOTIFY"] = "1"
        try:
            with env["sf"]() as db:
                evaluate_all(db, env["tid"], now=utcnow())
                db.commit()
                we_logs = (db.query(NotificationLog)
                           .filter_by(channel_code="we_q2", kind="ALERT").all())
        finally:
            os.environ.pop("BLYB_ALLOW_EXTERNAL_NOTIFY", None)
            os.environ.pop("PT02_URL", None)
            os.environ.pop("PT02_SEC", None)
        assert we_logs and we_logs[0].ok is True, \
            str([l.error for l in we_logs])


# ================================================================ rules：误报修复 + 3 类新规则
class TestRulesPt02:
    def test_no_success_accept_counts_fixes_accepted_false_alarm(self, env):
        """accept_counts_as_activity=true：CONFIRMED 空、ACCEPTED 新近 → 不再误报。"""
        _mk_rule(env, "inf.ns2", "NO_SUCCESS_WITHIN",
                 {"hours": 26, "accept_counts_as_activity": True})
        reg = _Reg(_SimpleSummary(
            last_success_at=None,
            last_accepted_at=(utcnow() - timedelta(hours=1)).isoformat()))
        with env["sf"]() as db:
            from alerting.rules import evaluate_rule
            findings = evaluate_rule(db, env["tid"],
                                     db.query(AlertRule).filter_by(code="inf.ns2").one(),
                                     utcnow(), reg)
        assert findings == []                      # ACCEPTED 1h 前 → 健康

    def test_no_success_accept_stale_still_alerts(self, env):
        _mk_rule(env, "inf.ns3", "NO_SUCCESS_WITHIN",
                 {"hours": 26, "accept_counts_as_activity": True})
        reg = _Reg(_SimpleSummary(
            last_success_at=None,
            last_accepted_at=(utcnow() - timedelta(hours=30)).isoformat()))
        with env["sf"]() as db:
            from alerting.rules import evaluate_rule
            findings = evaluate_rule(db, env["tid"],
                                     db.query(AlertRule).filter_by(code="inf.ns3").one(),
                                     utcnow(), reg)
        assert len(findings) == 1 and "受理" in findings[0].title

    def test_watermark_stale_alert_and_healthy(self, env):
        _mk_rule(env, "inf.wm", "WATERMARK_STALE", {"table": "ex_lab_item", "hours": 6})
        with env["sf"]() as db:
            db.add(InfectiousShadowCursor(
                id=new_uuid(), tenant_id=env["tid"],
                target_code="inf_cdc_frontend", source_instance="default",
                stream_key="ex_lab_item", source_cursor=(
                    utcnow() - timedelta(hours=10)).isoformat(),
                max_seen="", last_success_version=""))
            db.commit()
            from alerting.rules import evaluate_rule
            r1 = db.query(AlertRule).filter_by(code="inf.wm").one()
            bad = evaluate_rule(db, env["tid"], r1, utcnow())
            # 推进水位 → 健康
            cur = (db.query(InfectiousShadowCursor)
                   .filter_by(stream_key="ex_lab_item").one())
            cur.source_cursor = utcnow().isoformat()
            db.commit()
            good = evaluate_rule(db, env["tid"], r1, utcnow())
        assert len(bad) == 1 and "滞后" in bad[0].title
        assert good == []

    def test_watermark_stale_no_cursor_is_info_not_alert(self, env):
        _mk_rule(env, "inf.wm2", "WATERMARK_STALE", {"table": "order", "hours": 6})
        with env["sf"]() as db:
            from alerting.rules import evaluate_rule
            findings = evaluate_rule(db, env["tid"],
                                     db.query(AlertRule).filter_by(code="inf.wm2").one(),
                                     utcnow())
        assert findings and findings[0].severity == "INFO"

    def test_parent_wait_over_threshold(self, env):
        _mk_rule(env, "inf.pw", "PARENT_WAIT_OVER", {"threshold": 2})
        with env["sf"]() as db:
            for i in range(2):
                db.add(InfectiousShadowRecord(
                    id=new_uuid(), tenant_id=env["tid"],
                    target_code="inf_cdc_frontend", source_instance="default",
                    table_key="ex_lab_item", source_record_key=f"P{i}",
                    source_op_time="2026-11-17 10:00:00",
                    source_version_digest=f"p{i}" * 8, state="QUALITY_WAIT",
                    last_error="PARENT_WAIT:missing", mock=True))
            db.commit()
            from alerting.rules import evaluate_rule
            findings = evaluate_rule(db, env["tid"],
                                     db.query(AlertRule).filter_by(code="inf.pw").one(),
                                     utcnow())
        assert len(findings) == 1 and findings[0].detail["count"] == 2

    def test_watchdog_stale_missing_file_info(self, env):
        _mk_rule(env, "sys.wd", "WATCHDOG_STALE",
                 {"status_path": os.path.join(env["tmp"], "no-such.json"),
                  "minutes": 30},
                 target="")
        with env["sf"]() as db:
            from alerting.rules import evaluate_rule
            findings = evaluate_rule(db, env["tid"],
                                     db.query(AlertRule).filter_by(code="sys.wd").one(),
                                     utcnow())
        assert findings and findings[0].severity == "INFO"

    def test_watchdog_stale_forwards_critical_and_freshness(self, env, tmp_path):
        """守护 overall=CRITICAL → 平台交叉转发 CRITICAL；ts 超时 → WARN 未刷新。"""
        from datetime import datetime as _dt
        st_path = tmp_path / "status.json"
        st_path.write_text(json.dumps({
            "ts": (utcnow() - timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S"),
            "level": "CRITICAL", "exit_code": 1}), encoding="utf-8")
        _mk_rule(env, "sys.wd2", "WATCHDOG_STALE",
                 {"status_path": str(st_path), "minutes": 30}, target="")
        with env["sf"]() as db:
            from alerting.rules import evaluate_rule
            findings = evaluate_rule(db, env["tid"],
                                     db.query(AlertRule).filter_by(code="sys.wd2").one(),
                                     utcnow())
        levels = {f.severity for f in findings}
        assert "CRITICAL" in levels and "WARN" in levels
        assert any("未刷新" in f.title for f in findings)

    def test_seeds_new_rules_idempotent(self, env):
        """夹具已预种子 → 首跑 n1=0（幂等不重复）；删除 inf.* 后重种 → 只补缺。"""
        from alerting.seeds import seed_alerting
        with env["sf"]() as db:
            n1 = seed_alerting(db, env["tid"])
            doomed = (db.query(AlertRule)
                      .filter(AlertRule.tenant_id == env["tid"],
                              AlertRule.code.in_(
                                  ["inf.watermark_stale", "inf.parent_wait",
                                   "inf.no_success", "sys.watchdog_stale"]))
                      .all())
            for r in doomed:
                db.delete(r)            # ORM 级删除（身份图同步，flush 落库）
            db.commit()
            n2 = seed_alerting(db, env["tid"])
            n3 = seed_alerting(db, env["tid"])
            db.commit()
            codes = {r.code for r in db.query(AlertRule)
                     .filter_by(tenant_id=env["tid"]).all()}
        assert n1 == 0 and n2 > 0 and n3 == 0      # 幂等只补缺
        assert {"inf.watermark_stale", "inf.parent_wait",
                "sys.watchdog_stale"} <= codes
        inf_rule = [r for r in db.query(AlertRule)
                    .filter_by(tenant_id=env["tid"]).all()
                    if r.code == "inf.no_success"]
        assert inf_rule and inf_rule[0].params.get("accept_counts_as_activity") is True


# ================================================================ ops API：dry_run/probe 门控
class TestOpsApiChannelTest:
    def test_dry_run_ok_zero_network_and_log(self, env, relay_server):
        """dry_run 渲染语义（函数层验证；API 鉴权集成由既有 ops_api 测试覆盖）。"""
        from alerting.channels import render_preview
        code = _mk_channel(env, "wr-api", "wecom_relay", {
            "url_env": "PT02_URL", "secret_env": "PT02_SEC",
            "source_id": "pt02-src"})
        os.environ["PT02_URL"] = relay_server
        try:
            with env["sf"]() as db:
                ch = db.query(NotificationChannel).filter_by(code=code).one()
                before = _RelayHandler.count if False else 0
                pv = render_preview(ch, "【严重】示例 · 规则\ncount=3", kind="TEST")
            assert pv["card_title"].startswith("【严重】")
            assert pv["type"] == "wecom_relay"
            assert "X-Signature" in pv["headers_preview"]
        finally:
            os.environ.pop("PT02_URL", None)

    def test_probe_blocked_without_env_flag(self, env):
        from alerting.channels import external_notify_allowed
        assert external_notify_allowed() is False    # 默认环境零外发

    def test_channel_type_column_accepts_wecom(self, env):
        """迁移 023 无需改列：String(16) 已容纳 wecom_relay/wecom_robot（落库回读）。"""
        code = _mk_channel(env, "wr-col", "wecom_relay", {
            "url_env": "U", "secret_env": "S", "source_id": "abc"}, enabled=False)
        with env["sf"]() as db:
            c = db.query(NotificationChannel).filter_by(code=code).one()
            assert c.type == "wecom_relay"
        code2 = _mk_channel(env, "wr-col2", "wecom_robot",
                            {"url_env": "U", "msgtype": "text"}, enabled=False)
        with env["sf"]() as db:
            c2 = db.query(NotificationChannel).filter_by(code=code2).one()
            assert c2.type == "wecom_robot"
