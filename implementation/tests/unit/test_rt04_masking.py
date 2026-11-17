"""RT-04 B1：医保联调台递归脱敏防线（三层/未知节点/数字隐私/超限拒绝/原生列零明文）。

合成哨兵全部为本文件随机生成（每轮不同），不使用任何真实样例值。
"""
from __future__ import annotations

import json
import os
import random
import string
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401


def _rand_name() -> str:
    return "哨兵姓名" + "".join(random.choices(string.ascii_letters, k=6))


def _rand_idcard() -> str:
    # 合成：110101 + 19xx + 合法月日段 + 9 位随机（不构成真实证件号语义）
    return "110101199001" + f"{random.randint(10, 28):02d}" \
        + "".join(random.choices(string.digits, k=3)) + "X"


def _rand_mobile() -> str:
    return "1" + random.choice("35789") + "".join(
        random.choices(string.digits, k=9))


# ---------------------------------------------------------------- 纯函数层
def test_layer2_key_names_at_depth3_and_unknown_keys():
    from plugins.domains.medins_emr.masking import MASK, mask_sensitive
    name, idc, mob = _rand_name(), _rand_idcard(), _rand_mobile()
    msg = {
        "input": {
            "biz": "x",
            "nested": {"deep": {"opter_name": name, "certno": idc}},
            "arr": [{"patn_name": name}, {"unknown_blob": {"tel": mob}}],
        },
    }
    out = mask_sensitive(msg)
    blob = json.dumps(out, ensure_ascii=False)
    assert name not in blob and idc not in blob and mob not in blob
    assert out["input"]["nested"]["deep"]["opter_name"] == MASK
    assert out["input"]["nested"]["deep"]["certno"] == MASK
    assert out["input"]["arr"][0]["patn_name"] == MASK
    assert out["input"]["arr"][1]["unknown_blob"]["tel"] == MASK
    assert out["input"]["biz"] == "x"          # 非敏感业务字段保留


def test_layer2_numeric_personal_id_and_envelope_coverage():
    from plugins.domains.medins_emr.masking import MASK, mask_sensitive
    msg = {
        "psn_no": 1234567890,              # 数字型个人标识（int）
        "mdtrtarea_admvs": "370104",       # 非敏感
        "input": {"psn_no": "000123", "drugName": "阿莫西林"},
    }
    out = mask_sensitive(msg)
    assert out["psn_no"] == MASK                       # envelope 顶层
    assert out["input"]["psn_no"] == MASK              # 前导零字符串也打码
    assert out["input"]["drugName"] == "阿莫西林"       # 药品名不误伤
    assert out["mdtrtarea_admvs"] == "370104"


def test_layer3_value_pattern_fallback_mixed_array_and_strings():
    from plugins.domains.medins_emr.masking import MASK, mask_sensitive
    idc, mob = _rand_idcard(), _rand_mobile()
    msg = {"input": {"unknown_field": idc,
                     "mixed": [1, mob, {"keep": 1}, [idc]]}}
    out = mask_sensitive(msg)
    assert out["input"]["unknown_field"] == MASK
    assert out["input"]["mixed"][1] == MASK       # list 直接字符串
    assert out["input"]["mixed"][3][0] == MASK    # 嵌套数组尾项
    assert out["input"]["mixed"][0] == 1 and out["input"]["mixed"][2] == {"keep": 1}


def test_parent_sensitive_semantics_masks_whole_subtree():
    from plugins.domains.medins_emr.masking import MASK, mask_sensitive
    name = _rand_name()
    msg = {"input": {"opter_name": {"first": name, "extra": [name]}}}
    out = mask_sensitive(msg)
    assert out["input"]["opter_name"] == MASK     # 父节点命中 → 对象整体打码
    blob = json.dumps(out, ensure_ascii=False)
    assert name not in blob


def test_layer1_contract_sensitive_paths_flat_and_tree():
    from plugins.domains.medins_emr.masking import (MASK,
                                                    contract_sensitive_paths,
                                                    mask_sensitive)
    flat = [{"code": "disease_code", "sensitive": True},
            {"code": "ok_field", "sensitive": False}]
    tree = {"schema_version": 2, "root": "input", "children": [
        {"code": "data", "type": "array", "items": {"type": "object",
         "children": [
             {"code": "secret_mark", "type": "string", "sensitive": True},
             {"code": "plain", "type": "string"}]}}]}
    assert contract_sensitive_paths(flat) == {"disease_code"}
    assert contract_sensitive_paths(tree) == {"data[].secret_mark"}
    msg = {"input": {"disease_code": "J06.9", "ok_field": "v",
                     "data": [{"secret_mark": "S1", "plain": "p"}]}}
    out = mask_sensitive(msg, contract_nodes=tree, envelope_nodes=flat)
    # envelope 契约路径也作用于根（disease_code 在 input 下不命中根路径，
    # 但树契约的 data[].secret_mark 命中第 3 层数组元素）
    assert out["input"]["data"][0]["secret_mark"] == MASK
    assert out["input"]["data"][0]["plain"] == "p"
    # 平面契约 code 命中 envelope 顶层字段
    out2 = mask_sensitive({"disease_code": "X"}, envelope_nodes=flat)
    assert out2["disease_code"] == MASK


def test_limits_reject_oversize_and_too_deep():
    from contracts import ApiError
    from plugins.domains.medins_emr.masking import (MAX_DEPTH,
                                                    ensure_limits,
                                                    mask_sensitive)
    huge = {"input": {"blob": "x" * (300 * 1024)}}
    with pytest.raises(ApiError) as ei:
        mask_sensitive(huge)
    assert ei.value.code == "REQUEST_TOO_LARGE"
    deep, cur = {}, None
    cur = deep
    for _ in range(MAX_DEPTH + 2):
        cur["c"] = {}
        cur = cur["c"]
    with pytest.raises(ApiError) as ei2:
        ensure_limits(deep)
    assert ei2.value.code == "PAYLOAD_TOO_DEEP"


def test_mask_free_text_patterns_and_credentials():
    from plugins.domains.medins_emr.masking import MASK, mask_free_text
    idc, mob = _rand_idcard(), _rand_mobile()
    note = f"患者证件 {idc} 电话 {mob} token=abcdef123456 说明文字"
    out = mask_free_text(note)
    assert idc not in out and mob not in out
    assert f"token={MASK}" in out
    assert "说明文字" in out


# ---------------------------------------------------------------- API 落库原生列
@pytest.fixture()
def api(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/rt04mask.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["medins_emr"])
    from core_db import make_engine, make_session_factory, run_migrations
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    client = TestClient(create_app(session_factory=sf, engine=engine,
                                   registry=reg))
    from contracts import new_uuid
    from contracts.api import hash_token
    with sf() as db:
        from core_db.models import AuthSubject, Tenant
        from plugins.report_targets import seed_default_targets
        t = Tenant(id=new_uuid(), code="rt04m", name="脱敏")
        db.add(t)
        db.flush()
        tid = t.id
        seed_default_targets(db, tid)
        db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind="user",
                           subject_code="u-op", roles=["operator"],
                           token_hash=hash_token("tk-rt04"), status="ACTIVE",
                           target_scopes=["*"]))
        db.commit()
    yield {"client": client, "sf": sf}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


_SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "profiles", "medins_emr_sd", "samples",
    "11101.json")


def _msg(sent: dict) -> dict:
    """合成样例为底（保证可过校验）+ 覆盖哨兵字段。"""
    base = json.load(open(_SAMPLE, encoding="utf-8"))
    base.pop("_synthetic", None)
    base.setdefault("input", {})
    for k, v in sent.items():
        if k == "input":
            base["input"].update(v)
        else:
            base[k] = v
    return base


def test_validate_db_native_columns_zero_plaintext(api):
    name, idc, mob = _rand_name(), _rand_idcard(), _rand_mobile()
    msg = _msg({"opter_name": name, "tel": mob,
                "input": {"deep": {"certno": idc}, "patn_id": "P0001"}})
    r = api["client"].post("/api/v1/medins-emr/validate", json={
        "code": "11101", "message": msg},
        headers={"Authorization": "Bearer tk-rt04"})
    assert r.status_code == 200, r.text
    rid = r.json()["data"]["record_id"]
    from core_db.models import DebugExchange
    with api["sf"]() as db:
        row = db.get(DebugExchange, rid)
        # 原生列逐列检查（不只 JSON 聚合）
        for col in (row.request_masked, row.validation_errors, row.note,
                    row.response_masked):
            blob = json.dumps(col, ensure_ascii=False) if col is not None else ""
            assert name not in blob and idc not in blob and mob not in blob, col
        # 请求原文不落库：digest 非空且不含明文
        assert row.request_digest and idc not in row.request_digest


def test_send_mock_and_export_db_native_zero_plaintext(api):
    name, idc = _rand_name(), _rand_idcard()
    msg = _msg({"opter_name": name,
                "input": {"nested": [{"psn_name": name, "certno": idc}]}})
    c = api["client"]
    hdr = {"Authorization": "Bearer tk-rt04"}
    r1 = c.post("/api/v1/medins-emr/send-mock",
                json={"code": "11101", "message": msg}, headers=hdr)
    assert r1.status_code == 200, r1.text
    assert r1.json()["data"]["response"]["_mock"] is True   # mock 标记保留
    r2 = c.post("/api/v1/medins-emr/export-request",
                json={"code": "11101", "message": msg}, headers=hdr)
    assert r2.status_code == 200
    d = r2.json()["data"]
    # 完整用户输入仅当次响应回传下载
    assert d["message"]["input"]["nested"][0]["certno"] == idc
    from core_db.models import DebugExchange
    with api["sf"]() as db:
        rows = db.query(DebugExchange).filter(
            DebugExchange.mode.in_(("MOCK", "EXPORT"))).all()
        assert rows
        for row in rows:
            for col in (row.request_masked, row.response_masked,
                        row.validation_errors, row.note):
                blob = json.dumps(col, ensure_ascii=False) if col is not None else ""
                assert name not in blob and idc not in blob, (row.mode, col)


def test_record_result_note_masked_and_failure_outcome(api):
    name, idc, mob = _rand_name(), _rand_idcard(), _rand_mobile()
    c = api["client"]
    hdr = {"Authorization": "Bearer tk-rt04"}
    r = c.post("/api/v1/medins-emr/validate",
               json={"code": "11101", "message": _msg({})}, headers=hdr)
    rid = r.json()["data"]["record_id"]
    note = f"手工执行成功 姓名{name} 证件{idc} 手机{mob}"
    r2 = c.post(f"/api/v1/medins-emr/debug-exchanges/{rid}/record-result",
                json={"success": False, "platform_code": "-1",
                      "note": note}, headers=hdr)
    assert r2.status_code == 200
    assert r2.json()["data"]["evidence_level"] == "MANUAL_OBSERVATION"
    from core_db.models import AuditEvent, DebugExchange
    with api["sf"]() as db:
        row = db.get(DebugExchange, rid)
        assert name not in row.note and idc not in row.note \
            and mob not in row.note
        assert row.outcome == "FAIL"          # 失败回填如实反映
        ev = db.query(AuditEvent).filter_by(
            target=f"debug_exchange:{rid}").first()
        assert ev is not None
        audit_blob = json.dumps(ev.new_ref, ensure_ascii=False)
        assert idc not in audit_blob and mob not in audit_blob


def test_validation_errors_value_free_for_dict_and_calendar(api):
    c = api["client"]
    hdr = {"Authorization": "Bearer tk-rt04"}
    # 构造：字典错（opter_type 契约字典）+ 日历错（真实日历校验）
    msg = _msg({"opter_type": "9",
                "input": {"birthday": "2026-02-30"}})
    r = c.post("/api/v1/medins-emr/validate",
               json={"code": "11101", "message": msg}, headers=hdr)
    assert r.status_code == 200
    errs = r.json()["data"]["errors"]
    blob = json.dumps(errs, ensure_ascii=False)
    assert '"9"' not in blob.replace('"code"', "")   # 字典原值不回显
    # 恶意值串构造（长数字/敏感模式）不进错误文案
    idc = _rand_idcard()
    msg2 = _msg({"input": {"psn_id": idc}})
    r2 = c.post("/api/v1/medins-emr/validate",
                json={"code": "11101", "message": msg2}, headers=hdr)
    errs2 = r2.json()["data"]["errors"]
    assert idc not in json.dumps(errs2, ensure_ascii=False)
