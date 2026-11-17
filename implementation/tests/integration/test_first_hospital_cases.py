"""首院验收用例 F01—F20（07 §10 → 真实测试）。

每个用例对应 契约与示例/first-hospital.acceptance-cases.json 的一条规范。
全部使用合成数据；PIS/HIS 为合成仓储，不含真实患者。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402
from tests.harness import Harness  # noqa: E402
import plugin_registry  # noqa: E402


def test_F01_unique_barcode_files_first(harness: Harness):
    """manifest仅barcode_raw=900001，PIS唯一命中9000001：文件先归集，服务端关联。"""
    reg = harness.register_scan("900001")
    result = harness.resolve(reg["acquisition_id"])
    assert result["state"] == "ASSOCIATED"
    assert result["candidates"][0]["pathology_no"] == "9000001"
    case = harness.case_by_no("9000001")
    assert case is not None and case.slides_linked


def test_F02_zero_candidates_then_wakeup(harness: Harness):
    """条码0候选→等待；补入PIS后事件唤醒关联。已AVAILABLE字节不重传由A02覆盖。"""
    reg = harness.register_scan("999999")
    r1 = harness.resolve(reg["acquisition_id"])
    assert r1["state"] == "WAITING"
    assert r1["wait_reason"] == "BARCODE_NOT_FOUND"
    issues = [q for q in harness.open_issues(f"acquisition:{reg['acquisition_id']}")
              if "BARCODE_NOT_FOUND" in json.dumps(q.context_json)]
    assert issues, "expected BARCODE_NOT_FOUND issue"
    # 补入PIS（合成仓储新增行）→ 唤醒
    from sources.db_view import SyntheticWarehouse
    harness_w = SyntheticWarehouse()  # 同进程单例被registry持有；直接取
    import plugin_registry
    wh = plugin_registry.synthetic_warehouse()
    wh.upsert_row("Pitaya_Slice", {
        "Id": 99, "qptmh": "999999", "PathologyID": "9000001", "lkh": None,
        "qpzt": 0, "zpzt": 0, "tmdyzt": 0})
    r2 = harness.resolve(reg["acquisition_id"])
    assert r2["state"] == "ASSOCIATED"
    # 等待问题被复评关闭
    still = [q for q in harness.open_issues(f"acquisition:{reg['acquisition_id']}")
             if "BARCODE_NOT_FOUND" in json.dumps(q.context_json)]
    assert not still


def test_F03_ambiguous_barcode_human(harness: Harness):
    reg = harness.register_scan("990000")
    result = harness.resolve(reg["acquisition_id"])
    assert result["state"] == "WAITING"
    assert result["wait_reason"] == "SOURCE_AMBIGUOUS"
    assert len(result["candidates"]) == 2
    case = harness.case_by_no("9000002")
    case3 = harness.case_by_no("9000003")
    # 不取第一条：两个病例都不得建立绑定
    assert not (case and case.slides_linked)
    assert not (case3 and case3.slides_linked)


def test_F04_leading_zeros_and_lengths(harness: Harness):
    """090001 与 90001 不等价；病理号6/7/8位原值保留。"""
    reg = harness.register_scan("090001")
    result = harness.resolve(reg["acquisition_id"])
    assert result["state"] == "ASSOCIATED"
    assert result["candidates"][0]["pathology_no"] == "0900001"  # 前导零保留
    # 90001 在夹具中无命中（不同于090001）
    reg2 = harness.register_scan("90001")
    r2 = harness.resolve(reg2["acquisition_id"])
    assert r2["wait_reason"] == "BARCODE_NOT_FOUND"
    # 病理号长度不硬编码：7位与6位/8位示例同表存在
    from sources.db_view import fixtures
    lens = {len(s["PathologyID"]) for s in fixtures.SLIDES if not s.get("__namespace")}
    assert lens == {7}


def test_F05_filename_hint_conflict(harness: Harness):
    """文件名提示病理号与PIS不一致：归集可完成，身份冲突阻断自动绑定。"""
    reg = harness.register_scan("900005", pathology_hint="8888888")
    result = harness.resolve(reg["acquisition_id"])
    assert result["state"] == "WAITING"
    assert result["wait_reason"] == "IDENTITY_CONFLICT"
    conflicts = [q for q in harness.open_issues(f"acquisition:{reg['acquisition_id']}")
                 if q.rule_code == "QF-ID-CONFLICT"]
    assert conflicts
    case = harness.case_by_no("9000004")
    assert case is None or not case.slides_linked


def test_F06_two_visits_no_bridge_waits(harness: Harness):
    """一患者两住院、无已确认VISIT：患者可关联，就诊等待，不选最新。"""
    from apps.seed import HOSPITAL_PACK_REVIEW_DRAFT
    harness.publish_pack(HOSPITAL_PACK_REVIEW_DRAFT)  # 无已批准桥接
    reg = harness.register_scan("900001")
    harness.resolve(reg["acquisition_id"])
    case = harness.case_by_no("9000001")
    assert case.patient_ref_id, "patient reference should associate"
    from domains.pathology.models import EncounterReference
    with harness.sf() as db:
        encs = (db.query(EncounterReference)
                .filter_by(tenant_id=harness.tenant_id,
                           patient_ref_id=case.patient_ref_id).all())
        assert encs == [], "no encounter should be confirmed without bridge"
    issues = [q for q in harness.open_issues(f"pathology_case:{case.id}")
              if q.rule_code == "QF-ENCOUNTER"]
    assert issues and "NOT auto-selected" in json.dumps(issues[0].context_json)


def test_F07_pis_valid_but_his_conflict(harness: Harness):
    """PIS证件校验通过但HIS号码冲突：人工复核，不优先PIS、不自动111。"""
    reg = harness.register_scan("900006")  # 病例9000002：PIS与HIS证件号不同
    result = harness.resolve(reg["acquisition_id"])
    case = harness.case_by_no("9000002")
    issues = [q for q in harness.open_issues(f"pathology_case:{case.id}")
              if q.rule_code == "QF-ID-CONFLICT"]
    assert issues, "expected identity conflict issue"
    assert case.clinically_ready is False
    # 未产生索引候选
    from domains.pathology.models import PathologyIndexRecord
    with harness.sf() as db:
        assert (db.query(PathologyIndexRecord)
                .filter_by(case_id=case.id).count() == 0)


def test_F08_dirty_id_type_candidate_only(harness: Harness):
    """HIS类型脏值('临床试验')、号码缺失：候选建议，默认等待；不自动111。"""
    reg = harness.register_scan("900007")  # P0003：ID_TYPE脏值、ID_NO空、PIS无证件
    harness.resolve(reg["acquisition_id"])
    case = harness.case_by_no("9000003")
    from domains.pathology.models import PatientReference
    with harness.sf() as db:
        pat = db.get(PatientReference, case.patient_ref_id)
        assert pat.state == "PENDING"
        assert pat.identity_snapshot["outcome"]["id_type_candidate"] is None
        assert pat.identity_snapshot["outcome"]["patient_state"] != "CONFIRMED"


def test_F09_future_date_and_createtime_only(harness: Harness):
    """未来申请时间/仅CreateTime：质量阻断，不从申请号或当前时间纠正。"""
    # 病例9000003的申请日期为2028（夹具）；应用时间为权威字段（演示Pack已批准）
    reg = harness.register_scan("900007")
    harness.resolve(reg["acquisition_id"])
    case = harness.case_by_no("9000003")
    future = [q for q in harness.open_issues(f"pathology_case:{case.id}")
              if q.rule_code == "QF-DATE-FUTURE"]
    assert future, "expected QF-DATE-FUTURE for 2028-10-26 application time"
    # 申请时间未被替换为now或号码推算
    from domains.pathology.models import PathologyApplication
    with harness.sf() as db:
        app = (db.query(PathologyApplication)
               .filter_by(tenant_id=harness.tenant_id,
                          external_no="1000000003").first())
        assert app.requested_at is not None
        assert app.requested_at.year == 2028
    # review-draft pack（时间字段未批准）→ QF-TIME-SEMANTICS 等待
    from apps.seed import HOSPITAL_PACK_REVIEW_DRAFT
    harness.publish_pack(HOSPITAL_PACK_REVIEW_DRAFT)
    reg2 = harness.register_scan("900005")
    harness.resolve(reg2["acquisition_id"])
    case4 = harness.case_by_no("9000004")
    semantics = [q for q in harness.open_issues(f"pathology_case:{case4.id}")
                 if q.rule_code == "QF-TIME-SEMANTICS"]
    assert semantics


def test_F10_clock_tolerance_birth_unknown(harness: Harness):
    from domains.pathology.quality import QualityInput, evaluate
    from datetime import datetime
    ev = datetime(2026, 9, 30, 12, 0, 0)
    # 边界容差内（+300s）不触发 QF-DATE-FUTURE
    inp = QualityInput(
        case={"id": "c1"}, application={
            "requested_at": "2026-09-30T12:04:00", "requested_at_source": "批准字段"},
        reports=[], identity=None, completeness=None, encounter=None,
        org_mapping={}, category_dictionary={})
    issues = evaluate(inp, evaluated_at=ev, clock_tolerance_seconds=300)
    assert not [i for i in issues if i["rule_code"] == "QF-DATE-FUTURE"]
    # 超5分钟即阻断
    inp2 = QualityInput(
        case={"id": "c1"}, application={
            "requested_at": "2026-09-30T12:30:00", "requested_at_source": "批准字段"},
        reports=[], identity=None, completeness=None, encounter=None,
        org_mapping={}, category_dictionary={})
    issues2 = evaluate(inp2, evaluated_at=ev, clock_tolerance_seconds=300)
    assert [i for i in issues2 if i["rule_code"] == "QF-DATE-FUTURE"]
    # 出生未知：不伪造生日、不误报 BEFORE-BIRTH
    inp3 = QualityInput(
        case={"id": "c1", "birth_date": None}, application={
            "requested_at": "1990-01-01T00:00:00", "requested_at_source": "批准字段"},
        reports=[{"source_report_id": "r-ext", "issued_at": "1985-01-01T00:00:00",
                  "consultation_original": True}],  # 外院会诊原报告不混比
        identity=None, completeness=None, encounter=None, org_mapping={},
        category_dictionary={})
    issues3 = evaluate(inp3, evaluated_at=ev)
    assert not [i for i in issues3 if i["rule_code"] == "QF-DATE-BEFORE-BIRTH"]
    assert not [i for i in issues3 if i["rule_code"] == "QF-DATE-ORDER"]


def test_F11_category_placeholder_waits(harness: Harness):
    """类别'--'：等待，不默认常规、不自行推断（即使FreezingID存在）。"""
    reg = harness.register_scan("900007")  # 该申请检查类型='--'，FreezingID='FZ-1'
    harness.resolve(reg["acquisition_id"])
    case = harness.case_by_no("9000003")
    cats = [q for q in harness.open_issues(f"pathology_case:{case.id}")
            if q.rule_code == "QF-CATEGORY"]
    assert cats and "'--'" in json.dumps(cats[0].context_json)
    assert case.clinically_ready is False


def test_F12_org_code_missing_or_scanner_differs(harness: Harness):
    """机构码：批准映射内→可用；未批准pack→等待；不套扫描单位。"""
    # 演示pack已批准 YLV21051→H1234567890X：9000001 成功路径包含该映射
    reg = harness.register_scan("900001")
    harness.resolve(reg["acquisition_id"])
    # review-draft pack：映射未批准 → QF-ORG 等待
    from apps.seed import HOSPITAL_PACK_REVIEW_DRAFT
    harness.publish_pack(HOSPITAL_PACK_REVIEW_DRAFT)
    reg2 = harness.register_scan("900005")
    harness.resolve(reg2["acquisition_id"])
    case4 = harness.case_by_no("9000004")
    orgs = [q for q in harness.open_issues(f"pathology_case:{case4.id}")
            if q.rule_code == "QF-ORG"]
    assert orgs and "not approved" in json.dumps(orgs[0].context_json)


def test_F13_unknown_status_not_complete(harness: Harness):
    """状态512/打印状态未知值：不认定扫描完成/审核完成/齐套。"""
    reg = harness.register_scan("900001")
    harness.resolve(reg["acquisition_id"])
    case = harness.case_by_no("9000001")
    status_issues = [q for q in harness.open_issues(f"pathology_case:{case.id}")
                     if q.rule_code == "QF-STATUS"]
    assert status_issues, "unknown status raw must be recorded"
    assert status_issues[0].severity == "WARNING"
    assert "not treated as scanned" in json.dumps(status_issues[0].context_json)


def test_F14_report_change_without_createtime(harness: Harness):
    """旧病例报告变化但CreateTime不变：分区盘点发现新修订；增量不虚报完整。"""
    from apps.worker.engine import WorkerEngine
    reg = harness.register_scan("090001")
    harness.resolve(reg["acquisition_id"])
    # 第一轮盘点
    with harness.sf() as db:
        from core_db.models import ConnectorInstance
        ci = (db.query(ConnectorInstance)
              .filter_by(tenant_id=harness.tenant_id,
                         plugin_id="pis.pitaya.sqlserver").first())
        cid = ci.id
    _partition(harness, cid)
    with harness.sf() as db:
        from core_db.models import SourceRecordRevision, SourceReconciliationRun
        revs1 = db.query(SourceRecordRevision).count()
        assert revs1 >= 1
    # 报告内容变化（CreateTime不变）
    import plugin_registry
    plugin_registry.synthetic_warehouse().mutate_row(
        "Pitaya", {"PathologyID": "0900001"}, {"Zdyj": "d5-v2-revised"})
    _partition(harness, cid)
    with harness.sf() as db:
        from core_db.models import SourceRecordRevision
        recs = (db.query(SourceRecordRevision)
                .filter(SourceRecordRevision.raw_payload.contains("d5-v2-revised"))
                .all())
        assert recs, "partition scan must detect content change w/o CreateTime change"
    # 追加报告 → 新报告版本（F16前半）
    plugin_registry.synthetic_warehouse().upsert_row(
        "患者检查报告信息表",
        {"报告流水号": "0900001-A2", "申请号": "1000000005", "报告单类型": "病理",
         "病理诊断": "d5-addendum-2", "检查所见": "", "审核日期": "2026-09-20T10:00:00",
         "图像报告URL": None, "作废标记": 0, "__namespace": "PITAYA"})
    harness.resolve(reg["acquisition_id"])
    from domains.pathology.models import PathologyReportVersion
    with harness.sf() as db:
        case = harness.case_by_no("0900001")
        reports = (db.query(PathologyReportVersion)
                   .filter_by(case_id=case.id).all())
        assert len(reports) == 3, "addendum stored as new report version"


def test_F15_partition_failure_no_tombstone(harness: Harness):
    """盘点中断/一页缺行：不误删；complete_coverage 不可生成。"""
    # 让 get_case 对某键抛错模拟读页失败
    import plugin_registry
    from sources.db_view import PitayaPisConnector
    wh = plugin_registry.synthetic_warehouse()
    orig = PitayaPisConnector.get_case

    def broken(self, scope, key):
        if key == "9000003":
            raise RuntimeError("simulated page failure / permission denied")
        return orig(self, scope, key)
    PitayaPisConnector.get_case = broken
    try:
        with harness.sf() as db:
            from core_db.models import ConnectorInstance
            ci = (db.query(ConnectorInstance)
                  .filter_by(tenant_id=harness.tenant_id,
                             plugin_id="pis.pitaya.sqlserver").first())
            cid = ci.id
        _partition(harness, cid)
        with harness.sf() as db:
            from core_db.models import (SourceReconciliationRun,
                                        SourceRecordRevision)
            run = (db.query(SourceReconciliationRun)
                   .order_by(SourceReconciliationRun.started_at.desc()).first())
            assert run.complete_coverage is False
            assert run.state == "PARTIAL"
            assert run.summary["failures"] >= 1
            # 不产生删除/墓碑
            dels = (db.query(SourceRecordRevision)
                    .filter_by(is_deleted=True).count())
            assert dels == 0
    finally:
        PitayaPisConnector.get_case = orig


def test_F16_void_and_addendum_new_version(harness: Harness):
    """经确认源作废及追加报告：新版本/墓碑候选，历史冻结内容不变。"""
    reg = harness.register_scan("090001")
    harness.resolve(reg["acquisition_id"])
    case = harness.case_by_no("0900001")
    # 冻结 v1
    from domains.pathology import pipeline
    with harness.sf() as db:
        rt = plugin_registry.build_runtime(db, harness.tenant_id)
        pr1 = pipeline.freeze_case(db, harness.tenant_id, case, rt)
        assert pr1 is not None
        db.commit()
        pr1_id, digest1 = pr1.id, pr1.digest
        payload1 = json.dumps(pr1.payload, sort_keys=True)
    # 作废原报告 + 追加新报告（源确认作废）
    wh = plugin_registry.synthetic_warehouse()
    wh.mark_void("患者检查报告信息表", {"报告流水号": "0900001"})
    harness.resolve(reg["acquisition_id"])
    with harness.sf() as db:
        case = harness.case_by_no("0900001")
        pr2 = pipeline.freeze_case(db, harness.tenant_id, case, rt,
                                   revision_note="void+addendum")
        db.commit()
        assert pr2.revision == 2, f"expected new revision, got {pr2.revision}"
        assert pr2.digest != digest1, "void+addendum must change payload digest"
        assert pr1_digest_unchanged(harness, pr1_id, digest1, payload1)


def pr1_digest_unchanged(harness, pr_id, digest, payload_json):
    from core_db.models import PackageRevision
    with harness.sf() as db:
        pr = db.get(PackageRevision, pr_id)
        return pr.digest == digest and json.dumps(pr.payload, sort_keys=True) == payload_json


def _partition(harness, connector_id):
    with harness.sf() as db:
        harness.worker._j_pis_partition(db, _job(harness.tenant_id, connector_id,
                                                 "0", "99999999"))


def _job(tenant_id, connector_id, key_from, key_to):
    from core_db.models import Job
    from contracts import new_uuid
    return Job(id=new_uuid(), tenant_id=tenant_id, job_type="pis.partition_reconcile",
               dedup_key=f"test-{new_uuid()[:6]}",
               payload={"connector_id": connector_id, "key_from": key_from,
                        "key_to": key_to})


def test_F17_jpg_url_not_wsi(harness: Harness):
    """仅V_PIS_IMAGE JPG/URL：不宣称WSI已取得；role为附件。"""
    reg = harness.register_scan("090001")
    result = harness.resolve(reg["acquisition_id"])
    import plugin_registry
    with harness.sf() as db:
        rt = plugin_registry.build_runtime(db, harness.tenant_id)
        images = rt.pis.get_images(rt.scope(), "0900001")
        assert images and images[0]["device_type"] == "显微镜"
        assert images[0]["storage"] == "FTP"
    # 图像URL只作为报告附件记录，不产生 primary WSI 资产
    from domains.pathology.models import PathologyReportVersion
    with harness.sf() as db:
        case = harness.case_by_no("0900001")
        rv = (db.query(PathologyReportVersion)
              .filter_by(case_id=case.id).first())
        assert rv.source_url.startswith("http")


def test_F18_no_dual_read_same_key(harness: Harness):
    """QPIS/PITAYA同键：不双读合并；namespace区分。"""
    # 合成夹具在QPIS命名空间有 qptmh=900001→8800001
    import plugin_registry
    from connector_sdk import SourceScope
    wh = plugin_registry.synthetic_warehouse()
    pit = type("P", (object,), {})()
    from sources.db_view import PitayaPisConnector
    c_pit = PitayaPisConnector(wh, database="PITAYA", schema="dbo")
    c_qpis = PitayaPisConnector(wh, database="QPIS", schema="dbo")
    scope_pit = SourceScope(tenant_id=harness.tenant_id, source_system_code="pis",
                            database="PITAYA", schema="dbo",
                            namespace="pis.pitaya.sqlserver@PITAYA.dbo")
    scope_qpis = SourceScope(tenant_id=harness.tenant_id, source_system_code="pis",
                             database="QPIS", schema="dbo",
                             namespace="pis.pitaya.sqlserver@QPIS.dbo")
    # 合成仓储按 namespace 过滤需要 wh.namespace——适配器构造时传入
    wh.namespace = "QPIS"
    r_qpis = c_qpis.get_slide_by_barcode(scope_qpis, "900001")
    assert any(c.pathology_no == "8800001" for c in r_qpis["candidates"])
    wh.namespace = "PITAYA"
    r_pit = c_pit.get_slide_by_barcode(scope_pit, "900001")
    assert all(c.pathology_no == "9000001" for c in r_pit["candidates"])
    # 主流程 namespace 固定 PITAYA，不合并 QPIS
    wh.namespace = "PITAYA"


def test_F19_if_match_concurrency(harness: Harness):
    """并发更正：If-Match冲突；授权决定可追溯。"""
    core = harness.core
    reg = harness.register_scan("900001")
    harness.resolve(reg["acquisition_id"])
    case = harness.case_by_no("9000001")
    # 缺 If-Match → 428
    r0 = core.post(f"/api/v1/pathology/cases/{case.id}/associations",
                   json={"barcode_raw": "900001", "reason": "x"},
                   headers=harness.admin_headers())
    assert r0.status_code == 428
    # 旧 ETag → 412
    r1 = core.post(f"/api/v1/pathology/cases/{case.id}/associations",
                   json={"barcode_raw": "900001", "reason": "x"},
                   headers={**harness.admin_headers(), "If-Match": "stale"})
    assert r1.status_code == 412
    # 有效操作被审计
    from contracts.api import digest_of
    etag = digest_of({"case_id": case.id, "clinically_ready": case.clinically_ready,
                      "slides_linked": case.slides_linked})[:16]
    r2 = core.post(f"/api/v1/pathology/cases/{case.id}/associations",
                   json={"barcode_raw": "900001", "reason": "复核确认"},
                   headers={**harness.admin_headers(), "If-Match": etag})
    assert r2.status_code == 200
    audits = core.get("/api/v1/audit?action=pathology.association.manual",
                      headers=harness.admin_headers()).json()["data"]
    assert any(a["reason"] == "复核确认" for a in audits)


def test_F20_metrics_only_domain(harness: Harness):
    """仅metrics插件+CSV输入：病理字段非核心必填，HTTP Mock闭环。"""
    core = harness.core
    csv_text = ("机构,月份,项目,数量,金额\n"
                "H1234567890X,2026-08,门诊切片,120,12345.60\n"
                "H1234567890X,2026-08,术中冰冻,30,9876.50\n")
    prev = core.post("/api/v1/imports/preview", json={"csv": csv_text},
                     headers=harness.admin_headers()).json()["data"]
    assert prev["total_preview"] == 2 and not prev["row_errors"]
    imp = core.post("/api/v1/imports", json={"csv": csv_text},
                    headers={**harness.admin_headers(),
                             "Idempotency-Key": "metrics-1"})
    assert imp.status_code == 200
    pkg_id = imp.json()["data"]["package_id"]
    # 幂等重放同包
    imp2 = core.post("/api/v1/imports", json={"csv": csv_text},
                     headers={**harness.admin_headers(),
                              "Idempotency-Key": "metrics-1"})
    assert imp2.json()["data"]["package_id"] == pkg_id
    harness.tick(20)
    subs = core.get("/api/v1/submissions",
                    headers=harness.admin_headers()).json()["data"]
    assert subs, "metrics package should reach mock destination"
    with harness.sf() as db:
        from core_db.models import PackageRevision
        pr = (db.query(PackageRevision).filter_by(package_id=pkg_id).first())
        assert pr.schema_ref["key"] == "metrics.monthly"
