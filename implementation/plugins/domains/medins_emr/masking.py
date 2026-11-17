"""RT-04 B1：医保联调台递归脱敏防线（P0）。

三层防线（任务书 §3）：
1. 契约完整路径 sensitive 标记（平面/树契约均可，路径精确到数组元素层级）；
2. 敏感键名匹配（明确边界词表，含大小写与常见命名变体；避免笼统 `id|name`
   把所有业务字段误当 Secret）；
3. 字符串值模式兜底（18 位身份证 / 11 位手机号整串匹配；不宣称覆盖自由
   文本姓名的所有语义——那属于"安全隐藏/拒绝"策略，不在本模块兜底范围）。

硬规则：
- 递归所有 dict/list 及标量；父节点命中敏感语义时其对象/列表值整体打码，
  不能原样保留子树；
- 数字型个人标识（int/float 的 psn_no 等）同样打码；
- 未知字段（不在契约内）同样递归，不依赖契约才能脱敏；
- 请求大小/深度超限 → 明确 ApiError 拒绝（REQUEST_TOO_LARGE / PAYLOAD_TOO_DEEP），
  绝不跳过深层而原样保存。
"""
from __future__ import annotations

import re

MASK = "***"

# 请求体积/深度上限（联调台报文为手工 JSON，远小于此；超限=明确拒绝）
MAX_TOTAL_BYTES = 256 * 1024
MAX_DEPTH = 12

# ---------------------------------------------------------------- 第2层：敏感键名词表
# 以"词段"匹配（key 按非字母数字切词后比对），避免子串误伤（drugName/
# deptName/list_id 等业务字段不在词段内）。大小写不敏感。
_NAME_PREFIXES = ("psn", "patn", "opter", "coner", "contact",
                  "contacts", "respon", "receiver", "sender")
_PERSON_NAME_TERMS = {"name"}            # 仅当词段前缀属人员语义（psn_name 等）
_EXACT_TERMS = {
    # 姓名/操作人（整词）
    "psn_name", "patn_name", "opter_name", "coner_name",
    "username", "account_name",
    # 证件
    "certno", "cert_no", "certcode", "cert_code", "idcard", "id_card",
    "idcardno", "id_card_no", "psn_cert", "psn_cert_no", "psncertno",
    # 电话
    "mob", "mobile", "tel", "telno", "tel_no", "phone", "phoneno",
    "phone_no", "telephone", "contact_tel", "contact_way",
    # 地址
    "addr", "address", "curr_addr", "home_addr", "email",
    # 个人/医保个人编号（患者与就诊 ID 分离，两者都属个人标识）
    "psn_no", "psn_id", "psno", "patn_id", "patn_no", "patient_id",
    "medins_file_no", "psn_cert_type",
    # 签名/凭据类
    "signature", "signature_value", "token", "access_token",
    "secret", "secret_key", "password", "passwd", "pwd", "credential",
    "credentials", "cainfo",
}
# 键名包含（不限词段）即命中的强凭据语义
_CONTAINS_TERMS = ("certno", "idcard", "id_card", "psn_cert",
                   "medins_file_no", "password", "passwd", "secret",
                   "signature", "access_token")

# 第3层：字符串值整串模式兜底（fullmatch 语义；自由文本替换复用无锚形式）
_VALUE_ID = r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:[0-2]\d|3[01])\d{3}[\dXx]"
_VALUE_MOB = r"1[3-9]\d{9}"
_VALUE_PATTERNS = (
    re.compile(rf"^{_VALUE_ID}$"),            # 18 位身份证（真实日历段）
    re.compile(rf"^{_VALUE_MOB}$"),           # 11 位手机号
)
_VALUE_PATTERNS_UNANCHORED = (
    re.compile(_VALUE_ID),
    re.compile(_VALUE_MOB),
)

_SEG_SPLIT = re.compile(r"[^0-9a-zA-Z]+")


def _key_is_sensitive(key: str) -> bool:
    """敏感键名判定（第2层）：整键匹配 + 词段匹配 + 人员前缀姓名 + 强凭据子串。"""
    k = str(key or "").lower()
    if not k:
        return False
    if k in _EXACT_TERMS:                     # 整键（psn_no / certno / tel…）
        return True
    if any(t in k for t in _CONTAINS_TERMS):
        return True
    segs = [s for s in _SEG_SPLIT.split(k) if s]
    for i, s in enumerate(segs):
        if s in _EXACT_TERMS:
            return True
        # name 词段仅当左侧紧邻词段属人员语义（psn_name / patn_name /
        # opter_name / coner_name / contact_name …），drugName/list_id 不中
        if s == "name" and i > 0 and segs[i - 1] in _NAME_PREFIXES:
            return True
    return False


def _value_is_sensitive(v) -> bool:
    """第3层：字符串值整串模式兜底（身份证/手机号）。"""
    if not isinstance(v, str):
        return False
    return any(p.match(v) for p in _VALUE_PATTERNS)


# ---------------------------------------------------------------- 第1层：契约路径
def contract_sensitive_paths(contract_nodes: list[dict] | dict | None) -> set[str]:
    """契约 sensitive 标记 → 完整路径集合。

    支持两种契约形状：
    - 平面契约（LT-07）：nodes=[{code, sensitive, ...}]，路径=code（input.*
      顶层 / envelope 顶层）；
    - 树契约（RT-04 B3）：{schema_version, root, children:[{code,type,
      sensitive,children|items}]}——递归展开 object.children 与 array.items
      路径（数组元素路径不带下标：a.b[].c）。
    """
    out: set[str] = set()

    def walk_node(n: dict, prefix: str):
        code = str(n.get("code") or n.get("path") or "")
        path = f"{prefix}.{code}" if (prefix and code) else (prefix or code)
        if n.get("sensitive"):
            out.add(path)
        if n.get("type") == "object" or "children" in n:
            for c in n.get("children") or []:
                if isinstance(c, dict):
                    walk_node(c, path)
        if n.get("type") == "array" or "items" in n:
            items = n.get("items")
            if isinstance(items, dict):
                walk_node(items, path + "[]")

    nodes = contract_nodes
    if isinstance(nodes, dict):
        nodes = nodes.get("children") or nodes.get("nodes")
    for n in nodes or []:
        if isinstance(n, dict):
            walk_node(n, "")
    return out


def _norm_path(path: str) -> str:
    """实际路径 → 契约路径形态（数组下标归一为 []）。"""
    return re.sub(r"\[\d+\]", "[]", path)


# ---------------------------------------------------------------- 主体
def _reject_limits(root, total_bytes: int):
    if total_bytes > MAX_TOTAL_BYTES:
        from contracts import ApiError
        raise ApiError("REQUEST_TOO_LARGE",
                       f"报文超过 {MAX_TOTAL_BYTES // 1024}KB 上限，拒绝处理"
                       "（不截断、不跳过深层）")
    from contracts import ApiError

    def depth(obj, d: int) -> int:
        if d > MAX_DEPTH:
            raise ApiError("PAYLOAD_TOO_DEEP",
                           f"报文嵌套深度超过 {MAX_DEPTH} 层上限，拒绝处理"
                           "（不跳过深层而原样保存）")
        if isinstance(obj, dict):
            return max((depth(v, d + 1) for v in obj.values()), default=d)
        if isinstance(obj, list):
            return max((depth(v, d + 1) for v in obj), default=d)
        return d

    depth(root, 0)


def _mask(obj, *, path: str, sensitive_paths: set[str]):
    """递归打码。命中规则：
    - 当前路径在契约 sensitive 集合（含数组下标归一）→ 整值打码；
    - 键名敏感（父语义）→ 整值打码（对象/列表不保留）；
    - 标量字符串命中值模式 → 打码。
    返回安全副本（不改原对象）。
    """
    if _norm_path(path) in sensitive_paths:
        return MASK
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            child = f"{path}.{k}" if path else str(k)
            if _key_is_sensitive(k) or _norm_path(child) in sensitive_paths:
                out[k] = MASK          # 父语义命中：对象/列表整体打码
            else:
                out[k] = _mask(v, path=child, sensitive_paths=sensitive_paths)
        return out
    if isinstance(obj, list):
        return [_mask(v, path=f"{path}[]", sensitive_paths=sensitive_paths)
                for v in obj]
    if _value_is_sensitive(obj):
        return MASK
    return obj


def ensure_limits(message, total_bytes: int | None = None) -> None:
    """入口体积/深度守卫（超限抛 ApiError；各路由处理报文前先调用）。"""
    if total_bytes is None:
        import json
        total_bytes = len(json.dumps(message, ensure_ascii=False,
                                      default=str).encode("utf-8"))
    _reject_limits(message, total_bytes)


def mask_sensitive(message, *, contract_nodes: list | dict | None = None,
                   envelope_nodes: list | dict | None = None,
                   total_bytes: int | None = None):
    """envelope + input 全量递归打码（安全副本）。

    - message：完整报文（envelope 字段与 input 同级）；
    - contract_nodes：业务 input 契约（平面 nodes 或树 schema）；
    - envelope_nodes：公共报文头契约（同上）；
    - total_bytes：调用方已知的报文字节数（None 时按 canonical 序列化估算）。
    超限抛 ApiError（REQUEST_TOO_LARGE / PAYLOAD_TOO_DEEP）。
    """
    if total_bytes is None:
        import json
        total_bytes = len(json.dumps(message, ensure_ascii=False,
                                      default=str).encode("utf-8"))
    _reject_limits(message, total_bytes)
    paths = set()
    for nodes in (contract_nodes, envelope_nodes):
        if nodes:
            paths |= contract_sensitive_paths(nodes)
    if not isinstance(message, dict):
        return _mask(message, path="", sensitive_paths=paths)
    # input 子树路径带 "input." 前缀（契约 code 为 input 顶层字段）
    masked_input = None
    if isinstance(message.get("input"), (dict, list)):
        sub = _mask(message["input"], path="input", sensitive_paths=paths)
        masked_input = sub
    rest = {k: v for k, v in message.items() if k != "input"}
    masked_rest = _mask(rest, path="", sensitive_paths=paths)
    if masked_input is not None:
        masked_rest["input"] = masked_input
    return masked_rest


def mask_free_text(text: str, *, max_len: int = 500) -> str:
    """自由文本（人工 note / 回填说明）脱敏：值模式替换 + 标注式PII隐藏 + 截断。

    - 身份证/手机号模式整串替换（无锚定，可命中句中片段）；
    - 「姓名/证件/电话/地址/签名」等标注后跟的取值整体隐藏——这是对
      **带标注**自由文本的安全隐藏策略；不宣称覆盖无标注自由文本姓名的
      所有语义（该残留风险在交接如实登记）；
    - 键值形式 token=xxx / password：xxx 一律隐藏。
    """
    import re as _re
    if not text:
        return ""
    out = str(text)
    for p in _VALUE_PATTERNS_UNANCHORED:
        out = p.sub(MASK, out)
    out = _re.sub(
        r"(?i)(姓名|名字|患者名|联系人|证件号?|身份证|手机|电话|号码|地址)"
        r"\s*[:：是]?\s*([^\s，。;；,\.]{2,24})",
        lambda m: m.group(1) + "=" + MASK, out)
    out = _re.sub(r"(?i)(token|password|secret|signature)\s*[=:：]\s*\S+",
                  lambda m: m.group(1) + "=" + MASK, out)
    if len(out) > max_len:
        out = out[:max_len] + "…"
    return out
