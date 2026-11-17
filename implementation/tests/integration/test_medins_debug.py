"""LT-07 集成：msgid/envelope/signer + 联调 API（validate/send-mock/export/
record-result/脱敏不落库/权限）。"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import DebugExchange, Tenant  # noqa: E402
from plugins.report_targets import seed_default_targets  # noqa: E402
from contracts.api import hash_token  # noqa: E402

CST = timezone(timedelta(hours=8))


# ---------------------------------------------------------------- msgid/签名
def test_msgid_rule_30_chars_unique_per_seq():
    from plugins.destinations.medins_gateway import build_msgid
    now = datetime(2026, 11, 11, 8, 30, 0, tzinfo=CST)
    m1 = build_msgid("H123456789012345", now, 1)
    m2 = build_msgid("H123456789012345", now, 2)
    assert len(m1) == 30 and m1.startswith("H123456789012")
    assert "20261111083000" in m1
    assert m1 != m2                           # 同秒不同顺序号唯一


def test_formal_signer_unconfirmed():
    from connector_sdk import CapabilityError
    from plugins.destinations.medins_gateway import (SIGNATURE_UNCONFIRMED,
                                                     NoneSigner,
                                                     require_formal_signer)
    with pytest.raises(CapabilityError) as ei:
        require_formal_signer()
    assert ei.value.code == SIGNATURE_UNCONFIRMED
    assert NoneSigner().headers(b"x", None) == {}   # 仅 Mock


def test_envelope_values_from_config_not_sample():
    from plugins.destinations.medins_gateway import build_envelope
    now = datetime(2026, 11, 11, 8, 30, 0, tzinfo=CST)
    env = build_envelope({"fixmedins_code": "CFGORG12345678",
                          "mdtrtarea_admvs": "370104",
                          "opter": "op01"}, "11101", {"x": 1}, now)
    assert env["fixmedins_code"] == "CFGORG12345678"
    assert env["mdtrtarea_admvs"] == "370104"
    assert env["info"] == "11101"
    assert env["inf_time"] == "2026-11-11 08:30:00"
    assert len(env["msgid"]) == 30
    assert env["input"] == {"x": 1}


# ---------------------------------------------------------------- API
@pytest.fixture()
def api(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/medins.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["medins_emr"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    client = TestClient(create_app(session_factory=sf, engine=engine,
                                   registry=reg))
    with sf() as db:
        t = Tenant(id=new_uuid(), code="mi-h", name="医保")
        db.add(t)
        db.flush()
        tid = t.id
        seed_default_targets(db, tid)
        from core_db.models import AuthSubject
        for role, tok in (("admin", "tk-adm"), ("op", "tk-op"),
                          ("viewer", "tk-vw")):
            roles = {"admin": ["platform_admin"], "op": ["operator"],
                     "viewer": ["viewer"]}[role]
            db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind="user",
                               subject_code=f"u-{role}", roles=roles,
                               token_hash=hash_token(tok), status="ACTIVE",
                               target_scopes=["*"]))
        db.commit()
    yield {"client": client, "sf": sf, "engine": engine, "tid": tid}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _valid_message():
    sample_path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "profiles",
        "medins_emr_sd", "samples", "11101.json")
    if os.path.exists(sample_path):
        return json.load(open(sample_path, encoding="utf-8"))
    return {"info": "11101", "inf_time": "2026-01-01 00:00:00",
            "opter_type": "1", "fixmedins_code": "T" * 14,
            "input": {}}


@pytest.fixture()
def sample_msg():
    msg = _valid_message()
    msg.setdefault("mdtrtarea_admvs", "370104")
    msg.setdefault("infver", "V1.0")
    msg.setdefault("recer_sys_code", "TESTSYS")
    msg.setdefault("opter", "op01")
    msg.setdefault("opter_name", "测试患者")
    msg.pop("_synthetic", None)
    return msg


def test_transactions_listing(api):
    r = api["client"].get("/api/v1/medins-emr/transactions",
                          headers={"Authorization": "Bearer tk-vw"})
    assert r.status_code == 200
    codes = [t["code"] for t in r.json()["data"]]
    assert "11101" in codes and len(codes) >= 19


def test_validate_records_masked(api, sample_msg):
    sample_msg["input"] = {"patn_id": "P1", "certno": "000000199001010000"}
    r = api["client"].post("/api/v1/medins-emr/validate", json={
        "code": "11101", "message": sample_msg},
        headers={"Authorization": "Bearer tk-op"})
    assert r.status_code == 200
    data = r.json()["data"]
    rid = data["record_id"]
    with api["sf"]() as db:
        rec = db.get(DebugExchange, rid)
        blob = json.dumps(rec.request_masked, ensure_ascii=False)
        assert "000000199001010000" not in blob       # sensitive 打码
        assert rec.mode == "VALIDATE_ONLY"


def test_validate_permission_and_unknown_txn(api, sample_msg):
    c = api["client"]
    assert c.post("/api/v1/medins-emr/validate",
                  json={"code": "11101", "message": sample_msg},
                  headers={"Authorization": "Bearer tk-vw"}).status_code == 403
    assert c.post("/api/v1/medins-emr/validate",
                  json={"code": "99999", "message": sample_msg},
                  headers={"Authorization": "Bearer tk-op"}).status_code == 404


def test_send_mock_in_process_and_records(api, sample_msg):
    r = api["client"].post("/api/v1/medins-emr/send-mock", json={
        "code": "11101", "message": sample_msg, "__mock_infcode": 0},
        headers={"Authorization": "Bearer tk-op"})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["mock"] is True and d["response"]["infcode"] == 0
    assert d["response"]["_mock"] is True
    # 失败档
    r2 = api["client"].post("/api/v1/medins-emr/send-mock", json={
        "code": "11101", "message": sample_msg, "__mock_infcode": -1},
        headers={"Authorization": "Bearer tk-op"})
    assert r2.json()["data"]["response"]["infcode"] == -1


def test_send_mock_rejects_invalid(api, sample_msg):
    msg = dict(sample_msg)
    msg["info"] = ""                        # envelope 必填错
    msg["input"] = dict(msg.get("input") or {})
    msg["input"]["patn_id"] = None          # 顶层必填错（若契约必填）
    r = api["client"].post("/api/v1/medins-emr/send-mock", json={
        "code": "11101", "message": msg},
        headers={"Authorization": "Bearer tk-op"})
    assert r.status_code == 422              # 校验不过不发（info 必填）


def test_export_request_no_plaintext_in_db(api, sample_msg):
    sample_msg["input"] = {**sample_msg.get("input", {}),
                           "patn_id": "P1",
                           "certno": "000000199001010000"}
    r = api["client"].post("/api/v1/medins-emr/export-request", json={
        "code": "11101", "message": sample_msg},
        headers={"Authorization": "Bearer tk-op"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert "curl" in d and "<调用地址>" in d["curl"]
    assert any("签名" in h["value"] for h in d["headers"])
    # 明文只在响应（浏览器下载），库中只有打码版
    with api["sf"]() as db:
        rows = db.query(DebugExchange).filter_by(mode="EXPORT").all()
        assert rows
        blob = json.dumps([r.request_masked for r in rows], ensure_ascii=False)
        assert "000000199001010000" not in blob
    # 响应体含完整报文（用户下载），但这是唯一出口
    assert d["message"]["input"]["certno"] == "000000199001010000"


def test_record_result_manual_evidence(api, sample_msg):
    r = api["client"].post("/api/v1/medins-emr/validate", json={
        "code": "11101", "message": sample_msg},
        headers={"Authorization": "Bearer tk-op"})
    rid = r.json()["data"]["record_id"]
    r2 = api["client"].post(
        f"/api/v1/medins-emr/debug-exchanges/{rid}/record-result",
        json={"success": True, "platform_code": "0", "note": "院内手工执行成功"},
        headers={"Authorization": "Bearer tk-op"})
    assert r2.status_code == 200
    assert r2.json()["data"]["evidence_level"] == "MANUAL_OBSERVATION"
    with api["sf"]() as db:
        rec = db.get(DebugExchange, rid)
        assert "[人工回填] 成功" in rec.note and "平台返回码=0" in rec.note


def test_debug_history_listed(api, sample_msg):
    api["client"].post("/api/v1/medins-emr/validate", json={
        "code": "11101", "message": sample_msg},
        headers={"Authorization": "Bearer tk-op"})
    r = api["client"].get("/api/v1/medins-emr/debug-exchanges?code=11101",
                          headers={"Authorization": "Bearer tk-vw"})
    assert r.status_code == 200 and len(r.json()["data"]) >= 1
