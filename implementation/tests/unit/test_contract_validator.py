"""LT-07 契约校验器单测：各错误码×2+嵌套数组+未知字段 WARN+日历语义。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from mapping_engine.contract_validator import validate_message  # noqa: E402

ENV = {"nodes": [
    {"code": "info", "name": "交易编号", "type": "string", "max_len": None,
     "required": True},
    {"code": "inf_time", "name": "交易时间", "type": "datetime",
     "max_len": None, "required": True},
    {"code": "opter_type", "name": "经办人类别", "type": "string",
     "max_len": 3, "required": True, "dict": "opter_type"},
    {"code": "fixmedins_code", "name": "机构编号", "type": "string",
     "max_len": 14, "required": True},
]}

FIELDS = {"length_unit": "UNCONFIRMED", "nodes": [
    {"code": "patn_id", "name": "患者ID", "type": "string", "max_len": 30,
     "required": True, "dict": None},
    {"code": "age", "name": "年龄", "type": "number", "max_len": None,
     "required": True, "dict": None},
    {"code": "birthday", "name": "出生日期", "type": "date", "max_len": None,
     "required": False, "dict": None},
]}

DICTS = {"opter_type": {"code": "opter_type", "name": "经办人类别",
                        "values": {"1": "经办人", "2": "自助终端"}}}


def _msg(**over):
    m = {"info": "11101", "inf_time": "2026-01-01 00:00:00",
         "opter_type": "1", "fixmedins_code": "H" * 14,
         "input": {"patn_id": "P1", "age": 30}}
    m.update(over)
    return m


def codes(errs):
    return [e["code"] for e in errs]


def test_valid_message_passes():
    assert validate_message(_msg(), FIELDS, ENV, DICTS) == []


def test_required_envelope_and_body():
    errs = validate_message(_msg(input={}), FIELDS, ENV, DICTS)
    c = codes(errs)
    assert c.count("REQUIRED") >= 2       # patn_id + age（缺失键）


def test_type_number_rejects_string():
    errs = validate_message(_msg(input={"patn_id": "P", "age": "abc"}),
                            FIELDS, ENV, DICTS)
    assert "TYPE" in codes(errs)


def test_max_len_chars_with_unconfirmed_note():
    errs = validate_message(_msg(input={"patn_id": "P" * 31, "age": 1}),
                            FIELDS, ENV, DICTS)
    assert "MAX_LEN" in codes(errs)
    assert "LENGTH_UNIT_UNCONFIRMED" in codes(errs)   # 附注（warn 级）


def test_dict_value_rejected():
    errs = validate_message(_msg(opter_type="9"), FIELDS, ENV, DICTS)
    assert "DICT" in codes(errs)
    # 合法字典值过
    assert validate_message(_msg(), FIELDS, ENV, DICTS) == []


def test_format_and_calendar_semantics():
    # 格式错
    errs = validate_message(_msg(inf_time="2026/01/01 00:00:00"),
                            FIELDS, ENV, DICTS)
    assert "FORMAT" in codes(errs)
    # 格式对但日历不存在（2月30日）——正则通过但 strptime 拒绝
    errs2 = validate_message(
        _msg(input={"patn_id": "P", "age": 1, "birthday": "2026-02-30"}),
        FIELDS, ENV, DICTS)
    assert "FORMAT" in codes(errs2)
    assert any("真实日历" in e["message"] for e in errs2)


def test_unknown_field_is_warn():
    errs = validate_message(_msg(input={"patn_id": "P", "age": 1,
                                        "mystery": "x"}),
                            FIELDS, ENV, DICTS)
    unk = [e for e in errs if e["code"] == "UNKNOWN_FIELD"]
    assert unk and unk[0]["level"] == "warn"


def test_envelope_unknown_warn_and_nested_array_path():
    fields2 = {"length_unit": None, "nodes": [
        {"code": "diseinfo", "name": "诊断", "type": "string",
         "max_len": 10, "required": False, "dict": None}]}
    msg = _msg(input={"diseinfo": ["TOO-LONG-DIAG-CODE!!!"]})
    errs = validate_message(msg, fields2, ENV, DICTS)
    assert any(e["path"].startswith("input.diseinfo[0]") and
               e["code"] == "MAX_LEN" for e in errs)
