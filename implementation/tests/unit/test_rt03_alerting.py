"""RT-03 S05/S06/S07/S10：self 监控隔离、递归脱敏与通道防线。

覆盖：A 错 B 正常互不影响；A 恢复时 B 仍异常；self 恢复但业务告警保留；
DATA_UNAVAILABLE 不恢复；跨租户 self 指纹隔离；fingerprint 列长校验；
嵌套 dict/list 递归脱敏（非白名单不透传）；last_notified_at 冷却信息不丢
（防通知风暴）；历史 enabled=True 的 smtp/webhook 直接 dispatch 不外发；
内联 url 拒绝（url_env 缺失不绕过）；环境变量名规范；任意 xxx_env 非免检。
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.engine import utcnow  # noqa: E402
from core_db.models import (AlertEvent, AlertRule, NotificationChannel,  # noqa: E402
                            Tenant)


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/rt03al.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace", "pathology"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="rt03-al", name="告警隔离测试")
        db.add(t)
        db.flush()
        tid = t.id
        db.add(NotificationChannel(
            id=new_uuid(), tenant_id=tid, code="file_default", type="file",
            config={"path": f"var/alerts-test/rt03al-{new_uuid()[:8]}.jsonl"},
            enabled=True))
        db.commit()
    yield {"sf": sf, "engine": engine, "tid": tid, "reg": reg}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


class _SimpleSummary:
    def __init__(self, *, unknown=0, confirmed=0):
        self.last_success_at = "2026-11-13T00:00:00+00:00" if confirmed else None
        self.backlog = 0
        self.backlog_total = 0
        self.buckets = {"UNKNOWN": unknown, "REJECTED": 0, "CONFIRMED": confirmed,
                        "PENDING": 0, "SENDING": 0, "ACCEPTED": 0, "PARTIAL": 0,
                        "NOT_REPORTABLE": 0}
        self.evidence_kind = "real"


class _Reg:
    def __init__(self, summary):
        self.summary = summary

    def target_summary(self, db, tenant_id, code, days, **kw):
        return self.summary


def _rule(env, code, kind, params=None, *, target="drug_trace_alihealth",
          enabled=True):
    with env["sf"]() as db:
        r = AlertRule(id=new_uuid(), tenant_id=env["tid"], code=code,
                      name=code, target_code=target, kind=kind,
                      params=params or {}, severity="WARN", enabled=enabled,
                      cooldown_minutes=60, notify_channels=["file_default"])
        db.add(r)
        db.commit()
        return code


def _bad_rule(env, code):
    """坏 kind 规则（评估必抛 ValueError——评估器异常隔离路径）。"""
    with env["sf"]() as db:
        db.add(AlertRule(id=new_uuid(), tenant_id=env["tid"], code=code,
                         name=code, target_code="drug_trace_alihealth",
                         kind="NOT_A_KIND", params={}, severity="WARN",
                         enabled=True, cooldown_minutes=60,
                         notify_channels=["file_default"]))
        db.commit()


def _self_fp(tid, rule_code):
    return f"alerting.self:{tid}:{rule_code}"


# ===================================================== S05 self 隔离
def test_rule_a_error_b_normal_isolated(env):
    """A 规则异常 → A 的 self 事件；B 规则正常评估出业务告警，互不影响。"""
    from alerting.evaluator import evaluate_all
    _bad_rule(env, "boomA")
    _rule(env, "goodB", "UNKNOWN_OVER", {"threshold": 1})
    with env["sf"]() as db:
        out = evaluate_all(db, env["tid"], utcnow(),
                           registry=_Reg(_SimpleSummary(unknown=5)))
        db.commit()
        assert out["errors"] == 1
        assert out["new"] == 1                     # B 的业务告警照常落库
        assert db.query(AlertEvent).filter_by(
            fingerprint=_self_fp(env["tid"], "boomA"),
            resolved_at=None).count() == 1
        assert db.query(AlertEvent).filter_by(
            rule_code="goodB", resolved_at=None).count() == 1


def test_rule_a_recovers_while_b_still_failing(env):
    """A 恢复（本轮无异常）→ A 的 self 事件恢复；B 仍异常 → B self 保留。"""
    from alerting.evaluator import evaluate_all
    _bad_rule(env, "boomA")
    _bad_rule(env, "boomB")
    now = utcnow()
    with env["sf"]() as db:
        evaluate_all(db, env["tid"], now, registry=_Reg(_SimpleSummary()))
        db.commit()
        assert db.query(AlertEvent).filter_by(resolved_at=None).count() == 2
    # 修复 A（重新登记为好规则）后评估：A self 恢复、B self 保留
    with env["sf"]() as db:
        db.query(AlertRule).filter_by(code="boomA").delete()
        db.commit()
    _rule(env, "boomA", "UNKNOWN_OVER", {"threshold": 1})
    with env["sf"]() as db:
        out = evaluate_all(db, env["tid"], now + timedelta(minutes=5),
                           registry=_Reg(_SimpleSummary()))
        db.commit()
        assert out["self_recovered"] == 1
        assert db.query(AlertEvent).filter_by(
            fingerprint=_self_fp(env["tid"], "boomA"),
            resolved_at=None).count() == 0
        assert db.query(AlertEvent).filter_by(
            fingerprint=_self_fp(env["tid"], "boomB"),
            resolved_at=None).count() == 1


def test_self_recovered_but_business_alert_kept(env):
    """self 恢复条件=无异常；业务恢复条件=无 finding——两者独立：
    规则本轮发现业务问题（评估不抛错）→ self 恢复但业务告警保留。"""
    from alerting.evaluator import evaluate_all
    code = _rule(env, "selfbiz", "UNKNOWN_OVER", {"threshold": 1})
    now = utcnow()
    # 先制造 self 事件（坏 kind），再修复为本 kind 继续有业务 finding
    with env["sf"]() as db:
        db.query(AlertRule).filter_by(code=code).delete()
        db.commit()
    with env["sf"]() as db:
        db.add(AlertRule(id=new_uuid(), tenant_id=env["tid"], code=code,
                         name=code, target_code="drug_trace_alihealth",
                         kind="NOT_A_KIND", params={}, severity="WARN",
                         enabled=True, cooldown_minutes=60,
                         notify_channels=["file_default"]))
        db.commit()
        evaluate_all(db, env["tid"], now, registry=_Reg(_SimpleSummary()))
        db.commit()
        assert db.query(AlertEvent).filter_by(
            fingerprint=_self_fp(env["tid"], code),
            resolved_at=None).count() == 1
    with env["sf"]() as db:
        db.query(AlertRule).filter_by(code=code).delete()
        db.commit()
    _rule(env, code, "UNKNOWN_OVER", {"threshold": 1})
    with env["sf"]() as db:
        out = evaluate_all(db, env["tid"], now + timedelta(minutes=5),
                           registry=_Reg(_SimpleSummary(unknown=5)))
        db.commit()
        assert out["self_recovered"] == 1          # 评估器自身已恢复
        assert out["recovered"] == 0               # 业务仍有问题：不恢复
        ev = db.query(AlertEvent).filter_by(rule_code=code,
                                            resolved_at=None).one()
        assert ev.fingerprint == f"unknown:{'drug_trace_alihealth'}"


def test_data_gap_does_not_recover_business(env):
    """DATA_UNAVAILABLE（summary None）→ 不恢复既有业务告警。"""
    from alerting.evaluator import evaluate_all
    code = _rule(env, "gap", "UNKNOWN_OVER", {"threshold": 1})
    now = utcnow()
    with env["sf"]() as db:
        evaluate_all(db, env["tid"], now, registry=_Reg(_SimpleSummary(unknown=5)))
        db.commit()
    with env["sf"]() as db:
        out = evaluate_all(db, env["tid"], now + timedelta(minutes=5),
                           registry=_Reg(None))
        db.commit()
        assert out["recovered"] == 0
        ev = db.query(AlertEvent).filter_by(
            rule_code=code, fingerprint="unknown:drug_trace_alihealth",
            resolved_at=None).one()               # 业务告警未恢复
        assert ev.resolved_at is None


def test_self_fingerprint_tenant_and_length_bounds(env):
    """跨租户 self 指纹隔离；超长 rule_code → 指纹构造期拒绝（不写库）。"""
    from alerting.evaluator import self_fingerprint
    assert self_fingerprint("t1", "r") == "alerting.self:t1:r"
    assert self_fingerprint("t2", "r") != self_fingerprint("t1", "r")
    with pytest.raises(ValueError):
        self_fingerprint("t1", "x" * 200)


# ===================================================== S06 递归脱敏
def test_sanitize_detail_recursive_nested_and_depth(env):
    """嵌套 dict/list 字符串继续脱敏；非白名单不透传；深度/长度受限。"""
    from alerting.redact import sanitize_detail
    detail = {
        "code": "dt.run",                       # 白名单：保留
        "unmapped_states": {"98": 1, "note": "证件号110101199001010002"},  # 白名单嵌套
        "sql": "SELECT * FROM patient",         # 非白名单：不透传
        "payload": {"inner": {"deep": "身份证110101199001010003"}},  # 非白名单整体丢弃
        "schedule_code": {"a": ["长" * 600]},   # 白名单嵌套：截断+脱敏
    }
    out = sanitize_detail(detail)
    assert out["code"] == "dt.run"
    assert "110101199001010002" not in str(out["unmapped_states"])
    assert "sql" not in out and "payload" not in out
    assert len(str(out["schedule_code"])) < 600
    # 深度限制：白名单键携带超深结构 → 深处丢弃为空
    deep = {"error_code": {"l1": {"l2": {"l3": {"l4": {"l5": {"l6": {"l7": "x"}}}}}}}}
    out2 = sanitize_detail(deep)
    assert "l7" not in str(out2)


def test_cooldown_survives_redaction_last_notified_at(env):
    """last_notified_at 经脱敏保持 ISO 可解析（冷却判定不失效→防通知风暴）。"""
    from alerting.redact import sanitize_detail
    from datetime import datetime
    iso = "2026-11-13T00:30:00.123456"
    out = sanitize_detail({"count": 1, "last_notified_at": iso})
    assert datetime.fromisoformat(out["last_notified_at"]) == \
        datetime.fromisoformat(iso)
    # 非时间戳值不受该放行影响（普通白名单键仍脱敏）
    out2 = sanitize_detail({"observed": "110101199001010009"})
    assert "110101199001010009" not in out2["observed"]


def test_notification_log_error_masked(env):
    """NotificationLog.error/detail 经脱敏落库（evaluator._notify 路径）。"""
    from alerting.evaluator import _notify
    from core_db.models import NotificationLog
    with env["sf"]() as db:
        ch = db.query(NotificationChannel).filter_by(
            tenant_id=env["tid"], code="file_default").one()
        ch.config = {"path": "/absolutely/escaping/path.jsonl"}  # 触发 path 拒绝
        db.commit()
        _notify(db, env["tid"], [ch], kind="ALERT",
                title="标题 证件号110101199001010008",
                detail={"code": "x", "last_notified_at":
                        "2026-11-13T00:00:00"},
                rule_name="r", target_name="t", alert_event_id=None)
        db.commit()
        log = db.query(NotificationLog).filter_by(kind="ALERT").one()
        assert log.ok is False
        assert "110101199001010008" not in log.detail.get("title", "")


# ===================================================== S07 通道防线
def test_legacy_enabled_channels_dispatch_refused(env):
    """历史 enabled=True 的 smtp/webhook：直接 dispatch（send）拒绝不外发。"""
    from alerting.channels import send
    with env["sf"]() as db:
        for i, ctype in enumerate(("smtp", "webhook")):
            db.add(NotificationChannel(
                id=new_uuid(), tenant_id=env["tid"], code=f"legacy{i}",
                type=ctype, enabled=True,
                config={"host": "127.0.0.1", "to": ["a@b"],
                        "password_env": "BLYB_LEGACY_PWD"}
                if ctype == "smtp" else
                {"url_env": "BLYB_LEGACY_HOOK", "template": "generic"}))
        db.commit()
        err1 = send(db.query(NotificationChannel).filter_by(
            code="legacy0").one(), "正文", kind="ALERT")
        err2 = send(db.query(NotificationChannel).filter_by(
            code="legacy1").one(), "正文", kind="ALERT")
    assert "服务端停用" in err1 and "需修订" in err1
    assert "服务端停用" in err2 and "需修订" in err2


def test_inline_url_rejected_without_url_env(env):
    """内联 url 拒绝——url_env 缺失不能绕过（S07 核心反例）。"""
    from alerting.channels import validate_channel_config
    from contracts import ApiError
    with pytest.raises(ApiError) as e:
        validate_channel_config("webhook", {"url": "https://eco.example/hook"})
    assert e.value.code == "SECRET_INLINE_FORBIDDEN"
    with pytest.raises(ApiError):
        validate_channel_config("webhook", {"url": "https://x", "url_env":
                                            "BLYB_HOOK_URL"})


def test_env_name_and_allowed_fields_strict(env):
    """url_env/password_env 名须 ^[A-Z][A-Z0-9_]{2,63}$；任意 xxx_env 非免检
    （webhook 拒绝 api_key_env）；webhook 必填 url_env。"""
    from alerting.channels import validate_channel_config
    from contracts import ApiError
    for bad in ("X", "lower_case", "BLYB-", "A" * 65):   # 总长 >64 非法
        with pytest.raises(ApiError):
            validate_channel_config("webhook", {"url_env": bad})
    with pytest.raises(ApiError):               # webhook 必填 url_env
        validate_channel_config("webhook", {"template": "generic"})
    with pytest.raises(ApiError):               # 任意 xxx_env 非免检通行证
        validate_channel_config("webhook", {"url_env": "BLYB_HOOK_URL",
                                            "api_key_env": "BLYB_KEY"})
    validate_channel_config("webhook", {"url_env": "BLYB_HOOK_1",
                                        "template": "wecom"})


def test_smtp_requires_host_to_and_password_env_only(env):
    from alerting.channels import validate_channel_config
    from contracts import ApiError
    with pytest.raises(ApiError):               # 缺 to
        validate_channel_config("smtp", {"host": "h",
                                         "password_env": "BLYB_SMTP_PWD"})
    with pytest.raises(ApiError):               # 缺 host
        validate_channel_config("smtp", {"to": ["a@b"],
                                         "password_env": "BLYB_SMTP_PWD"})
    validate_channel_config("smtp", {"host": "h", "to": ["a@b"],
                                     "password_env": "BLYB_SMTP_PWD",
                                     "user": "u"})
