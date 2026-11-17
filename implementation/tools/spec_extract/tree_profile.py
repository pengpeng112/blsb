"""RT-04 B2：树形契约 Profile 生成器（23 交易新 revision）。

与 LT-07 build_profile（平面契约）的差异（任务书 §8）：
- 交易集合 = 清单表(19) ∪ 正文有依据的交易标题(12601～12604) = **精确 23**；
- 输入表与输出表分离：输出 infcode/output 等只进 _output.json，绝不混入
  input 必填；
- 每字段保留 docx 来源定位（标题路径/表序号/行号）、方向、容器归属、
  字典来源与未确认项；
- 嵌套归属：样例 shape（**全元素**合并路径，不取前两个；空数组保容器）
  的唯一匹配；同名字段多路径不猜 → UNRESOLVED_PATH 待核清单；
- 类型判断：日期时间先于日期；容器/数值/字符严格区分；
- 代码标识 Y/是/1 → has_code_dict=true；无字典名 dict=null；opter_type
  为正文已证实字典（合成值取自它，不猜行业默认）；
- 合成样例由新契约生成（内容全新）；样例根对象只含协议字段（无
  _synthetic 混入——元数据在 transactions.json）；
- shape 只提键名/容器/路径，**不导出任何原始叶值**。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone

from .docx_tables import extract_tables, is_param_table, txn_of_heading, \
    txn_transaction_list

EXTRACTOR_VERSION = "spec_extract/2.0-tree"
SCHEMA_VERSION = 2
NEW_TXNS = ("12601", "12602", "12603", "12604")
EXPECTED_TXNS = 23


def _sha256(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _norm_len(raw) -> int | None:
    m = re.search(r"\d+", str(raw or ""))
    return int(m.group()) if m else None


def _required(raw) -> bool:
    return str(raw or "").strip().upper() in ("Y", "是", "1", "必填", "TRUE")


def _dict_flag(raw) -> tuple[bool, str | None]:
    """代码标识列 → (has_code_dict, dict_name)。Y/是/1 只是字典标志；
    有明确字典名才返回名称（正文已证实）。"""
    s = str(raw or "").strip()
    if not s:
        return False, None
    if s.upper() in ("Y", "是", "1"):
        return True, None
    # 明确写了字典名（如 "Y，opter_type" / "opter_type"）
    name = re.sub(r"[^A-Za-z0-9_]", "", s)
    return bool(name), (name or None)


def _type_of(raw) -> str:
    """B2.6：日期时间先于日期；数值/字符严格区分。"""
    t = str(raw or "")
    if "日期时间" in t or "时间型" in t or "时间" in t:
        return "datetime"
    if "日期型" in t or "日期" in t:
        return "date"
    if "数值" in t:
        return "number"
    if "字符" in t:
        return "string"
    return "string"


def _is_sensitive(code: str, name: str) -> bool:
    """敏感标记：与运行时防线（domains/medins_emr/masking）同词表口径。"""
    try:
        from plugins.domains.medins_emr.masking import _key_is_sensitive
        if _key_is_sensitive(code):
            return True
    except Exception:                     # noqa: BLE001 —— 工具独立运行兜底
        pass
    n = str(name or "")
    return ("证件" in n or "姓名" in n or "电话" in n or "手机" in n
            or "地址" in n)


# ---------------------------------------------------------------- shape（全元素合并）
def _shape_paths(value, path: str = "") -> dict[str, set[str]]:
    """值 → {归一化路径: 类型}，数组合并**全部**元素路径（[i]→[]）；
    空数组保留 array 容器。只含结构（键/容器/路径），无值。"""
    out: dict[str, set[str]] = {}

    def add(p: str, t: str):
        out.setdefault(p, set()).add(t)

    def walk(v, p: str):
        np = re.sub(r"\[\d+\]", "[]", p)
        if isinstance(v, dict):
            add(np or "$", "object")
            for k in v:
                walk(v[k], f"{np}.{k}" if np else k)
        elif isinstance(v, list):
            add(np, "array")
            for item in v:                 # 全元素（不取前两个）
                walk(item, f"{np}[]")
            if not v:
                add(f"{np}[]", "empty")    # 空数组保容器（无成员字段可推）
        else:
            add(np, type(v).__name__)

    walk(value, path)
    return out


def _leaf_index(shape_paths: dict[str, set[str]]) -> dict[str, list[str]]:
    """叶键 → 出现的容器路径（input 前缀剥离；供唯一匹配）。"""
    idx: dict[str, list[str]] = {}
    for p, types in shape_paths.items():
        if p in ("$", "input") or p.endswith("[]"):
            continue
        leaf = p.rsplit(".", 1)[-1]
        container = p.rsplit(".", 1)[0] if "." in p else ""
        idx.setdefault(leaf, [])
        if container not in idx[leaf]:
            idx[leaf].append(container)
    return idx


def _norm_container(c: str) -> str:
    return c[len("input"):] if c.startswith("input") else c


# ---------------------------------------------------------------- 主构建
def classify_tables(tables: list[dict]) -> dict:
    """docx 表分类：清单/公共输入/公共输出/逐交易输入片段。"""
    out = {"list": None, "envelope": None, "output": None, "by_txn": {},
           "dict_tables": []}
    for t in tables:
        leaf = t["heading_path"][-1] if t["heading_path"] else ""
        if "接口编号" in "".join(t["header"]) and \
                "接口名称" in "".join(t["header"]) and out["list"] is None:
            out["list"] = t
            continue
        if not is_param_table(t["header"]):
            continue
        # 叶子标题判别（祖先窗口含"接口输入报文格式定义"，须按叶区分）
        if "输入报文格式" in leaf:
            out["envelope"] = t
            continue
        if "输出报文格式" in leaf:
            out["output"] = t
            continue
        txn = None
        for h in reversed(t["heading_path"]):
            txn = txn_of_heading(h)
            if txn:
                break
        if txn:
            out["by_txn"].setdefault(txn, []).append(t)
    return out


def _rows_to_fields(table: dict, direction: str) -> list[dict]:
    """参数表行 → 字段节点（带来源定位）。"""
    header = table["header"]
    col = {}
    for i, h in enumerate(header):
        for key, names in (
                ("code", ("参数代码", "数据元标识", "字段")),
                ("name", ("参数名称", "数据元名称", "名称")),
                ("type", ("参数类型", "类型")),
                ("len", ("参数长度", "长度")),
                ("dict", ("代码标识",)),
                ("req", ("是否必填", "是否非空")),
                ("desc", ("说明", "备注"))):
            if key not in col and any(n in h for n in names):
                col[key] = i
    fields = []
    for ri, r in enumerate(table["rows"], start=2):   # 行号含表头=文档行
        ci = col.get("code")
        if ci is None or len(r) <= ci or not str(r[ci] or "").strip():
            continue
        code = str(r[ci]).strip()
        def g(k):
            i = col.get(k)
            return r[i] if i is not None and i < len(r) else ""
        has_dict, dict_name = _dict_flag(g("dict"))
        node = {
            "code": code,
            "name": str(g("name") or "").strip(),
            "type": _type_of(g("type")),
            "max_len": _norm_len(g("len")),
            "dict": dict_name,
            "has_code_dict": has_dict or bool(dict_name),
            "required": _required(g("req")),
            "sensitive": _is_sensitive(code, str(g("name") or "")),
            "source": "spec",
            "direction": direction,
            "locator": {
                "heading_path": table["heading_path"][-3:],
                "table_index": table["table_index"],
                "row": ri,
            },
        }
        desc = str(g("desc") or "").strip()
        if desc:
            node["notes"] = desc[:120]
        fields.append(node)
    return fields


def _assign_paths(fields: list[dict], shape: dict | None,
                    unresolved: list[dict],
                    fragments: list[list[dict]] | None = None) -> list[dict]:
    """归属（B2.5）：正文容器证据优先，其次样例唯一匹配；仍歧义 →
    UNRESOLVED_PATH 待核（不猜）。

    片段（docx 单表）= 一个容器的字段组：片段内**唯一匹配**字段的多数
    容器即该片段容器；片段内多路径字段若候选含该容器 → 归入（证据=
    同片段邻居字段的共同容器）。"""
    if shape is None:
        for f in fields:
            unresolved.append({"code": f["code"],
                               "reason": "NO_SAMPLE_SHAPE",
                               "detail": "无样例结构——顶层归属（待人工核对）"})
            f["path"] = f["code"]
        return fields
    idx = _leaf_index(shape)

    def cands_of(f):
        return sorted({_norm_container(c) for c in idx.get(f["code"], [])})

    for f in fields:
        norm = cands_of(f)
        f["_cands"] = norm
        if len(norm) == 1:
            f["path"] = f"{norm[0]}.{f['code']}" if norm[0] else f["code"]
        elif not norm:
            f["path"] = "UNRESOLVED_PATH"
            f["unresolved"] = True
        else:
            f["path"] = None                    # 片段消歧后再定
    for frag in (fragments or []):
        votes: dict[str, int] = {}
        for f in frag:
            c = idx.get(f["code"], [])
            if len({*map(_norm_container, c)}) == 1:
                k = _norm_container(c[0])
                votes[k] = votes.get(k, 0) + 1
        frag_container = max(votes, key=lambda k: votes[k]) if votes else None
        for f in frag:
            if f.get("path") is None and frag_container is not None \
                    and frag_container in (f.get("_cands") or []):
                f["path"] = (f"{frag_container}.{f['code']}"
                             if frag_container else f["code"])
    for f in fields:
        if f.get("path") is None:
            unresolved.append({"code": f["code"],
                               "reason": "MULTI_SAMPLE_PATH",
                               "detail": f"样例多路径 "
                               f"{[p or '(top)' for p in f['_cands']]}——不猜"})
            f["path"] = "UNRESOLVED_PATH"
            f["unresolved"] = True
        elif f["path"] == "UNRESOLVED_PATH" and not f["_cands"]:
            unresolved.append({"code": f["code"],
                               "reason": "NO_SAMPLE_PATH",
                               "detail": "规范有而样例无——归属待人工核对"})
        f.pop("_cands", None)
    return fields


def _build_tree(fields: list[dict], container_types: dict[str, set[str]]) -> dict:
    """字段（含 path）→ 树 schema（object.children / array.items）。"""
    root = {"code": "input", "type": "object", "children": {}}
    for f in fields:
        path = f.get("path") or f["code"]
        if path == "UNRESOLVED_PATH":
            continue                        # 待核清单不挂树（不造假准确）
        parts = [p for p in path.split(".") if p]
        node = root
        for i, seg in enumerate(parts[:-1]):
            key = seg
            child_key = f"{node['code']}.{key}"
            as_array = key.endswith("[]") or \
                "array" in container_types.get(key, set())
            seg_clean = key[:-2] if key.endswith("[]") else key
            nxt_seg = parts[i + 1]
            nxt_array = nxt_seg.endswith("[]")
            if child_key not in node["children"]:
                node["children"][child_key] = {
                    "code": seg_clean,
                    "type": "array" if as_array else "object",
                    "children": {}}
            node = node["children"][child_key]
            if as_array and node["type"] != "array":
                node["type"] = "array"
            if node["type"] == "array" and "items" not in node:
                node["items"] = {"code": seg_clean, "type": "object",
                                 "children": {}}
            if node["type"] == "array":
                node = node["items"]
        leaf_key = parts[-1]
        if leaf_key in node["children"] or any(
                node["children"][k]["code"] == leaf_key
                for k in node["children"]):
            continue                          # 同名重复（样例合并已归一）
        clean = {k: v for k, v in f.items() if k not in ("path",)}
        node["children"][f"{node['code']}.{leaf_key}"] = {
            **clean, "code": leaf_key}

    def finalize(n: dict) -> dict:
        out = {k: v for k, v in n.items() if k != "children"}
        if n.get("children"):
            out["children"] = [finalize(c) for c in n["children"].values()]
        if n.get("type") == "array":
            out["items"] = finalize(n.get("items") or
                                    {"code": n["code"], "type": "object"})
        if not out.get("children") and out.get("type") in ("object", "array") \
                and "items" not in out:
            out.pop("children", None)
            if out["type"] == "object":
                out["type"] = "object"      # 空容器保留（不臆造成员字段）
        return out

    return finalize(root)


def _container_types_from_shape(shape: dict[str, set[str]]) -> dict[str, set]:
    """路径段 → 容器类型（array 判定）。"""
    out: dict[str, set] = {}
    for p, types in shape.items():
        for seg in p.split("."):
            s = seg[:-2] if seg.endswith("[]") else seg
            out.setdefault(s, set()).update(types)
    return out


def _synth_value(node: dict, dicts: dict):
    """合成值：全部新内容；字典取已证实 opter_type；类型/长度感知。"""
    t = node.get("type")
    ml = node.get("max_len")
    if t == "number":
        return 1
    if t == "date":
        return "2026-02-01"
    if t == "datetime":
        return "2026-02-01 08:00:00"
    d = node.get("dict") or (node["code"] if node.get("has_code_dict")
                             and node["code"] in dicts else None)
    if d and d in dicts:
        first = sorted(dicts[d]["values"])[0]
        return first
    code = node["code"]
    name = node.get("name", "")
    raw = f"合成{code[:8]}" if code else "合成值"
    if node.get("sensitive") or _is_sensitive(code, name):
        raw = "合成敏感值占位"
    if ml and len(raw) > int(ml):
        raw = raw[:int(ml)]
    return raw


def _synth_from_tree(tree: dict, dicts: dict, depth: int = 0):
    if depth > 8:
        return None
    if tree.get("type") == "array":
        return [_synth_from_tree(tree.get("items") or {}, dicts, depth + 1)
                for _ in range(1)]
    if tree.get("children"):
        return {c["code"]: _synth_from_tree(c, dicts, depth + 1)
                for c in tree["children"] if not c.get("unresolved")}
    if tree.get("type") == "object":
        return {}                            # 空容器保结构
    return _synth_value(tree, dicts)


def build(spec_path: str, sample_dirs: list[str], out_dir: str,
          target_code: str = "medins_emr_sd",
          revision: str = "r2-23txn") -> dict:
    tables = extract_tables(spec_path)
    cls = classify_tables(tables)
    list_txns = txn_transaction_list(tables)
    names = {t["code"]: t["name"] for t in list_txns}
    body_txns = sorted(set(cls["by_txn"]))
    all_txns = sorted(set(names) | set(body_txns))
    assert len(all_txns) == EXPECTED_TXNS, \
        f"交易并集应为 {EXPECTED_TXNS}，实际 {len(all_txns)}: {all_txns}"
    legacy19 = sorted(set(names))
    assert legacy19 == sorted(set(names) & set(all_txns)) and \
        set(all_txns) - set(legacy19) == set(NEW_TXNS), \
        "并集必须=原19+12601~12604"

    # 样例 shape（全元素合并；不取值）
    shapes: dict[str, dict] = {}
    for d in sample_dirs:
        for name in sorted(os.listdir(d)):
            if not name.endswith((".txt", ".json")):
                continue
            m = re.match(r"(1\d{4})", name)
            if not m:
                continue
            raw = open(os.path.join(d, name), "rb").read()
            obj = None
            for enc in ("utf-8", "gbk"):
                try:
                    obj = json.loads(raw.decode(enc))
                    break
                except (UnicodeDecodeError, ValueError):
                    continue
            if obj is None:
                continue
            shapes.setdefault(m.group(1), _shape_paths(obj.get("input", {})))

    os.makedirs(os.path.join(out_dir, "fields"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "samples"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "dicts"), exist_ok=True)

    # 公共报文头 / 输出契约（输入输出分离）
    envelope_fields = _rows_to_fields(cls["envelope"], "envelope") \
        if cls["envelope"] else []
    for n in envelope_fields:
        if n["code"] == "info" and n.get("max_len") == 4:
            n["max_len"] = None
            n["notes"] = "规范长度4与交易号5位矛盾——长度待医保确认"
        if n["code"] == "input":
            n["type"] = "object"
            n["notes"] = "交易输入对象（各交易契约为其 children）"
    envelope_tree = {
        "code": "_envelope", "schema_version": SCHEMA_VERSION,
        "length_unit": "UNCONFIRMED",
        "children": [n for n in envelope_fields],
    }
    _write(out_dir, "fields/_envelope.json", envelope_tree)
    output_fields = _rows_to_fields(cls["output"], "output") \
        if cls["output"] else []
    _write(out_dir, "fields/_output.json", {
        "code": "_output", "schema_version": SCHEMA_VERSION,
        "length_unit": "UNCONFIRMED",
        "note": "响应公共字段（infcode/err_msg/output…）——不混入 input 必填",
        "children": output_fields})

    # 已证实字典（正文公共报文头 opter_type；其余 dict=null+has_code_dict）
    _write(out_dir, "dicts/opter_type.json", {
        "code": "opter_type", "name": "经办人类别",
        "values": {"1": "经办人", "2": "自助终端", "3": "移动终端"},
        "source": "spec:公共报文头表第11行（LT-07 已证实，沿用）"})
    dicts = {"opter_type": {"values": {"1": "1", "2": "2", "3": "3"}}}

    txn_list = []
    unresolved_all: dict[str, list] = {}
    for code in all_txns:
        frags = cls["by_txn"].get(code, [])
        # 片段与合并列表共用同一字段对象（片段消歧直接作用于最终字段）
        frag_fields = [_rows_to_fields(t, "input") for t in frags]
        fields = [f for frag in frag_fields for f in frag]
        shape = shapes.get(code)
        unresolved: list[dict] = []
        fields = _assign_paths(fields, shape, unresolved,
                               fragments=frag_fields)
        unresolved_all[code] = unresolved
        ct = _container_types_from_shape(shape) if shape else {}
        tree = _build_tree(fields, ct)
        contract = {
            "code": code, "schema_version": SCHEMA_VERSION,
            "envelope_ref": "fields/_envelope.json",
            "root": "input", "length_unit": "UNCONFIRMED",
            "children": tree.get("children", []),
        }
        _write(out_dir, f"fields/{code}.json", contract)
        # 合成样例：根对象只含协议字段（envelope+input；无 _synthetic 混入）
        env_synthetic: dict = {}
        for n in envelope_fields:
            if n["code"] == "input":
                continue
            if n["required"] or n["code"] in ("info", "msgid", "inf_time"):
                v = _synth_value({**n, "dict": "opter_type"
                                  if n["code"] == "opter_type" else n.get("dict")}, dicts) \
                    if n["code"] == "opter_type" else _synth_value(n, dicts)
                env_synthetic[n["code"]] = v
        env_synthetic["info"] = code
        env_synthetic["input"] = _synth_from_tree(tree, dicts)
        _write(out_dir, f"samples/{code}.json", env_synthetic)
        txn_list.append({
            "code": code, "name": names.get(code, ""),
            "direction": "upload",
            "fields_ref": f"fields/{code}.json",
            "sample_ref": f"samples/{code}.json",
            "has_fields": bool(frags), "has_sample_shape": shape is not None,
            "sample_synthetic": True,
            "unresolved_count": len(unresolved),
            "notes": "" if not unresolved else
            f"{len(unresolved)} 字段 UNRESOLVED_PATH 待核"})

    _write(out_dir, "transactions.json", txn_list)

    profile = {
        "target_code": target_code,
        "name": "山东医保电子病历上报",
        "spec_version": "V2.0",
        "revision": revision,
        "schema_version": SCHEMA_VERSION,
        "spec_source": [{"path": os.path.basename(spec_path),
                         "sha256": _sha256(spec_path)}],
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "extractor_version": EXTRACTOR_VERSION,
        "status": "DRAFT",
        "transactions_count": len(all_txns),
        "txn_source": {
            "list_table": legacy19,
            "body_headings": sorted(set(body_txns)),
            "union_rule": "清单表 ∪ 正文交易标题 = 原19 + 12601~12604"},
        "msgid_rule": {
            "confirmed": True,
            "rule": "定点医药机构编号(12)+时间(14,yyyyMMddHHmmss)+顺序号(4)",
            "length": 30, "unique": True,
            "source": "spec:公共报文头表 msgid 行说明"},
        "signature": {"confirmed": False,
                      "reason": "规范定义 cainfo/signtype 字段但未给出算法与向量"},
        "length_unit": "UNCONFIRMED",
        "unresolved_total": sum(len(v) for v in unresolved_all.values()),
    }
    _write(out_dir, "profile.json", profile)
    _write(out_dir, "unresolved.json",
           {"schema_version": SCHEMA_VERSION, "by_txn": unresolved_all})
    return {"profile": profile, "unresolved": unresolved_all,
            "txn_list": txn_list}


def _write(out_dir: str, rel: str, obj) -> None:
    p = os.path.join(out_dir, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)


def write_report(out_dir: str, build_stats: dict, spec_path: str) -> str:
    """报告自动读取实际产物统计（B2.8）。"""
    fields_dir = os.path.join(out_dir, "fields")
    samples_dir = os.path.join(out_dir, "samples")
    ffiles = sorted(f for f in os.listdir(fields_dir) if f.endswith(".json")
                    and not f.startswith("_"))
    sfiles = sorted(f for f in os.listdir(samples_dir) if f.endswith(".json"))
    txns = json.load(open(os.path.join(out_dir, "transactions.json"),
                          encoding="utf-8"))
    codes = [t["code"] for t in txns]
    errs: list[str] = []
    if len(codes) != EXPECTED_TXNS:
        errs.append(f"交易数 {len(codes)} != {EXPECTED_TXNS}")
    if len(codes) != len(set(codes)):
        errs.append("交易代码重复")
    if set(ffiles) != {f"{c}.json" for c in codes}:
        errs.append("fields 与 transactions 不一一对应")
    if set(sfiles) != {f"{c}.json" for c in codes}:
        errs.append("samples 与 transactions 不一一对应")
    unresolved = build_stats.get("unresolved", {})
    total_unresolved = sum(len(v) for v in unresolved.values())
    lines = [
        "# RT-04 B2 提取报告（23 交易树形契约 revision r2-23txn）",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).isoformat()}",
        f"- 规范原件：{os.path.basename(spec_path)} "
        f"(sha256={_sha256(spec_path)[:16]}…)",
        f"- 交易集合：**{len(codes)} = 原19（清单表） + 12601~12604（正文标题）**，"
        " transactions/fields/samples 一一对应、无重复/孤儿",
        f"- fields：{len(ffiles)} + _envelope/_output；samples：{len(sfiles)}"
        "（全部合成，根对象只含协议字段）",
        f"- UNRESOLVED_PATH 待核字段：{total_unresolved}（逐笔见 unresolved.json；"
        "同名字段多路径不猜、规范有样例无的显式待核）",
        "",
        "## 保留待核项（如实列出，不以删告警换零告警）",
        "- 长度单位（字符/字节）规范未说明 → length_unit=UNCONFIRMED",
        "- 签名算法（cainfo/signtype）规范未给 → SIGNATURE_UNCONFIRMED",
        "- info 长度 4 与交易号 5 位矛盾 → max_len 置空待医保确认",
        f"- UNRESOLVED_PATH：{total_unresolved} 项",
        "",
        "## 错误",
        f"- {'0 errors' if not errs else ' / '.join(errs)}",
    ]
    if total_unresolved:
        lines.append("")
        lines.append("## UNRESOLVED_PATH 明细（代码/原因）")
        for txn, items in sorted(unresolved.items()):
            for it in items:
                lines.append(f"- {txn} {it['code']} {it['reason']}")
    rep = os.path.join(out_dir, "EXTRACTION_REPORT.md")
    with open(rep, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return rep


def main(argv=None) -> int:
    import argparse
    base = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))          # implementation/
    ap = argparse.ArgumentParser(description="生成树形契约 Profile（23 交易）")
    ap.add_argument("--spec", default=os.path.join(
        base, "..", "数据上报", "2、医保电子病历上报",
        "山东省医疗保障信息平台定点医疗机构医保电子病历接口规范V2.0(1)(2).docx"))
    ap.add_argument("--samples", action="append", default=[
        os.path.join(base, "..", "数据上报", "2、医保电子病历上报",
                     "调用机构医保(1)", "调用机构医保"),
        os.path.join(base, "..", "数据上报", "2、医保电子病历上报",
                     "12601-12604调用机构医保测试报文示例")])
    ap.add_argument("--out", default=os.path.join(
        base, "profiles", "medins_emr_sd", "revisions", "r2-23txn"))
    args = ap.parse_args(argv)
    stats = build(args.spec, args.samples, args.out)
    rep = write_report(args.out, stats, args.spec)
    print(json.dumps({"report": rep,
                      "transactions": stats["profile"]["transactions_count"],
                      "unresolved": stats["profile"]["unresolved_total"]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
