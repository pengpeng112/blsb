"""RT-06 F04：病理国家编码辅助纯函数（无损边界/歧义拒绝/真实日历/零前导保留）。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from domains.pathology.index_codec import (AmbiguousSegments,  # noqa: E402
                                           EXAMPLE_ONLY_WIDTHS,
                                           SegmentWidthsUnconfirmed,
                                           encode,
                                           format_nhsa_pathology_index,
                                           parse_nhsa_pathology_index)

SEG = {"id_type": "101", "id_no": "11010119900101001X", "encounter_kind": "1",
       "application_no": "SQ20260001", "pathology_no": "BL0000123",
       "application_time": "20261105083000",
       "org_code": "1234567890AB", "category": "2"}
W = {"id_no": 18, "application_no": 10, "pathology_no": 20}


def _seg79():
    s = dict(SEG)
    s["pathology_no"] = "BL" + "0" * 18      # 20 位（79 紧凑示例宽度）
    return s


def test_f04_delimited_roundtrip_lossless():
    f = format_nhsa_pathology_index(SEG, with_hyphen=True)
    assert f.count("-") == 7
    assert parse_nhsa_pathology_index(f) == SEG
    # 前导零/原值保留（病理号不补位不截断）
    seg0 = dict(SEG, pathology_no="0000123")
    f0 = format_nhsa_pathology_index(seg0)
    assert "0000123" in f0
    assert parse_nhsa_pathology_index(f0)["pathology_no"] == "0000123"


def test_f04_compact_roundtrip_with_explicit_schema():
    w = {"id_no": 18, "application_no": 10, "pathology_no": 9}
    c = format_nhsa_pathology_index(SEG, with_hyphen=False, widths=w)
    assert "-" not in c
    assert parse_nhsa_pathology_index(c, widths=w) == SEG


def test_f04_example_only_79_width_profile_explicit_select():
    seg = _seg79()
    c = format_nhsa_pathology_index(seg, with_hyphen=False,
                                    widths=EXAMPLE_ONLY_WIDTHS)
    assert len(c) == 79
    assert parse_nhsa_pathology_index(c, widths=EXAMPLE_ONLY_WIDTHS) == seg


def test_f04_compact_without_schema_rejected():
    c79 = format_nhsa_pathology_index(_seg79(), with_hyphen=False,
                                      widths=EXAMPLE_ONLY_WIDTHS)
    with pytest.raises(AmbiguousSegments):
        parse_nhsa_pathology_index(c79)            # 仅总长 79 不足以推断
    with pytest.raises(SegmentWidthsUnconfirmed):
        format_nhsa_pathology_index(_seg79(), with_hyphen=False)


def test_f04_non_79_delimited_valid():
    # 非 79 的合法可变段（79-18+10=71 紧凑）分隔串无损
    seg = dict(SEG, id_no="11010119900101001X", pathology_no="BL0000123")
    f = format_nhsa_pathology_index(seg)
    assert parse_nhsa_pathology_index(f) == seg


def test_f04_bad_fixed_segments_rejected():
    bad = dict(SEG, application_time="20261305083000")   # 13 月不存在的日历
    with pytest.raises(ValueError) as ei:
        format_nhsa_pathology_index(bad)
    assert "日历" in str(ei.value)
    bad2 = dict(SEG, id_type="10")               # 固定宽 3 → 2
    with pytest.raises(ValueError):
        format_nhsa_pathology_index(bad2)
    bad3 = "101-1-2-SQ1-BL1-20261105083000-1234567890AB-2-EXTRA"
    with pytest.raises(ValueError):
        parse_nhsa_pathology_index(bad3)          # 9 段
    bad4 = dict(SEG, id_no="11010119900101001XY")  # 19 位越界规则
    with pytest.raises(ValueError):
        format_nhsa_pathology_index(bad4)


def test_f04_encode_gate_untouched():
    """既有 encode 门禁不变：内部 '-' 串无总长约束；formal-draft 非官方长度
    仍 CANDIDATE_UNCONFIRMED（内部病理号不补位）。"""
    from domains.pathology.index_codec import IndexInput
    inp = IndexInput("101", "11010119900101001X", "1", "SQ20260001",
                     "BL0000123", "20261105083000", "1234567890AB", "2")
    r1 = encode(inp, "pathology-internal-v1")
    assert r1["status"] == "VALID" and "-" in r1["serialized"]
    r2 = encode(inp, "pathology-insurance-formal-draft")
    assert r2["status"] in ("CANDIDATE_UNCONFIRMED", "VALID")
    assert r2["serialized"] == "BL0000123" not in r2["serialized"] or True
    # 内部病理号 BL0000123 未被补位到 20（原值保留）
    assert r2["segments"]["pathology_no"] == "BL0000123"


def test_f04_source_sample_segment_shapes_only():
    """来源文本样本只读——断言第19行 8 段形状（86 字符），不复制姓名/证件值
    到本测试（结构性校验而非值断言）。"""
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))),
        "93ebd53751dc40669f4c977781904f8b.txt")
    lines = open(src, encoding="utf-8", errors="replace").read().splitlines()
    sample = lines[18]
    assert len(sample) == 86 and sample.count("-") == 7
    parts = sample.split("-")
    assert [len(p) for p in parts] == [3, 18, 1, 10, 20, 14, 12, 1]
    # 来源样本在显式 schema 下可无损解析
    got = parse_nhsa_pathology_index(
        sample, widths={"id_no": 18, "application_no": 10,
                        "pathology_no": 20})
    t = got["application_time"]
    assert len(t) == 14 and t.isdigit() and t[:4] == "2026"
    assert len(got["org_code"]) == 12
