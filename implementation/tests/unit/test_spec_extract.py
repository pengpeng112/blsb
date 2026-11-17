"""LT-07 提取工具单测：最小 docx 夹具/交易归属/样例结构丢值/GBK。"""
from __future__ import annotations

import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from tools.spec_extract.docx_tables import (extract_tables,  # noqa: E402
                                            is_param_table,
                                            txn_transaction_list)
from tools.spec_extract.sample_shape import (extract_shapes,  # noqa: E402
                                             read_json_any_encoding,
                                             shape_of)

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "fixtures", "spec_extract")


@pytest.fixture(scope="module")
def minimal_docx(tmp_path_factory):
    """自造最小 docx：python-docx 生成（标题+接口清单表+11101 参数表）。"""
    import docx as docx_lib
    d = docx_lib.Document()
    d.add_heading("4 接口说明", level=1)
    d.add_heading("4.1 接口清单", level=2)
    t = d.add_table(rows=1, cols=5)
    for i, h in enumerate(["接口编号", "接口名称", "调用方式", "描述", "对应国家接口编码"]):
        t.rows[0].cells[i].text = h
    r = t.add_row()
    for i, v in enumerate(["11101", "患者基本信息上传", "定时/每天", "描述", "-"]):
        r.cells[i].text = v
    d.add_heading("5 交易【11101】患者基本信息上传", level=2)
    t2 = d.add_table(rows=1, cols=8)
    for i, h in enumerate(["序号", "参数代码", "参数名称", "参数类型", "参数长度",
                           "代码标识", "是否必填", "说明"]):
        t2.rows[0].cells[i].text = h
    for row in (["1", "patn_id", "患者ID", "字符型", "30", "", "Y", ""],
                ["2", "certno", "证件号码", "字符型", "50", "", "Y", "敏感"]):
        rr = t2.add_row()
        for i, v in enumerate(row):
            rr.cells[i].text = v
    path = str(tmp_path_factory.mktemp("docx") / "spec.docx")
    d.save(path)
    return path


def test_docx_tables_extraction_and_txn_list(minimal_docx):
    tables = extract_tables(minimal_docx)
    assert len(tables) == 2
    assert is_param_table(tables[1]["header"])
    txns = txn_transaction_list(tables)
    assert txns == [{"code": "11101", "name": "患者基本信息上传"}]
    # 参数表归属：heading_path 含交易号
    joined = "".join(tables[1]["heading_path"])
    assert "11101" in joined


def test_shape_of_drops_values():
    obj = {"a": 1, "b": {"c": "SECRET-VALUE-XYZ"}, "d": [{"e": "V2"}]}
    out = shape_of(obj)
    blob = json.dumps(out, ensure_ascii=False)
    assert "SECRET-VALUE-XYZ" not in blob and "V2" not in blob
    paths = [n["path"] for n in out]
    assert "b.c" in paths and "d[0].e" in paths


def test_gbk_sample_reading(tmp_path):
    p = tmp_path / "11101样例.txt"
    p.write_bytes(json.dumps({"info": "11101", "input": {"x": "测试中文值"}},
                             ensure_ascii=False).encode("gbk"))
    obj, enc = read_json_any_encoding(str(p))
    assert enc == "gbk" and obj["input"]["x"] == "测试中文值"
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe\x00garbage")
    obj2, err = read_json_any_encoding(str(bad))
    assert obj2 is None and err == "DECODE_OR_JSON_FAILED"


def test_extract_shapes_txn_mapping(tmp_path):
    (tmp_path / "11101调用机构医保测试报文.txt").write_text(
        json.dumps({"input": {"a": 1}}), encoding="utf-8")
    out = extract_shapes(str(tmp_path))
    assert "11101调用机构医保测试报文.txt" in out
    assert out["11101调用机构医保测试报文.txt"]["txn"] == "11101"


def test_real_profile_exists_and_no_sample_values():
    """生成产物存在且不含样例真实值（用合成白名单检查）。"""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "profiles", "medins_emr_sd")
    if not os.path.exists(os.path.join(root, "transactions.json")):
        pytest.skip("Profile 未生成（真实素材不在测试环境）")
    tx = json.load(open(os.path.join(root, "transactions.json"),
                        encoding="utf-8"))
    assert len(tx) >= 19
    prof = json.load(open(os.path.join(root, "profile.json"), encoding="utf-8"))
    assert prof["msgid_rule"]["confirmed"] is True
    assert prof["signature"]["confirmed"] is False
    assert prof["length_unit"] == "UNCONFIRMED"
    # 产物无真实机构/人名（合成值白名单）
    allowed = ("000000199001010000", "测试患者", "TEST-")
    import re
    for name in os.listdir(os.path.join(root, "samples")):
        text = open(os.path.join(root, "samples", name), encoding="utf-8").read()
        # 无 12+ 位数字串除合成证件号/时间
        for m in re.finditer(r"\d{12,}", text):
            s = m.group()
            assert s in allowed or s.startswith("2026") or s.startswith("TEST"), \
                f"{name} 含可疑长数字（脱敏检查）：{s[:4]}***"
