"""影像 CSV Profile 生成器（RT-06 F02 重修）：PACS_V2 字段清单 → revision 目录。

与 LT-09 旧版差异（任务书 F02）：
- 输出完整 revision 目录：profile.json + datasets.json + fields/<DS>.json×16 +
  samples/<DS>.json×16 + EXTRACTION_REPORT.md（旧版只写 fields）；
- 来源列全保留：dataset/label/field/type/length/requirement/notes/status/
  source_row/data_element——类型/长度/必填按来源映射，来源没写的约束保持
  SPEC_FIELD_CONSTRAINT_PENDING（不因旧生成器没用就丢弃）；
- status=EXTRACTED_NOT_APPROVED 原样进 profile 元数据，不升级为正式批准；
- 名称推断的 sensitive 标 inferred+依据（不凭含 date 就硬作正式阻断）；
- 真实数据集代码（APPLY_INFO/CHK_INFO 等大写）原样保留；中文名归一展示但
  保留原文 label_raw；asset_info 是内部投影概念，不冒充官方数据集；
- 合成样例全新生成、每个 0 errors；生成物可追溯到原件 SHA256；
- 公共校验器承认 source=spec_csv（mapping_engine 侧配套）。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from datetime import datetime, timezone

from .tree_profile import _write  # 复用 JSON 落盘（ensure_ascii=False, indent=1）

EXTRACTOR_VERSION = "spec_extract/2.1-csv"
SCHEMA_VERSION = 2

_TYPE_MAP = {"字符串": "string", "数字": "number",
             "日期": "date", "日期时间": "datetime"}
_REQ_ALWAYS = "必填"


def _sha256(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _sensitive_inferred(field: str, label: str) -> dict | None:
    """名称推断的敏感标记：标 inferred + 依据（正式敏感以契约审批为准）。"""
    f = (field or "").lower()
    l = label or ""
    for kw, why in (("name", "字段名含 name"),
                    ("tel", "字段名含 tel"), ("phone", "字段名含 phone"),
                    ("mobile", "字段名含 mobile"),
                    ("idcard", "字段名含 idcard"),
                    ("addr", "字段名含 addr")):
        if kw in f:
            return {"inferred": True, "basis": why}
    if "姓名" in l or "电话" in l or "地址" in l:
        return {"inferred": True, "basis": "中文名含姓名/电话/地址"}
    return None


def _norm_label(raw: str) -> str:
    """中文归一展示（压掉换行/多空白），原文保留在 label_raw。"""
    return re.sub(r"\s+", "", str(raw or ""))


def read_rows(csv_path: str) -> list[dict]:
    """CSV → 原样行列表（utf-8-sig；csv 标准解析，字段含换行正确处理）。"""
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def build(csv_path: str, out_dir: str,
          target_code: str = "medins_imaging_jn",
          revision: str = "r2-csv16") -> dict:
    rows = read_rows(csv_path)
    if len(rows) != 288:
        raise ValueError(f"来源 CSV 应为 288 字段行，实际 {len(rows)}")

    datasets: dict[str, dict] = {}
    fields_by_ds: dict[str, list[dict]] = {}
    pending = 0
    conditional = 0
    for r in rows:
        ds = (r.get("dataset") or "").strip()
        code = (r.get("field") or "").strip()
        if not ds or not code:
            raise ValueError(f"来源行缺 dataset/field：{r}")
        typ_raw = (r.get("type") or "").strip()
        length_raw = (r.get("length") or "").strip()
        req_raw = (r.get("requirement") or "").strip()
        label_raw = (r.get("label") or "").strip()
        status = (r.get("status") or "").strip()
        typ = _TYPE_MAP.get(typ_raw, "string")
        notes: list[str] = []
        if not length_raw:
            notes.append("SPEC_FIELD_CONSTRAINT_PENDING:来源未给长度")
            pending += 1
        if req_raw == "":
            notes.append("SPEC_FIELD_CONSTRAINT_PENDING:来源未给必填性")
            pending += 1
            required = False
        elif req_raw == _REQ_ALWAYS:
            required = True
        else:
            required = True
            conditional += 1
            notes.append(f"conditional_required:{req_raw}")
        node: dict = {
            "code": code,
            "name": _norm_label(label_raw),
            "label_raw": label_raw,
            "data_element": (r.get("data_element") or "").strip(),
            "type": typ,
            "type_raw": typ_raw,
            "max_len": int(length_raw) if length_raw.isdigit() else None,
            "dict": None,
            "required": required,
            "source": "spec_csv",
            "status": status,
            "source_row": int(r.get("source_row") or 0),
            "source_table_index": (r.get("source_table_index") or "").strip(),
            "notes": notes,
        }
        sens = _sensitive_inferred(code, label_raw)
        if sens:
            node["sensitive"] = True
            node["sensitive_inferred"] = sens
        fields_by_ds.setdefault(ds, []).append(node)
        datasets.setdefault(ds, {"field_count": 0})
        datasets[ds]["field_count"] += 1
    if len(datasets) != 16:
        raise ValueError(f"数据集应 16 个，实际 {len(datasets)}")

    os.makedirs(out_dir, exist_ok=True)
    # datasets.json：清单（来源无数据集中文名列 → name 置空并标 pending，不编造）
    ds_list = [{"code": ds, "name": "", "field_count": meta["field_count"],
                "name_pending": "SPEC_DATASET_NAME_UNPROVIDED"}
               for ds, meta in datasets.items()]
    _write(out_dir, "datasets.json", {
        "schema_version": SCHEMA_VERSION, "datasets": ds_list,
        "count": len(ds_list)})

    total_fields = 0
    for ds, nodes in fields_by_ds.items():
        total_fields += len(nodes)
        contract = {"code": ds, "schema_version": SCHEMA_VERSION,
                    "root": "input", "length_unit": "UNCONFIRMED",
                    "source": "spec_csv", "nodes": nodes}
        _write(out_dir, f"fields/{ds}.json", contract)
        sample = {"input": {}}
        for n in nodes:
            if not n["required"]:
                continue
            v: object
            if n["type"] == "number":
                v = 1
            elif n["type"] == "date":
                v = "2026-02-01"
            elif n["type"] == "datetime":
                v = "2026-02-01 08:00:00"
            else:
                v = f"合成{n['code'][:10]}"
                if n.get("max_len") and len(v) > n["max_len"]:
                    v = v[:n["max_len"]]
            sample["input"][n["code"]] = v
        _write(out_dir, f"samples/{ds}.json", sample)

    profile = {
        "target_code": target_code,
        "name": "济南医保影像云索引",
        "revision": revision,
        "schema_version": SCHEMA_VERSION,
        "status": "EXTRACTED_NOT_APPROVED",     # 来源状态原样——不升级批准
        "source_status": sorted({r.get("status", "") for r in rows}),
        "source": [{"path": os.path.basename(csv_path),
                    "sha256": _sha256(csv_path)}],
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "extractor_version": EXTRACTOR_VERSION,
        "datasets_count": len(ds_list),
        "fields_count": total_fields,
        "constraints": {
            "type_present": sum(1 for r in rows if (r.get("type") or "").strip()),
            "length_present": sum(1 for r in rows if (r.get("length") or "").strip()),
            "requirement_present": sum(1 for r in rows
                                       if (r.get("requirement") or "").strip()),
            "pending_total": pending,
            "conditional_required": conditional,
        },
        "asset_info_note": "asset_info 为内部投影概念（LT-09 接入指南），"
                           "不在本 CSV 16 数据集清单中——不新增第17个官方数据集",
        "length_unit": "UNCONFIRMED",
    }
    _write(out_dir, "profile.json", profile)

    report = [
        "# RT-06 F02 提取报告（medins_imaging_jn r2-csv16）",
        "",
        f"- 生成时间：{profile['extracted_at']}",
        f"- 来源原件：{os.path.basename(csv_path)} (sha256="
        f"{_sha256(csv_path)[:16]}…)",
        f"- 数据集 **{len(ds_list)}**、字段 **{total_fields}**（288 全行校验通过）",
        f"- 约束覆盖：type {profile['constraints']['type_present']}/288、"
        f"length {profile['constraints']['length_present']}/288、"
        f"requirement {profile['constraints']['requirement_present']}/288",
        f"- SPEC_FIELD_CONSTRAINT_PENDING 合计 **{pending}**（长度/必填缺失，"
        "逐字段见 fields/*.json notes）",
        f"- 条件必填（放射/医保结算等原文保留）{conditional} 条",
        "- status=EXTRACTED_NOT_APPROVED 全量保留——不升级为正式批准",
        "- asset_info 为内部投影概念，不在 16 数据集清单（不新增第17个官方数据集）",
        "- 数据集代码为规范大写原样（APPLY_INFO/CHK_INFO/…）；中文名归一展示但"
        "原文保留在 label_raw",
    ]
    rep = os.path.join(out_dir, "EXTRACTION_REPORT.md")
    with open(rep, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(report) + "\n")
    return {"profile": profile, "datasets": ds_list,
            "fields_by_ds": {k: len(v) for k, v in fields_by_ds.items()}}


def main(argv=None) -> int:
    import argparse
    base = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))                      # implementation/
    ap = argparse.ArgumentParser(description="影像 CSV → 树形 Profile revision")
    ap.add_argument("--csv", default=os.path.join(
        base, "..", "设计交付v0.4", "契约与示例",
        "PACS_V2_字段清单_原样提取.csv"))
    ap.add_argument("--out", default=os.path.join(
        base, "profiles", "medins_imaging_jn", "revisions", "r2-csv16"))
    args = ap.parse_args(argv)
    stats = build(args.csv, args.out)
    print(json.dumps({"datasets": stats["profile"]["datasets_count"],
                      "fields": stats["profile"]["fields_count"],
                      "pending": stats["profile"]["constraints"]["pending_total"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
