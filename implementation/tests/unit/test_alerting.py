"""LT-04 告警单测：规则 9 kind 各 1 正 1 反 + DATA_UNAVAILABLE + 异常隔离 +
评估器去重/冷却/恢复/ACK + 通道（file/smtp monkeypatch/webhook 本机服务）+
内联秘密拒绝 + 脱敏。"""
from __future__ import annotations

import json
import os
import sys
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.engine import utcnow  # noqa: E402
from core_db.models import (AlertEvent, AlertRule, DataPackage,  # noqa: E402
                            Destination, NotificationChannel, PackageRevision,
                            ReportTarget, Schedule, ScheduleRun, Submission,
                            Tenant)
from plugins.report_targets import seed_default_targets  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/alert.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace", "pathology"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="alert-h", name="告警测试")
        db.add(t)
        db.flush()
        tid = t.id
        seed_default_targets(db, tid)
        db.commit()
    yield {"sf": sf, "engine": engine, "tid": tid, "registry": reg,
           "tmp": str(tmp_path)}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


class _SimpleSummary:
    def __init__(self, *, last_success_at=None, backlog=0, backlog_total=None,
                 unknown=0, rejected=0, confirmed=0, evidence_kind="real"):
        self.last_success_at = last_success_at
        self.backlog = backlog
        self.backlog_total = backlog_total
        self.buckets = {"UNKNOWN": unknown, "REJECTED": rejected,
                        "CONFIRMED": confirmed, "PENDING": 0, "SENDING": 0,
                        "ACCEPTED": 0, "PARTIAL": 0, "NOT_REPORTABLE": 0}
        self.evidence_kind = evidence_kind


class _Reg:
    """可控 summary 钩子注册表（测试注入）。"""
    def __init__(self, summary=None, error=False):
        self.summary = summary
        self.error = error

    def target_summary(self, db, tenant_id, code, days):
        if self.error:
            raise RuntimeError("hook boom")
        return self.summary


def _rule(env, code, kind, params=None, *, target="drug_trace_alihealth",
          enabled=True, cooldown=60):
    with env["sf"]() as db:
        r = AlertRule(id=new_uuid(), tenant_id=env["tid"], code=code,
                      name=code, target_code=target, kind=kind,
                      params=params or {}, severity="WARN", enabled=enabled,
                      cooldown_minutes=cooldown, notify_channels=["file_default"])
        db.add(r)
        db.commit()
        return r.code


def _mk_channel(env, code="file_default", ctype="file", config=None,
                enabled=True):
    with env["sf"]() as db:
        if db.query(NotificationChannel).filter_by(
                tenant_id=env["tid"], code=code).first() is None:
            db.add(NotificationChannel(
                id=new_uuid(), tenant_id=env["tid"], code=code, type=ctype,
                config=(dict(config) if config is not None else
                        {"path": f"var/alerts-test/alerts-{new_uuid()[:8]}.jsonl"}),
                enabled=enabled))
            db.commit()
        return code


# ================================================================ 规则（1正1反）
def test_rule_no_success_positive_negative(env):
    from alerting.rules import evaluate_rule
    _mk_channel(env)
    code = _rule(env, "r1", "NO_SUCCESS_WITHIN", {"hours": 26})
    now = utcnow()
    with env["sf"]() as db:
        r = db.query(AlertRule).filter_by(code=code).one()
        reg = _Reg(_SimpleSummary(last_success_at=(now - timedelta(hours=30)).isoformat()))
        f = evaluate_rule(db, env["tid"], r, now, reg)
        assert f and f[0].fingerprint == "no_success:drug_trace_alihealth"
        reg2 = _Reg(_SimpleSummary(last_success_at=now.isoformat()))
        assert evaluate_rule(db, env["tid"], r, now, reg2) == []


def test_rule_backlog_and_unknown_and_rate(env):
    from alerting.rules import evaluate_rule
    _mk_channel(env)
    now = utcnow()
    with env["sf"]() as db:
        rb = db.query(AlertRule).filter_by(
            code=_rule(env, "rb", "BACKLOG_OVER", {"threshold": 500})).one()
        assert evaluate_rule(db, env["tid"], rb, now,
                             _Reg(_SimpleSummary(backlog_total=800))) != []
        assert evaluate_rule(db, env["tid"], rb, now,
                             _Reg(_SimpleSummary(backlog_total=10, backlog=10))) == []
        ru = db.query(AlertRule).filter_by(
            code=_rule(env, "ru", "UNKNOWN_OVER", {"threshold": 1})).one()
        assert evaluate_rule(db, env["tid"], ru, now,
                             _Reg(_SimpleSummary(unknown=3))) != []
        assert evaluate_rule(db, env["tid"], ru, now,
                             _Reg(_SimpleSummary(unknown=0))) == []
        rr = db.query(AlertRule).filter_by(code=_rule(
            env, "rr", "REJECT_RATE_OVER",
            {"rate": 0.05, "min_total": 50})).one()
        hot = _SimpleSummary(rejected=10, confirmed=100)
        assert evaluate_rule(db, env["tid"], rr, now, _Reg(hot)) != []
        # 总数不足不判
        thin = _SimpleSummary(rejected=10, confirmed=20)
        assert evaluate_rule(db, env["tid"], rr, now, _Reg(thin)) == []
        low = _SimpleSummary(rejected=1, confirmed=200)
        assert evaluate_rule(db, env["tid"], rr, now, _Reg(low)) == []


def test_rule_summary_unavailable_is_data_gap_not_healthy(env):
    from alerting.rules import evaluate_rule
    _mk_channel(env)
    code = _rule(env, "rdu", "UNKNOWN_OVER", {"threshold": 1})
    with env["sf"]() as db:
        r = db.query(AlertRule).filter_by(code=code).one()
        f = evaluate_rule(db, env["tid"], r, utcnow(), _Reg(None))
        assert f and f[0].fingerprint.startswith("data_unavailable:")
        f2 = evaluate_rule(db, env["tid"], r, utcnow(), _Reg(error=True))
        assert f2 and f2[0].fingerprint.startswith("data_unavailable:")


def test_rule_job_failed_and_skipped(env):
    from alerting.rules import evaluate_rule
    _mk_channel(env)
    _rule(env, "jf", "JOB_FAILED", {"schedule_code": "sch1"})  # 先建规则
    now = utcnow()
    with env["sf"]() as db:                       # 单会话准备 + 断言（避免嵌套写锁）
        s = Schedule(id=new_uuid(), tenant_id=env["tid"], code="sch1",
                     job_type="metrics.run", cron="*/5 * * * *", enabled=True)
        db.add(s)
        db.flush()
        sid = s.id
        r = db.query(AlertRule).filter_by(code="jf").one()
        db.add(ScheduleRun(id=new_uuid(), tenant_id=env["tid"],
                           schedule_id=sid, run_key="cron:a",
                           fire_time=now - timedelta(hours=1), state="FAILED",
                           error_code="X"))
        db.commit()
        assert evaluate_rule(db, env["tid"], r, now, _Reg(None)) != []
    _rule(env, "sk", "SCHEDULE_SKIPPED", {"schedule_code": "sch1",
                                           "consecutive": 3})
    with env["sf"]() as db:
        r = db.query(AlertRule).filter_by(code="sk").one()
        # 反例：最近一次 SUCCEEDED 打断连击
        db.add(ScheduleRun(id=new_uuid(), tenant_id=env["tid"],
                           schedule_id=r.id, run_key="cron:s",
                           fire_time=utcnow(), state="SUCCEEDED"))
        db.commit()
        assert evaluate_rule(db, env["tid"], r, now, _Reg(None)) == []
    with env["sf"]() as db:
        sid = (db.query(Schedule).filter_by(code="sch1").one()).id
        for i in range(3):
            db.add(ScheduleRun(id=new_uuid(), tenant_id=env["tid"],
                               schedule_id=sid, run_key=f"cron:sk{i}",
                               fire_time=now - timedelta(minutes=15 * (3 - i)),
                               state="SKIPPED_OVERLAP"))
        db.commit()
        assert evaluate_rule(db, env["tid"], r, now, _Reg(None)) != []


def test_rule_run_timeout_stalled(env):
    from alerting.rules import evaluate_rule
    _mk_channel(env)
    code = _rule(env, "rt", "RUN_TIMEOUT", {}, target="")
    with env["sf"]() as db:
        s = Schedule(id=new_uuid(), tenant_id=env["tid"], code="sch2",
                     job_type="metrics.run", cron="*/5 * * * *", enabled=True)
        db.add(s)
        db.flush()
        db.add(ScheduleRun(id=new_uuid(), tenant_id=env["tid"],
                           schedule_id=s.id, run_key="cron:t",
                           fire_time=utcnow(), state="TIMED_OUT"))
        db.commit()
        r = db.query(AlertRule).filter_by(code=code).one()
        f = evaluate_rule(db, env["tid"], r, utcnow(), _Reg(None))
        assert f and f[0].fingerprint.startswith("run.timed_out:")
        # 反例：无 TIMED_OUT 行
        db.query(ScheduleRun).filter_by(state="TIMED_OUT").delete()
        db.commit()
        assert evaluate_rule(db, env["tid"], r, utcnow(), _Reg(None)) == []


def test_rule_date_expiry_reads_evidence_not_hardcoded(env):
    from alerting.rules import evaluate_rule
    _mk_channel(env)
    code = _rule(env, "de", "DATE_EXPIRY",
                 {"source": "drug_trace.production_evidence",
                  "label": "药品发送授权", "warn_days": 7})
    now = utcnow()
    with env["sf"]() as db:
        from domains.drug_trace.models import DrugTraceProductionEvidence
        r = db.query(AlertRule).filter_by(code=code).one()
        # 无证据 → INFO 待核（不算正常）
        f0 = evaluate_rule(db, env["tid"], r, now, _Reg(None))
        assert f0 and f0[0].severity == "INFO"
        # 已登记 30 天后到期 → 不告警
        db.add(DrugTraceProductionEvidence(
            id=new_uuid(), tenant_id=env["tid"], domain="drug_trace",
            profile="alihealth_yljg_retail_v1", destination_id="d",
            capability="send", status="APPROVED",
            valid_until=now + timedelta(days=30)))
        db.commit()
        assert evaluate_rule(db, env["tid"], r, now, _Reg(None)) == []
        # 3 天后到期 → WARN；已过期 → CRITICAL
        db.query(DrugTraceProductionEvidence).delete()
        db.add(DrugTraceProductionEvidence(
            id=new_uuid(), tenant_id=env["tid"], domain="drug_trace",
            profile="p", destination_id="d", capability="send",
            status="APPROVED", valid_until=now + timedelta(days=3)))
        db.commit()
        f1 = evaluate_rule(db, env["tid"], r, now, _Reg(None))
        assert f1 and f1[0].severity == "WARN"
        db.query(DrugTraceProductionEvidence).delete()
        db.add(DrugTraceProductionEvidence(
            id=new_uuid(), tenant_id=env["tid"], domain="drug_trace",
            profile="p", destination_id="d", capability="send",
            status="APPROVED", valid_until=now - timedelta(days=1)))
        db.commit()
        f2 = evaluate_rule(db, env["tid"], r, now, _Reg(None))
        assert f2 and f2[0].severity == "CRITICAL"


# ================================================================ 评估器
def test_evaluator_dedupe_cooldown_recover(env):
    from alerting.evaluator import evaluate_all
    _mk_channel(env)
    code = _rule(env, "ev1", "UNKNOWN_OVER", {"threshold": 1}, cooldown=60)
    now = utcnow()
    reg = _Reg(_SimpleSummary(unknown=5))
    with env["sf"]() as db:
        out = evaluate_all(db, env["tid"], now, registry=reg)
        db.commit()
        assert out["new"] == 1
    # 同指纹重复评估：occurrences 增加、冷却内不再通知
    with env["sf"]() as db:
        out = evaluate_all(db, env["tid"], now + timedelta(minutes=5),
                           registry=reg)
        db.commit()
        assert out["new"] == 0 and out["re_notified"] == 0
        ev = (db.query(AlertEvent)
              .filter_by(rule_code=code, resolved_at=None).one())
        assert ev.occurrences == 2
    # 超冷却再通知
    with env["sf"]() as db:
        out = evaluate_all(db, env["tid"], now + timedelta(minutes=90),
                           registry=reg)
        db.commit()
        assert out["re_notified"] == 1
    # 恢复：证据完整 HEALTHY（unknown=0 且有 summary）
    reg_ok = _Reg(_SimpleSummary(unknown=0, confirmed=10))
    with env["sf"]() as db:
        out = evaluate_all(db, env["tid"], now + timedelta(minutes=95),
                           registry=reg_ok)
        db.commit()
        assert out["recovered"] == 1
        ev = (db.query(AlertEvent).filter_by(rule_code=code).one())
        assert ev.resolved_at is not None and ev.state == "RESOLVED"


def test_evaluator_data_gap_does_not_recover(env):
    from alerting.evaluator import evaluate_all
    _mk_channel(env)
    code = _rule(env, "ev2", "UNKNOWN_OVER", {"threshold": 1})
    now = utcnow()
    with env["sf"]() as db:
        evaluate_all(db, env["tid"], now, registry=_Reg(_SimpleSummary(unknown=5)))
        db.commit()
    # summary 不可用（DATA_UNAVAILABLE finding）→ 不恢复既有告警
    with env["sf"]() as db:
        out = evaluate_all(db, env["tid"], now + timedelta(minutes=10),
                           registry=_Reg(None))
        db.commit()
        assert out["recovered"] == 0
        ev = (db.query(AlertEvent)
              .filter(AlertEvent.rule_code == code,
                      AlertEvent.fingerprint == "unknown:drug_trace_alihealth",
                      AlertEvent.resolved_at.is_(None)).first())
        assert ev is not None


def test_evaluator_rule_exception_isolated(env):
    from alerting.evaluator import evaluate_all
    _mk_channel(env)
    _rule(env, "good", "UNKNOWN_OVER", {"threshold": 1})
    with env["sf"]() as db:
        # 坏 kind 规则直接 ORM 插入（API 层校验会拦；评估器须异常隔离）
        db.add(AlertRule(id=new_uuid(), tenant_id=env["tid"], code="boom",
                         name="boom", target_code="drug_trace_alihealth",
                         kind="NOT_A_KIND", params={}, severity="WARN",
                         enabled=True, cooldown_minutes=60,
                         notify_channels=["file_default"]))
        db.commit()
        out = evaluate_all(db, env["tid"], utcnow(), registry=_Reg(None))
        db.commit()
        assert out["errors"] == 1                 # 异常隔离不中断
        assert out["rules"] == 2                  # 另一条正常评估
        # RT-03 S05：self 指纹=alerting.self:{tenant}:{rule}（规则级隔离）
        assert (db.query(AlertEvent)
                .filter(AlertEvent.fingerprint ==
                        f"alerting.self:{env['tid']}:boom",
                        AlertEvent.resolved_at.is_(None)).count()) == 1


def test_evaluator_ack_no_renotify_but_recovers(env):
    from alerting.evaluator import evaluate_all
    _mk_channel(env)
    code = _rule(env, "ev3", "UNKNOWN_OVER", {"threshold": 1})
    now = utcnow()
    reg = _Reg(_SimpleSummary(unknown=5))
    with env["sf"]() as db:
        evaluate_all(db, env["tid"], now, registry=reg)
        db.commit()
        ev = (db.query(AlertEvent).filter_by(rule_code=code,
                                             resolved_at=None).one())
        ev.acked_by = "op"; ev.acked_at = now
        db.commit()
    with env["sf"]() as db:
        out = evaluate_all(db, env["tid"], now + timedelta(hours=3),
                           registry=reg)
        db.commit()
        assert out["re_notified"] == 0            # ACK 后不重复通知
        out2 = evaluate_all(db, env["tid"], now + timedelta(hours=4),
                            registry=_Reg(_SimpleSummary(unknown=0)))
        db.commit()
        assert out2["recovered"] == 1             # 恢复照发


# ================================================================ 通道/脱敏
def test_channel_file_writes_jsonl(env):
    from alerting.channels import send
    rel = f"var/alerts-test/unit-{new_uuid()[:8]}.jsonl"
    _mk_channel(env, "f1", config={"path": rel})
    with env["sf"]() as db:
        ch = db.query(NotificationChannel).filter_by(code="f1").one()
        err = send(ch, "测试正文", kind="ALERT")
    assert err == "", err
    line = open(rel, encoding="utf-8").read().strip()
    payload = json.loads(line)
    assert payload["kind"] == "ALERT" and payload["text"] == "测试正文"


def test_channel_file_path_traversal_blocked(env):
    from alerting.channels import send, validate_channel_config
    from contracts import ApiError
    with pytest.raises(ApiError):
        validate_channel_config("file", {"path": "../outside.jsonl"})
    with pytest.raises(ApiError):
        validate_channel_config("file", {"path": "C:/abs/x.jsonl"})


def test_channel_smtp_monkeypatch_and_missing_env(env, monkeypatch):
    """RT-03 S07：dispatch（send）对 smtp 一律拒绝不外发；传输管道
    （_send_smtp）经直接调用保持既有断言（FakeSMTP/凭据缺失语义不变）。"""
    from alerting.channels import send, _send_smtp
    sent = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"], sent["port"] = host, port
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def login(self, u, p):
            sent["login"] = (u, p)
        def sendmail(self, frm, to, body):
            sent["mail"] = (frm, to, body)

    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    monkeypatch.setenv("BLYB_TEST_SMTP_PWD", "sekret-value")
    with env["sf"]() as db:
        db.add(NotificationChannel(
            id=new_uuid(), tenant_id=env["tid"], code="s1", type="smtp",
            config={"host": "127.0.0.1", "port": 25, "user": "u",
                    "password_env": "BLYB_TEST_SMTP_PWD",
                    "from": "a@b", "to": ["c@d"]}, enabled=True))
        db.commit()
        ch = db.query(NotificationChannel).filter_by(code="s1").one()
        # dispatch 入口拒绝（历史 enabled=True 也不实际网络发送）
        err = send(ch, "hi", kind="ALERT")
        assert "服务端停用" in err and "需修订" in err
        assert "mail" not in sent                 # 未触网
        # 传输管道直测：登录凭据/发件语义与 LT-04 一致
        assert _send_smtp(ch, "hi", "ALERT") == ""
        assert sent["login"] == ("u", "sekret-value") and sent["mail"]
    # 环境变量缺失 → 错误字符串（不抛出；传输层直测）
    monkeypatch.delenv("BLYB_TEST_SMTP_PWD", raising=False)
    with env["sf"]() as db:
        ch = db.query(NotificationChannel).filter_by(code="s1").one()
        err = _send_smtp(ch, "hi", "ALERT")
    assert "凭据未配置" in err


def test_channel_webhook_templates_and_local_server(env):
    """RT-03 S07：dispatch（send）对 webhook 一律拒绝；模板/回环服务器
    语义经 _send_webhook 直接调用保持既有断言（loopback 强制、结束即关）。"""
    from alerting.channels import send, _send_webhook
    received = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length", 0))
            received.append(json.loads(self.rfile.read(n)))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/hook"
        os.environ["BLYB_TEST_HOOK_URL"] = url
        with env["sf"]() as db:
            for i, tpl in enumerate(("wecom", "dingtalk", "generic")):
                db.add(NotificationChannel(
                    id=new_uuid(), tenant_id=env["tid"], code=f"w{i}",
                    type="webhook", enabled=True,
                    config={"url_env": "BLYB_TEST_HOOK_URL", "template": tpl}))
            db.commit()
            ch0 = db.query(NotificationChannel).filter_by(code="w0").one()
            err = send(ch0, "x", kind="ALERT")     # dispatch 拒绝、不外发
            assert "服务端停用" in err and "需修订" in err
            assert received == []
            for i, tpl in enumerate(("wecom", "dingtalk", "generic")):
                ch = db.query(NotificationChannel).filter_by(code=f"w{i}").one()
                assert _send_webhook(ch, f"msg-{tpl}", "ALERT") == ""
        assert len(received) == 3
        assert received[0] == {"msgtype": "text", "text": {"content": "msg-wecom"}}
        assert received[1]["msgtype"] == "text"
        assert received[2]["source"] == "blyb-alerts"
        # URL 环境变量缺失 → 错误
        os.environ.pop("BLYB_TEST_HOOK_URL", None)
        with env["sf"]() as db:
            ch = db.query(NotificationChannel).filter_by(code="w0").one()
            assert "URL 未配置" in _send_webhook(ch, "x", "ALERT")
    finally:
        srv.shutdown()
        srv.server_close()


def test_channel_inline_secrets_rejected(env):
    from alerting.channels import validate_channel_config
    from contracts import ApiError
    with pytest.raises(ApiError) as e1:
        validate_channel_config("smtp", {"host": "h", "to": ["a@b"],
                                         "password": "plain"})
    assert e1.value.code == "SECRET_INLINE_FORBIDDEN"
    with pytest.raises(ApiError):
        validate_channel_config("webhook", {"url": "https://x/k=secret",
                                            "url_env": "BLYB_TEST_HOOK_URL"})
    # RT-03 S07：url_env 缺失时内联 url 仍被拒（不能因未传 url_env 绕过）
    with pytest.raises(ApiError) as e2:
        validate_channel_config("webhook", {"url": "https://x/hook"})
    assert e2.value.code == "SECRET_INLINE_FORBIDDEN"
    # RT-03 S07：环境变量名须 ^[A-Z][A-Z0-9_]{2,63}$（"X"/"U" 不再合法）
    with pytest.raises(ApiError):
        validate_channel_config("webhook", {"url_env": "X"})
    with pytest.raises(ApiError):
        validate_channel_config("smtp", {"host": "h", "to": ["a@b"],
                                         "password_env": "X"})
    # 合法：只带 *_env 引用（规范名）+ 通道专属允许字段
    validate_channel_config("smtp", {"host": "h", "to": ["a@b"],
                                     "password_env": "BLYB_SMTP_PWD"})
    validate_channel_config("webhook", {"url_env": "BLYB_HOOK_URL",
                                        "template": "generic"})
    # 任意 xxx_env 不是免检通行证：不在允许字段表内的键一律拒绝
    with pytest.raises(ApiError):
        validate_channel_config("webhook", {"url_env": "BLYB_HOOK_URL",
                                            "api_key_env": "BLYB_KEY"})


def test_redact_alert_text_whitelist_and_masking():
    from alerting.redact import alert_text
    text = alert_text("标题", {"count": 3, "sql": "SELECT * FROM t",
                               "payload": {"a": 1}, "note": "证号000000199001010000"},
                      target_name="业务A", rule_name="规则R")
    assert "count=3" in text and "业务A" in text and "规则R" in text
    assert "sql" not in text and "SELECT" not in text
    assert "payload" not in text
    assert "000000199001010000" not in text     # 证件号打码


def test_smtp_webhook_enable_and_test_rejected_this_batch(env):
    from alerting.channels import assert_channel_change_allowed
    from contracts import ApiError
    with pytest.raises(ApiError) as e1:
        assert_channel_change_allowed("smtp", enabling=True)
    assert e1.value.code == "NOT_ALLOWED_THIS_BATCH"
    with pytest.raises(ApiError):
        assert_channel_change_allowed("webhook", enabling=False, testing=True)
    assert_channel_change_allowed("file", enabling=True)      # file 不受限
    assert_channel_change_allowed("smtp", enabling=False, testing=False)
