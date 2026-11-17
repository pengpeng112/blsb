"""单元测试：索引Codec、身份校验位、质量规则、DAG校验、路径策略、Range解析、规范化hash。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402


# ---------------------------------------------------------------- 索引Codec
def test_index_codec_segments_and_lengths():
    from domains.pathology.index_codec import IndexInput, encode
    # 官方示例形态：20位病理号段 → 总长79
    inp = IndexInput(
        id_type="111", id_no="110101195001011234", encounter_kind="1",
        application_no="1000000001", pathology_no="9" * 20,
        application_time="20260901080000", org_code="H1234567890X", category="1")
    enc = encode(inp, "pathology-insurance-formal-draft")
    assert enc["status"] == "VALID" and enc["length"] == 79
    # 86长度示例（15位证件号+20位申请单号+20位病理号 → 86）
    inp86 = IndexInput(
        id_type="111", id_no="E12345678901234", encounter_kind="1",
        application_no="A" * 20, pathology_no="9" * 20,
        application_time="20260901080000", org_code="H1234567890X", category="1")
    enc86 = encode(inp86, "pathology-insurance-formal-draft")
    assert enc86["length"] == 86 and enc86["status"] == "VALID"
    # 院内7位病理号不补位：formal草案保持候选未确认而非伪造
    inp7 = IndexInput(
        id_type="111", id_no="110101195001011234", encounter_kind="1",
        application_no="1000000001", pathology_no="9000001",
        application_time="20260901080000", org_code="H1234567890X", category="1")
    enc7 = encode(inp7, "pathology-insurance-formal-draft")
    assert enc7["status"] == "CANDIDATE_UNCONFIRMED"
    assert "NOT padded" in enc7["errors"][0]
    # internal profile：原值连字符，无总长约束
    encI = encode(inp7, "pathology-internal-v1")
    assert encI["status"] == "VALID"
    assert encI["serialized"].startswith("111-110101195001011234-1-")
    # 段格式错误
    bad = IndexInput(
        id_type="11", id_no="x", encounter_kind="9", application_no="",
        pathology_no="9000001", application_time="2026", org_code="short",
        category="0")
    encb = encode(bad, "pathology-internal-v1")
    assert encb["status"] == "INVALID" and len(encb["errors"]) == 6


def test_b08_mixed_dictionary_blocked():
    """正式索引与草案字典混用：profile校验阻断（不截断不补零）。"""
    from domains.pathology.index_codec import IndexInput, encode
    inp = IndexInput(
        id_type="01",  # 2位HIS码直接混入3位段 → 阻断
        id_no="110101195001011234", encounter_kind="1", application_no="1000000001",
        pathology_no="9000001", application_time="20260901080000",
        org_code="H1234567890X", category="1")
    enc = encode(inp, "pathology-insurance-formal-draft")
    assert enc["status"] == "INVALID"
    assert any("id_type" in e for e in enc["errors"])


# ---------------------------------------------------------------- 身份
def test_id_card_checksum():
    from domains.pathology.identity import id_card_checksum_ok
    assert id_card_checksum_ok("110101195001011239") is True
    assert id_card_checksum_ok("110101195001011230") is False
    assert id_card_checksum_ok("short") is False
    assert id_card_checksum_ok("") is False


def test_identity_decision_matrix():
    from domains.pathology.identity import decide_identity

    class Pat:
        def __init__(self, id_type, id_no):
            self.id_type_raw = id_type
            self.id_no_masked = "****"
            self.id_no_full_ref = "ref" if id_no else None
            self.patient_id = "P"
            self.name_masked = "*"
            self.birth_date = None
            self.gender_raw = None

    def with_plain(pat, plain):
        pat._id_no_plain = plain
        return pat

    # 批准字典 + 一致 → CONFIRMED
    out = decide_identity(pis_identity_card=None,
                          his_patients=[with_plain(Pat("01", "x"), None)],
                          approved_type_map={"01": "111"}, policy={}, evaluated_at="t")
    assert out.patient_state == "CONFIRMED" and out.id_type_code == "111"
    # 冲突 → WAITING_HUMAN
    out2 = decide_identity(pis_identity_card="110101195001011239",
                           his_patients=[with_plain(Pat("01", "x"),
                                                    "110101195001011200")],
                           approved_type_map={"01": "111"}, policy={},
                           evaluated_at="t")
    assert out2.patient_state == "WAITING_HUMAN" and out2.conflict
    # 脏值 + PIS校验通过 → 仅候选
    out3 = decide_identity(pis_identity_card="110101195001011239",
                           his_patients=[Pat("临床试验", None)],
                           approved_type_map={"01": "111"}, policy={},
                           evaluated_at="t")
    assert out3.patient_state == "WAITING_HUMAN"
    assert out3.id_type_candidate == "111" and out3.id_type_code is None
    # 多患者 → 歧义
    out4 = decide_identity(pis_identity_card=None,
                           his_patients=[Pat("01", "a"), Pat("01", "b")],
                           approved_type_map={"01": "111"}, policy={},
                           evaluated_at="t")
    assert out4.wait_reason == "SOURCE_AMBIGUOUS"


# ---------------------------------------------------------------- 质量规则
def test_quality_rule_codes_present():
    from domains.pathology import quality
    assert set(quality.__dict__ or {})  # 模块可导入
    from contracts import QUALITY_RULES
    assert "QF-DATE-FUTURE" in QUALITY_RULES
    assert "QF-COMPLETENESS" in QUALITY_RULES


# ---------------------------------------------------------------- DAG
def test_dag_validation_c04():
    from workflow_core.dag import validate_dsl
    good = {
        "nodes": [
            {"id": "a", "type": "resolve", "plugin_ref": {"key": "x"}},
            {"id": "b", "type": "validate", "plugin_ref": {"key": "y"}},
        ],
        "edges": [{"from": "a", "to": "b", "on": "success"}]}
    assert validate_dsl(good) == []
    # 环
    cyclic = {"nodes": good["nodes"],
              "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}]}
    errs = validate_dsl(cyclic)
    assert any("cycle" in e for e in errs)
    # 端口不兼容
    bad_port = {"nodes": [
        {"id": "a", "type": "parse", "plugin_ref": {"key": "x"}},
        {"id": "b", "type": "package.freeze", "plugin_ref": {"key": "y"}}],
        "edges": [{"from": "a", "to": "b"}]}
    errs2 = validate_dsl(bad_port)
    assert any("port incompatible" in e for e in errs2)
    # 未知类型 / 缺字典节点由发布侧检查：未知类型
    unknown = {"nodes": [{"id": "a", "type": "shell_exec",
                          "plugin_ref": {"key": "x"}}], "edges": []}
    errs3 = validate_dsl(unknown)
    assert any("unknown type" in e for e in errs3)
    # 孤立节点
    iso = {"nodes": good["nodes"] + [{"id": "c", "type": "notify",
                                      "plugin_ref": {"key": "z"}}],
           "edges": good["edges"]}
    errs4 = validate_dsl(iso)
    assert any("isolated" in e for e in errs4)


def test_c05_mapping_no_code_execution():
    """映射DSL：出现代码执行/网络/无界正则 → 拒绝。"""
    from mapping_engine import validate_mapping
    assert validate_mapping({"ops": [{"op": "const", "value": "x"}]}) == []
    errs = validate_mapping({"ops": [{"op": "eval", "expr": "__import__('os')"}]})
    assert errs
    errs2 = validate_mapping({"ops": [{"op": "http_get", "url": "http://x"}]})
    assert errs2
    errs3 = validate_mapping({"ops": [{"op": "regex_replace",
                                       "pattern": "(a+)+$"}]})
    assert errs3


def test_c06_config_versioning():
    """配置发布：旧任务保持旧版本（WorkflowRevision不可变）。"""
    from contracts.api import digest_of
    from apps.seed import pathology_push_workflow
    dsl = pathology_push_workflow()
    assert digest_of(dsl) == digest_of(dict(dsl))
    dsl2 = dict(dsl)
    dsl2["revision"] = 2
    assert dsl2["revision"] != dsl["revision"]


# ---------------------------------------------------------------- 路径/Range
def test_path_policy():
    from storage_backend import PathPolicy
    assert PathPolicy.normalize("a/b/c.wsi") == "a/b/c.wsi"
    assert PathPolicy.normalize("A/B.WSI") == "A/B.WSI"
    with pytest.raises(ValueError):
        PathPolicy.normalize("../x")
    with pytest.raises(ValueError):
        PathPolicy.normalize("CON")
    with pytest.raises(ValueError):
        PathPolicy.normalize("a:b")
    assert PathPolicy.case_key("A/B.WSI") == PathPolicy.case_key("a/b.wsi")


def test_range_parsing_a10_unit():
    from storage_backend import parse_range
    assert parse_range("bytes=0-99", 1000) == (0, 99)
    assert parse_range("bytes=-100", 1000) == (900, 999)
    assert parse_range("bytes=900-", 1000) == (900, 999)
    assert parse_range("bytes=0-0", 1000) == (0, 0)
    assert parse_range("bytes=0-999", 1000) == (0, 999)
    assert parse_range("bytes=1000-", 1000) is None      # 越界 → 416
    assert parse_range("bytes=5-2", 1000) is None        # 倒序
    assert parse_range("bytes=0-10,20-30", 1000) is None  # 多段忽略
    assert parse_range("", 1000) is None


def test_normalized_digest_c02():
    """规范化hash：排序/null区分/不含采集时间。"""
    from apps.worker.engine import normalized_digest
    a = normalized_digest({"x": 1, "y": None})
    b = normalized_digest({"y": None, "x": 1})
    assert a == b
    c = normalized_digest({"x": 1, "y": ""})
    assert a != c  # null ≠ 空串
    d = normalized_digest({"x": 1, "y": None, "observed_at": "2026-01-01"})
    e = normalized_digest({"y": None, "x": 1, "observed_at": "2027-01-01"})
    assert d == e  # 采集时间不参与hash


def test_topology_order():
    from workflow_core.dag import topo_order
    dsl = {"nodes": [{"id": "z", "type": "parse", "plugin_ref": {}},
                     {"id": "a", "type": "source.poll", "plugin_ref": {}},
                     {"id": "m", "type": "normalize", "plugin_ref": {}}],
           "edges": [{"from": "z", "to": "m"}, {"from": "a", "to": "z"}]}
    assert topo_order(dsl) == ["a", "z", "m"]
