"""RT-04 B6：本地泄漏扫描器（可复跑；只读原样例，输出零敏感原值）。

流程：
1. **哨兵采集（只读原件）**：扫描原始测试报文目录，按敏感键名提取待比对
   值（内存集合，绝不写出）；
2. **显式排除**：合成公共值（000000199001010000/测试患者/2026-01-01/
   370104 等已知合成常量）与过短值（<4 字符）——排除规则与数量入报告；
3. **比对范围**：新旧 profiles、plugins、tests、生成报告与日志、本地
   合成测试 DB（SQLite 二进制字节）；
4. **文本表示**：原文与 JSON 转义形（\\" 与 \\\\uXXXX）均检查；
5. **输出**：仅命中文件/哨兵来源（文件+字段路径）/计数——**禁止打印
   敏感原值**；退出码 0=零命中，1=有命中（CI 可用）。

边界（如实声明）：不声称覆盖自由文本姓名的所有语义；未知自由文本的
防线是运行时安全隐藏/拒绝（masking），本扫描器是对**已知真实样例值**
的仓储级回扫。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata

# 敏感键（与 plugins/domains/medins_emr/masking 同词表口径；本工具独立
# 实现以免运行时模块成为扫描对象自身的依赖环）
_SENSITIVE_EXACT = {
    "opter_name", "psn_name", "patn_name", "coner_name", "certno", "idcard",
    "tel", "mobile", "phone", "addr", "address", "psn_no", "medins_file_no",
    "cainfo", "sign_no"}
_SENSITIVE_CONTAINS = ("certno", "idcard", "psn_no", "medins_file_no",
                       "opter_name", "patn_name")

# 显式排除的合成公共值（与数量一并报告）
SYNTHETIC_COMMON = {
    "000000199001010000",        # 合成证件号（build_profile/tree_profile 哨兵）
    "00000019900101001X",
    "测试患者", "测试患者001", "合成医院", "合成科",
    "2026-01-01", "2026-01-01 00:00:00", "2026-02-01", "2026-02-01 08:00:00",
    "370104",                    # 合成区划（演示常量）
    "MOCK" + "0" * 26,           # Mock inf_refmsgid
    "T" * 14,                    # 合成机构编码占位
    "测试值", "合成值",
}
MIN_SENTINEL_LEN = 4

_DEFAULT_SCAN_ROOTS = [
    "profiles", "plugins", "tests", "packages", "apps", "tools",
    "var",                       # 本地合成测试 DB / 日志
    os.path.join("docs", "..", ".."),   # 占位：默认不含 docs（见 --extra）
]


def _key_is_sensitive(key: str) -> bool:
    k = str(key or "").lower()
    if not k:
        return False
    if k in _SENSITIVE_EXACT:
        return True
    return any(t in k for t in _SENSITIVE_CONTAINS)


def extract_sentinels(sample_dirs: list[str]) -> dict[str, list[str]]:
    """原样例目录 → {哨兵id: [值...]}；哨兵id=文件名#字段路径（不含值）。"""
    out: dict[str, list[str]] = {}

    def walk(obj, path: str, file_id: str):
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f"{path}.{k}" if path else k
                if _key_is_sensitive(k) and isinstance(v, (str, int)) \
                        and str(v):
                    out.setdefault(f"{file_id}#{p}", []).append(str(v))
                walk(v, p, file_id)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]", file_id)

    for d in sample_dirs:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith((".txt", ".json")):
                continue
            fp = os.path.join(d, fn)
            obj = None
            for enc in ("utf-8", "gbk"):
                try:
                    obj = json.loads(open(fp, "rb").read().decode(enc))
                    break
                except (UnicodeDecodeError, ValueError):
                    continue
            if obj is not None:
                walk(obj, "", fn)
    return out


def _escape_forms(value: str) -> set[str]:
    """同一值的多种文本表示：原文 / JSON 双引号转义 / \\uXXXX 转义。"""
    forms = {value}
    j = json.dumps(value, ensure_ascii=True)[1:-1]
    forms.add(j)
    forms.add(json.dumps(value, ensure_ascii=False)[1:-1])
    return {f for f in forms if f}


def scan_tree(sentinel_values: list[str], roots: list[str],
              *, text_exts: tuple[str, ...] = (
                  ".py", ".json", ".md", ".txt", ".mjs", ".ts", ".vue",
                  ".xml", ".log", ".sql", ".yml", ".yaml", ".cfg", ".ini")) \
        -> list[dict]:
    """输出目录扫描（文本原文+转义形）+ 本地 SQLite 字节扫描。"""
    vmap = []
    for v in sentinel_values:
        for f in _escape_forms(v):
            vmap.append(f)
    hits: dict[str, dict[str, int]] = {}
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [x for x in dirnames
                           if x not in ("__pycache__", "node_modules",
                                        ".pytest_cache", ".git", "dist")]
            for fn in sorted(filenames):
                fp = os.path.join(dirpath, fn)
                try:
                    rel = os.path.relpath(fp, ".").replace(os.sep, "/")
                except ValueError:                 # 跨盘（Windows 临时目录）
                    rel = os.path.abspath(fp).replace(os.sep, "/")
                try:
                    if fn.endswith(text_exts):
                        blob = open(fp, "rb").read()
                        text = blob.decode("utf-8", errors="replace")
                        if b"\x00" not in blob:      # SQLite 等二进制走字节
                            counts = {}
                            for f in vmap:
                                n = text.count(f)
                                if n:
                                    counts[f] = n
                            if counts:
                                hits.setdefault(rel, {}).update(counts)
                            continue
                    # 二进制（含 var/*.sqlite3）：字节级比对
                    blob = open(fp, "rb").read()
                    enc_forms = [f.encode("utf-8") for f in vmap] + \
                                [f.encode("gbk") for f in vmap]
                    counts = {}
                    for b in enc_forms:
                        n = blob.count(b)
                        if n:
                            counts[b.decode("utf-8", "replace")] = n
                    if counts:
                        hits.setdefault(rel, {}).update(counts)
                except OSError:
                    continue
    # 汇总为不含原值的命中记录：文件 + 命中形态计数（值→***）
    out = []
    for rel in sorted(hits):
        total = sum(hits[rel].values())
        out.append({"file": rel, "hit_forms": len(hits[rel]),
                    "hit_count": total})
    return out


def run_scan(sample_dirs: list[str], scan_roots: list[str]) -> dict:
    sentinels = extract_sentinels(sample_dirs)
    excluded_short = 0
    excluded_synthetic: dict[str, int] = {}
    values: list[str] = []
    seen: set[str] = set()
    for sid, vals in sentinels.items():
        for v in vals:
            v = str(v).strip()
            if len(v) < MIN_SENTINEL_LEN:
                excluded_short += 1
                continue
            if v in SYNTHETIC_COMMON:
                excluded_synthetic[v] = excluded_synthetic.get(v, 0) + 1
                continue
            if v and v not in seen:
                seen.add(v)
                values.append(v)
    hits = scan_tree(values, scan_roots)
    return {
        "sentinel_sources": len(sentinels),
        "sentinel_values": len(values),
        "excluded": {"short_values": excluded_short,
                     "synthetic_common": {"count":
                                          sum(excluded_synthetic.values()),
                                          "kinds": len(excluded_synthetic)}},
        "scan_roots": scan_roots,
        "hit_files": len(hits),
        "hits": hits,
        "clean": not hits,
    }


def main(argv=None) -> int:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ws = os.path.dirname(base)                     # 工作区根
    ap = argparse.ArgumentParser(
        description="泄漏扫描（只读；输出不含敏感原值；退出 1=有命中）")
    ap.add_argument("--samples", action="append", default=[
        os.path.join(ws, "数据上报", "2、医保电子病历上报",
                     "调用机构医保(1)", "调用机构医保"),
        os.path.join(ws, "数据上报", "2、医保电子病历上报",
                     "12601-12604调用机构医保测试报文示例")])
    ap.add_argument("--roots", action="append", default=[
        os.path.join(base, "profiles"), os.path.join(base, "plugins"),
        os.path.join(base, "tests"), os.path.join(base, "packages"),
        os.path.join(base, "apps"), os.path.join(base, "tools"),
        os.path.join(base, "var")])
    ap.add_argument("--extra", action="append", default=[],
                    help="额外扫描根（如生成报告目录）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    roots = args.roots + args.extra
    rep = run_scan(args.samples, roots)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(f"sentinel_sources={rep['sentinel_sources']} "
              f"values={rep['sentinel_values']} "
              f"excluded_short={rep['excluded']['short_values']} "
              f"excluded_synthetic={rep['excluded']['synthetic_common']['count']}"
              f" hit_files={rep['hit_files']} "
              f"{'CLEAN' if rep['clean'] else 'LEAK DETECTED'}")
        for h in rep["hits"][:50]:
            print(f"  HIT {h['file']} forms={h['hit_forms']} "
                  f"count={h['hit_count']}")
    return 0 if rep["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
