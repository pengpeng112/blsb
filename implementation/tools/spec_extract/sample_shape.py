"""样例结构推断（LT-07）：JSON 报文 → 字段路径树（类型/数组），**丢弃全部值**。

编码：先 utf-8 后 gbk，都失败记录跳过。输出可安全入库（不含样例值）。
"""
from __future__ import annotations

import json
import os


def read_json_any_encoding(path: str) -> tuple[dict | None, str]:
    """返回 (obj, encoding|错误原因)。"""
    raw = open(path, "rb").read()
    for enc in ("utf-8", "gbk"):
        try:
            return json.loads(raw.decode(enc)), enc
        except (UnicodeDecodeError, ValueError):
            continue
    return None, "DECODE_OR_JSON_FAILED"


def shape_of(value, path: str = "") -> list[dict]:
    """值 → [{path,type,array,children_keys}]（无值输出）。"""
    out: list[dict] = []
    if isinstance(value, dict):
        out.append({"path": path or "$", "type": "object",
                    "keys": sorted(value.keys())})
        for k in sorted(value.keys()):
            child_path = f"{path}.{k}" if path else k
            out.extend(shape_of(value[k], child_path))
    elif isinstance(value, list):
        out.append({"path": path, "type": "array", "length": len(value)})
        for i, v in enumerate(value[:2]):        # 前两元素形状足够
            out.extend(shape_of(v, f"{path}[{i}]"))
    else:
        out.append({"path": path, "type": type(value).__name__})
    return out


def extract_shapes(sample_dir: str) -> dict:
    """目录 → {文件名: {"shape": [...], "encoding": enc, "error": err}}。
    另按文件名前 5 位数字归交易号。"""
    import re
    out: dict[str, dict] = {}
    for name in sorted(os.listdir(sample_dir)):
        if not name.endswith(".txt") and not name.endswith(".json"):
            continue
        path = os.path.join(sample_dir, name)
        if not os.path.isfile(path):
            continue
        obj, info = read_json_any_encoding(path)
        entry: dict = {"file": name}
        m = re.match(r"(1\d{4})", name)
        entry["txn"] = m.group(1) if m else None
        if obj is None:
            entry["error"] = info
            entry["shape"] = None
        else:
            entry["encoding"] = info
            entry["shape"] = shape_of(obj)
        out[name] = entry
    return out
