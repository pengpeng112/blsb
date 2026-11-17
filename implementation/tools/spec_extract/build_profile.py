"""合成 Profile 生成器（LT-07）：docx 字段表为主 + 样例结构为辅 → Profile 目录。

红线（R09/任务书 CAUTION）：
- 样例只取结构（shape），**不取值**；合成样例值全部重新生成（证件号
  000000199001010000、姓名"测试患者"、日期 2026-01-01 等）；
- 原件 sha256 记入 profile.json（派生物可追溯）；
- 规范未说明的（长度单位/签名/msgid 特例）显式 UNCONFIRMED，不猜。
"""
from __future__ import annotations

import hashlib
import json
import os
import re

from .docx_tables import (TXN_RE, extract_tables, is_param_table,
                          txn_of_heading, txn_transaction_list)
from .sample_shape import extract_shapes

EXTRACTOR_VERSION = "spec_extract/1.0"

SENSITIVE_KEYS = ("certno", "patn_name", "name", "tel", "mobile", "phone",
                  "addr", "address", "id_card", "opter_name")


def _sha256(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _norm_len(raw: str) -> int | None:
    m = re.search(r"\d+", str(raw or ""))
    return int(m.group()) if m else None


def _required(raw: str) -> bool:
    return str(raw or "").strip().upper() in ("Y", "是", "必填", "TRUE")


def _sensitive(code: str, name: str) -> bool:
    c = str(code or "").lower()
    n = str(name or "")
    if "certno" in c or "idcard" in c or "证件" in n:
        return True
    if c.endswith("name") or "姓名" in n or "patn_name" in c:
        return True
    if any(k in c for k in ("tel", "mobile", "phone")) or "电话" in n:
        return True
    if "addr" in c or "地址" in n:
        return True
    return False


def _type_of(raw: str) -> str:
    t = str(raw or "")
    if "数值" in t or "数值型" in t:
        return "number"
    if "日期" in t or "时间型" in t:
        return "datetime"
    if "日期型" in t:
        return "date"
    return "string"


def _synth_value(node: dict, dicts: dict) -> object:
    """合成值：类型/字典/敏感语义感知 + max_len 截断（保证可通过自身契约）。"""
    code = node.get("path", "").split(".")[-1].lower()
    name = node.get("name", "")
    t = node.get("type")
    ml = node.get("max_len")
    if t == "number":
        return 0
    if t == "date":
        return "2026-01-01"
    if t == "datetime":
        return "2026-01-01 00:00:00"
    if node.get("dict") and node["dict"] in dicts and dicts[node["dict"]]:
        first = sorted(dicts[node["dict"]]["values"])[0]
        return first      # 明确字典的合成示例（标注 dict 来源，非业务默认值）
    raw: str
    if "certno" in code or "证件" in name:
        raw = "000000199001010000"
    elif "name" in code or "姓名" in name:
        raw = "测试患者"
    elif "time" in code or "date" in code:
        raw = "2026-01-01 00:00:00"
    elif t == "datetime":
        raw = "2026-01-01 00:00:00"
    else:
        raw = f"测试{code[:6]}" if code else "测试值"
    if ml and len(raw) > int(ml):
        raw = raw[:int(ml)]                # 契约内合成（长度待确认口径下演示用）
    return raw


def _flatten_params(rows: list[list[str]], header: list[str]) -> list[dict]:
    """参数表行 → 字段节点（顶层；层级由样例结构补充）。"""
    col = {k: i for i, k in enumerate(
        ["seq", "code", "name", "type", "len", "dict", "required", "desc"])}
    # 适配两种表头：参数代码/数据元标识
    def idx_of(*names):
        for n in names:
            for i, h in enumerate(header):
                if n in h:
                    return i
        return None
    ci = idx_of("参数代码", "数据元标识", "字段")
    ni = idx_of("参数名称", "数据元名称", "名称")
    ti = idx_of("参数类型", "类型")
    li = idx_of("参数长度", "长度")
    di = idx_of("代码标识", "代码")
    ri = idx_of("是否必填", "是否非空")
    si = idx_of("说明", "备注")
    _ = col
    out = []
    for r in rows:
        if ci is None or len(r) <= ci or not r[ci]:
            continue
        code = r[ci] if ci is not None else ""
        name = r[ni] if ni is not None and ni < len(r) else ""
        node = {
            "code": code, "name": name,
            "type": _type_of(r[ti] if ti is not None and ti < len(r) else ""),
            "max_len": _norm_len(r[li] if li is not None and li < len(r) else ""),
            "dict": (r[di].strip() or None)
            if di is not None and di < len(r) else None,
            "required": _required(r[ri] if ri is not None and ri < len(r) else ""),
            "sensitive": _sensitive(code, name),
            "source": "spec",
        }
        out.append(node)
    return out


def build(spec_path: str, sample_dirs: list[str], out_dir: str,
          target_code: str = "medins_emr_sd") -> dict:
    tables = extract_tables(spec_path)
    txns = txn_transaction_list(tables)
    txn_names = {t["code"]: t["name"] for t in txns}

    # 交易参数表归属：标题路径中最近的交易号
    fields_by_txn: dict[str, list[dict]] = {}
    unassigned: list[int] = []
    envelope_rows: list[list[str]] = []
    envelope_header: list[str] = []
    for t in tables:
        if not is_param_table(t["header"]):
            continue
        txn = None
        for h in reversed(t["heading_path"]):
            txn = txn_of_heading(h)
            if txn:
                break
        if txn is None:
            # 报文头表（heading 含"公共"/"报文头"/"数据元"）单独收集
            joined = "".join(t["heading_path"])
            if "报文" in joined or "公共" in joined or "数据元" in joined:
                if not envelope_rows:
                    envelope_header = t["header"]
                    envelope_rows = t["rows"]
                continue
            unassigned.append(t["table_index"])
            continue
        fields_by_txn.setdefault(txn, []).extend(
            _flatten_params(t["rows"], t["header"]))

    # 样例结构
    shapes: dict[str, dict] = {}
    for d in sample_dirs:
        shapes.update(extract_shapes(d))
    shapes_by_txn: dict[str, dict] = {}
    for fname, e in shapes.items():
        if e.get("txn") and e.get("shape") is not None:
            shapes_by_txn.setdefault(e["txn"], e)

    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "fields"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "samples"), exist_ok=True)

    # 公共报文头契约
    envelope_nodes = _flatten_params(envelope_rows, envelope_header) \
        if envelope_rows else []
    # 规范矛盾待核：info 长度标注 4 但交易编号为 5 位（清单表）——置 None 记 notes
    for n in envelope_nodes:
        if n["code"] == "info" and n.get("max_len") == 4:
            n["max_len"] = None
            n["notes"] = "规范长度4与交易号5位矛盾——长度待医保确认"
    envelope = {
        "code": "_envelope",
        "length_unit": "UNCONFIRMED",     # 规范未说明字符/字节——显式待确认
        "nodes": envelope_nodes,
    }
    with open(os.path.join(out_dir, "fields", "_envelope.json"), "w",
              encoding="utf-8", newline="\n") as fh:
        json.dump(envelope, fh, ensure_ascii=False, indent=1)

    # 字典（规范中能提取的明确代码表：table5 形态 STATUS 表等——本版仅内建
    # 已证实的；其余标注待核）
    dicts: dict[str, dict] = {}
    with open(os.path.join(out_dir, "dicts", "opter_type.json"), "w",
              encoding="utf-8", newline="\n") as fh:
        json.dump({"code": "opter_type", "name": "经办人类别",
                   "values": {"1": "经办人", "2": "自助终端", "3": "移动终端"},
                   "source": "spec:公共报文头表第11行"},
                  fh, ensure_ascii=False, indent=1)

    txn_list = []
    covered = set(fields_by_txn) | set(shapes_by_txn)
    for t in txns:
        code = t["code"]
        # 字段契约：spec 为主；样例结构补 sample_only 路径
        nodes = [dict(n) for n in fields_by_txn.get(code, [])]
        shape = shapes_by_txn.get(code)
        sample_shape_paths = set()
        if shape:
            for s in shape["shape"]:
                p = s["path"]
                if p in ("$", ) or p.endswith("]"):
                    continue
                sample_shape_paths.add(p)
        spec_codes = {n["code"] for n in nodes}
        for p in sorted(sample_shape_paths):
            top = p.split(".")[0]
            if top not in spec_codes and p not in spec_codes:
                nodes.append({"code": p, "name": "", "type": "string",
                              "max_len": None, "dict": None, "required": False,
                              "sensitive": _sensitive(p, ""),
                              "source": "sample_only"})
        with open(os.path.join(out_dir, "fields", f"{code}.json"), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump({"code": code,
                       "envelope_ref": "fields/_envelope.json",
                       "root": "input",
                       "length_unit": "UNCONFIRMED",
                       "nodes": nodes}, fh, ensure_ascii=False, indent=1)
        # 合成样例（envelope 合成 + input 顶层字段合成）
        sample_env = {}
        for n in envelope_nodes:
            if n["required"] or n["code"] in ("info", "msgid", "inf_time"):
                sample_env[n["code"]] = _synth_value(
                    {**n, "path": n["code"]}, dicts)
        sample_env["info"] = code
        sample_env["input"] = {n["code"]: _synth_value(
            {**n, "path": n["code"]}, dicts)
            for n in nodes if n["source"] == "spec" and n["required"]}
        sample_env["_synthetic"] = True       # 明显合成标记
        with open(os.path.join(out_dir, "samples", f"{code}.json"), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump(sample_env, fh, ensure_ascii=False, indent=1)
        txn_list.append({"code": code, "name": t["name"],
                         "direction": "upload", "has_fields": code in fields_by_txn,
                         "has_sample_shape": code in shapes_by_txn,
                         "fields_ref": f"fields/{code}.json",
                         "sample_ref": f"samples/{code}.json",
                         "notes": ""})

    with open(os.path.join(out_dir, "transactions.json"), "w",
              encoding="utf-8", newline="\n") as fh:
        json.dump(txn_list, fh, ensure_ascii=False, indent=1)

    profile = {
        "target_code": target_code,
        "name": "山东医保电子病历上报",
        "spec_version": "V2.0",
        "spec_source": [{"path": os.path.basename(spec_path),
                         "sha256": _sha256(spec_path)}],
        "extracted_at": __import__("core_db.engine", fromlist=["utcnow"]) \
            .utcnow().isoformat() if False else None,
        "extractor_version": EXTRACTOR_VERSION,
        "status": "DRAFT",
        "msgid_rule": {
            "confirmed": True,
            "rule": "定点医药机构编号(12)+时间(14,yyyyMMddHHmmss)+顺序号(4)",
            "length": 30, "unique": True,
            "source": "spec:公共报文头表 msgid 行说明"},
        "signature": {"confirmed": False,
                      "reason": "规范定义 cainfo/signtype 字段但未给出算法与向量"},
        "length_unit": "UNCONFIRMED",
    }
    from datetime import datetime, timezone
    profile["extracted_at"] = datetime.now(timezone.utc).isoformat()
    with open(os.path.join(out_dir, "profile.json"), "w",
              encoding="utf-8", newline="\n") as fh:
        json.dump(profile, fh, ensure_ascii=False, indent=1)

    stats = {
        "spec_txns": len(txns),
        "fields_extracted": len(fields_by_txn),
        "samples_found": len(shapes_by_txn),
        "sample_files": len(shapes),
        "unassigned_tables": unassigned,
        "txn_without_fields": sorted(set(t["code"] for t in txns)
                                     - set(fields_by_txn)),
        "shape_only_txns": sorted(set(shapes_by_txn) - set(fields_by_txn)),
    }
    return stats


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="生成上报平台契约 Profile")
    ap.add_argument("--target", default="medins_emr_sd")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--samples", action="append", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    stats = build(args.spec, args.samples, args.out, args.target)
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
