"""契约测试：合成夹具与设计契约样例对齐；manifest示例通过校验；驱动能力边界。"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402

DESIGN_DIR = r"F:\python\blyb\设计交付v0.4\契约与示例"


def test_fixture_matches_design_synthetic_fixture():
    """实现夹具的 slides 与 first-hospital.synthetic-fixture.json 逐条一致。"""
    with open(os.path.join(DESIGN_DIR, "first-hospital.synthetic-fixture.json"),
              encoding="utf-8") as f:
        design = json.load(f)
    from sources.db_view import fixtures
    ours = [{"Id": s["Id"], "qptmh": s["qptmh"], "PathologyID": s["PathologyID"],
             "lkh": s["lkh"]} for s in fixtures.SLIDES
            if not s.get("__namespace") and s["Id"] <= 6]
    assert ours == design["slides"], "实现夹具必须与设计合成夹具保持一致"
    # 期望查询数
    from sources.db_view import SyntheticWarehouse
    wh = SyntheticWarehouse()
    from connector_sdk import SourceScope
    from sources.db_view import PitayaPisConnector
    c = PitayaPisConnector(wh)
    scope = SourceScope(tenant_id="t", source_system_code="pis", database="PITAYA",
                        schema="dbo")
    for exp in design["lookup_expectations"]:
        r = c.get_slide_by_barcode(scope, exp["barcode_raw"])
        assert len(r["candidates"]) == exp["candidate_count"], exp


def test_design_manifest_example_passes_our_validation():
    """设计 acquisition-manifest.example.json 通过我们的 pydantic 模型。"""
    with open(os.path.join(DESIGN_DIR, "acquisition-manifest.example.json"),
              encoding="utf-8") as f:
        example = json.load(f)
    from apps.storage_service.schemas import AcquisitionManifest
    m = AcquisitionManifest.model_validate(example)
    assert m.identity_hints.barcode_raw == "900001"
    assert m.files[0].sha256 == ("ba7816bf8f01cfea414140de5dae2223b00361a3"
                                 "96177a9cb410ff61f20015ad")
    # transport-fixture.bin 内容为 abc（README）
    import hashlib
    fixture = os.path.join(DESIGN_DIR, "transport-fixture.bin")
    with open(fixture, "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == m.files[0].sha256


def test_workflow_dsl_examples_valid():
    """设计示例与我们的 DAG 校验对齐情况（差异记录于 docs/接口与设计差异.md）。

    pathology 示例完全通过；metrics 示例 map→send 缺 target.validate，
    被我们的更严格端口规则拒绝——副作用前必须显式验证节点。
    """
    from workflow_core.dag import validate_dsl
    with open(os.path.join(DESIGN_DIR, "pathology-workflow.example.json"),
              encoding="utf-8") as f:
        assert validate_dsl(json.load(f)) == []
    with open(os.path.join(DESIGN_DIR, "metrics-workflow.example.json"),
              encoding="utf-8") as f:
        errors = validate_dsl(json.load(f))
    assert errors == ["edge map(target.map:mapped) -> send(deliver.push)"
                      " port incompatible; send accepts ['validated']"], errors


def test_seed_workflows_match_design_topology():
    from apps.seed import (metrics_push_workflow, pathology_push_workflow,
                           pathology_publish_workflow)
    from workflow_core.dag import validate_dsl
    for wf in (pathology_push_workflow(), pathology_publish_workflow(),
               metrics_push_workflow()):
        assert validate_dsl(wf) == [], wf["workflow_key"]


def test_sqlserver_driver_unavailable_explicit():
    """未安装 pyodbc 时显式 DRIVER_UNAVAILABLE，不假成功。"""
    from connector_sdk import CapabilityError
    from sources.db_view.warehouse import SqlServerWarehouse, OracleWarehouse, render_sql
    try:
        import pyodbc  # noqa: F401
    except ImportError:
        with _raises(CapabilityError):
            SqlServerWarehouse()
    try:
        import oracledb  # noqa: F401
    except ImportError:
        with _raises(CapabilityError):
            OracleWarehouse()
    # 模板渲染：方言/参数化
    sql = render_sql("pis.slide_by_barcode", "sqlserver", limit=2,
                     database="PITAYA", schema="dbo")
    assert "TOP (2)" in sql and "[PITAYA].[dbo].[Pitaya_Slice]" in sql
    assert "qptmh = ?" in sql
    # 无方言变体的模板显式报错
    with _raises(CapabilityError):
        render_sql("his.patient_by_id", "sqlserver", limit=2)


class _raises:
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        import pytest
        self.cm = pytest.raises(self.exc)
        return self.cm.__enter__()

    def __exit__(self, *a):
        return self.cm.__exit__(*a)


def test_connector_capability_contract():
    """PIS/HIS 连接器能力契约（10 §2.1/§2.3）在合成仓储上逐条验证。"""
    from connector_sdk import SourceScope
    from sources.db_view import (MedrecHisConnector, PitayaPisConnector,
                                 SyntheticWarehouse)
    wh = SyntheticWarehouse()
    pis = PitayaPisConnector(wh)
    his = MedrecHisConnector(wh)
    ps = SourceScope(tenant_id="t", source_system_code="pis", database="PITAYA",
                     schema="dbo", namespace="ns")
    hs = SourceScope(tenant_id="t", source_system_code="his", database="",
                     schema="MEDREC", namespace="hns")
    # get_slide_by_barcode 0/1/N
    assert len(pis.get_slide_by_barcode(ps, "999999")["candidates"]) == 0
    assert len(pis.get_slide_by_barcode(ps, "900001")["candidates"]) == 1
    amb = pis.get_slide_by_barcode(ps, "990000")
    assert len(amb["candidates"]) == 2 and amb["truncated"]
    # 前导零
    z = pis.get_slide_by_barcode(ps, "090001")
    assert z["candidates"][0].barcode_raw == "090001"
    # get_case / list_slides / get_applications / get_reports
    case = pis.get_case(ps, "9000001")
    assert case.pathology_no == "9000001"
    assert case.application_time is None  # 权威申请时间字段未确认
    slides, _ = pis.list_slides(ps, "9000001")
    assert len(slides) >= 3
    apps = pis.get_applications(ps, "1000000001")
    assert apps[0].encounter_kind_raw == "住院"
    reports = pis.get_reports(ps, "0900001")
    assert any(r.source_key == "0900001-A1" for r in reports)
    # poll_changes 标记 INSERT_DISCOVERY_ONLY
    rows = pis.poll_changes(ps, "cases", {"since": "2026-01-01T00:00:00",
                                          "until": "2027-01-01T00:00:00"}, 50)
    assert any(r["source_key"] == "9000001" for r in rows)
    # reconcile_partition（R10：返回 (keys, 是否证明末页) 的keyset分页协议）
    keys, last_page = pis.reconcile_partition(ps, "0", "99999999", 200)
    assert "9000001" in keys
    assert last_page is True  # 5行<200 即短页证明
    # HIS get_patient / get_encounter（无visit_ref→返回全部，不选最新）
    pats = his.get_patient(hs, "P0001")
    assert len(pats) == 1 and pats[0].id_no_masked is None
    encs = his.get_encounter(hs, "P0001", None, "inpatient")
    assert len(encs) == 2  # F06：两次住院
    assert his.get_encounter(hs, "P0001", "2", "inpatient")[0].visit_id == "2"
    # get_source_dictionary 不内置 01→111
    assert his.get_source_dictionary("id_type", "v1") is None
    # 图像：JPG/URL 非WSI
    imgs = pis.get_images(ps, "0900001")
    assert imgs[0]["device_type"] == "显微镜"


def test_acceptance_cases_json_registered():
    """验收清单 JSON 可解析且 20 条与测试一一对应。"""
    with open(os.path.join(DESIGN_DIR, "first-hospital.acceptance-cases.json"),
              encoding="utf-8") as f:
        spec = json.load(f)
    assert spec["kind"] == "IMPLEMENTATION_ACCEPTANCE_SPEC_NOT_TEST_RESULTS"
    ids = [c["id"] for c in spec["cases"]]
    assert ids == [f"F{i:02d}" for i in range(1, 21)]
    test_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "integration", "test_first_hospital_cases.py")
    with open(test_file, encoding="utf-8") as f:
        body = f.read()
    for case_id in ids:
        assert f"def test_{case_id}" in body, f"{case_id} 缺少实现测试"
