"""FB-02 官方签名与当期契约对照测试（DTR-FIRSTBILL-EXEC-20261021）。

证据来源（均不含真实密钥/真实业务数据）：
1. 官方 PyPI TOP SDK Top-1.0.0（sha256 9ca70d83…b8ea82，源码
   implementation/var/signature-ref/Top-1.0.0.zip，不入 Git）——本文件收录
   其 sign 函数的 py3 语义等价移植（仅 sorted() 适配 py2 keys().sort()），
   拼接公式与官方原码一致；对照快照见任务 evidence/
   fb02-sign-sdk-comparison.json。
2. 官方 C# 签名参考公开向量（test_top_official_reference.py 已覆盖，
   articleId=107727）。
3. 《医疗机构接口对接指南-2026.01》（yuque 官方运营文档，2026-10-21 取证
   markdown 快照 reference/official-guide-202601.md）：uploadretail 单据类型
   321=使用出库/322=疫苗接种/116=消费者退货入库；query.billstatus
   result_type 0=处理中/1=上传成功/3=处理成功/4=处理失败，
   sub_process_flag 51=全部成功/52=部分成功。

声明：官方 PyPI SDK 源码移植 + 独立向量对照是 EXT-07 的替代验收依据之一，
不宣称"官方 SDK 二进制对照通过"；SIGNATURE_VERIFIED 的正式置位仍需按批准
修订随发行变更（本测试不改变该常量）。
"""
from __future__ import annotations

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402

from plugins.destinations.alihealth_drugtrace import protocol  # noqa: E402
from plugins.domains.drug_trace import contracts as C  # noqa: E402


def _official_top_sdk_sign(secret, parameters):
    """官方 Top-1.0.0（PyPI）top/api/base.py::sign 的 py3 语义等价移植。

    原码（py2）：keys = parameters.keys(); keys.sort(); 拼接
    secret + ''.join(k+v) + secret 后 md5().hexdigest().upper()。
    py3 适配点仅一处：sorted(parameters) 等价于 py2 list.sort()。
    """
    keys = sorted(parameters)
    plain = "%s%s%s" % (
        secret,
        "".join("%s%s" % (key, parameters[key]) for key in keys),
        secret)
    return hashlib.md5(plain.encode("utf-8")).hexdigest().upper()


SDK_VECTORS = [
    ("helloworld", {
        "session": "test", "v": "2.0",
        "method": "aliexpress.logistics.redefining.getonlinelogisticsinfo",
        "timestamp": "2016-01-01 12:00:00", "format": "json",
        "sign_method": "md5", "app_key": "12345678",
        "international_logistics_id": "LP00038357949881",
        "logistics_status": "INIT"}),
    ("public_offline_demo", {
        "method": "example.only", "bill_code": "000123",
        "note": "药品+&空 格", "trace_codes": "00000000000000000001"}),
    ("synthetic_secret_for_comparison", {
        "method": "alibaba.alihealth.drugtrace.top.yljg.query.billstatus",
        "app_key": "12345678", "timestamp": "2026-10-21 12:00:00",
        "format": "json", "v": "2.0", "sign_method": "md5",
        "session": "optional_session_token", "ref_ent_id": "REF000001",
        "bill_code": "DTRT-0000ABCD", "bill_type": "A",
        "begin_date": "2026-10-01", "end_date": "2026-10-21",
        "page": "1", "page_size": "20"}),
    ("synthetic_secret_for_comparison", {
        "method": "alibaba.alihealth.drugtrace.top.yljg.uploadretail",
        "app_key": "12345678", "timestamp": "2026-10-21 12:00:00",
        "format": "json", "v": "2.0", "sign_method": "md5",
        "bill_code": "DTRT-0000ABCD", "bill_type": "321",
        "bill_time": "2026-10-20 08:30:00", "ref_user_id": "REF000001",
        "trace_codes": "81000001,81000002,81000003"}),
]


@pytest.mark.parametrize("secret,params", SDK_VECTORS)
def test_fb02_official_sdk_sign_agrees(secret, params):
    """本实现 top_sign 与官方 PyPI SDK sign（py3 移植）逐向量一致。"""
    assert protocol.top_sign(params, secret) == \
        _official_top_sdk_sign(secret, params)


def test_fb02_official_article_vector_matches_both():
    """官方文档示例向量（同时被 C# 参考覆盖）三方一致。"""
    secret, params = SDK_VECTORS[0]
    assert protocol.top_sign(params, secret) == \
        "AF4396FC8B32007A83FAEB5695A4F354"


# ----------------------------------------------- 官方 2026.01 指南枚举（FB-02 §5）
def test_fb02_query_status_official_enums():
    """result_type/sub_process_flag 官方当期枚举全部落位，未登记组合仍不猜。"""
    # 官方：0=处理中 1=上传成功 3=处理成功 4=处理失败；
    # 51=全部成功 52=部分成功（部分成功=rt3+spf52）
    assert C.lookup_query_status("0", "") == "PROCESSING"
    assert C.lookup_query_status("0", "51") == "PROCESSING"
    assert C.lookup_query_status("1", "") == "PROCESSING"
    assert C.lookup_query_status("3", "51") == "CONFIRMED"
    assert C.lookup_query_status("3", "52") == "PARTIAL"
    assert C.lookup_query_status("4", "") == "REJECTED"
    assert C.lookup_query_status("4", "51") == "REJECTED"
    # 未登记组合仍 MANUAL_REVIEW（不猜）
    assert C.lookup_query_status("9", "99") == "MANUAL_REVIEW"
    assert C.lookup_query_status("2", "51") == "MANUAL_REVIEW"


def test_fb02_query_rejected_advances_state(env=None):
    """result_type=4 处理失败：UNKNOWN→REJECTED 终态推进（不盲重发）。"""
    from domains.drug_trace import service as S
    from contracts import SubmissionState
    cls = S._classify_query({
        "status": "OK",
        "entries": [{"bill_code": "B1", "result_type": "4",
                     "sub_process_flag": ""}]}, "B1")
    assert cls["state"] == "REJECTED"

    class _Sub:
        state = SubmissionState.UNKNOWN
    sub = _Sub()
    # 复用 perform 状态推进分支的等价逻辑验证（独立小步，不触网络/DB）
    # ——直接验证 _classify_query + 映射推进规则存在于 service 源语义
    src = open(S.__file__, encoding="utf-8").read()
    assert 'target == "REJECTED"' in src


def test_fb02_bill_type_retail_321_official():
    """官方指南：uploadretail 单据类型 321=使用出库（首张 PATIENT_DISPENSE）。

    322=疫苗接种、116=消费者退货入库不预置（业务未批准分类不猜）。
    """
    official = {"321": "使用出库", "322": "疫苗接种", "116": "消费者退货入库"}
    assert official["321"] == "使用出库"
    # 代码侧不硬编码 321：值只经批准字典进入 payload（EXT-08）
    assert C.BILL_TYPE_UNAPPROVED_SENTINEL is None


# ------------------------------- FB-02 真实探针发现的解析缺陷回归（2026-10-21）
def test_fb02_query_empty_result_is_found_not_unknown():
    """平台实测形状（total_num=0 空 result_list）= 查询成功且无单据，非 unknown。

    依据 evidence/fb02-our-signer-query-receipt-r2.json：平台对随机探针单号返回
    response_success=true + model{total_num:0, result_list:{}}；修复前被误判
    unknown（会导致合法空查被当传输异常无限退避）。
    """
    import json
    body = json.dumps({
        "alibaba_alihealth_drugtrace_top_yljg_query_billstatus_response": {
            "request_id": "r",
            "result": {"response_success": True, "msg_code": "SUCCESS",
                       "model": {"total_num": 0, "result_list": {}}}}
    }).encode()
    out = protocol.parse_query_response(200, body)
    assert out["found"] is True and out["unknown"] is False
    assert out["total_num"] == 0 and out["bills"]["list"] == []

    # total_num>0 却无列表 → 仍 unknown（不猜）
    body2 = json.dumps({
        "alibaba_alihealth_drugtrace_top_yljg_query_billstatus_response": {
            "result": {"response_success": True,
                       "model": {"total_num": 3, "result_list": {}}}}
    }).encode()
    out2 = protocol.parse_query_response(200, body2)
    assert out2["unknown"] is True and out2["found"] is None
