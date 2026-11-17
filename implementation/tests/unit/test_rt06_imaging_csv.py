"""RT-06 F02：影像 CSV 契约（16 数据集/288 字段）新 revision + 校验器 spec_csv。

验收（任务书 §3）：288 行 type 全有/285 length/214 requirement；16 全新合成样例
每个 0 errors；EXTRACTED_NOT_APPROVED 不升级；SPEC_FIELD_CONSTRAINT_PENDING 标记；
删必填/超长/类型错反例证明校验规则生效（不是"0错误=没校验"）。
"""
from __future__ import annotations

import json
import os

import pytest

from tools.spec_extract.csv_profile import build, read_rows

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CSV = os.path.join(BASE, "..", "设计交付v0.4", "契约与示例",
                   "PACS_V2_字段清单_原样提取.csv")
REV = os.path.join(BASE, "profiles", "medins_imaging_jn", "revisions",
                   "r2-csv16")
from mapping_engine.contract_validator import validate_message  # noqa: E402
from plugins.domains.medins_emr.profile_store import load_json  # noqa: E402


def _rev_load(rel):
    return json.load(open(os.path.join(REV, rel), encoding="utf-8"))


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out = tmp_path_factory.mktemp("imaging_rev")
    stats = build(CSV, str(out))
    return stats, str(out)


def test_f02_source_counts_and_dataset_list(generated):
    rows = read_rows(CSV)
    assert len(rows) == 288
    assert sum(1 for r in rows if (r.get("type") or "").strip()) == 288
    assert sum(1 for r in rows if (r.get("length") or "").strip()) == 285
    assert sum(1 for r in rows
               if (r.get("requirement") or "").strip()) == 214
    stats, out = generated
    assert stats["profile"]["datasets_count"] == 16
    assert stats["profile"]["fields_count"] == 288
    ds = _rev_load("datasets.json") if os.path.exists(
        os.path.join(REV, "datasets.json")) else \
        json.load(open(os.path.join(out, "datasets.json"), encoding="utf-8"))
    codes = [d["code"] for d in ds["datasets"]]
    assert codes == ["APPLY_INFO", "CHK_INFO", "SERIES_INFO", "IMAGE_INFO",
                     "PACS_HOST_INFO", "RIS_HOST_INFO", "RIS_PACS", "RPT_INFO",
                     "RPT_INFO_DETAILS", "RPT_PATH_INFO", "RPT_IMAGE",
                     "SETL_INFO", "SETL_DETAIL_INFO", "APPLY_ITEM_INFO",
                     "DOC_INFO", "BOOK_INFO"]      # 规范大写原样
    assert "asset_info" not in codes              # 内部投影概念不混入


def test_f02_constraints_pending_and_status(generated):
    stats, out = generated
    prof = json.load(open(os.path.join(out, "profile.json"),
                          encoding="utf-8"))
    assert prof["status"] == "EXTRACTED_NOT_APPROVED"   # 不升级批准
    assert prof["constraints"]["length_present"] == 285
    assert prof["constraints"]["requirement_present"] == 214
    assert prof["constraints"]["pending_total"] > 0     # 缺失约束显式标记
    # 逐字段：3 个无长度字段带 PENDING 标记；74 个无必填字段同
    pend_len = pend_req = cond = 0
    for fn in os.listdir(os.path.join(out, "fields")):
        c = json.load(open(os.path.join(out, "fields", fn),
                           encoding="utf-8"))
        for n in c["nodes"]:
            tags = " ".join(n.get("notes") or [])
            if "来源未给长度" in tags:
                pend_len += 1
            if "来源未给必填性" in tags:
                pend_req += 1
            if any(t.startswith("conditional_required:") for t in
                   (n.get("notes") or [])):
                cond += 1
    assert pend_len == 3 and pend_req == 74
    assert cond == 13                                   # 条件必填合计
    # 敏感推断标 inferred（不凭含 date 硬作阻断）
    for fn in os.listdir(os.path.join(out, "fields")):
        c = json.load(open(os.path.join(out, "fields", fn),
                           encoding="utf-8"))
        for n in c["nodes"]:
            if n.get("sensitive"):
                assert n["sensitive_inferred"]["inferred"] is True


def test_f02_samples_zero_errors_parametrized():
    rev_files = sorted(os.listdir(os.path.join(REV, "samples")))
    assert len(rev_files) == 16
    for fn in rev_files:
        code = fn[:-5]
        contract = _rev_load(f"fields/{fn}")
        sample = _rev_load(f"samples/{fn}")
        assert sample["input"], code
        errs = validate_message(sample, contract, None, {})
        hard = [e for e in errs if e["level"] == "error"]
        assert not hard, (code, hard[:3])


def test_f02_reverse_cases_prove_rules_active():
    contract = _rev_load("fields/APPLY_INFO.json")
    sample = _rev_load("samples/APPLY_INFO.json")
    # 删必填 → REQUIRED
    bad = {"input": dict(sample["input"])}
    first_req = [n for n in contract["nodes"] if n["required"]][0]["code"]
    del bad["input"][first_req]
    errs = validate_message(bad, contract, None, {})
    assert any(e["code"] == "REQUIRED" and e["path"].endswith(first_req)
               for e in errs)
    # 超长 → MAX_LEN
    node = [n for n in contract["nodes"]
            if n.get("max_len") and n["type"] == "string"][0]
    over = {"input": dict(sample["input"]),
            }
    over["input"][node["code"]] = "x" * (node["max_len"] + 1)
    errs2 = validate_message(over, contract, None, {})
    assert any(e["code"] == "MAX_LEN" for e in errs2)
    # 类型错（number 字段给字符串）→ TYPE
    num = [n for n in contract["nodes"] if n["type"] == "number"]
    if num:
        bad_t = {"input": dict(sample["input"])}
        bad_t["input"][num[0]["code"]] = "不是数字"
        errs3 = validate_message(bad_t, contract, None, {})
        assert any(e["code"] == "TYPE" for e in errs3)


def test_f02_no_real_sample_values_in_revision():
    """来源 CSV 的 label/data_element 可进契约（结构描述），但样例值必须全新
    合成——检查样例中不出现 CSV 的任何 label/data_element 值。"""
    rows = read_rows(CSV)
    sentinels = {r["label"] for r in rows if len(r.get("label", "")) >= 4}
    hits = []
    for fn in os.listdir(os.path.join(REV, "samples")):
        blob = open(os.path.join(REV, "samples", fn), encoding="utf-8").read()
        for st in sentinels:
            if st in blob:
                hits.append((fn, st))
    assert not hits, hits[:5]


def test_f02_traceability_and_report():
    prof = _rev_load("profile.json")
    assert len(prof["source"][0]["sha256"]) == 64
    rows = read_rows(CSV)
    import hashlib
    assert prof["source"][0]["sha256"] == hashlib.sha256(
        open(CSV, "rb").read()).hexdigest()
    report = open(os.path.join(REV, "EXTRACTION_REPORT.md"),
                  encoding="utf-8").read()
    assert "EXTRACTED_NOT_APPROVED" in report
    assert "asset_info" in report
    assert "SPEC_FIELD_CONSTRAINT_PENDING" in report
