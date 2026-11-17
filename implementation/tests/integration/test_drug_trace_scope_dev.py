"""药品追溯·口径扩展开发测试（DTR-SCOPE-DEV-20261027 SD-02—SD-07）。

覆盖：
- r11 SourceProfile fail-closed（视图未建 → unresolved 阻断生产采集）
- r11 视图 DDL 列契约与 r10 机器比对（17/18 列同名同序）
- 住院（INPATIENT_DISPENSE）采集/质量/冻结/DRY_RUN；无批准字典 → 发送被拒
- 退药冲正（PATIENT_RETURN）：0 自有码 + 原 CONFIRMED 单 → VALID/冲正 payload
  用原发药码（只读）；无原单 → WAITING（QD-RETURN-ORIGINAL-PENDING）
- NOT_REPORTABLE 终态（7 天内/外两例；仅门诊）+ 存量 WAITING 幂等迁移
- rescan 三分支（跳过 CONFIRMED / 码到换版 / 无变化跳过）+ 源侧消失
- rescan API（角色/键数上限/审计/job 类型）
- 驱动 v3 回看阶段（失败不改退出码；成功路径新 VALID 单进入发送队列）
- SD-06 205 对照聚合纯函数

合成数据全部为编造值（无患者真实信息）；未连接真实 HIS/平台。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402

from contracts import ApiError, new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import Destination, DictionaryRevision, Job, Submission  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
R11_SQL = ROOT / "docs/development/drug-trace-scope-dev-20261027/sql/r11_inpatient_views.sql"
R10_SQL = (ROOT / "docs/development/drug-trace-content-contract-20261022/sql/"
           "rollback_to_observed_views.sql")
DRIVER = (ROOT / "docs/development/drug-trace-timed-report-20261024/"
          "daily_upload_driver.py")
SD06 = ROOT / "docs/development/drug-trace-scope-dev-20261027/sd06_report.py"


@pytest.fixture()
def env(tmp_path):
    """drug_trace 启用的完整栈（SQLite 内存文件）。"""
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/sd.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        from core_db.models import Tenant
        t = Tenant(id=new_uuid(), code="first-hospital", name="首院")
        db.add(t)
        db.commit()
        tenant_id = t.id
    yield {"sf": sf, "engine": engine, "tenant_id": tenant_id, "reg": reg}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


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


def _bill_row(bill_id="B0001", pharmacy="YF01", kind="PATIENT_DISPENSE",
              updated="2026-10-12T08:00:00", revision="1", finalized=True,
              bill_time="2026-10-12T07:55:00", **extra):
    row = {
        "source_system": "HIS", "source_bill_id": bill_id,
        "source_bill_no": bill_id, "source_revision": revision,
        "business_kind": kind, "pharmacy_code": pharmacy,
        "bill_time": bill_time, "updated_at": updated, "finalized": finalized,
        "is_void": False,
    }
    row.update(extra)
    return row


def _code_row(bill_id="B0001", pharmacy="YF01", kind="PATIENT_DISPENSE",
              record_id="R1", line_id="L1", code="81000000000123",
              revision="1", updated="2026-10-12T08:00:00", deleted=False,
              quantity="1", unit="盒", **extra):
    row = {
        "source_system": "HIS", "source_bill_id": bill_id, "pharmacy_code": pharmacy,
        "business_kind": kind, "source_revision": revision,
        "source_line_id": line_id, "source_code_record_id": record_id,
        "trace_code": code, "updated_at": updated, "is_deleted": deleted,
        "quantity": quantity, "unit": unit,
    }
    row.update(extra)
    return row


def _wh_with(rows_bills, rows_codes, namespace="HIS"):
    from sources.db_view.drug_trace import SyntheticDrugTraceWarehouse
    w = SyntheticDrugTraceWarehouse(namespace=namespace)
    for r in rows_bills:
        w.add_bill(r)
    for r in rows_codes:
        w.add_code(r)
    return w


def _mock_dest(env, code="alihealth-test"):
    with env["sf"]() as db:
        dest = Destination(
            id=new_uuid(), tenant_id=env["tenant_id"], code=code,
            profile_revision="alihealth_yljg_retail_v1", mode="PUSH",
            capabilities=["upload", "query"], is_mock=True, status="ENABLED")
        db.add(dest)
        db.commit()
        return dest.id


def _dicts(env, entries):
    """写批准字典：entries = [(namespace, [(code, target), ...])]。"""
    with env["sf"]() as db:
        for ns, items in entries:
            db.add(DictionaryRevision(
                id=new_uuid(), tenant_id=env["tenant_id"], namespace=ns,
                version=f"v-{new_uuid()[:8]}",
                items=[{"code": c, "target": t} for c, t in items],
                source="target", approved_by="admin"))
        db.commit()


# ============================================================ r11 profile/DDL
def test_r11_source_profiles_fail_closed():
    """SD-02/A02：r11 profile 登记但视图未建 → unresolved 阻断生产采集。"""
    from sources.db_view.source_profiles import (get_source_profile,
                                                 HIS_INP_VIEWS_20261027_R11,
                                                 HIS_RETURN_VIEWS_20261027_R11)
    from connector_sdk import CapabilityError
    for profile, views in (
            (HIS_INP_VIEWS_20261027_R11,
             ("PHARMACY.V_DRUG_TRACE_BILL_INP", "PHARMACY.V_DRUG_TRACE_CODE_INP")),
            (HIS_RETURN_VIEWS_20261027_R11,
             ("PHARMACY.V_DRUG_TRACE_RETURN",
              "PHARMACY.V_DRUG_TRACE_RETURN_CODE"))):
        assert get_source_profile(profile.key).key == profile.key
        assert dict(profile.objects)["bill_view"] == views[0]
        assert dict(profile.objects)["code_view"] == views[1]
        assert profile.registered_write_policy == "readonly"
        assert profile.unresolved
        with pytest.raises(CapabilityError) as ei:
            profile.require_runtime_ready(config={
                "collection_mode": "batch",
                "dsn_env": profile.dsn_env,
                "objects": dict(profile.objects)})
        assert ei.value.code == "SOURCE_PROFILE_UNVERIFIED"
        # fail-closed 不影响 r10
        from sources.db_view.source_profiles import HIS_VIEWS_20261022_R10
        assert not HIS_VIEWS_20261022_R10.unresolved


def _view_aliases(sql_text: str, view_name: str, contract: list[str]) -> list[str]:
    """提取视图内契约列别名的首现有序序列（UNION ALL 双分支去重）。"""
    m = re.search(rf"CREATE OR REPLACE VIEW\s+\w*\.?{view_name}\s+AS(.*?)"
                  rf"WITH READ ONLY;", sql_text, re.S | re.I)
    assert m, f"view {view_name} not found"
    allowed = set(contract)
    seen, uniq = set(), []
    for a in re.findall(r"\bAS\s+(\w+)\b", m.group(1)):
        if a in allowed and a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq


def test_r11a_profiles_gate():
    """DTR-SCOPE-DEPLOY-20261028：视图已验证后住院 r11a 放行、退药 r11a 仍闸到首单试传。"""
    from sources.db_view.source_profiles import (get_source_profile,
                                                 HIS_INP_VIEWS_20261028_R11A,
                                                 HIS_RETURN_VIEWS_20261028_R11A)
    from connector_sdk import CapabilityError
    inp = get_source_profile(HIS_INP_VIEWS_20261028_R11A.key)
    assert inp.evidence_level == "LIVE_CONTRACT_VERIFIED" and not inp.unresolved
    inp.require_runtime_ready(config={
        "collection_mode": "batch", "dsn_env": inp.dsn_env,
        "objects": dict(inp.objects)})          # 不抛 = 生产采集可用
    ret = get_source_profile(HIS_RETURN_VIEWS_20261028_R11A.key)
    assert ret.unresolved == ("return_first_bill_smoke_pending",)
    with pytest.raises(CapabilityError) as ei:
        ret.require_runtime_ready(config={
            "collection_mode": "batch", "dsn_env": ret.dsn_env,
            "objects": dict(ret.objects)})
    assert ei.value.code == "SOURCE_PROFILE_UNVERIFIED"


def test_r11_ddl_column_contract_matches_r10():
    """A02 机器比对：r11 四视图列契约与 r10 逐列一致（bill 17 / code 18）。"""
    r10 = R10_SQL.read_text(encoding="utf8")
    r11 = R11_SQL.read_text(encoding="utf8")
    r10_bill = _view_aliases(r10, "V_DRUG_TRACE_BILL", [
        "source_system", "source_bill_id", "source_bill_no", "source_revision",
        "business_kind", "pharmacy_code", "warehouse_code", "supplier_code",
        "bill_time", "updated_at", "source_event_seq", "finalized", "is_void",
        "expected_code_count", "patient_ref", "encounter_ref",
        "source_bill_kind_raw"])
    r10_code = _view_aliases(r10, "V_DRUG_TRACE_CODE", [
        "source_system", "source_bill_id", "pharmacy_code", "business_kind",
        "source_revision", "source_line_id", "source_code_record_id",
        "trace_code", "quantity", "unit", "code_level", "parent_code",
        "updated_at", "is_deleted", "f_trace_code", "s_trace_code",
        "t_trace_code", "bill_time"])
    assert len(r10_bill) == 17 and len(r10_code) == 18
    for view in ("V_DRUG_TRACE_BILL_INP", "V_DRUG_TRACE_RETURN"):
        got = _view_aliases(r11, view, r10_bill)
        assert got == r10_bill, f"{view} 列契约与 r10 不一致: {got}"
    for view in ("V_DRUG_TRACE_CODE_INP", "V_DRUG_TRACE_RETURN_CODE"):
        got = _view_aliases(r11, view, r10_code)
        assert got == r10_code, f"{view} 列契约与 r10 不一致: {got}"


# ============================================================ SD-02 住院
def test_inpatient_dispense_ingest_valid_and_waiting(env):
    """SD-02：住院单走既有质量规则（有码 VALID/0 码 WAITING，K1 同样适用）。"""
    from domains.drug_trace import contracts as C
    from domains.drug_trace import service
    from domains.drug_trace.models import (DrugTraceBill, DrugTraceBillRevision)
    conn = _connector_id(env)
    inp_bill = _bill_row("INP|6852|050103", pharmacy="050103",
                         kind="INPATIENT_DISPENSE",
                         source_bill_kind_raw="REQ_NO=6852;DIS_REC=2026102609235;SRC=inpatient_request",
                         source_bill_no="6852", revision="r1")
    inp_codes = [_code_row("INP|6852|050103", pharmacy="050103",
                           kind="INPATIENT_DISPENSE", record_id="IR1",
                           revision="r1")]
    wh = _wh_with([inp_bill,
                   _bill_row("INP|6853|050103", pharmacy="050103",
                             kind="INPATIENT_DISPENSE",
                             source_bill_kind_raw="REQ_NO=6853;SRC=inpatient_request",
                             source_bill_no="6853", revision="r1")],
                  inp_codes)
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 12),
                               window_end=datetime(2026, 10, 13))
        bills = db.query(DrugTraceBill).filter_by(
            business_kind="INPATIENT_DISPENSE").order_by(
            DrugTraceBill.source_bill_no).all()
        assert [b.source_bill_no for b in bills] == ["6852", "6853"]
        revs = {b.source_bill_no:
                db.get(DrugTraceBillRevision, b.current_revision_id).quality_state
                for b in bills}
        assert revs["6852"] == "VALID"
        assert revs["6853"] == "WAITING"
        assert C.profile_for_kind("INPATIENT_DISPENSE") == C.PROFILE_RETAIL


def test_inpatient_full_chain_and_dict_gate(env):
    """SD-02/A03：住院 采集→质量→冻结→DRY_RUN 绿；无批准字典 → 发送被拒。"""
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBill, DrugTraceBillRevision
    conn = _connector_id(env)
    dest_id = _mock_dest(env)
    wh = _wh_with([_bill_row("INP|6859|050107", pharmacy="050107",
                             kind="INPATIENT_DISPENSE",
                             source_bill_kind_raw="REQ_NO=6859;SRC=inpatient_request",
                             source_bill_no="6859", revision="r1")],
                  [_code_row("INP|6859|050107", pharmacy="050107",
                             kind="INPATIENT_DISPENSE", record_id="IR9",
                             revision="r1")])
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 12),
                               window_end=datetime(2026, 10, 13))
        bill = db.query(DrugTraceBill).filter_by(
            business_kind="INPATIENT_DISPENSE").one()
        rev = db.get(DrugTraceBillRevision, bill.current_revision_id)
        # 字典未批准（DRAFT 不参与映射）→ prepare 被门禁拦截（fail-closed）
        db.add(DictionaryRevision(
            id=new_uuid(), tenant_id=env["tenant_id"],
            namespace="drugtrace.bill_type", version="draft-inp",
            items=[{"code": "INPATIENT_DISPENSE", "target": "325"}],
            source="target", approved_by=""))  # DRAFT：值待平台确认
        db.commit()
        with pytest.raises(ApiError) as ei:
            service.prepare_submission(db, env["tenant_id"], rev.id,
                                       profile="alihealth_yljg_retail_v1",
                                       destination_id=dest_id)
        assert ei.value.code == "DICTIONARY_UNMAPPED"
        # 批准字典（演练环境）→ 冻结 + DRY_RUN 零副作用预览
        _dicts(env, [("drugtrace.bill_type",
                      [("INPATIENT_DISPENSE", "325")]),
                     ("drugtrace.ref_ent_id", [("050107", "ENT-H")]),
                     ("drugtrace.query_bill_type",
                      [("INPATIENT_DISPENSE", "A")])])
        result = service.prepare_submission(
            db, env["tenant_id"], rev.id,
            profile="alihealth_yljg_retail_v1", destination_id=dest_id)
        sub = db.get(Submission, result["submission_id"])
        dry = service.perform_send(db, env["tenant_id"], sub, dry_run=True)
        assert dry["outcome"] == "DRY_RUN"
        assert dry["biz_params"]["bill_type"] == "325"
        assert sub.state == "PREPARED"      # dry-run 零副作用


# ============================================================ SD-03 退药冲正
def _confirmed_original(env, conn):
    """原发药单：采集→VALID→prepare→mock send ACCEPTED→query CONFIRMED。"""
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBill, DrugTraceBillRevision
    dest_id = _mock_dest(env)
    _dicts(env, [("drugtrace.bill_type", [("PRESC_SOURCE=0;FLAG=1", "321")]),
                 ("drugtrace.ref_ent_id", [("050121", "ENT-H")]),
                 ("drugtrace.query_bill_type", [("PRESC_SOURCE=0;FLAG=1", "A")])])
    wh = _wh_with(
        [_bill_row("2026-10-26 00:00:00|57370|050121", pharmacy="050121",
                   kind="PATIENT_DISPENSE", source_bill_no="57370",
                   revision="s1",
                   source_bill_kind_raw="PRESC_SOURCE=0;FLAG=1",
                   bill_time="2026-10-26T15:26:55",
                   updated="2026-10-26T15:26:55")],
        [_code_row("2026-10-26 00:00:00|57370|050121", pharmacy="050121",
                   kind="PATIENT_DISPENSE", record_id="OR1", revision="s1",
                   code="81000000000570", updated="2026-10-26T15:27:00"),
         _code_row("2026-10-26 00:00:00|57370|050121", pharmacy="050121",
                   kind="PATIENT_DISPENSE", record_id="OR2", revision="s1",
                   code="81000000000571", updated="2026-10-26T15:27:00")])
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 26),
                               window_end=datetime(2026, 10, 27))
        bill = db.query(DrugTraceBill).filter_by(
            business_kind="PATIENT_DISPENSE").one()
        rev = db.get(DrugTraceBillRevision, bill.current_revision_id)
        result = service.prepare_submission(
            db, env["tenant_id"], rev.id,
            profile="alihealth_yljg_retail_v1", destination_id=dest_id)
        sub = db.get(Submission, result["submission_id"])
        service.perform_send(db, env["tenant_id"], sub)     # mock → ACCEPTED
        out = service.perform_query(db, env["tenant_id"], sub)  # mock 3/51 → CONFIRMED
        assert out["classification"]["state"] == "CONFIRMED"
        db.refresh(sub)
        assert sub.state == "CONFIRMED"
        return {"bill": bill, "revision": rev, "submission": sub,
                "dest_id": dest_id}


def test_patient_return_reversal_from_original(env):
    """SD-03/A03：退药 0 自有码 → 原单 CONFIRMED 冻结码只读复用 → 冻结/DRY_RUN。"""
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBill, DrugTraceBillRevision
    conn = _connector_id(env)
    orig = _confirmed_original(env, conn)
    ret_raw = ("PRESC_NO=57370;PAT_ID=P1;"
               "SEND_EXEC=2026-10-26 15:26:55;SRC=cli_return")
    ret_bill = _bill_row("RET|20261026000000|57370|050121", pharmacy="050121",
                         kind="PATIENT_RETURN", source_bill_no="57370",
                         revision="t1", source_bill_kind_raw=ret_raw,
                         bill_time="2026-10-26T15:31:52",
                         updated="2026-10-26T15:31:52")
    wh = _wh_with([ret_bill], [])      # 退药 IN 码未写入（SD-01.2 实测）
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 26),
                               window_end=datetime(2026, 10, 27))
        ret = db.query(DrugTraceBill).filter_by(
            business_kind="PATIENT_RETURN").one()
        rev = db.get(DrugTraceBillRevision, ret.current_revision_id)
        assert rev.quality_state == "VALID"
        assert any(i["rule"] == "QD-RETURN-REVERSES-ORIGINAL"
                   for i in rev.quality_issues)
        # 原单冻结 payload 未被修改（只读复用）
        from core_db.models import PackageRevision
        orig_payload = db.get(PackageRevision,
                              orig["submission"].package_revision_id).payload
        assert len(orig_payload["codes"]) == 2
        _dicts(env, [("drugtrace.bill_type", [("PATIENT_RETURN", "323")]),
                     ("drugtrace.query_bill_type", [("PATIENT_RETURN", "A")])])
        result = service.prepare_submission(
            db, env["tenant_id"], rev.id,
            profile="alihealth_yljg_retail_v1",
            destination_id=orig["dest_id"])
        sub = db.get(Submission, result["submission_id"])
        payload = db.get(PackageRevision,
                         sub.package_revision_id).payload
        assert [c["trace_code"] for c in payload["codes"]] == \
            ["81000000000570", "81000000000571"]
        assert payload["reversal_of"]["bill_code"] == orig_payload["bill_code"]
        assert payload["reversal_of"]["submission_id"] == orig["submission"].id
        dry = service.perform_send(db, env["tenant_id"], sub, dry_run=True)
        assert dry["outcome"] == "DRY_RUN"
        assert dry["biz_params"]["trace_codes"].count(",") == 1


def test_patient_return_without_original_waits(env):
    """SD-03：原发药单未 CONFIRMED → QD-RETURN-ORIGINAL-PENDING 等待。"""
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBill, DrugTraceBillRevision
    conn = _connector_id(env)
    wh = _wh_with([_bill_row("RET|20261026101000|99999|050121",
                             pharmacy="050121", kind="PATIENT_RETURN",
                             source_bill_no="99999", revision="t2",
                             source_bill_kind_raw="PRESC_NO=99999;PAT_ID=P9;SRC=cli_return",
                             bill_time="2026-10-26T15:40:00",
                             updated="2026-10-26T15:40:00")], [])
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 26),
                               window_end=datetime(2026, 10, 27))
        ret = db.query(DrugTraceBill).filter_by(
            business_kind="PATIENT_RETURN").one()
        rev = db.get(DrugTraceBillRevision, ret.current_revision_id)
        assert rev.quality_state == "WAITING"
        assert any(i["rule"] == "QD-RETURN-ORIGINAL-PENDING"
                   for i in rev.quality_issues)


# ============================================================ SD-05 NOT_REPORTABLE
def test_not_reportable_7day_boundary():
    """SD-05/A05：7 天内 WAITING / 超 7 天 NOT_REPORTABLE；仅门诊分支。"""
    from domains.drug_trace import contracts as C
    from domains.drug_trace import quality
    now = datetime(2026, 10, 27, 12, 0, 0)
    inside = _bill_row("X1", bill_time="2026-10-22T12:00:00")   # 5 天前
    outside = _bill_row("X2", bill_time="2026-10-18T00:00:00")  # 9 天前
    st_in, iss_in = quality.evaluate_revision(inside, [], profile=None,
                                              code_limit_guard=0, now=now)
    st_out, iss_out = quality.evaluate_revision(outside, [], profile=None,
                                                code_limit_guard=0, now=now)
    assert st_in == C.BillQualityState.WAITING
    assert any(i["rule"] == "QD-NO-CODES" for i in iss_in)
    assert st_out == C.BillQualityState.NOT_REPORTABLE
    assert any(i["rule"] == "QD-NO-CODES-FINAL" for i in iss_out)
    # 边界日：恰 7 天整（含）仍 WAITING，7 天+1 秒 → NOT_REPORTABLE
    edge_in = _bill_row("X3a", bill_time=(now - timedelta(days=7))
                        .strftime("%Y-%m-%dT%H:%M:%S"))
    st_edge_in, _ = quality.evaluate_revision(edge_in, [], profile=None,
                                              code_limit_guard=0, now=now)
    assert st_edge_in == C.BillQualityState.WAITING
    edge_out = _bill_row("X3b", bill_time=(now - timedelta(days=7, seconds=1))
                         .strftime("%Y-%m-%dT%H:%M:%S"))
    st_edge_out, _ = quality.evaluate_revision(edge_out, [], profile=None,
                                               code_limit_guard=0, now=now)
    assert st_edge_out == C.BillQualityState.NOT_REPORTABLE
    # 住院/退药分支不适用（仍 WAITING）
    st_inp, iss_inp = quality.evaluate_revision(
        _bill_row("X4", kind="INPATIENT_DISPENSE",
                  bill_time="2026-10-10T00:00:00"), [],
        profile=None, code_limit_guard=0, now=now)
    assert st_inp == C.BillQualityState.WAITING
    st_ret, iss_ret = quality.evaluate_revision(
        _bill_row("X5", kind="PATIENT_RETURN",
                  bill_time="2026-10-10T00:00:00"), [],
        profile=None, code_limit_guard=0, now=now)
    assert st_ret == C.BillQualityState.WAITING
    assert any(i["rule"] == "QD-RETURN-ORIGINAL-PENDING" for i in iss_ret)


def test_waiting_to_not_reportable_migration_on_touch(env):
    """SD-05：存量 WAITING 老单在同 digest 重导/rescan 触碰时幂等迁移。"""
    from domains.drug_trace import ingest, service
    from domains.drug_trace.models import DrugTraceBill, DrugTraceBillRevision
    conn = _connector_id(env)
    old = _bill_row("OLD1", bill_time="2026-10-10T08:00:00",
                    updated="2026-10-10T08:00:00")
    wh = _wh_with([old], [])
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 10),
                               window_end=datetime(2026, 10, 11))
        bill = db.query(DrugTraceBill).one()
        rev = db.get(DrugTraceBillRevision, bill.current_revision_id)
        assert rev.quality_state == "NOT_REPORTABLE"   # 首次导入即超窗
        # 人工把判定拨回 WAITING 模拟“存量老单”，重导同 digest → 幂等迁移
        rev.quality_state = "WAITING"
        db.commit()
        result = ingest.import_bill(db, env["tenant_id"], conn,
                                    dict(old), [], profile=None)
        assert result.get("migrated_not_reportable") is True
        assert result["state"] == "NOT_REPORTABLE"
        assert rev.quality_state == "NOT_REPORTABLE"
        # 快照原文未被改写
        assert rev.header["source_bill_id"] == "OLD1"
        # 再触碰幂等（不再重复迁移标志）
        result2 = ingest.import_bill(db, env["tenant_id"], conn,
                                     dict(old), [], profile=None)
        assert "migrated_not_reportable" not in result2
        assert result2["state"] == "NOT_REPORTABLE"


# ============================================================ SD-04 rescan
def test_rescan_three_branches(env):
    """SD-04/A04：跳过 CONFIRMED / 码到换版 / 无变化迁移或跳过（+源侧消失）。

    时间稳健：bill_time 相对真实 now 构造（5 天内=回看窗内、9 天前=超窗），
    不用固定日期（避免测试随日历漂移改变质量判定）。
    """
    from domains.drug_trace import service
    from domains.drug_trace import quality as Q
    from domains.drug_trace.models import DrugTraceBill, DrugTraceBillRevision
    conn = _connector_id(env)
    dest_id = _mock_dest(env)
    _dicts(env, [("drugtrace.bill_type", [("PATIENT_DISPENSE", "321")]),
                 ("drugtrace.ref_ent_id", [("YF01", "ENT-H")]),
                 ("drugtrace.query_bill_type", [("PATIENT_DISPENSE", "A")])])
    now = Q.now_gmt8()
    fresh = (now - timedelta(days=1)).replace(microsecond=0)
    old = (now - timedelta(days=9)).replace(microsecond=0)
    rows_bills = [
        _bill_row("A-old", bill_time=old.isoformat(sep=" "),
                  updated=old.isoformat(sep=" ")),
        _bill_row("B-codes", bill_time=fresh.isoformat(sep=" "),
                  updated=fresh.isoformat(sep=" ")),
        _bill_row("C-confirmed", bill_time=fresh.isoformat(sep=" "),
                  updated=fresh.isoformat(sep=" ")),
    ]
    rows_codes = [_code_row("C-confirmed", record_id="CR1",
                            updated=fresh.isoformat(sep=" "))]
    wh = _wh_with(rows_bills, rows_codes)
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=now - timedelta(days=10),
                               window_end=now + timedelta(days=1))
        a_bill = db.query(DrugTraceBill).filter_by(
            source_bill_id="A-old").one()
        b_bill = db.query(DrugTraceBill).filter_by(
            source_bill_id="B-codes").one()
        c_bill = db.query(DrugTraceBill).filter_by(
            source_bill_id="C-confirmed").one()
        assert db.get(DrugTraceBillRevision,
                      a_bill.current_revision_id).quality_state == \
            "NOT_REPORTABLE"       # 超窗无码：首次导入即终态
        assert db.get(DrugTraceBillRevision,
                      b_bill.current_revision_id).quality_state == "WAITING"
        # C 走到 CONFIRMED（mock 全链路）
        c_rev = db.get(DrugTraceBillRevision, c_bill.current_revision_id)
        result = service.prepare_submission(
            db, env["tenant_id"], c_rev.id,
            profile="alihealth_yljg_retail_v1", destination_id=dest_id)
        sub = db.get(Submission, result["submission_id"])
        service.perform_send(db, env["tenant_id"], sub)
        service.perform_query(db, env["tenant_id"], sub)
        db.refresh(sub)
        assert sub.state == "CONFIRMED"
        # 模拟“存量老单”：把 A 拨回 WAITING，验证 rescan 触碰时迁移
        a_rev = db.get(DrugTraceBillRevision, a_bill.current_revision_id)
        a_rev.quality_state = "WAITING"
        db.commit()
        # 码到：源侧 B 换版本 2 并写入码行（模拟迟到码补写）
        later = (fresh + timedelta(hours=2)).isoformat(sep=" ")
        wh.mutate_bill({"source_bill_id": "B-codes"},
                       {"source_revision": "2", "updated_at": later})
        wh.add_code(_code_row("B-codes", record_id="BR1", revision="2",
                              updated=later))
        out = service.run_rescan(db, env["tenant_id"], conn, wh, bill_keys=[
            {"source_system": "HIS", "pharmacy_code": "YF01",
             "business_kind": "PATIENT_DISPENSE", "source_bill_id": "C-confirmed"},
            {"source_system": "HIS", "pharmacy_code": "YF01",
             "business_kind": "PATIENT_DISPENSE", "source_bill_id": "B-codes"},
            {"source_system": "HIS", "pharmacy_code": "YF01",
             "business_kind": "PATIENT_DISPENSE", "source_bill_id": "A-old"},
            {"source_system": "HIS", "pharmacy_code": "YF01",
             "business_kind": "PATIENT_DISPENSE", "source_bill_id": "GONE"},
        ])
        assert out["state"] == "DONE"
        assert out["skipped_confirmed"] == 1          # 已 CONFIRMED 不重报
        assert out["revisions_created"] == 1          # B 码到 → 新 revision
        assert out["migrated_not_reportable"] == 1    # A 存量 WAITING → 终态
        assert out["skipped_missing_source"] == 1     # GONE：源侧无此键
        assert not out["errors"]
        db.refresh(b_bill)
        assert b_bill.current_source_revision == "2"
        assert db.get(DrugTraceBillRevision,
                      b_bill.current_revision_id).quality_state == "VALID"
        assert db.get(DrugTraceBillRevision,
                      a_bill.current_revision_id).quality_state == \
            "NOT_REPORTABLE"


def test_rescan_api_endpoint_roles_and_limits(tmp_path):
    """SD-04/A04：rescan API（operator|admin、≤500 键、审计、job 类型）。"""
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/api.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    from core_db.models import AuthSubject, ConnectorInstance, Tenant
    client = TestClient(create_app(session_factory=sf, engine=engine,
                                   registry=reg))
    with sf() as db:
        t = Tenant(id=new_uuid(), code="first-hospital", name="首院")
        db.add(t)
        db.flush()
        tid = t.id
        subjects = {}
        for role in ("operator", "admin", "auditor"):
            token = f"tok-{role}"
            s = AuthSubject(
                id=new_uuid(), tenant_id=tid, kind="user",
                subject_code=f"user-{role}",
                roles={"operator": ["operator"], "admin": ["platform_admin"],
                       "auditor": ["auditor"]}[role],
                token_hash=__import__("contracts.api",
                                      fromlist=["hash_token"]).hash_token(token),
                status="ACTIVE")
            db.add(s)
            subjects[role] = token
        conn = ConnectorInstance(id=new_uuid(), tenant_id=tid,
                                 direction="source",
                                 plugin_id="sources.db_view.drug_trace",
                                 name="his", status="ENABLED")
        db.add(conn)
        db.commit()
        conn_id = conn.id
    hdr_ok = {"Authorization": "Bearer tok-operator"}
    hdr_admin = {"Authorization": "Bearer tok-admin"}
    hdr_auditor = {"Authorization": "Bearer tok-auditor"}
    body = {"connector_id": conn_id, "mode": "rescan",
            "bill_keys": [{"source_system": "HIS", "pharmacy_code": "050103",
                           "business_kind": "PATIENT_DISPENSE",
                           "source_bill_id": "X1"}]}
    r = client.post("/api/v1/drug-trace/collection-runs", json=body,
                    headers=hdr_auditor)
    assert r.status_code == 403
    r = client.post("/api/v1/drug-trace/collection-runs", json=body,
                    headers=hdr_ok)
    assert r.status_code == 200, r.text
    run_id = r.json()["data"]["collection_run_id"]
    job_id = r.json()["data"]["job_id"]
    with sf() as db:
        job = db.get(Job, job_id)
        assert job is not None and job.job_type == "drug_trace.rescan"
        assert job.dedup_key.startswith(f"rescan:{run_id}#")
        assert job.payload["bill_keys"] == body["bill_keys"]
        # worker 执行（synthetic 空仓储 → 全部 missing，不误删本地）
        from domains.drug_trace import plugin
        plugin._j_rescan(db, job)
        from domains.drug_trace.models import DrugTraceCollectionRun
        run = db.get(DrugTraceCollectionRun, run_id)
        assert run.state == "DONE"
        assert run.summary["skipped_missing_source"] == 1
    # 键数上限：501 键 → VALIDATION_FAILED
    big = dict(body, bill_keys=[
        {"source_system": "HIS", "pharmacy_code": "050103",
         "business_kind": "PATIENT_DISPENSE",
         "source_bill_id": f"K{i}"} for i in range(501)])
    r = client.post("/api/v1/drug-trace/collection-runs", json=big,
                    headers=hdr_admin)
    assert r.status_code == 422
    assert "500" in r.json()["error"]["message"]
    # 空 bill_keys → 拒绝
    r = client.post("/api/v1/drug-trace/collection-runs",
                    json={"connector_id": conn_id, "mode": "rescan",
                          "bill_keys": []}, headers=hdr_ok)
    assert r.status_code == 422
    # 窗口模式仍可用（v2 兼容）
    r = client.post("/api/v1/drug-trace/collection-runs",
                    json={"connector_id": conn_id,
                          "window_begin": "2026-10-12T00:00:00",
                          "window_end": "2026-10-13T00:00:00",
                          "mode": "synthetic"}, headers=hdr_ok)
    assert r.status_code == 200
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


# ============================================================ 驱动 v3 回看
@pytest.fixture()
def driver(monkeypatch):
    monkeypatch.setattr(sys, 'stdin', io.StringIO('public-test-token\n'))
    monkeypatch.setenv('BLYB_DAILY_REPORT_ENABLED', '0')
    spec = importlib.util.spec_from_file_location('sd_driver_v3', DRIVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_driver_lookback_success_path(driver, monkeypatch):
    """A04：成功路径——rescan 运行 DONE 后新 VALID 单进入发送队列（假 HTTP）。"""
    day = datetime(2026, 10, 26).date()
    cand = [type("Row", (), {"bill_id": "b1", "rev_id": "r1",
                             "source_system": "HIS",
                             "pharmacy_code": "050121",
                             "business_kind": "PATIENT_DISPENSE",
                             "source_bill_id": "K1"})()]
    monkeypatch.setattr(driver, 'lookback_candidates', lambda *a, **k: cand)
    monkeypatch.setattr(driver, 'lookback_sendable',
                        lambda *a, **k: [("b1", "r1")])
    monkeypatch.setattr(driver, 'existing_submission_rev',
                        lambda *a, **k: None)
    monkeypatch.setattr(driver, 'wait_for_send',
                        lambda *a, **k: ("CONFIRMED", None))
    calls = []

    def fake_call(method, path, body=None, timeout=90):
        calls.append((method, path.split('?')[0], body))
        if method == 'POST' and path.endswith('/collection-runs'):
            return 201, {'collection_run_id': 'run-1'}
        if method == 'GET' and path.startswith('/drug-trace/collection-runs'):
            return 200, [{'id': 'run-1', 'state': 'DONE',
                          'window': [None, None], 'summary': {}}]
        if method == 'POST' and '/prepare' in path:
            return 201, {'submission_id': 'sub-1'}
        if method == 'POST' and '/send' in path:
            return 201, {'job_id': 'job-1'}
        raise AssertionError(f'unexpected {method} {path}')

    monkeypatch.setattr(driver, 'call', fake_call)
    out = driver.run_lookback(db=None, tenant_id='t', dest_id='d', day=day,
                              total_deadline=__import__('time').monotonic() + 3600)
    assert out['ok'] is True and out['sent'] == 1 and out['prepared_new'] == 1
    assert out['runs'] == [{'id': 'run-1', 'state': 'DONE'}]
    kinds = [(m, p) for m, p, _b in calls]
    assert ('POST', '/drug-trace/collection-runs') in kinds
    assert ('POST', '/drug-trace/bills/b1/prepare') in kinds
    assert ('POST', '/drug-trace/submissions/sub-1/send') in kinds
    rescan_body = next(b for m, p, b in calls
                       if p == '/drug-trace/collection-runs')
    assert rescan_body['mode'] == 'rescan'
    assert rescan_body['bill_keys'][0]['source_bill_id'] == 'K1'


def test_driver_lookback_failure_isolated(driver, monkeypatch):
    """A04：回看失败只写 receipt['lookback']（safe 包裹不抛、退出码隔离）。"""
    monkeypatch.setattr(driver, 'lookback_candidates',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('db down')))
    out = driver.safe_run_lookback(db=None, tenant_id='t', dest_id='d',
                                   day=datetime(2026, 10, 26).date(),
                                   total_deadline=0)
    assert out['ok'] is False and out['error_type'] == 'RuntimeError'
    # rescan 创建失败同样隔离
    driver2 = driver
    monkeypatch.setattr(
        driver2, 'lookback_candidates',
        lambda *a, **k: [type("Row", (), dict(bill_id='b', rev_id='r',
                                              source_system='HIS',
                                              pharmacy_code='P',
                                              business_kind='K',
                                              source_bill_id='S'))()])
    monkeypatch.setattr(driver2, 'call',
                        lambda *a, **k: (500, {'error_code': 'INTERNAL'}))
    out2 = driver2.run_lookback(db=None, tenant_id='t', dest_id='d',
                                day=datetime(2026, 10, 26).date(),
                                total_deadline=0)
    assert out2['ok'] is False and out2['error_step'] == 'rescan_create'
    # LOOKBACK_DAYS=0 → 显式停用
    monkeypatch.setattr(driver2, 'LOOKBACK_DAYS', 0)
    out3 = driver2.safe_run_lookback(db=None, tenant_id='t', dest_id='d',
                                     day=datetime(2026, 10, 26).date(),
                                     total_deadline=0)
    assert out3['ok'] is True and out3.get('reason') == 'LOOKBACK_DISABLED'


# ============================================================ SD-06 纯函数
def test_sd06_comparison_pure_function():
    """SD-06/A06：205 对照聚合纯函数（宏观判定/小时重叠/边界说明）。"""
    spec = importlib.util.spec_from_file_location("sd06_report", SD06)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    daily = [
        {"BUSINESS_ID": "3503", "D": "2026-10-23", "UPLOAD_STATUS": 1,
         "LOGS": 24, "REC": 26, "SUCC": 25, "FAIL": 1},
        {"BUSINESS_ID": "3503", "D": "2026-10-25", "UPLOAD_STATUS": 1,
         "LOGS": 24, "REC": 0, "SUCC": 0, "FAIL": 0},
    ]
    hourly = [
        {"BUSINESS_ID": "3503", "H": "2026-10-23 08", "UPLOAD_STATUS": 1,
         "REC": 3, "SUCC": 3, "FAIL": 0},
        {"BUSINESS_ID": "3503", "H": "2026-10-25 04", "UPLOAD_STATUS": 1,
         "REC": 0, "SUCC": 0, "FAIL": 0},
    ]
    out = mod.build_comparison(
        daily, hourly,
        [{"day": "2026-10-23", "hours": ["17"], "submissions": 1},
         {"day": "2026-10-25", "hours": ["04"], "submissions": 204}])
    by_day = out["vendor_by_day"]
    assert by_day["2026-10-23"]["3503"]["records"] == 26
    w23, w25 = out["our_windows"]
    assert w23["vendor_active_same_day"] is True
    assert w23["vendor_activity_in_our_hours"] == []      # 17 时无活动
    assert w25["vendor_day_records_total"] == 0
    assert w25["macro_read"].startswith("vendor uploaded ZERO")
    assert any("no bill-level detail" in n for n in out["macro_notes"])
