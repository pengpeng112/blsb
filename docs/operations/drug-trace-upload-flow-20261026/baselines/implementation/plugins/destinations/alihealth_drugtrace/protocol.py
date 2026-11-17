"""TOP 协议编码与响应解析（W05 → FIX2-01 → FB-02 签名资格修订）。

签名算法按 TOP 公开规范实现（MD5: uppercase(md5(secret + 按key排序的k+v拼接 +
secret))）。EXT-07 签名一致性证据已于 DTR-FIRSTBILL-EXEC-20261021 按批准修订
关闭（官方 C# 参考向量 + 官方 PyPI SDK 源码移植对照 + 平台两次真实受理；依据
链见 SIGNATURE_VERIFIED 常量处注释与任务 evidence）。真实 transport 的签名
调用仍受其余门禁约束（业务证据/配额/DMZ 白名单独立生效）。

响应契约（2026-10-17 官方页复核，FIX2-01）：
- 上传方法响应键 `{method}_response` 内为 `response_success`（布尔）+ 可选
  model/msg_code/msg_info；不再解析自行假设的 `success` 字段。
- 查询方法响应为嵌套 `result.response_success / result.model.total_num /
  result.model.result_list.bill_deal_status_search_do`（R2-02 修订）。
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, quote

from connector_sdk import CapabilityError

SIGN_METHOD_MD5 = "md5"
FORMAT_JSON = "json"
API_VERSION = "2.0"

# 签名一致性证据（EXT-07）——DTR-FIRSTBILL-EXEC-20261021 按批准修订置 True。
# 依据链（全部可审查，不含真实密钥）：
# 1. 官方 C# 签名参考（developer.alibaba.com articleId=107727）公开向量 4 例
#    逐字一致（docs/development/platform-live-test-20261020/evidence/
#    public-reference-vectors.json；tests/integration/test_top_official_reference.py）。
# 2. 官方 PyPI TOP SDK Top-1.0.0（zip sha256 9ca70d83…b8ea82）sign 函数
#    py3 语义等价移植对照 4 向量一致，含官方文档示例向量与中文/前导零/session
#    用例（docs/development/drug-trace-firstbill-exec-20261021/evidence/
#    fb02-sign-sdk-comparison.json；tests/integration/test_drug_trace_fb02_
#    official.py）。已知无害差异（空值过滤/表单编码/时间戳格式）在该证据单列。
# 3. 平台实际接受：2026-10-20 两次真实只读调用（query.billstatus + 企业目录）
#    由独立参考签名器签名并被平台受理成功（response_success=true；receipts
#    platform-live-test-20261020/evidence/*-probe-receipt.json）。
# 置 True 不放宽任何业务门禁：证据/配额/DMZ 白名单/scope 校验独立生效；
# 真实发送仍需 send 证据集合。回滚本资格=随发行回退本常量，不改运行时状态。
SIGNATURE_VERIFIED = True

# 转发器/客户端允许的 TOP 方法白名单（仅药品追溯三方法；FIX2-06 同步）
ALLOWED_TOP_METHODS = frozenset({
    "alibaba.alihealth.drugtrace.top.yljg.uploadinoutbill",
    "alibaba.alihealth.drugtrace.top.yljg.uploadretail",
    "alibaba.alihealth.drugtrace.top.yljg.query.billstatus",
})


def top_sign(params: dict[str, str], app_secret: str) -> str:
    """TOP MD5；与官方 C# 参考一致，空参数名/空字符串值不参与拼接。

    调用方传入尚未添加 sign 的字符串参数；不在此处 URL 编码。
    官方参考代码的局部对照不等于 SDK 验收，正式签名门禁仍独立生效。
    """
    plain = "".join(f"{k}{params[k]}" for k in sorted(params) if k and params[k])
    return hashlib.md5((app_secret + plain + app_secret).encode("utf-8")).hexdigest().upper()


def assert_signing_allowed(transport, app_secret: str) -> None:
    """签名前置断言。

    - EXT-07 已按批准修订关闭（见 SIGNATURE_VERIFIED 证据链注释）。
    - 保留与资格无关的安全属性：mock 标记密钥（`mock:` SecretRef 经
      resolve_secret_default 解析为 `__mock__…`）**永远**不允许上真实
      transport——真实目的地必须在配置层使用 env: SecretRef（R2-04/T06
      语义随签名资格修订保留，防御纵深不变）。
    """
    from .transport import MockTransport
    if not isinstance(transport, MockTransport) and \
            app_secret.startswith("__mock__"):
        raise CapabilityError(
            "CAPABILITY_UNSUPPORTED",
            "mock-marked secret must never sign a real transport; use an "
            "env: SecretRef injected in the restricted worker environment")


def build_form(method: str, app_key: str, app_secret: str, *,
               biz_params: dict[str, str], timestamp: str, session: str = "",
               sign_method: str = SIGN_METHOD_MD5) -> tuple[bytes, str]:
    """构造 application/x-www-form-urlencoded 请求体与签名。

    返回 (body_bytes, content_type)。表单值按 UTF-8 百分号编码（TOP 规范）；
    签名覆盖全部业务参数，timestamp/sign 为瞬时传输参数，不进入业务快照审计。
    签名资格校验由调用方（client）先经 `assert_signing_allowed` 完成。
    """
    params: dict[str, str] = {
        "method": method,
        "app_key": app_key,
        "timestamp": timestamp,
        "format": FORMAT_JSON,
        "v": API_VERSION,
        "sign_method": sign_method,
    }
    if session:
        params["session"] = session
    params.update({k: str(v) for k, v in biz_params.items()})
    params["sign"] = top_sign(params, app_secret)
    parts = [f"{quote(k, safe='')}={quote(params[k], safe='')}"
             for k in sorted(params)]
    return "&".join(parts).encode("utf-8"), "application/x-www-form-urlencoded;charset=utf-8"


def method_response_key(method: str) -> str:
    return f"{method.replace('.', '_')}_response"


def extract_top_method(body: bytes) -> str | None:
    """从表单体提取 TOP method（转发器白名单用）。

    返回 None 表示缺失；`duplicate` 由调用方拒绝（歧义）。
    """
    values = parse_qs(body.decode("utf-8", errors="replace"),
                      keep_blank_values=True)
    methods = values.get("method") or []
    if len(methods) != 1:
        return None
    return methods[0] or None


class UploadOutcome:
    ACCEPTED = "ACCEPTED"   # 受理（传输/网关层成功；业务确认另查）
    PARTIAL = "PARTIAL"     # 部分成功（含逐码明细时）
    REJECTED = "REJECTED"   # 明确拒绝（业务/数据/权限错误）
    UNKNOWN = "UNKNOWN"     # 结构无法判定/未知枚举 → 先查询，不盲重发


def parse_response(method: str, http_status: int, body: bytes) -> dict:
    """解析 TOP JSON envelope → {outcome, raw, parsed, error}。

    - HTTP 非 200：UNKNOWN（网关语义不可靠，交由上层查询）。
    - error_response 存在：按 code 分类（认证/权限/限流/数据错误）。
    - 方法响应键内按官方 `response_success` 判定；缺该字段 → UNKNOWN
      （不用自行假设的 `success` 字段制造兼容假象，R2-02）。
    - 非 JSON（HTML 错误页等）：UNKNOWN，不伪装成功。
    """
    if http_status != 200:
        return {"outcome": UploadOutcome.UNKNOWN, "http_status": http_status,
                "parsed": {}, "error": {"reason": f"http {http_status}"}}
    text = body.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except ValueError:
        return {"outcome": UploadOutcome.UNKNOWN, "http_status": http_status,
                "parsed": {}, "error": {"reason": "non-JSON body",
                                         "head": text[:120]}}
    if not isinstance(data, dict):
        return {"outcome": UploadOutcome.UNKNOWN, "http_status": http_status,
                "parsed": {}, "error": {"reason": "non-dict JSON"}}
    err = data.get("error_response")
    if isinstance(err, dict):
        code = str(err.get("code", ""))
        msg = str(err.get("msg", "") or err.get("sub_msg", ""))
        outcome = UploadOutcome.REJECTED
        # 限流/系统繁忙：可重试类别（上层退避；不自动立即重发）
        if code.startswith("10") or "limit" in msg.lower() or \
                "busy" in msg.lower() or code in ("5", "7"):
            outcome = UploadOutcome.UNKNOWN
        return {"outcome": outcome, "http_status": http_status,
                "parsed": {}, "error": {"code": code, "msg": msg},
                "error_code": code}
    key = method_response_key(method)
    resp = data.get(key)
    if resp is None:
        return {"outcome": UploadOutcome.UNKNOWN, "http_status": http_status,
                "parsed": {}, "error": {"reason": f"missing {key}",
                                         "keys": sorted(data.keys())}}
    out = {"outcome": UploadOutcome.UNKNOWN, "http_status": http_status,
           "parsed": resp, "error": {}}
    if "response_success" in resp:
        if resp.get("response_success") is True:
            out["outcome"] = UploadOutcome.ACCEPTED
        else:
            out["outcome"] = UploadOutcome.REJECTED
            out["error"] = {"msg_code": str(resp.get("msg_code", "")),
                            "msg_info": str(resp.get("msg_info", ""))}
        # 官方部分成功以查询侧 sub_process_flag 为准；上传侧 fail_infos 仅当
        # 官方文档确认后启用（当前未知形状一律不猜）。
        if out["outcome"] == UploadOutcome.REJECTED and \
                isinstance(resp.get("fail_infos"), list) and resp["fail_infos"]:
            out["partial_details"] = resp["fail_infos"]
    return out


QUERY_METHOD = "alibaba.alihealth.drugtrace.top.yljg.query.billstatus"


def parse_query_response(http_status: int, body: bytes) -> dict:
    """query.billstatus 解析 → {found, bills, total_num, unknown, reason?}。

    官方嵌套形状（2026-10-17 页复核，R2-02 修订）：
      {key: {"result": {"response_success": true, "model": {"total_num": N,
        "result_list": {"bill_deal_status_search_do": [entry, ...]}}}}}
    错误页/error_response/形状不明 → unknown=True 或 error，由上层转待核实，
    绝不猜成成功。枚举解释（result_type/sub_process_flag）由领域 service 做。
    """
    if http_status != 200:
        return {"found": None, "bills": {"list": []}, "total_num": None,
                "unknown": True, "reason": f"http {http_status}"}
    text = body.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except ValueError:
        return {"found": None, "bills": {"list": []}, "total_num": None,
                "unknown": True, "reason": "non-JSON body"}
    if isinstance(data, dict) and isinstance(data.get("error_response"), dict):
        err = data["error_response"]
        return {"found": None, "bills": {"list": []}, "total_num": None,
                "unknown": False,
                "error": {"code": str(err.get("code", "")),
                          "msg": str(err.get("msg", ""))}}
    key = method_response_key(QUERY_METHOD)
    resp = data.get(key) if isinstance(data, dict) else None
    if not isinstance(resp, dict) or not isinstance(resp.get("result"), dict):
        return {"found": None, "bills": {"list": []}, "total_num": None,
                "unknown": True, "reason": "missing query response key/result"}
    result = resp["result"]
    if result.get("response_success") is not True:
        return {"found": None, "bills": {"list": []}, "total_num": None,
                "unknown": False,
                "error": {"msg_code": str(result.get("msg_code", "")),
                          "msg_info": str(result.get("msg_info", ""))}}
    model = result.get("model")
    if not isinstance(model, dict):
        return {"found": None, "bills": {"list": []}, "total_num": None,
                "unknown": True, "reason": "model missing/unrecognized"}
    total_num = model.get("total_num")
    entries: list | None = None
    if isinstance(model.get("result_list"), dict):
        container = model["result_list"]
        if isinstance(container.get("bill_deal_status_search_do"), list):
            entries = container["bill_deal_status_search_do"]
    if entries is None:
        # FB-02 真实探针发现（2026-10-21 平台实测）：total_num=0 时平台返回
        # 空 result_list（无 bill_deal_status_search_do 键）——这是「查询成功
        # 且无单据」，不是形状未知。仅 total_num==0 时空集视为 found；
        # total_num>0 却无列表仍 unknown（不猜）。
        if isinstance(total_num, int) and total_num == 0:
            return {"found": True, "bills": {"list": []},
                    "total_num": 0, "unknown": False}
        return {"found": None, "bills": {"list": []}, "total_num": None,
                "unknown": True, "reason": "result_list.bill_deal_status_"
                                           "search_do missing"}
    return {"found": True, "bills": {"list": entries},
            "total_num": total_num if isinstance(total_num, int) else None,
            "unknown": False}
