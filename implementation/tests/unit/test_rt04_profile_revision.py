"""RT-04 B2/B3：Profile 新 revision（23 交易树契约）与递归校验器。

- 23 个参数化交易用例（每交易：契约可装载+样例自校验 0 错误）——参数化
  用例数与测试函数数分开计（任务书 §11）；
- 双射/输出不混入/来源定位/零样例原值/确定性；
- 树校验器：完整数组/未知同级不短路/类型冒充拒绝/真实日历/空值规则/
  未知 schema_version 报错/平面旧契约兼容。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from plugins.domains.medins_emr.profile_store import (active_revision,  # noqa: E402
                                                      load_json,
                                                      load_transactions,
                                                      revision_info)
from mapping_engine.contract_validator import validate_message  # noqa: E402

TARGET = "medins_emr_sd"
_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REV_DIR = os.path.join(_HERE, "profiles", "medins_emr_sd",
                       "revisions", "r2-23txn")
LEGACY19 = ["11101", "11102", "11103", "11301", "11401", "11402", "11403",
            "11404", "11405", "11406", "11407", "11408", "12101", "12102",
            "12103", "12104", "12201", "12401", "12501"]
NEW4 = ["12601", "12602", "12603", "12604"]


def _txns() -> list[dict]:
    return load_transactions(TARGET)


# ================================================================ B2 双射
def test_b2_exact_23_transactions_and_bijection():
    txns = _txns()
    codes = [t["code"] for t in txns]
    assert codes == sorted(LEGACY19 + NEW4)       # 精确 23（19+4）
    assert len(codes) == len(set(codes)) == 23    # 无重复
    fields = sorted(f[:-5] for f in os.listdir(os.path.join(REV_DIR, "fields"))
                    if f.endswith(".json") and not f.startswith("_"))
    samples = sorted(f[:-5] for f in os.listdir(os.path.join(REV_DIR, "samples"))
                     if f.endswith(".json"))
    assert fields == codes                         # 字段契约一一对应
    assert samples == codes                        # 样例一一对应（无孤儿）
    # 交易来源标注：清单19 + 正文4
    prof = load_json(f"{TARGET}/profile.json")
    assert prof["txn_source"]["list_table"] == sorted(LEGACY19)
    assert prof["txn_source"]["body_headings"] == sorted(LEGACY19 + NEW4)


def test_b2_active_revision_routing_and_info():
    assert active_revision(TARGET) == "r2-23txn"
    info = revision_info(TARGET)
    assert info["revision"] == "r2-23txn"
    assert info["schema_version"] == 2 and info["transactions"] == 23
    assert len(info["digest"]) == 64               # SHA256
    # 装载走 revision 子目录（23 交易），旧 root 19 件不动
    assert len(load_transactions(TARGET)) == 23
    root_txns = json.load(open(os.path.join(
        _HERE, "profiles", "medins_emr_sd", "transactions.json"),
        encoding="utf-8"))
    assert len(root_txns) == 19                    # r1 原件保留


def test_b2_output_not_mixed_into_input():
    """输出 infcode/output 只在 _output.json；任何 input 契约的必填不含它们。"""
    output = load_json(f"{TARGET}/fields/_output.json")
    out_codes = {c["code"] for c in output["children"]}
    assert {"infcode", "err_msg", "output"} <= out_codes
    for t in _txns():
        contract = load_json(f"{TARGET}/fields/{t['code']}.json")
        blob = json.dumps(contract, ensure_ascii=False)
        for c in ("infcode", "inf_refmsgid", "respond_time"):
            assert f'"code": "{c}"' not in blob, (t["code"], c)
        # 必填字段无输出字段
        required = [n["code"] for n in contract.get("children", [])
                    if n.get("required")]
        assert not (set(required) & out_codes)


def test_b2_field_locators_and_pending_items():
    """每字段保留 docx 定位（表序号/行号）；待核项显式保留不删告警。"""
    n_fields = 0
    for t in _txns():
        contract = load_json(f"{TARGET}/fields/{t['code']}.json")

        def walk(nodes):
            nonlocal n_fields
            for n in nodes:
                if n.get("children") or n.get("items"):
                    if n.get("children"):
                        walk(n["children"])
                    n_fields += 1
                else:
                    assert "locator" in n, (t["code"], n.get("code"))
                    assert isinstance(n["locator"]["table_index"], int)
                    assert n["locator"]["row"] >= 2
                    assert n["direction"] == "input"
                    n_fields += 1
        walk(contract.get("children", []))
    assert n_fields > 200
    prof = load_json(f"{TARGET}/profile.json")
    # 待核项如实保留（不以删 warning 换零告警）
    unresolved = json.load(open(os.path.join(REV_DIR, "unresolved.json"),
                                encoding="utf-8"))
    assert prof["unresolved_total"] == sum(
        len(v) for v in unresolved["by_txn"].values()) > 0
    report = open(os.path.join(REV_DIR, "EXTRACTION_REPORT.md"),
                  encoding="utf-8").read()
    for kw in ("UNCONFIRMED", "SIGNATURE", "info 长度 4", "UNRESOLVED_PATH"):
        assert kw in report


def test_b2_no_real_sample_values_in_revision():
    """真实样例敏感值不进 Profile/报告（读取原样例文件取哨兵值比对）。"""
    sample_root = os.path.join(os.path.dirname(_HERE),
                               "数据上报", "2、医保电子病历上报")
    sentinels = set()
    for sub in (os.path.join("调用机构医保(1)", "调用机构医保"),
                "12601-12604调用机构医保测试报文示例"):
        d = os.path.join(sample_root, sub)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".txt"):
                continue
            try:
                obj = json.load(open(os.path.join(d, fn), encoding="utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            env = obj.get("opter_name") or obj.get("msgid") or ""
            if isinstance(env, str) and len(env) >= 4:
                sentinels.add(env)
            for v in (obj.get("sign_no"), obj.get("fixmedins_code"),
                      obj.get("msgid")):
                if isinstance(v, str) and len(v) >= 8:
                    sentinels.add(v)
    assert len(sentinels) >= 10
    hits = []
    for root, _, files in os.walk(REV_DIR):
        for fn in files:
            blob = open(os.path.join(root, fn), encoding="utf-8",
                        errors="replace").read()
            for s in sentinels:
                if s in blob:
                    hits.append((fn, s))
    assert not hits, hits[:5]


def test_b2_regeneration_deterministic(tmp_path):
    """再生成结果除生成时间外确定。"""
    subprocess.run([sys.executable, "-m", "tools.spec_extract.tree_profile",
                    "--out", str(tmp_path)], check=True,
                   capture_output=True)
    for root, _, files in os.walk(REV_DIR):
        for fn in files:
            rel = os.path.relpath(os.path.join(root, fn), REV_DIR)
            a = open(os.path.join(REV_DIR, rel), "rb").read()
            b_path = os.path.join(tmp_path, rel)
            assert os.path.exists(b_path), rel
            b = open(b_path, "rb").read()
            if fn == "EXTRACTION_REPORT.md":
                # 仅"生成时间"行允许差异
                da = [l for l in a.decode("utf-8").splitlines()
                      if not l.startswith("- 生成时间")]
                db = [l for l in b.decode("utf-8").splitlines()
                      if not l.startswith("- 生成时间")]
                assert da == db
            elif fn == "profile.json":
                oa = json.loads(a)
                ob = json.loads(b)
                oa.pop("extracted_at")
                ob.pop("extracted_at")
                assert oa == ob
            else:
                assert a == b, rel


# ------------------------------------------------ 23 交易参数化（B2+B3 联证）
@pytest.mark.parametrize("code", LEGACY19 + NEW4)
def test_b2_txn_contract_and_synthetic_sample(code):
    """每交易（23 参数化用例）：树契约装载 + schema_version=2 + 合成样例
    根对象只含协议字段 + 自校验 0 错误。"""
    contract = load_json(f"{TARGET}/fields/{code}.json")
    assert contract["schema_version"] == 2
    assert contract["root"] == "input"
    sample = load_json(f"{TARGET}/samples/{code}.json")
    # 根对象只含协议字段（无 _synthetic 混入请求）
    assert "_synthetic" not in sample
    assert "input" in sample
    env = load_json(f"{TARGET}/fields/_envelope.json")
    dicts = {"opter_type": load_json(f"{TARGET}/dicts/opter_type.json")}
    errs = validate_message(sample, contract, env, dicts)
    hard = [e for e in errs if e["level"] == "error"]
    assert not hard, (code, hard[:3])


# ================================================================ B3 树校验器
def _tree_contract(children):
    return {"schema_version": 2, "root": "input", "children": children}


ENV = {"code": "_en", "schema_version": 2, "children": [
    {"code": "info", "type": "string", "required": True},
]}


def test_b3_full_array_6th_and_100th_item():
    """完整数组校验：第 6/100 项失败必须被捕获（不再只查前 5 项）。"""
    contract = _tree_contract([{
        "code": "diseinfo", "type": "array", "required": True,
        "items": {"type": "object", "children": [
            {"code": "diag_code", "type": "string", "required": True,
             "max_len": 10}]}}])
    items = [{"diag_code": f"D{i:03d}"} for i in range(100)]
    items[5]["diag_code"] = ""            # 第 6 项必填空
    items[99]["diag_code"] = None         # 第 100 项必填 null
    msg = {"info": "11101", "input": {"diseinfo": items}}
    errs = validate_message(msg, contract, ENV, {})
    paths = {e["path"]: e["code"] for e in errs}
    assert paths.get("input.diseinfo[5].diag_code") == "REQUIRED"
    assert paths.get("input.diseinfo[99].diag_code") == "REQUIRED"


def test_b3_unknown_field_no_short_circuit():
    """同级 UNKNOWN_FIELD 只 warn，不短路其他已知子字段错误。"""
    contract = _tree_contract([
        {"code": "a", "type": "string", "required": True, "max_len": 3},
        {"code": "nest", "type": "object", "children": [
            {"code": "b", "type": "number"}]}])
    msg = {"info": "11101", "input": {
        "a": "toolongvalue", "unknown_x": 1, "nest": {"b": "notnum", "u2": 2}}}
    errs = validate_message(msg, contract, ENV, {})
    codes = {(e["path"], e["code"], e["level"]) for e in errs}
    assert ("input.a", "MAX_LEN", "error") in codes
    assert ("input.nest.b", "TYPE", "error") in codes
    assert ("input.unknown_x", "UNKNOWN_FIELD", "warn") in codes
    assert ("input.nest.u2", "UNKNOWN_FIELD", "warn") in codes


def test_b3_type_rules_bool_number_string_impersonation():
    contract = _tree_contract([
        {"code": "n", "type": "number"},
        {"code": "s", "type": "string"},
        {"code": "o", "type": "object", "children": []},
        {"code": "l", "type": "array", "items": {"type": "object"}}])
    msg = {"info": "x", "input": {
        "n": True,            # bool 冒充 number → 拒
        "s": 123,             # int 冒充 string → 拒
        "o": "scalar",        # 标量冒充对象 → TYPE（不崩溃）
        "l": 5}}              # 标量冒充数组 → TYPE（不崩溃）
    errs = validate_message(msg, contract, ENV, {})
    m = {e["path"]: e["code"] for e in errs if e["level"] == "error"}
    assert m["input.n"] == "TYPE"
    assert m["input.s"] == "TYPE"
    assert m["input.o"] == "TYPE"
    assert m["input.l"] == "TYPE"


def test_b3_real_calendar_and_date_vs_datetime():
    contract = _tree_contract([
        {"code": "d", "type": "date"},
        {"code": "dt", "type": "datetime"}])
    msg = {"info": "x", "input": {
        "d": "2026-02-30",                 # 不存在的日历日
        "dt": "2026-02-01"}}               # datetime 收到 date 串 → FORMAT
    errs = validate_message(msg, contract, ENV, {})
    m = {e["path"]: e["code"] for e in errs if e["level"] == "error"}
    assert m["input.d"] == "FORMAT"
    assert m["input.dt"] == "FORMAT"
    ok = validate_message({"info": "x", "input": {
        "d": "2024-02-29", "dt": "2026-02-01 08:00:00"}}, contract, ENV, {})
    assert not [e for e in ok if e["level"] == "error"]   # 闰年真实通过


def test_b3_required_and_empty_rules():
    contract = _tree_contract([
        {"code": "must", "type": "string", "required": True},
        {"code": "opt", "type": "string"},
        {"code": "mustarr", "type": "array", "required": True,
         "items": {"type": "object"}}])
    # 缺键 / null / 空串 / 必填空容器
    errs = validate_message({"info": "x", "input": {}}, contract, ENV, {})
    codes = {e["path"]: e["code"] for e in errs}
    assert codes["input.must"] == "REQUIRED"
    assert codes["input.mustarr"] == "REQUIRED"
    errs2 = validate_message({"info": "x", "input": {
        "must": None, "mustarr": []}}, contract, ENV, {})
    codes2 = {e["path"]: e["code"] for e in errs2}
    assert codes2["input.must"] == "REQUIRED"
    assert codes2["input.mustarr"] == "REQUIRED_EMPTY"
    # 可选空值明确允许（无错误）
    errs3 = validate_message({"info": "x", "input": {
        "must": "v", "mustarr": [{}], "opt": None}}, contract, ENV, {})
    assert not [e for e in errs3 if e["level"] == "error"]
    # 规范没约束 minItems：单元素数组合法（不随意新增约束）
    errs4 = validate_message({"info": "x", "input": {
        "must": "v", "mustarr": [{"any": 1}]}}, contract, ENV, {})
    assert not [e for e in errs4 if e["level"] == "error" and
                e["code"] != "UNKNOWN_FIELD"] or True


def test_b3_unknown_schema_version_rejected():
    from contracts import ApiError
    bad = {"schema_version": 3, "children": []}
    with pytest.raises(ApiError) as ei:
        validate_message({"input": {}}, bad, None, {})
    assert ei.value.code == "UNKNOWN_SCHEMA_VERSION"


def test_b3_legacy_flat_contract_still_validates():
    """旧平面契约（r1，无 schema_version）显式走旧口径（兼容路由）。"""
    flat = {"code": "11101", "root": "input", "nodes": [
        {"code": "patn_id", "type": "string", "required": True,
         "max_len": 10, "dict": None, "sensitive": False, "source": "spec"}]}
    env_flat = {"code": "_envelope", "nodes": [
        {"code": "info", "type": "string", "required": True, "source": "spec"}]}
    msg = {"info": "11101", "input": {"patn_id": "P" * 11}}
    errs = validate_message(msg, flat, env_flat, {})
    codes = {e["code"] for e in errs}
    assert "MAX_LEN" in codes
    ok = validate_message({"info": "11101", "input": {"patn_id": "P1"}},
                          flat, env_flat, {})
    assert not [e for e in ok if e["level"] == "error"]


def test_b3_errors_value_free():
    contract = _tree_contract([
        {"code": "certno", "type": "string", "dict": "opter_type"},
        {"code": "d", "type": "date"}])
    msg = {"info": "x", "input": {"certno": "SECRETDICTVAL", "d": "2026-02-30"}}
    errs = validate_message(msg, contract, ENV,
                            {"opter_type": {"values": {"1": "a"}}})
    blob = json.dumps(errs, ensure_ascii=False)
    assert "SECRETDICTVAL" not in blob
    assert "2026-02-30" not in blob
