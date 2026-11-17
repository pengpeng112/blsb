"""契约校验器（LT-07 平面；RT-04 B3 树形递归重写）。

- 单一树 schema（schema_version=2）：object.children / array.items；逐容器
  校验 required/type/max_len/dict/format，错误路径含完整数组索引
  （input.diseinfo[3].diag_code）；
- 平面契约兼容（无 schema_version 或 =1）：显式版本路由走旧平面遍历；
  **未知 schema_version 一律报错**（UNKNOWN_SCHEMA_VERSION），不默认按
  旧树解释；
- UNKNOWN_FIELD 同级仅 warn，不短路其他已知子字段；未知节点仍交安全层
  （masking）递归脱敏——校验器不承担脱敏；
- 校验**完整数组**（不再只查前 5 项）；number 拒 bool；string 不接受
  dict/list/int 冒充；object/array 类型不符只报 TYPE 不崩溃；
- 日期真实日历、日期/日期时间区分；长度单位未知保留
  LENGTH_UNIT_UNCONFIRMED 提示（不当作正式验收）；
- 错误文案不含原始敏感值（只含路径/规则/长度等安全元信息）。
"""
from __future__ import annotations

import re
from datetime import datetime

# 合法字段来源（RT-06 F02）：spec=docx 正文；spec_csv=影像清单 CSV；
# None=历史契约（未标来源）。其余来源显式拒绝（不静默忽略节点）。
_VALID_SOURCES = ("spec", "spec_csv", None)

_FORMAT_PATTERNS = {
    "yyyy-MM-dd HH:mm:ss": ("%Y-%m-%d %H:%M:%S",
                            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"),
    "yyyy-MM-dd": ("%Y-%m-%d", r"^\d{4}-\d{2}-\d{2}$"),
}
UNKNOWN_SCHEMA_VERSION = "UNKNOWN_SCHEMA_VERSION"


def _err(path: str, code: str, message: str, level: str = "error") -> dict:
    return {"path": path, "code": code, "level": level, "message": message}


def _type_matches(value, t: str) -> bool:
    """B3：number 拒 bool；string 不接受 dict/list/int 冒充。"""
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "string":
        return isinstance(value, str)
    if t == "date" or t == "datetime":
        return isinstance(value, str)
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    return True


_TYPE_LABEL = {"number": "数值", "string": "字符", "date": "日期",
               "datetime": "日期时间", "object": "对象", "array": "数组"}


def _check_leaf(path: str, value, node: dict, dicts: dict,
                length_unit_unconfirmed: bool) -> list[dict]:
    errs: list[dict] = []
    t = node.get("type") or "string"
    name = node.get("name") or path
    if not _type_matches(value, t):
        errs.append(_err(
            path, "TYPE",
            f"{name} 类型须为{_TYPE_LABEL.get(t, t)}"
            f"（实际 {type(value).__name__}）"))
        return errs
    if t in ("date", "datetime"):
        fmt = "yyyy-MM-dd HH:mm:ss" if t == "datetime" else "yyyy-MM-dd"
        pat = _FORMAT_PATTERNS[fmt][1]
        if not re.match(pat, value):
            errs.append(_err(path, "FORMAT", f"{path} 日期格式须为 {fmt}"))
        else:
            try:
                datetime.strptime(value, _FORMAT_PATTERNS[fmt][0])
            except ValueError:
                errs.append(_err(path, "FORMAT", f"{path} 不是真实日历日期"))
    if node.get("max_len") and t == "string" and len(value) > int(node["max_len"]):
        unit = "字符（长度单位待确认）" if length_unit_unconfirmed else "字符"
        errs.append(_err(path, "MAX_LEN",
                         f"{name} 超长：{len(value)}>{node['max_len']}{unit}"))
    d = node.get("dict")
    if d and d in dicts and t == "string":
        if value not in dicts[d]["values"]:
            errs.append(_err(path, "DICT",
                             f"{path} 不在字典 {d} 取值范围内"))
    return errs


def _is_empty(value) -> bool:
    return value is None or value == "" or \
        (isinstance(value, (dict, list)) and len(value) == 0)


def _walk_tree(obj, node: dict, path: str, errs: list[dict],
               known_extra: set, dicts: dict,
               length_unit_unconfirmed: bool,
               *, array_index: int | None = None) -> None:
    """递归校验树节点。node={"code","type",children|items,...}。"""
    t = node.get("type") or "object"
    disp = path if array_index is None else f"{path}[{array_index}]"
    children = {c["code"]: c for c in node.get("children") or []}
    if t == "array":
        if not isinstance(obj, list):
            if obj is not None:
                errs.append(_err(disp, "TYPE",
                                 f"{disp} 须为数组（实际 {type(obj).__name__}）"))
            return
        items_node = node.get("items") or {"type": "object"}
        if node.get("required") and len(obj) == 0:
            errs.append(_err(disp, "REQUIRED_EMPTY",
                             f"{disp} 为必填数组但为空容器"))
        # 完整数组：第 6 项/第 100 项同样校验（不再只查 v[:5]）
        for i, item in enumerate(obj):
            _walk_tree(item, items_node, path, errs, known_extra, dicts,
                       length_unit_unconfirmed, array_index=i)
        return
    # object
    if not isinstance(obj, dict):
        if obj is not None:
            errs.append(_err(disp, "TYPE",
                             f"{disp} 须为对象（实际 {type(obj).__name__}）"))
        return
    # 必填子字段：缺键/空值/空必填容器 → REQUIRED / REQUIRED_EMPTY
    for code, child in children.items():
        cpath = f"{disp}.{code}" if disp else code
        if child.get("required"):
            if code not in obj:
                errs.append(_err(cpath, "REQUIRED",
                                 f"{child.get('name') or cpath} 必填"))
            elif _is_empty(obj[code]):
                if isinstance(obj[code], (dict, list)):
                    errs.append(_err(cpath, "REQUIRED_EMPTY",
                                     f"{child.get('name') or cpath}"
                                     " 为必填容器但为空"))
                else:
                    errs.append(_err(cpath, "REQUIRED",
                                     f"{child.get('name') or cpath} 必填"
                                     "（空值）"))
    # 逐键校验（UNKNOWN 同级仅 warn，不短路其余子字段）
    for k, v in obj.items():
        cpath = f"{disp}.{k}" if disp else k
        child = children.get(k)
        if child is None:
            if cpath not in known_extra:
                errs.append(_err(cpath, "UNKNOWN_FIELD",
                                 f"未知字段 {cpath}（样例有而规范无，待人工核对）",
                                 level="warn"))
            continue
        if child.get("required") is True and _is_empty(v):
            continue                        # 已按 REQUIRED/REQUIRED_EMPTY 记
        if v is None:
            continue                        # 可选空值：明确允许
        if (child.get("type") in ("object", "array")):
            _walk_tree(v, child, cpath, errs, set(), dicts,
                       length_unit_unconfirmed)
        else:
            errs.extend(_check_leaf(cpath, v, child, dicts,
                                    length_unit_unconfirmed))


def _validate_tree(message: dict, fields_contract: dict,
                   envelope_contract: dict | None,
                   dicts: dict) -> list[dict]:
    errs: list[dict] = []
    length_unc = str(fields_contract.get("length_unit", "")) == "UNCONFIRMED"
    env_children = [c for c in
                    (envelope_contract or {}).get("children") or
                    (envelope_contract or {}).get("nodes") or []
                    if c.get("code") != "input"]
    env_nodes = {c["code"]: c for c in env_children}
    env_known = set(env_nodes)
    # envelope 顶层（message 根，除 input）
    _walk_tree({k: v for k, v in message.items() if k != "input"},
               {"type": "object", "children": env_children},
               "", errs, set(), dicts, length_unc)
    # input 子树
    body = message.get("input")
    input_children = fields_contract.get("children") or \
        fields_contract.get("nodes") or []
    has_required = any(c.get("required") for c in input_children)
    if not isinstance(body, dict):
        if has_required or True:
            errs.append(_err("input", "REQUIRED", "input 必填且须为对象"))
    else:
        _walk_tree(body,
                   {"type": "object", "children": input_children},
                   "input", errs, set(), dicts, length_unc)
    _ = env_known
    if length_unc and any(e["code"] == "MAX_LEN" for e in errs):
        errs.append({"path": "*", "code": "LENGTH_UNIT_UNCONFIRMED",
                     "level": "warn",
                     "message": "规范未说明长度按字符或字节——长度校验为"
                                "演示口径（字符数），不构成正式长度校验通过"})
    return errs


# ---------------------------------------------------------------- 平面兼容（LT-07）
def _check_scalar(path: str, value, node: dict, dicts: dict,
                  length_unit_unconfirmed: bool) -> list[dict]:
    return _check_leaf(path, value, node, dicts, length_unit_unconfirmed)


def _validate_flat(message: dict, fields_contract: dict,
                   envelope_contract: dict | None,
                   dicts: dict) -> list[dict]:
    errs: list[dict] = []
    length_unc = str(fields_contract.get("length_unit", "")) == "UNCONFIRMED"
    env_nodes = {n["code"]: n for n in
                 (envelope_contract or {}).get("nodes", [])}
    body_nodes = {n["code"]: n for n in fields_contract.get("nodes", [])
                  if n.get("source") in _VALID_SOURCES}

    def walk(obj: dict, nodes: dict, prefix: str, known_extra: set):
        for k, node in nodes.items():
            if node.get("required") and k not in obj and \
                    f"{prefix}{k}" not in ("input",):
                errs.append(_err(f"{prefix}{k}", "REQUIRED",
                                 f"{node.get('name') or prefix + k} 必填"))
        for k, v in obj.items():
            path = f"{prefix}{k}"
            node = nodes.get(k)
            if node is None and path not in known_extra:
                errs.append(_err(path, "UNKNOWN_FIELD",
                                 f"未知字段 {path}（样例有而规范无，待人工核对）",
                                 level="warn"))
                continue
            if node is None:
                continue
            if node.get("required") and (v is None or v == ""):
                errs.append(_err(path, "REQUIRED",
                                 f"{node.get('name') or path} 必填"))
                continue
            if isinstance(v, list):
                for i, item in enumerate(v[:5]):      # 平面旧口径（保留）
                    ipath = f"{path}[{i}]"
                    if isinstance(item, dict):
                        walk(item, nodes, ipath + ".", set())
                    else:
                        errs.extend(_check_scalar(ipath, item, node, dicts,
                                                  length_unc))
                continue
            if isinstance(v, dict):
                walk(v, nodes, path + ".", set())
                continue
            errs.extend(_check_scalar(path, v, node, dicts, length_unc))

    env_known = {n for n in env_nodes if n != "input"}
    env_nodes_no_input = {k: v for k, v in env_nodes.items() if k != "input"}
    walk({k: v for k, v in message.items() if k != "input"},
         env_nodes_no_input, "", env_known)
    body = message.get("input")
    if not isinstance(body, dict):
        if any(n.get("required") for n in fields_contract.get("nodes", [])):
            errs.append(_err("input", "REQUIRED", "input 必填且须为对象"))
    else:
        walk(body, body_nodes, "input.", set())

    if length_unc and any(e["code"] == "MAX_LEN" for e in errs):
        errs.append({"path": "*", "code": "LENGTH_UNIT_UNCONFIRMED",
                     "level": "warn",
                     "message": "规范未说明长度按字符或字节——长度校验为"
                                "演示口径（字符数），不构成正式长度校验通过"})
    return errs


def validate_message(message: dict, fields_contract: dict,
                     envelope_contract: dict | None = None,
                     dicts: dict | None = None) -> list[dict]:
    """message={envelope 字段..., input:{...}}；返回错误列表（中文）。

    schema_version 路由：1/缺省=平面（LT-07 兼容）；2=树形递归（RT-04 B3）；
    其他 → UNKNOWN_SCHEMA_VERSION 错（不默认按旧树解释）。
    """
    dicts = dicts or {}
    ver = fields_contract.get("schema_version", 1)
    if ver == 2:
        return _validate_tree(message, fields_contract, envelope_contract,
                              dicts)
    if ver in (1, None):
        return _validate_flat(message, fields_contract, envelope_contract,
                              dicts)
    from contracts import ApiError
    raise ApiError(UNKNOWN_SCHEMA_VERSION,
                   f"未知的契约 schema_version={ver!r}；支持 1(平面)/2(树形)，"
                   "拒绝默认按旧树解释")
