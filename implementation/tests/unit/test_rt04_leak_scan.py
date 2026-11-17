"""RT-04 B5/B6：泄漏扫描器（实跑零泄漏 + 合成泄漏夹具可检出 + 报告不回显值）。"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from tools.leak_scan import extract_sentinels, run_scan  # noqa: E402


def _make_dirs(tmp_path, *, sample_value, leak_value=None, escaped=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    sdir = tmp_path / "samples"
    sdir.mkdir()
    (sdir / "18888调用机构医保测试报文.txt").write_text(
        json.dumps({"opter_name": sample_value, "input": {}}, ensure_ascii=False),
        encoding="utf-8")
    root = tmp_path / "scanroot"
    root.mkdir()
    if leak_value is not None:
        if escaped:
            # JSON 转义形（\\uXXXX）也要命中
            esc = json.dumps(leak_value, ensure_ascii=True)[1:-1]
            (root / "leak.json").write_text('{"a": "' + esc + '"}',
                                            encoding="utf-8")
        else:
            (root / "leak.py").write_text(
                f"# note X = {leak_value!r}\n", encoding="utf-8")
    return str(sdir), str(root)


def test_scanner_detects_plaintext_and_escaped(tmp_path):
    """已知合成泄漏夹具：原文与 JSON 转义形均检出；报告不含原值。"""
    val = "哨兵甲乙丙丁戊己"
    sdir, root = _make_dirs(tmp_path, sample_value=val, leak_value=val)
    rep = run_scan([sdir], [root])
    assert not rep["clean"] and rep["hit_files"] == 1
    assert rep["hits"][0]["file"].endswith("leak.py")
    assert rep["hits"][0]["hit_count"] >= 1
    # 报告不回显值
    assert val not in json.dumps(rep, ensure_ascii=False)

    # 转义形（\uXXXX）
    sdir2, root2 = _make_dirs(tmp_path / "x2", sample_value=val,
                              leak_value=val, escaped=True)
    rep2 = run_scan([sdir2], [root2])
    assert not rep2["clean"]
    assert val not in json.dumps(rep2, ensure_ascii=False)


def test_scanner_clean_tree_and_exclusions(tmp_path):
    """干净目录 → CLEAN；合成公共值/过短值显式排除并计数。"""
    val = "000000199001010000"          # 合成公共值（排除清单）
    sdir, root = _make_dirs(tmp_path / "c1", sample_value=val,
                            leak_value=val)
    rep = run_scan([sdir], [root])
    assert rep["clean"]                      # 排除清单值不告警
    assert rep["excluded"]["synthetic_common"]["count"] >= 1
    # 过短值排除计数
    sdir2 = tmp_path / "c2" / "samples"
    sdir2.mkdir(parents=True)
    (sdir2 / "1.txt").write_text(
        json.dumps({"tel": "12"}), encoding="utf-8")
    sent = extract_sentinels([str(sdir2)])
    assert sent and all(len(v) < 4 for vs in sent.values() for v in vs)
    rep2 = run_scan([str(sdir2)], [str(tmp_path / "c2")])
    assert rep2["excluded"]["short_values"] >= 1


def test_scanner_real_tree_zero_leaks():
    """实跑仓库默认范围（profiles/plugins/tests/packages/apps/tools/var）
    ——B5 验收：0 泄漏。"""
    base = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    ws = os.path.dirname(base)
    rep = run_scan(
        [os.path.join(ws, "数据上报", "2、医保电子病历上报",
                      "调用机构医保(1)", "调用机构医保"),
         os.path.join(ws, "数据上报", "2、医保电子病历上报",
                      "12601-12604调用机构医保测试报文示例")],
        [os.path.join(base, d) for d in
         ("profiles", "plugins", "tests", "packages", "apps", "tools",
          "var")])
    assert rep["clean"], rep["hits"][:5]
    assert rep["sentinel_values"] > 0
