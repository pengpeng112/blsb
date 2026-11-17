"""药品追溯第二轮整改测试（FIX2；R2-01—16 反例 → 预期正确行为）。

本文件把 review-2-20261017 的 16 个离线反例逐条转换为正确业务断言：
反例只证明旧缺陷，这里断言修复后的协议/门禁/状态机/分页/对账行为。
全部离线：临时 SQLite、fake DBAPI、本地 TLS 模拟；零外连、零真实数据。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402

from contracts import ApiError, SubmissionState, new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import (ConfigRevision, DeliveryAttempt, Destination,
                            DictionaryRevision, Job, PackageRevision,
                            ReceiptEvent, Submission, Tenant)  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/fix2.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="first-hospital", name="首院")
        db.add(t)
        db.commit()
        tenant_id = t.id
    yield {"sf": sf, "engine": engine, "tenant_id": tenant_id, "reg": reg}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _bill_row(bill_id="B0001", pharmacy="YF01", kind="PATIENT_DISPENSE",
              updated="2026-10-12T08:00:00", revision="1", finalized=True,
              bill_time="2026-10-12T07:55:00", **extra):
    row = {"source_system": "HIS", "source_bill_id": bill_id,
           "source_bill_no": bill_id, "source_revision": revision,
           "business_kind": kind, "pharmacy_code": pharmacy,
           "bill_time": bill_time, "updated_at": updated,
           "finalized": finalized, "is_void": False}
    row.update(extra)
    return row


def _code_row(bill_id="B0001", pharmacy="YF01", kind="PATIENT_DISPENSE",
              record_id="R1", line_id="L1", code="81000000000123",
              revision="1", updated="2026-10-12T08:00:00", deleted=False,
              quantity="1", unit="盒", **extra):
    row = {"source_system": "HIS", "source_bill_id": bill_id,
           "pharmacy_code": pharmacy, "business_kind": kind,
           "source_revision": revision, "source_line_id": line_id,
           "source_code_record_id": record_id, "trace_code": code,
           "updated_at": updated, "is_deleted": deleted,
           "quantity": quantity, "unit": unit}
    row.update(extra)
    return row


def _connector_id(env):
    from core_db.models import ConnectorInstance
    with env["sf"]() as db:
        c = ConnectorInstance(id=new_uuid(), tenant_id=env["tenant_id"],
                              direction="source",
                              plugin_id="sources.db_view.drug_trace",
                              name="his-drugtrace", status="ENABLED")
        db.add(c)
        db.commit()
        return c.id


def _wh_with(rows_bills, rows_codes, namespace="HIS"):
    from sources.db_view.drug_trace import SyntheticDrugTraceWarehouse
    w = SyntheticDrugTraceWarehouse(namespace=namespace)
    for r in rows_bills:
        w.add_bill(r)
    for r in rows_codes:
        w.add_code(r)
    return w


def _dicts(env, extra=()):
    """标准零售字典 + 额外命名空间。"""
    with env["sf"]() as db:
        for ns, items in (
                ("drugtrace.bill_type", [{"code": "323", "target": "323"}]),
                ("drugtrace.ref_ent_id", [{"code": "YF01", "target": "ENT-H"}]),
                # FIX3-01：ref_user_id 字典废止（上传 ref_user_id=ref_ent_id 值）；
                # 查询选择器独立字典（官方 A/AI/AO）
                ("drugtrace.query_bill_type", [{"code": "323", "target": "A"}]),
                *extra):
            db.add(DictionaryRevision(
                id=new_uuid(), tenant_id=env["tenant_id"], namespace=ns,
                version="1", items=items, source="target", approved_by="admin"))
        db.commit()


def _frozen_retail(env, kind="PATIENT_DISPENSE", bill_id="FD"):
    """采集一单 VALID 零售单 → 字典 → mock 目的地 → PREPARED 提交。"""
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBillRevision
    conn = _connector_id(env)
    wh = _wh_with(
        [_bill_row(bill_id, kind=kind, source_bill_kind_raw="323")],
        [_code_row(bill_id, kind=kind, record_id="R1")])
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 12),
                               window_end=datetime(2026, 10, 13))
        rev = db.query(DrugTraceBillRevision).first()
        _dicts(env)
        dest = Destination(
            id=new_uuid(), tenant_id=env["tenant_id"], code="alihealth-mock",
            profile_revision="alihealth_yljg_retail_v1", mode="PUSH",
            capabilities=["upload", "query"], is_mock=True, status="ENABLED")
        db.add(dest)
        db.commit()
        result = service.prepare_submission(
            db, env["tenant_id"], rev.id,
            profile="alihealth_yljg_retail_v1", destination_id=dest.id)
        payload = db.get(PackageRevision,
                         result["package_revision_id"]).payload
        return {"revision": rev.id, "dest": dest, "submission": result,
                "bill_code": payload["bill_code"]}


UPLOAD_RETAIL_KEY = "alibaba_alihealth_drugtrace_top_yljg_uploadretail_response"
QUERY_KEY = ("alibaba_alihealth_drugtrace_top_yljg_query_billstatus_response")


def _query_body(entries, total=None):
    import json
    total = total if total is not None else len(entries)
    return json.dumps({QUERY_KEY: {"result": {"response_success": True,
            "model": {"total_num": total, "result_list": {
                "bill_deal_status_search_do": entries}}}}}).encode()


class FakeQueryClient:
    """平台查询替身：按页返回官方嵌套形状的已解析结果；记录请求参数。"""
    def __init__(self, pages, bill_code="FD"):
        self.pages = pages            # list[list[entry]]
        self.calls = []
        self.bill_code = bill_code

    def query_bill_status(self, biz):
        self.calls.append(dict(biz))
        page_no = int(biz.get("page", "1"))
        entries = self.pages[page_no - 1] if page_no <= len(self.pages) else []
        import json
        total = sum(len(p) for p in self.pages)
        body = json.dumps({QUERY_KEY: {"result": {
            "response_success": True, "model": {"total_num": total,
            "result_list": {"bill_deal_status_search_do": entries}}}}}).encode()
        from destinations.alihealth_drugtrace.protocol import parse_query_response
        return parse_query_response(200, body)


# ================================================================ T01 请求契约
def test_t01_official_request_params_two_profiles():
    """R2-01 修复：两 profile 独立 schema；官方必填齐全；内部字段不透传。"""
    from domains.drug_trace import service as S
    from domains.drug_trace import contracts as C
    base = {"bill_code": "DTRT-X", "bill_type": "323",
            "bill_time": "2026-10-12 07:55:00", "codes": [
                {"trace_code": "81000000000123"}]}
    retail = S.build_biz_params({**base, "profile": C.PROFILE_RETAIL,
                                 "ref_user_id": "USER-H"})
    assert retail["ref_user_id"] == "USER-H"
    assert "ref_ent_id" not in retail and "_doc" not in retail
    assert "supplier_ref" not in retail and "warehouse_code" not in retail
    assert retail["bill_time"] == "2026-10-12 07:55:00"
    inout = S.build_biz_params({
        **base, "profile": C.PROFILE_INOUT, "ref_user_id": "USER-H",
        "physic_type": "101", "from_user_id": "SUP-1", "to_user_id": "USER-H",
        "client_type": "1"})
    required_inout = {"bill_code", "bill_time", "bill_type", "physic_type",
                      "ref_user_id", "from_user_id", "to_user_id",
                      "trace_codes", "client_type"}
    assert required_inout <= set(inout), required_inout - set(inout)
    # 缺必填 → 显式失败（不隐式发空）
    with pytest.raises(ApiError):
        S.build_biz_params({**base, "profile": C.PROFILE_INOUT})
    with pytest.raises(ApiError):     # bill_time 缺失（R2-01）
        S.build_biz_params({**base, "profile": C.PROFILE_RETAIL,
                            "ref_user_id": "U", "bill_time": None})


# ================================================================ T02 响应契约
def test_t02_official_upload_and_query_fixtures():
    """R2-02 修复：response_success 上传信封 + 查询嵌套展开。"""
    from destinations.alihealth_drugtrace import protocol as P
    m = "alibaba.alihealth.drugtrace.top.yljg.uploadretail"
    accepted = {P.method_response_key(m): {"response_success": True,
                                           "model": "SYN-QUEUE"}}
    import json
    result = P.parse_response(m, 200, json.dumps(accepted).encode())
    assert result["outcome"] == "ACCEPTED"
    # 查询官方嵌套形状：result.model.result_list.bill_deal_status_search_do
    parsed = P.parse_query_response(200, _query_body([
        {"bill_code": "SYN-B1", "result_type": "3", "sub_process_flag": "51"}]))
    assert parsed["found"] is True and not parsed["unknown"]
    assert parsed["total_num"] == 1
    assert parsed["bills"]["list"][0]["bill_code"] == "SYN-B1"
    # 错误页/丢响应 → unknown，不猜
    bad = P.parse_query_response(200, b"<html>")
    assert bad["unknown"] is True


def test_t03_partial_success_never_confirmed(env, tmp_path):
    """R2-03 修复：rt=3+spf=52 → PARTIAL（绝不 CONFIRMED）；51 才全成功。"""
    from domains.drug_trace import service as S
    entry = {"bill_code": "FD", "result_type": "3"}
    partial = S._classify_query(
        {"found": True, "unknown": False,
         "bills": {"list": [dict(entry, sub_process_flag="52")]}}, "FD")
    assert partial["state"] == "PARTIAL" and partial["sub_process_flag"] == "52"
    full = S._classify_query(
        {"found": True, "unknown": False,
         "bills": {"list": [dict(entry, sub_process_flag="51")]}}, "FD")
    assert full["state"] == "CONFIRMED"
    processing = S._classify_query(
        {"found": True, "unknown": False,
         "bills": {"list": [{"bill_code": "FD", "result_type": "1",
                             "sub_process_flag": ""}]}}, "FD")
    assert processing["state"] == "PROCESSING"
    unknown_enum = S._classify_query(
        {"found": True, "unknown": False,
         "bills": {"list": [{"bill_code": "FD", "result_type": "9",
                             "sub_process_flag": "99"}]}}, "FD")
    assert unknown_enum["state"] == "MANUAL_REVIEW"   # 未知枚举不猜


# ================================================================ T04 查询分页
def test_t04_query_full_pagination_and_request_fields(env, tmp_path):
    """R2-02 修复：查询请求带 begin_date/end_date/bill_type；读全部分页。"""
    from domains.drug_trace import service as S
    prep = _frozen_retail(env)
    with env["sf"]() as db:
        sub = db.get(Submission, prep["submission"]["submission_id"])
        bc = prep["bill_code"]
        client = FakeQueryClient([
            [{"bill_code": "OTHER", "result_type": "3", "sub_process_flag": "51"}],
            [{"bill_code": bc, "result_type": "3", "sub_process_flag": "51"}],
        ])
        result = S.perform_query(db, env["tenant_id"], sub, client=client)
        # 第 2 页才出现目标单：必须读完所有页才判定
        assert result["classification"]["state"] == "CONFIRMED"
        assert result["pages_read"] >= 2
        assert sub.state == SubmissionState.CONFIRMED
        req = client.calls[0]
        assert req.get("begin_date") and req.get("end_date")   # 创建日窗口
        # FIX3-01/R3-01：查询用独立选择器（官方 A/AI/AO），不再复用上传枚举 323
        assert req.get("bill_type") == "A"
        assert req.get("ref_ent_id") == "ENT-H"
        assert req.get("page") == "1"


# ================================================================ T05 证据绑定
def test_t05_evidence_exact_binding(env, tmp_path):
    """R2-05 修复：destination 精确匹配 + config_release 绑定 + 代码必需项。"""
    from domains.drug_trace import service as S
    from domains.drug_trace import contracts as C
    prep = _frozen_retail(env)
    send_keys = [i["key"] for i in S.REQUIRED_EVIDENCE["send"]]
    assert {"EXT-05", "EXT-10", "EXT-12"} <= set(send_keys)  # 发送三重门禁
    with env["sf"]() as db:
        dest2 = Destination(
            id=new_uuid(), tenant_id=env["tenant_id"], code="other-dest",
            profile_revision="alihealth_yljg_retail_v1", mode="PUSH",
            capabilities=["upload"], is_mock=True, status="ENABLED")
        db.add(dest2)
        db.commit()
        items = [{"key": k} for k in send_keys]
        ev = S.approve_evidence(
            db, env["tenant_id"], profile=C.PROFILE_RETAIL, capability="send",
            destination_id=prep["dest"].id, provided_items=items,
            approved_by="t", config_release_id="OLD",
            scope={"window_begin": "2026-10-01T00:00:00",
                   "window_end": "2026-11-01T00:00:00",
                   "pharmacies": ["YF01"], "max_bills": 10})  # FIX3-04 完整scope
        db.commit()
        # 同证据用于另一 destination / 新 config_release → 不 APPROVED（不再 first 撞库）
        st_other = S.evidence_state(
            db, env["tenant_id"], profile=C.PROFILE_RETAIL, capability="send",
            destination_id=dest2.id)
        assert st_other["status"] == "MISSING"
        st_newcfg = S.evidence_state(
            db, env["tenant_id"], profile=C.PROFILE_RETAIL, capability="send",
            destination_id=prep["dest"].id, config_release_id="NEW")
        assert st_newcfg["status"] == "MISMATCH_CONFIG"
        # 用户缩减 required_items 无效：代码必需项仍全部要求
        ev.required_items = [{"key": "EXT-03"}]
        db.commit()
        ev.provided_items = [{"key": "EXT-03"}]
        db.commit()
        st_shrunk = S.evidence_state(
            db, env["tenant_id"], profile=C.PROFILE_RETAIL, capability="send",
            destination_id=prep["dest"].id)
        missing = {m["key"] for m in st_shrunk["missing"]}
        assert "EXT-05" in missing and "EXT-12" in missing
        # 撤销 → REVOKED
        S.revoke_evidence(db, env["tenant_id"], ev.id)
        db.commit()
        assert S.evidence_state(
            db, env["tenant_id"], profile=C.PROFILE_RETAIL, capability="send",
            destination_id=prep["dest"].id)["status"] == "REVOKED"


def test_t05b_send_scope_window_blocks_out_of_range(env, tmp_path):
    """EXT-12 scope：bill_time 超出批准窗口 → 拒绝发送。"""
    from domains.drug_trace import service as S
    from domains.drug_trace import contracts as C
    prep = _frozen_retail(env)
    send_keys = [i["key"] for i in S.REQUIRED_EVIDENCE["send"]]
    with env["sf"]() as db:
        dest = db.get(Destination, prep["dest"].id)
        dest.is_mock = False
        db.commit()
        sub = db.get(Submission, prep["submission"]["submission_id"])
        S.approve_evidence(
            db, env["tenant_id"], profile=C.PROFILE_RETAIL, capability="send",
            destination_id=dest.id,
            provided_items=[{"key": k} for k in send_keys], approved_by="t",
            scope={"window_begin": "2026-12-01T00:00:00",
                   "window_end": "2027-01-01T00:00:00",
                   "pharmacies": ["YF01"], "max_bills": 10})  # 不含 2026-10-12
        db.commit()
        with pytest.raises(ApiError) as ei:
            S.perform_send(db, env["tenant_id"], sub)
        assert ei.value.code == "PROFILE_UNAPPROVED"
        assert "EXT-12" in ei.value.message


# ================================================================ T06 Mock 隔离
def test_t06_mock_secret_rejected_on_real_transport():
    """R2-04 修复：__mock__ 密钥 + RealTransport → 签名前拒绝，零网络。"""
    from connector_sdk import CapabilityError
    from destinations.alihealth_drugtrace.client import AlihealthDrugTraceClient
    from destinations.alihealth_drugtrace.transport import RealTransport, TransportResult
    transport = RealTransport("https://invalid.example")
    calls = []

    def fake_post(body, ctype):
        calls.append(body)
        return TransportResult(http_status=200, body=b"{}")
    transport.post = fake_post
    client = AlihealthDrugTraceClient(
        transport, app_key="synthetic", app_secret_ref="mock:offline",
        resolve_secret=lambda ref: "__mock__offline")
    with pytest.raises(CapabilityError):
        client.upload_retail({"bill_code": "SYN-B1"})
    assert not calls


def test_t06b_real_destination_rejects_mock_secret_ref(env, tmp_path):
    """真实目标的 mock: SecretRef 在 build_client 即拒绝（非签名时才发现）。"""
    from domains.drug_trace import service as S
    prep = _frozen_retail(env)
    with env["sf"]() as db:
        dest = db.get(Destination, prep["dest"].id)
        dest.is_mock = False
        cfg = ConfigRevision(
            id=new_uuid(), tenant_id=env["tenant_id"], config_type="connector",
            owner_id=dest.id, revision=1,
            content_json={"dmz_base_url": "https://dmz.invalid",
                          "app_key": "k", "app_secret_ref": "mock:offline"},
            digest="d", state="PUBLISHED")
        db.add(cfg)
        db.flush()
        dest.config_revision = cfg.id
        db.commit()
        with pytest.raises(ApiError) as ei:
            S.build_client(db, env["tenant_id"], dest)
        assert "non-mock" in ei.value.message


# ================================================================ T08 配置闭包
def test_t08_config_closure_blocks_drift(env, tmp_path):
    """R2-05/15 修复：prepare 固定 config_release；漂移后旧提交拒绝发送。"""
    from domains.drug_trace import service as S
    from domains.drug_trace import contracts as C
    prep = _frozen_retail(env)
    send_keys = [i["key"] for i in S.REQUIRED_EVIDENCE["send"]]
    with env["sf"]() as db:
        dest = db.get(Destination, prep["dest"].id)
        dest.is_mock = False
        cfg1 = ConfigRevision(
            id=new_uuid(), tenant_id=env["tenant_id"], config_type="connector",
            owner_id=dest.id, revision=1,
            content_json={"dmz_base_url": "https://dmz1.invalid",
                          "app_key": "k", "app_secret_ref": "env:X",
                          "dmz_allow_hosts": ["dmz1.invalid"]},
            digest="d1", state="PUBLISHED")
        db.add(cfg1)
        db.flush()
        dest.config_revision = cfg1.id
        db.commit()
        # cfg1 之后 prepare 的新提交：闭包固定为 cfg1
        conn2 = _connector_id(env)
        wh2 = _wh_with([_bill_row("CL2", source_bill_kind_raw="323")],
                       [_code_row("CL2", record_id="R1")])
        S.run_collection(db, env["tenant_id"], conn2, wh2,
                         window_begin=datetime(2026, 10, 12),
                         window_end=datetime(2026, 10, 13))
        from domains.drug_trace.models import DrugTraceBill
        bill2 = db.query(DrugTraceBill).filter_by(
            source_bill_id="CL2").one()
        result2 = S.prepare_submission(
            db, env["tenant_id"], bill2.current_revision_id,
            profile=C.PROFILE_RETAIL, destination_id=dest.id)
        sub2 = db.get(Submission, result2["submission_id"])
        pr2 = db.get(PackageRevision, sub2.package_revision_id)
        assert pr2.provenance["config_release_id"] == cfg1.id   # 闭包已固定
        S.approve_evidence(
            db, env["tenant_id"], profile=C.PROFILE_RETAIL, capability="send",
            destination_id=dest.id,
            provided_items=[{"key": k} for k in send_keys], approved_by="t",
            config_release_id=cfg1.id,        # FIX3-04：精确配置绑定
            scope={"window_begin": "2026-10-01T00:00:00",
                   "window_end": "2026-11-01T00:00:00",
                   "pharmacies": ["YF01"], "max_bills": 10})
        db.commit()
        import os as _os
        _os.environ["X"] = "real-secret"
        try:
            # 配置漂移：destination 换到 cfg2 → 旧提交（闭包 cfg1）必须拒绝
            cfg2 = ConfigRevision(
                id=new_uuid(), tenant_id=env["tenant_id"],
                config_type="connector", owner_id=dest.id, revision=2,
                content_json={"dmz_base_url": "https://dmz2.invalid",
                              "app_key": "k", "app_secret_ref": "env:X"},
                digest="d2", state="PUBLISHED")
            db.add(cfg2)
            db.flush()
            dest.config_revision = cfg2.id
            db.commit()
            with pytest.raises(ApiError) as ei:
                S.perform_send(db, env["tenant_id"], sub2)
            assert ei.value.code == "VERSION_CONFLICT"
            assert "re-prepare" in ei.value.message
        finally:
            _os.environ.pop("X", None)


def test_t08b_repeat_prepare_returns_real_binding(env, tmp_path):
    """R2-15 修复：重复 prepare 返回旧 Submission 的真实 PackageRevision。"""
    from domains.drug_trace import service as S
    prep = _frozen_retail(env)
    with env["sf"]() as db:
        r2 = S.prepare_submission(
            db, env["tenant_id"], prep["revision"],
            profile="alihealth_yljg_retail_v1",
            destination_id=prep["dest"].id)
        assert r2["created"] is False
        assert r2["submission_id"] == prep["submission"]["submission_id"]
        assert r2["package_revision_id"] == \
            prep["submission"]["package_revision_id"]   # 不新建冻结包
        # profile 与 destination 不完全匹配 → 拒绝（不再 first 撞两个之一）
        dest_inout = Destination(
            id=new_uuid(), tenant_id=env["tenant_id"], code="inout-dest",
            profile_revision="alihealth_yljg_inout_v1", mode="PUSH",
            capabilities=["upload"], is_mock=True, status="ENABLED")
        db.add(dest_inout)
        db.commit()
        with pytest.raises(ApiError) as ei:
            S.prepare_submission(db, env["tenant_id"], prep["revision"],
                                 profile="alihealth_yljg_retail_v1",
                                 destination_id=dest_inout.id)
        assert ei.value.code == "CAPABILITY_UNSUPPORTED"


# ================================================================ T09 Oracle 规范化
def test_t09_oracle_fake_dbapi_normalized():
    """R2-07 修复：大写列名/datetime/Decimal/'0' 全部真实导入成功。"""
    from datetime import datetime as DT
    from sources.db_view.drug_trace import OracleDrugTraceWarehouse

    class Cursor:
        description = [("SOURCE_BILL_ID",), ("BILL_TIME",), ("FINALIZED",),
                       ("IS_VOID",)]
        def execute(self, *a): pass
        def fetchall(self):
            return [("SYN-B1", DT(2026, 10, 12, 8), "1", "0")]
        def close(self): pass

    class Conn:
        def cursor(self): return Cursor()
        def close(self): pass

    row = OracleDrugTraceWarehouse(connection_factory=Conn).fetch_all(
        "drugtrace.bill_header",
        {"source_system": "HIS", "pharmacy_code": "YF1",
         "business_kind": "PATIENT_DISPENSE", "source_bill_id": "SYN-B1"}, 2)[0]
    # 列名小写（业务读小写键）；datetime → ISO 字符串（json 可存）
    assert "source_bill_id" in row
    assert isinstance(row["bill_time"], str)
    # digest 不再 TypeError；bool 归一：'0' → False（不再 bool('0')=True）
    from domains.drug_trace import ingest, quality
    ingest.content_digest({"bill_time": row["bill_time"]}, [])
    assert quality.as_bool(row["is_void"], "is_void") is False
    assert quality.as_bool(row["finalized"], "finalized") is True
    with pytest.raises(ApiError):
        quality.as_bool("2", "finalized")      # 未知值显式报错


def test_t09b_oracle_uppercase_columns_import(env):
    """R2-07 修复：fake DBAPI 大写+原生类型整单导入（不只 render_sql）。"""
    from domains.drug_trace import ingest
    from sources.db_view.drug_trace import OracleDrugTraceWarehouse

    class Wh:
        """fake 驱动形态：列名大写、datetime 原生、数量 Decimal。"""
        dialect_name = "oracle"
        def dialect(self): return "oracle"
        def fetch_all(self, template_id, params, limit, **kw):
            if template_id == "drugtrace.bill_header":
                return [{"SOURCE_SYSTEM": "HIS", "SOURCE_BILL_ID": "UP1",
                         "SOURCE_BILL_NO": "UP1", "SOURCE_REVISION": "1",
                         "BUSINESS_KIND": "PATIENT_DISPENSE",
                         "PHARMACY_CODE": "YF01",
                         "BILL_TIME": datetime(2026, 10, 12, 7, 55),
                         "UPDATED_AT": datetime(2026, 10, 12, 8),
                         "FINALIZED": "1", "IS_VOID": "0",
                         "SOURCE_BILL_KIND_RAW": "323",
                         "WAREHOUSE_CODE": "", "SUPPLIER_CODE": "",
                         "SOURCE_EVENT_SEQ": "", "EXPECTED_CODE_COUNT": 1,
                         "PATIENT_REF": "", "ENCOUNTER_REF": ""}]
            if template_id == "drugtrace.bill_codes":
                return [{"SOURCE_SYSTEM": "HIS", "SOURCE_BILL_ID": "UP1",
                         "PHARMACY_CODE": "YF01",
                         "BUSINESS_KIND": "PATIENT_DISPENSE",
                         "SOURCE_REVISION": "1", "SOURCE_LINE_ID": "L1",
                         "SOURCE_CODE_RECORD_ID": "R1",
                         "TRACE_CODE": "81000000000123",
                         "QUANTITY": Decimal("1"), "UNIT": "盒",
                         "CODE_LEVEL": "", "PARENT_CODE": "",
                         "UPDATED_AT": datetime(2026, 10, 12, 8),
                         "IS_DELETED": "0", "F_TRACE_CODE": "",
                         "S_TRACE_CODE": "", "T_TRACE_CODE": "",
                         "BILL_TIME": datetime(2026, 10, 12, 7, 55)}]
            return []
    # 规范化后与合成仓储同一套小写键口径 → 直接走整单导入
    class Normalizing(Wh):
        def fetch_all(self, template_id, params, limit, **kw):
            from sources.db_view.drug_trace import normalize_row
            return [normalize_row(r) for r in super().fetch_all(
                template_id, params, limit, **kw)]
    conn = _connector_id(env)
    with env["sf"]() as db:
        from domains.drug_trace.models import DrugTraceBill
        header = Normalizing().fetch_all(
            "drugtrace.bill_header", {"source_system": "HIS"}, 2)[0]
        lines = Normalizing().fetch_all("drugtrace.bill_codes", {}, 200)
        result = ingest.import_bill(db, env["tenant_id"], conn, header, lines,
                                    profile="alihealth_yljg_retail_v1")
        assert result["created"] is True
        bill = db.query(DrugTraceBill).first()
        assert bill.finalized is True and bill.is_void is False


# ================================================================ T10 分页
def test_t10_same_timestamp_multipage_and_cursor_guard(env):
    """R2-08 修复：同刻>两页正常翻页；游标与 SQL 拼接键一致（含 source_system）。"""
    from domains.drug_trace import service, ingest
    conn = _connector_id(env)
    rows = [_bill_row(f"S{i}", updated="2026-10-12T08:00:00")
            for i in range(5)]        # 同刻 5 行 → page_limit=2 需 3 页
    codes = [_code_row(f"S{i}", record_id="R1") for i in range(5)]
    wh = _wh_with(rows, codes)
    with env["sf"]() as db:
        summary = ingest.collect_window(
            db, env["tenant_id"], conn, wh,
            window_begin=datetime(2026, 10, 12),
            window_end=datetime(2026, 10, 13), run=_run(env, conn),
            page_limit=2)
        assert summary["bills_seen"] == 5 and summary["revisions_created"] == 5
    # 游标口径（FIX3-02/R3-02）：逐字段 keyset——SQL 谓词与 ORDER BY 同构，
    # 不再用竖线拼接字符串比较（YF1/YF10 拼接序≠元组序会跳页）
    tail = {"source_system": "HIS", "pharmacy_code": "YF01",
            "business_kind": "PATIENT_DISPENSE", "source_bill_id": "B1"}
    assert ingest.key_parts(tail) == ["HIS", "YF01", "PATIENT_DISPENSE", "B1"]
    from sources.db_view.drug_trace import render_sql
    sql = render_sql("drugtrace.poll_bills", "oracle", limit=10)
    assert "source_system > :since_ss" in sql        # 等值前缀逐字段比较
    assert "source_bill_id > :since_sb" in sql
    assert "|| '|'" not in sql                       # 拼接比较已废除
    assert "ORDER BY updated_at, source_system, pharmacy_code, business_kind,"            " source_bill_id" in sql                  # 谓词与排序同列同序


def _run(env, conn):
    from domains.drug_trace.models import DrugTraceCollectionRun
    return DrugTraceCollectionRun(
        id=new_uuid(), tenant_id=env["tenant_id"], connector_id=conn,
        stream="drugtrace.bills", state="RUNNING",
        window_begin=datetime(2026, 10, 12), window_end=datetime(2026, 10, 13))


def test_t10b_non_advancing_cursor_aborts(env):
    """R2-08 修复：源返回重复满页（游标不推进）→ 显式报错不死循环。"""
    from domains.drug_trace import ingest
    conn = _connector_id(env)
    same_page = [_bill_row("X1", updated="2026-10-12T08:00:00"),
                 _bill_row("X2", updated="2026-10-12T08:00:00")]

    class StuckWh:
        def fetch_all(self, template_id, params, limit, **kw):
            if template_id == "drugtrace.poll_bills":
                return [dict(r) for r in same_page]    # 永远同一满页
            if template_id == "drugtrace.bill_header":
                return same_page
            return []
    with env["sf"]() as db:
        with pytest.raises(ApiError) as ei:
            ingest.collect_window(
                db, env["tenant_id"], conn, StuckWh(),
                window_begin=datetime(2026, 10, 12),
                window_end=datetime(2026, 10, 13), run=_run(env, conn),
                page_limit=2)
        assert ei.value.code == "SOURCE_LOOP_SUSPECTED"


def test_t10c_cross_source_same_display_no_merged(env):
    """R2-08 修复：不同 source_bill_id 同展示单号不合并（键=完整来源主键）。"""
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBill
    conn = _connector_id(env)
    wh = _wh_with(
        [_bill_row("A1", source_bill_no="SAME-NO"),
         _bill_row("A2", source_bill_no="SAME-NO",
                   updated="2026-10-12T08:01:00")],
        [_code_row("A1", record_id="R1"), _code_row("A2", record_id="R1")])
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 12),
                               window_end=datetime(2026, 10, 13))
        assert db.query(DrugTraceBill).count() == 2


# ================================================================ T11 重取账本
def test_t11_retry_ledger_persists_and_refetches(env):
    """R2-08 修复：持续变更单登记持久账本；下一轮先重取（不靠短回看）。"""
    from domains.drug_trace import ingest, service
    from domains.drug_trace.models import DrugTraceRetryBill
    conn = _connector_id(env)
    wh = _wh_with([_bill_row("RV")], [_code_row("RV", record_id="R1")])
    calls = {"n": 0}

    class ChangingWh:
        """主单复核时 source_revision 持续变化 → VERSION_CONFLICT。"""
        def fetch_all(self, template_id, params, limit, **kw):
            if template_id == "drugtrace.bill_header":
                calls["n"] += 1
                row = dict(wh.bills[0])
                row["source_revision"] = f"rev-{calls['n']}"   # 每次都变
                return [row]
            return wh.fetch_all(template_id, params, limit, **kw)
    with env["sf"]() as db:
        summary = service.run_collection(
            db, env["tenant_id"], conn, ChangingWh(),
            window_begin=datetime(2026, 10, 12), window_end=datetime(2026, 10, 13))
        assert summary["skipped_version_changing"] == 1
        rows = db.query(DrugTraceRetryBill).all()
        assert len(rows) == 1 and rows[0].source_bill_id == "RV"
        assert rows[0].attempts >= 1
        # 下一轮：源已稳定 → 账本重取成功出账（窗口不含该单也重取）
        r2 = service.run_collection(
            db, env["tenant_id"], conn, wh,
            window_begin=datetime(2026, 10, 20),
            window_end=datetime(2026, 10, 21))
        assert r2["retried_ok"] == 1
        assert db.query(DrugTraceRetryBill).count() == 0
        from domains.drug_trace.models import DrugTraceBill
        assert db.query(DrugTraceBill).filter_by(
            source_bill_id="RV").count() == 1


# ================================================================ T13 停发
def test_t13_paused_run_worker_skips(env, tmp_path):
    """R2-09 修复：PAUSED 的 run 不被 worker 强行 RUNNING/DONE。"""
    from domains.drug_trace import plugin, service
    from domains.drug_trace.models import DrugTraceCollectionRun
    prep_conn = _connector_id(env)
    run = DrugTraceCollectionRun(
        id=new_uuid(), tenant_id=env["tenant_id"], connector_id=prep_conn,
        stream="drugtrace.bills", profile="alihealth_yljg_retail_v1",
        state="PAUSED", window_begin=datetime(2026, 10, 12),
        window_end=datetime(2026, 10, 13))
    with env["sf"]() as db:
        db.add(run)
        db.commit()
        job = SimpleNamespace(tenant_id=env["tenant_id"], payload={
            "collection_run_id": run.id, "connector_id": prep_conn,
            "connector_config": {"mode": "synthetic"},
            "profile": "alihealth_yljg_retail_v1",
            "window_begin": "2026-10-12T00:00:00",
            "window_end": "2026-10-13T00:00:00"})
        with patch.object(service, "make_warehouse",
                          return_value=_wh_with([], [])):
            result = plugin._j_collect(db, job)
        assert "PAUSED" in str(result.get("skipped", ""))
        assert run.state == "PAUSED"          # 不再被改成 DONE


def test_t13b_oracle_collect_revoked_evidence_blocks(env):
    """R2-09 修复：worker 执行期证据撤销 → 已排队 oracle 采集任务拒绝。"""
    from connector_sdk import CapabilityError
    from domains.drug_trace import plugin, service
    from domains.drug_trace import contracts as C
    conn = _connector_id(env)
    with env["sf"]() as db:
        run = _run(env, conn)
        run.state = "PENDING"
        db.add(run)
        db.commit()
        job = SimpleNamespace(tenant_id=env["tenant_id"], payload={
            "collection_run_id": run.id, "connector_id": conn,
            "connector_config": {"mode": "oracle"},
            "profile": C.PROFILE_RETAIL,
            "window_begin": "2026-10-12T00:00:00",
            "window_end": "2026-10-13T00:00:00"})
        with pytest.raises(ApiError) as ei:
            plugin._j_collect(db, job)      # 无 collect 证据 → fail-closed
        assert ei.value.code == "PROFILE_UNAPPROVED"


def test_t13c_pause_midrun_safe_point(env):
    """R2-09 修复：采集中途暂停 → 页边界安全点停止、断点保留、run 保持 PAUSED。"""
    from domains.drug_trace import ingest, service
    from domains.drug_trace.models import (DrugTraceCollectionRun,
                                           DrugTraceRetryBill)  # noqa: F401
    from core_db.models import SourceCheckpoint
    conn = _connector_id(env)
    wh = _wh_with(
        [_bill_row(f"P{i}", updated="2026-10-12T08:00:00") for i in range(4)],
        [_code_row(f"P{i}", record_id="R1") for i in range(4)])
    with env["sf"]() as db:
        run = DrugTraceCollectionRun(
            id=new_uuid(), tenant_id=env["tenant_id"], connector_id=conn,
            stream="drugtrace.bills", state="RUNNING",
            window_begin=datetime(2026, 10, 12),
            window_end=datetime(2026, 10, 13))
        db.add(run)
        db.commit()
        calls = {"n": 0}

        def gate():
            # FIX3-02：gate 语义——独立会话暂停（第 2 次调用=第一页已提交后），
            # 并以独立只读会话回读状态（本会话 expire_on_commit=False 缓存不可信）
            calls["n"] += 1
            if calls["n"] >= 2:
                with env["sf"]() as other:
                    other.get(DrugTraceCollectionRun, run.id).state = "PAUSED"
                    other.commit()
            with env["sf"]() as other:
                cur = other.get(DrugTraceCollectionRun, run.id)
                st = cur.state if cur is not None else "MISSING"
            if st == "RUNNING":
                return None
            return "paused" if st == "PAUSED" else "cancelled"
        summary = ingest.collect_window(
            db, env["tenant_id"], conn, wh,
            window_begin=datetime(2026, 10, 12),
            window_end=datetime(2026, 10, 13), run=run,
            page_limit=2, gate=gate)
        assert summary.get("stopped") == "paused"
        assert summary["bills_seen"] == 2   # 仅第一页（2 单）即停
        # 断点保留：checkpoint 与第一页同事务提交（未推进到窗口末）
        cp = db.query(SourceCheckpoint).filter_by(
            tenant_id=env["tenant_id"], connector_id=conn,
            stream="drugtrace.bills").one()
        assert cp.cursor_json["completed_window"] is False
        # 逐字段键停在第一页尾（FIX3-02：since_key 为 4 元组，非拼接字符串）
        assert cp.cursor_json["since_key"] == ["HIS", "YF01",
                                               "PATIENT_DISPENSE", "P1"]
        # run_collection 的映射：stopped=paused → run 保持 PAUSED 不置 DONE
        run2 = DrugTraceCollectionRun(
            id=new_uuid(), tenant_id=env["tenant_id"], connector_id=conn,
            stream="drugtrace.bills", state="RUNNING",
            window_begin=datetime(2026, 10, 20),
            window_end=datetime(2026, 10, 21))
        db.add(run2)
        db.commit()
        pages2 = {"n": 0}

        def sc2():
            db.commit()
            cur = db.get(DrugTraceCollectionRun, run2.id)
            pages2["n"] += 1
            if pages2["n"] >= 2:
                cur.state = "PAUSED"
                db.commit()
            return cur.state == "RUNNING"
        with patch.object(ingest, "collect_window",
                          side_effect=lambda *a, **kw: {
                              "bills_seen": 1, "stopped": "paused"}):
            out = service.run_collection(
                db, env["tenant_id"], conn, wh,
                window_begin=datetime(2026, 10, 20),
                window_end=datetime(2026, 10, 21), run_id=run2.id)
        assert out.get("stopped") == "paused"
        db.refresh(run2)
        assert run2.state == "PAUSED"


def job_tenant(env):
    return env["tenant_id"]


# ================================================================ T14 禁重发
def test_t14_unknown_sending_confirmed_no_resend(env, tmp_path):
    """R2-06 修复：UNKNOWN/SENDING/CONFIRMED 多入口禁止重发（service+worker）。"""
    from domains.drug_trace import plugin, service
    prep = _frozen_retail(env)
    calls = []
    orig = service.build_client

    def counting_factory(db_, tid, dest_, **kw):
        calls.append(1)
        return orig(db_, tid, dest_, **kw)
    with env["sf"]() as db:
        sub = db.get(Submission, prep["submission"]["submission_id"])
        for state in (SubmissionState.UNKNOWN, SubmissionState.SENDING,
                      SubmissionState.CONFIRMED, SubmissionState.ACCEPTED):
            sub.state = state
            db.commit()
            with pytest.raises(ApiError):
                service.perform_send(db, env["tenant_id"], sub)
            job = SimpleNamespace(tenant_id=env["tenant_id"],
                                  payload={"submission_id": sub.id})
            res = plugin._j_send(db, job)
            if state in (SubmissionState.UNKNOWN, SubmissionState.SENDING):
                assert res.get("action") == "query_enqueued"   # 只查询
            else:
                assert str(res.get("skipped", "")).startswith("state=")
        service.build_client = counting_factory
        try:
            for state in (SubmissionState.UNKNOWN, SubmissionState.SENDING,
                          SubmissionState.CONFIRMED, SubmissionState.ACCEPTED):
                sub.state = state
                db.commit()
                job = SimpleNamespace(tenant_id=env["tenant_id"],
                                      payload={"submission_id": sub.id})
                plugin._j_send(db, job)
        finally:
            service.build_client = orig
        assert not calls, "任何非 PREPARED 状态都不得构造发送 client"


# ================================================================ T17 轮询代次
def test_t17_query_generations_and_receipt_history(env, tmp_path):
    """R2-06 修复：查询代次推进；已完成 job 不吞手工重查；回执保留变化。"""
    from domains.drug_trace import service as S
    from workflow_core.jobs import enqueue_job
    from contracts import JobState
    prep = _frozen_retail(env)
    with env["sf"]() as db:
        sub = db.get(Submission, prep["submission"]["submission_id"])
        # 第一轮查询（处理中）
        bc = prep["bill_code"]
        c1 = FakeQueryClient([[{"bill_code": bc, "result_type": "1",
                                "sub_process_flag": ""}]])
        S.perform_query(db, env["tenant_id"], sub, client=c1)
        assert sub.state == SubmissionState.SENDING      # 在途可观测
        # 处理中 → 已排下一代
        nexts = db.query(Job).filter(
            Job.tenant_id == env["tenant_id"],
            Job.job_type == "drug_trace.query_status",
            Job.dedup_key.like(f"q:{sub.id}:%")).all()
        assert len(nexts) == 1        # PROCESSING 下一代（带退避）
        # 第一代 job 标 SUCCEEDED 后，同 payload 不再被吞（新代次=新任务）
        first = min(nexts, key=lambda j: j.dedup_key)
        first.state = JobState.SUCCEEDED
        db.commit()
        manual = S._enqueue_query_job(db, env["tenant_id"], sub.id)
        assert manual.id not in {j.id for j in nexts}
        assert manual.payload["generation"] >= 2
        # 第二轮查询（终态确认）
        c2 = FakeQueryClient([[{"bill_code": bc, "result_type": "3",
                                "sub_process_flag": "51"}]])
        S.perform_query(db, env["tenant_id"], sub, client=c2)
        assert sub.state == SubmissionState.CONFIRMED
        # 回执保留每次状态变化（≥2 条：PROCESSING→SENDING 期 + CONFIRMED）
        receipts = db.query(ReceiptEvent).filter_by(
            submission_ref=sub.id).order_by(ReceiptEvent.received_at).all()
        states = [r.parsed_result.get("state") for r in receipts]
        assert len(receipts) >= 2, states
        assert states[-1] == "CONFIRMED"
        assert any(s != "CONFIRMED" for s in states[:-1])


# ================================================================ T18 对账
def test_t18_reconciliation_requires_remote_evidence(env, tmp_path):
    """R2-10 修复：本地 CONFIRMED 但平台无单/部分成功 → 不 matched。"""
    from domains.drug_trace import service as S
    from domains.drug_trace import contracts as C
    prep = _frozen_retail(env)
    recon_keys = [i["key"] for i in S.REQUIRED_EVIDENCE["reconcile"]]
    with env["sf"]() as db:
        dest = db.get(Destination, prep["dest"].id)
        S.approve_evidence(
            db, env["tenant_id"], profile=C.PROFILE_RETAIL,
            capability="reconcile", destination_id=dest.id,
            provided_items=[{"key": k} for k in recon_keys], approved_by="t")
        db.commit()
        sub = db.get(Submission, prep["submission"]["submission_id"])
        sub.state = SubmissionState.CONFIRMED
        db.commit()
        # 平台查无此单 → missing_remote，matched=0
        notfound = FakeQueryClient([[{"bill_code": "OTHER",
                                      "result_type": "3",
                                      "sub_process_flag": "51"}]])
        r1 = S.run_reconciliation(
            db, env["tenant_id"], destination_id=dest.id,
            profile=C.PROFILE_RETAIL, client=notfound,
            window_begin=datetime(2026, 10, 12),
            window_end=datetime(2026, 10, 13))
        assert r1["summary"]["matched"] == 0
        assert r1["summary"]["missing_remote"] == 1
        # 平台部分成功 → state_mismatch，仍不 matched
        partial = FakeQueryClient([[{"bill_code": prep["bill_code"],
                                     "result_type": "3",
                                     "sub_process_flag": "52"}]])
        r2 = S.run_reconciliation(
            db, env["tenant_id"], destination_id=dest.id,
            profile=C.PROFILE_RETAIL, client=partial,
            window_begin=datetime(2026, 10, 12),
            window_end=datetime(2026, 10, 13))
        assert r2["summary"]["matched"] == 0
        assert r2["summary"]["state_mismatch"] == 1
        # 平台确认全成功 + 本地 CONFIRMED → matched
        full = FakeQueryClient([[{"bill_code": prep["bill_code"],
                                  "result_type": "3",
                                  "sub_process_flag": "51"}]])
        r3 = S.run_reconciliation(
            db, env["tenant_id"], destination_id=dest.id,
            profile=C.PROFILE_RETAIL, client=full,
            window_begin=datetime(2026, 10, 12),
            window_end=datetime(2026, 10, 13))
        assert r3["summary"]["matched"] == 1
        assert r3["code_level_verified"] is False    # 码级能力未核验


def test_t18b_reconciliation_platform_failure_unverified(env, tmp_path):
    """R2-10 修复：平台通道失败 → UNVERIFIED（绝不 matched）。"""
    from domains.drug_trace import service as S
    from domains.drug_trace import contracts as C
    prep = _frozen_retail(env)
    recon_keys = [i["key"] for i in S.REQUIRED_EVIDENCE["reconcile"]]
    with env["sf"]() as db:
        dest = db.get(Destination, prep["dest"].id)
        S.approve_evidence(
            db, env["tenant_id"], profile=C.PROFILE_RETAIL,
            capability="reconcile", destination_id=dest.id,
            provided_items=[{"key": k} for k in recon_keys], approved_by="t")
        db.commit()
        sub = db.get(Submission, prep["submission"]["submission_id"])
        sub.state = SubmissionState.CONFIRMED
        db.commit()

        class BrokenClient:
            def query_bill_status(self, biz):
                raise RuntimeError("platform down")
        r = S.run_reconciliation(
            db, env["tenant_id"], destination_id=dest.id,
            profile=C.PROFILE_RETAIL, client=BrokenClient(),
            window_begin=datetime(2026, 10, 12),
            window_end=datetime(2026, 10, 13))
        assert r["platform_ok"] is False
        assert r["summary"]["matched"] == 0
        assert r["summary"]["unverified_remote"] >= 1


# ================================================================ T26 A15 隔离
def test_t26_cross_tenant_isolation(env):
    """R2-16/FIX2-T26：跨 tenant 的直读/prepare/对账/证据全部拒绝。"""
    from domains.drug_trace import service as S
    from domains.drug_trace import contracts as C
    prep = _frozen_retail(env)
    with env["sf"]() as db:
        other = Tenant(id=new_uuid(), code="other-hospital", name="外院")
        db.add(other)
        db.commit()
        # 跨租户直读
        with pytest.raises(ApiError):
            S.prepare_submission(db, other.id, prep["revision"],
                                 profile=C.PROFILE_RETAIL,
                                 destination_id=prep["dest"].id)
        # 跨租户对账（destination 归属校验）
        recon_keys = [i["key"] for i in S.REQUIRED_EVIDENCE["reconcile"]]
        S.approve_evidence(
            db, other.id, profile=C.PROFILE_RETAIL, capability="reconcile",
            destination_id=prep["dest"].id,
            provided_items=[{"key": k} for k in recon_keys], approved_by="x")
        db.commit()
        with pytest.raises(ApiError):
            S.run_reconciliation(db, other.id, destination_id=prep["dest"].id,
                                 profile=C.PROFILE_RETAIL,
                                 window_begin=datetime(2026, 10, 12),
                                 window_end=datetime(2026, 10, 13))
        # 跨租户证据读取：evidence_state 按租户过滤，查不到对方证据
        st = S.evidence_state(db, env["tenant_id"], profile=C.PROFILE_RETAIL,
                              capability="reconcile",
                              destination_id=prep["dest"].id)
        assert st["status"] == "MISSING"


# ================================================================ T27 输入规则
def test_t27_parent_child_same_bill_blocked():
    """FIX2-07：父子包装码同单 → INVALID 阻断。"""
    from domains.drug_trace import quality
    parent = "8100000000000F"
    child = "81000000000001"
    lines = [_code_row("PC", record_id="R1", code=parent),
             _code_row("PC", record_id="R2", code=child, parent_code=parent)]
    st, issues = quality.evaluate_revision(
        _bill_row("PC"), lines, profile="alihealth_yljg_retail_v1",
        code_limit_guard=3500)
    assert st == "INVALID"
    assert any(i["rule"] == "QD-PARENT-CHILD-SAME-BILL" for i in issues)
    # 无父子冲突的正常单不受影响
    lines_ok = [_code_row("PC", record_id="R1", code=parent),
                _code_row("PC", record_id="R2", code=child)]
    st_ok, _ = quality.evaluate_revision(
        _bill_row("PC"), lines_ok, profile="alihealth_yljg_retail_v1",
        code_limit_guard=3500)
    assert st_ok == "VALID"


def test_t27b_bill_time_required_at_freeze(env, tmp_path):
    """FIX2-01：bill_time 缺失的单不能完成冻结发送链（TOP 必填）。"""
    from domains.drug_trace import service as S
    from domains.drug_trace.models import DrugTraceBillRevision
    conn = _connector_id(env)
    wh = _wh_with([_bill_row("NB", bill_time=None,
                             source_bill_kind_raw="323")],
                  [_code_row("NB", record_id="R1")])
    with env["sf"]() as db:
        S.run_collection(db, env["tenant_id"], conn, wh,
                         window_begin=datetime(2026, 10, 12),
                         window_end=datetime(2026, 10, 13))
        rev = db.query(DrugTraceBillRevision).first()
        _dicts(env)
        dest = Destination(
            id=new_uuid(), tenant_id=env["tenant_id"], code="ali-mock",
            profile_revision="alihealth_yljg_retail_v1", mode="PUSH",
            capabilities=["upload"], is_mock=True, status="ENABLED")
        db.add(dest)
        db.commit()
        result = S.prepare_submission(
            db, env["tenant_id"], rev.id,
            profile="alihealth_yljg_retail_v1", destination_id=dest.id)
        sub = db.get(Submission, result["submission_id"])
        with pytest.raises(ApiError) as ei:
            S.perform_send(db, env["tenant_id"], sub, dry_run=True)
        assert "bill_time" in ei.value.message
        # 零副作用：无尝试、状态不变
        assert db.query(DeliveryAttempt).filter_by(
            submission_id=sub.id).count() == 0
        assert sub.state == SubmissionState.PREPARED
