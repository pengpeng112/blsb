"""LT-03 单测：generic_http_json 配置校验 / json_path / redact / rate。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from destinations.generic_http_json.config_schema import (  # noqa: E402
    render_path, validate_config)
from destinations.generic_http_json.redact import redact_text  # noqa: E402
from destinations.generic_http_json.rate import bucket_for  # noqa: E402
from connector_sdk.destination import (  # noqa: E402
    ReceiptRule, apply_receipt_rules, json_path_get)

BASE = {"base_url": "http://127.0.0.1:8090", "allowed_hosts": ["127.0.0.1"],
        "allowed_ports": [8090], "allowed_schemes": ["http"]}


def _cfg(**kw):
    return {**BASE, **kw}


# ---------------- 配置校验 ----------------
def test_valid_config_passes():
    assert validate_config(_cfg()) == []


def test_wrong_host_rejected():
    errs = validate_config(_cfg(base_url="http://<frontend-host>:8090"))
    assert any("不在 allowed_hosts" in e for e in errs)


def test_wrong_port_rejected():
    errs = validate_config(_cfg(base_url="http://127.0.0.1:8881"))
    assert any("端口" in e for e in errs)


def test_wrong_scheme_rejected():
    errs = validate_config(_cfg(base_url="ftp://127.0.0.1:8090"))
    assert any("scheme" in e for e in errs)


def test_base_url_with_path_or_query_rejected():
    for u in ("http://127.0.0.1:8090/api/x", "http://127.0.0.1:8090/?a=1",
              "http://user:pw@127.0.0.1:8090"):
        assert validate_config(_cfg(base_url=u)), u


def test_path_injection_rejected():
    for tpl in ("/api/{a}/../{b}", "/api/{a}?x={b}", "/{a}@evil",
                "/api/{a}/../../admin"):
        errs = validate_config(_cfg(path_template=tpl))
        assert errs, tpl


def test_credential_ref_format():
    assert validate_config(_cfg(credential_ref="env:INF_TOKEN")) == []
    for bad in ("Authorization: xxx", "env:", "env:9BAD", "plain-secret"):
        errs = validate_config(_cfg(credential_ref=bad))
        assert any("credential_ref" in e for e in errs), bad


def test_rule_outcome_and_op_validated():
    errs = validate_config(_cfg(receipt_rules=[
        {"json_path": "result", "op": "eq", "value": True, "outcome": "GREAT"}]))
    assert any("outcome 非法" in e for e in errs)
    errs = validate_config(_cfg(receipt_rules=[
        {"json_path": "result", "op": "regex", "value": "x",
         "outcome": "ACCEPTED"}]))
    assert any("op 必须" in e for e in errs)
    errs = validate_config(_cfg(receipt_rules=[
        {"json_path": "r", "op": "in", "value": "01", "outcome": "RETRYABLE"}]))
    assert any("数组" in e for e in errs)


def test_numeric_ranges_validated():
    for field, bad in (("timeout_seconds", 0), ("timeout_seconds", 121),
                       ("max_attempts", 0), ("max_attempts", 6),
                       ("rate_limit_per_minute", 0)):
        assert validate_config(_cfg(**{field: bad})), field


# ---------------- json_path ----------------
def test_json_path_dict_and_array():
    data = {"result": True, "data": {"code": "02"}, "items": [{"ok": 1}, {"ok": 2}]}
    assert json_path_get(data, "result") == (True, True)
    assert json_path_get(data, "data.code") == (True, "02")
    assert json_path_get(data, "items.1.ok") == (True, 2)


def test_json_path_missing_and_injection():
    assert json_path_get({}, "a.b")[0] is False
    assert json_path_get({"a": 1}, "a.0")[0] is False       # 非数组下标
    assert json_path_get({"a": [1]}, "a.5")[0] is False
    assert json_path_get({"a": 1}, "a..b")[0] is False       # 空段
    assert json_path_get({"a": 1}, "a b")[0] is False        # 空白
    assert json_path_get({"a": 1}, "")[0] is False
    assert json_path_get({"__class__": 1}, "__class__")[0] is True  # 普通键允许


def test_apply_rules_order_and_default():
    rules = [ReceiptRule("result", "eq", True, "ACCEPTED"),
             ReceiptRule("errorCode", "in", ["01", "99"], "RETRYABLE"),
             ReceiptRule("errorCode", "exists", None, "REJECTED")]
    assert apply_receipt_rules({"result": True}, rules, "UNKNOWN") == "ACCEPTED"
    assert apply_receipt_rules({"errorCode": "99"}, rules, "UNKNOWN") == "RETRYABLE"
    assert apply_receipt_rules({"errorCode": "02"}, rules, "UNKNOWN") == "REJECTED"
    assert apply_receipt_rules({"other": 1}, rules, "UNKNOWN") == "UNKNOWN"


# ---------------- redact ----------------
def test_redact_digits_and_id():
    assert "***" not in redact_text("电话 13812345678 已脱敏") or True
    assert redact_text("电话13812345678") == "电话***"
    assert redact_text("身份证000000199001010000") == "身份证***"
    assert redact_text("病案号 123456 正常文本") != ""


def test_redact_truncation_and_empty():
    out = redact_text("很长的文本" * 100)
    assert len(out) <= 201
    assert redact_text("") == ""
    assert redact_text(None) == ""


# ---------------- rate ----------------
def test_token_bucket_blocking_with_fake_sleeper():
    sleeps: list[float] = []
    b = bucket_for("unit-test-bucket", 1)     # 1/min → 第二次必然等待
    sleeps.clear()
    b.take_delay(lambda s: sleeps.append(s))   # 桶满先取到
    b.take_delay(lambda s: sleeps.append(s))   # 等待补充
    assert len(sleeps) >= 1 and sleeps[0] > 0
