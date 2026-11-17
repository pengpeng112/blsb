"""适配器客户端（W05 → FIX2-01/02 修订）：两上传方法 + 单据状态查询。

上传方法路由（DTR-B02）：
- uploadinoutbill ← profile alihealth_yljg_inout_v1（供应商流转）
- uploadretail    ← profile alihealth_yljg_retail_v1（患者发/退药）
绝不按「OUT 方向」选方法；profile 由业务分类决定，分类不明在领域层已等待。

时间戳：TOP 规范 yyyy-MM-dd HH:mm:ss，显式按 GMT+8 墙钟生成（不依赖容器
未验证的本地时区，FIX2-01）；瞬时参数不进业务快照。

签名豁免（R2-04 修订）：未验签阶段只允许 MockTransport（类型强绑定），
与密钥字符串前缀无关——真实 transport 上的任何密钥都会被拒绝。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from connector_sdk import CapabilityError

from . import protocol as P

METHODS = {
    "alibaba.alihealth.drugtrace.top.yljg.uploadinoutbill",
    "alibaba.alihealth.drugtrace.top.yljg.uploadretail",
    "alibaba.alihealth.drugtrace.top.yljg.query.billstatus",
}

TOP_TZ = timezone(timedelta(hours=8))  # TOP 平台墙钟 GMT+8（显式，不依赖容器 TZ)


def _timestamp() -> str:
    return datetime.now(TOP_TZ).strftime("%Y-%m-%d %H:%M:%S")


class AlihealthDrugTraceClient:
    """transport + protocol 的薄封装。

    app_secret_ref 为 SecretRef 引用（不落明文）；resolve_secret 由受限后端
    注入。未验签阶段的离线通道以 MockTransport 类型判定，不再看密钥前缀。
    """

    def __init__(self, transport, *, app_key: str, app_secret_ref: str,
                 resolve_secret, session: str = ""):
        if not app_key:
            raise CapabilityError("CAPABILITY_UNSUPPORTED", "app_key required")
        self.transport = transport
        self.app_key = app_key
        self.app_secret_ref = app_secret_ref   # SecretRef 引用，不落明文
        self._resolve_secret = resolve_secret  # fn(ref)->secret（受限后端）
        self.session = session

    def _secret(self) -> str:
        sec = self._resolve_secret(self.app_secret_ref)
        if not sec:
            raise CapabilityError("CAPABILITY_UNSUPPORTED",
                                  f"secret {self.app_secret_ref} unresolved")
        return sec

    # ------------------------------------------------------------ 上传
    def upload(self, method: str, biz: dict[str, str]) -> dict:
        if method not in METHODS or method.endswith("query.billstatus"):
            raise CapabilityError("CAPABILITY_UNSUPPORTED",
                                  f"method not allowed for upload: {method}")
        secret = self._secret()
        P.assert_signing_allowed(self.transport, secret)
        body, ctype = P.build_form(method, self.app_key, secret,
                                   biz_params=biz, timestamp=_timestamp(),
                                   session=self.session)
        result = self.transport.post(body, ctype)
        if result.error and result.http_status is None:
            # 传输失败（超时/断连/TLS）：UNKNOWN——可能已到上游，上层必须先查询
            return {"outcome": P.UploadOutcome.UNKNOWN,
                    "error": {"reason": "transport-failure",
                              "detail": result.error, "timeout": result.timeout}}
        return P.parse_response(method, result.http_status, result.body)

    def upload_inout(self, biz: dict[str, str]) -> dict:
        return self.upload("alibaba.alihealth.drugtrace.top.yljg.uploadinoutbill", biz)

    def upload_retail(self, biz: dict[str, str]) -> dict:
        return self.upload("alibaba.alihealth.drugtrace.top.yljg.uploadretail", biz)

    # ------------------------------------------------------------ 查询
    def query_bill_status(self, biz: dict[str, str]) -> dict:
        method = P.QUERY_METHOD
        secret = self._secret()
        P.assert_signing_allowed(self.transport, secret)
        body, ctype = P.build_form(method, self.app_key, secret,
                                   biz_params=biz, timestamp=_timestamp(),
                                   session=self.session)
        result = self.transport.post(body, ctype)
        if result.error and result.http_status is None:
            return {"found": None, "bills": {"list": []}, "total_num": None,
                    "unknown": True, "reason": "transport-failure"}
        return P.parse_query_response(result.http_status, result.body)
