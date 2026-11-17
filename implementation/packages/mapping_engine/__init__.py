"""受限声明式映射引擎（02 §7.4、05 C05）。

允许的操作白名单：const / field / dict_map / concat / date_format / decimal_scale /
regex_replace(限长限时的安全模式)。禁止：eval、网络、文件、系统命令、数据库写入。
"""
from __future__ import annotations

import re

ALLOWED_OPS = {
    "const": {"value"},
    "field": {"source_field"},
    "dict_map": {"source_field", "dictionary", "default"},
    "concat": {"parts"},
    "date_format": {"source_field", "from_format", "to_format"},
    "decimal_scale": {"source_field", "places"},
    "regex_replace": {"source_field", "pattern", "replacement"},
    "null_fallback": {"source_field", "fallback_field"},
}

FORBIDDEN_HINTS = ("import", "exec", "eval(", "__", "os.", "subprocess", "open(",
                   "socket", "http://", "https://", "ftp://")

MAX_PATTERN_LENGTH = 128
_MAX_INPUT_LENGTH = 1_000_000


def validate_mapping(mapping: dict) -> list[str]:
    """返回错误清单；空列表=合法。"""
    errors: list[str] = []
    for i, op in enumerate((mapping or {}).get("ops", [])):
        if not isinstance(op, dict):
            errors.append(f"op[{i}] not an object")
            continue
        kind = op.get("op")
        if kind not in ALLOWED_OPS:
            errors.append(f"op[{i}] {kind!r} not in whitelist {sorted(ALLOWED_OPS)}")
            continue
        expr_blob = " ".join(str(v) for v in op.values())
        for hint in FORBIDDEN_HINTS:
            if hint in expr_blob:
                errors.append(f"op[{i}] contains forbidden construct {hint!r}")
        if kind == "regex_replace":
            pattern = op.get("pattern", "")
            if len(pattern) > MAX_PATTERN_LENGTH:
                errors.append(f"op[{i}] regex pattern exceeds {MAX_PATTERN_LENGTH} chars")
            elif _unsafe_regex(pattern):
                errors.append(f"op[{i}] regex pattern risks catastrophic backtracking")
    return errors


def _unsafe_regex(pattern: str) -> bool:
    """启发式拒绝灾难性回退模式（R20）。标准库 re 无超时，校验是主防线：
    - 嵌套量词 / 相邻量词；
    - 量词化的分支组（如 (a|aa)+ ）：分支间前缀重叠会导致指数回退；
    - 组内再含量词组后接量词。"""
    compact = pattern.replace(" ", "")
    nested_quantifier_group = re.search(r"\([^)]*[+*][^)]*\)[+*{]", compact)
    adjacent_quantifiers = re.search(r"(\+\*|\*\+|\+\+|\*\*)", compact)
    quantified_alternation = re.search(r"\([^()]*\|[^()]*\)[+*{]", compact)
    alternation_nested_quant = re.search(r"\([^()]*[+*][^()]*\|[^()]*\)[+*{]",
                                         compact) or \
        re.search(r"\([^()]*\|[^()]*[+*][^()]*\)[+*{]", compact)
    return bool(nested_quantifier_group or adjacent_quantifiers or
                quantified_alternation or alternation_nested_quant)


def apply_mapping(mapping: dict, record: dict, dictionaries: dict | None = None) -> dict:
    """执行受限映射。任何未知操作抛 ValueError；不执行任意代码。"""
    if validate_mapping(mapping):
        raise ValueError("mapping failed validation")
    out: dict = {}
    for i, op in enumerate(mapping.get("ops", [])):
        key = op.get("target", f"out_{i}")
        kind = op.get("op")
        if kind == "const":
            out[key] = op["value"]
        elif kind == "field":
            out[key] = record.get(op["source_field"])
        elif kind == "dict_map":
            d = (dictionaries or {}).get(op["dictionary"], {})
            v = record.get(op["source_field"])
            if v in d:
                out[key] = d[v]
            elif "default" in op:
                out[key] = op["default"]
            else:
                raise KeyError(f"dictionary miss: {op['dictionary']}[{v!r}]")
        elif kind == "concat":
            out[key] = "".join(str(record.get(p, "")) for p in op["parts"])
        elif kind == "regex_replace":
            # 标准库 re 无 timeout 参数；防回退依赖 validate_mapping 的
            # 模式校验（限长+拒绝嵌套量词），并对输入长度设上限。
            text = str(record.get(op["source_field"], ""))
            if len(text) > _MAX_INPUT_LENGTH:
                raise ValueError(f"regex_replace input exceeds {_MAX_INPUT_LENGTH} chars")
            out[key] = re.sub(op["pattern"], op["replacement"], text)
        elif kind == "date_format":
            from datetime import datetime
            raw = record.get(op["source_field"])
            if raw is None:
                out[key] = None
            else:
                out[key] = datetime.strptime(str(raw),
                                             op["from_format"]).strftime(op["to_format"])
        elif kind == "decimal_scale":
            from decimal import Decimal, InvalidOperation
            raw = record.get(op["source_field"])
            if raw is None:
                out[key] = None
            else:
                try:
                    q = Decimal(1).scaleb(-int(op["places"]))
                    out[key] = str(Decimal(str(raw)).quantize(q))
                except (InvalidOperation, ValueError, ArithmeticError) as exc:
                    raise ValueError(f"decimal_scale failed: {exc}") from exc
        elif kind == "null_fallback":
            v = record.get(op["source_field"])
            out[key] = v if v is not None else record.get(op["fallback_field"])
    return out
