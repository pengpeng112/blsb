"""RT-04 A0：传染病影子「双时间与投递证据」契约（纯定义与纯函数）。

每条影子记录的证据字段（含义与边界）：
====================  ======================================================
字段                   语义
====================  ======================================================
tenant/target          租户与上报业务编码（inf_cdc_frontend）
source_instance        来源实例标识（多实例互不合并；缺省 "default"）
stream/table           流/表键（TABLES 键，如 exam / exam_item）
source_record_key      源业务键（记录 ID 字符串——**保留前导零，不转 int**）
source_op_time         源业务时间（视图 OPERATION_TIME；**源版本身份组成**，
                       与上传时间无关；缺失=质量失败，不得用上传时刻伪造）
collect_time           采集时间（本地扫描到该行的时刻）
freeze_time            冻结时间（不可变快照生成时刻；固定配置 revision）
upload_time            上传时间（报文 operationTime 重写为接口上传时刻，
                       每次重写可不同——**不参与幂等身份**）
source_version_digest  源版本摘要（规范化源行内容的稳定 SHA256；同 op_time
                       不同内容靠它显式暴露冲突，不静默覆盖）
snapshot_digest        不可变快照摘要（PackageRevision.digest=冻结 payload
                       按仓库 canonical_json 的完整 SHA256；与
                       Submission.snapshot_digest 一致）
submission_state       Submission 状态（PREPARED/ACCEPTED/…/UNKNOWN）
mock                   该次投递是否 Mock（Mock 证据不得当真实成功）
patient_id / visit_id  患者 ID 与就诊 ID **分开**（均为字符串，保留前导零）
====================  ======================================================

水位与已发版本语义（硬定义）：
- 水位（source_cursor）= 已经**可靠持久化到本地待处理队列/快照**的最大
  扫描键 (OPERATION_TIME, 业务键)——不是"读到过的最大时间"。未持久化任何
  队列的本轮实现采用**保守推进**：只推进到"全部记录已终态处理（成功投递/
  显式质量失败/显式冲突）"的最大扫描键，失败/UNKNOWN 记录之后的键不越过
  （性能影响：失败记录会阻塞水位、后续班次重扫重叠窗，见交接说明）。
- 已发版本（delivered_result 非空）= 得到**明确投递结果**
  （ACCEPTED/CONFIRMED/PARTIAL）的源版本。UNKNOWN/RETRYABLE/质量失败/
  冲突**不进入**已发集合——重试时按同一源版本幂等键重新投递，而不是
  造新版本。
- 禁止读到一页就推进到页末最大时间而丢掉失败记录。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ShadowEvidence:
    """单条影子记录的完整证据（A0 契约载体；各字段语义见模块 docstring）。"""
    tenant_id: str
    target_code: str = "inf_cdc_frontend"
    source_instance: str = "default"
    table: str = ""
    source_record_key: str = ""          # 字符串；前导零保留
    source_op_time: str = ""             # 规范化 "YYYY-MM-DD HH:MM:SS"（源时区 CST）
    patient_id: str = ""                 # 患者与就诊分离（均字符串）
    visit_id: str = ""
    collect_time: str = ""
    freeze_time: str = ""
    upload_time: str = ""                # 每次重写不同；不参与身份
    source_version_digest: str = ""      # 源行内容稳定摘要
    snapshot_digest: str = ""            # 冻结 payload 完整 SHA256
    submission_id: str = ""
    submission_state: str = ""
    mock: bool = True
    quality_issues: list[str] = field(default_factory=list)

    def identity(self) -> str:
        """幂等身份（A1）：tenant+target+source_instance+table/record+
        规范化 source_op_time（+源内容 digest 冲突检测独立进行）。"""
        return (f"{self.tenant_id}|{self.target_code}|{self.source_instance}"
                f"|{self.table}/{self.source_record_key}"
                f"|{self.source_op_time}")

    def version_key(self) -> str:
        """源版本键 = op_time + 源内容摘要（同时间不同内容=不同版本→冲突）。"""
        return f"{self.source_op_time}|{self.source_version_digest[:16]}"


def normalize_op_time(raw) -> str:
    """源时间规范化：字符串直通（保留原样）；datetime→CST 字符串；
    空值返回 ""（调用方按质量失败处理，不得伪造）。"""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    from datetime import timezone, timedelta
    CST = timezone(timedelta(hours=8))
    if hasattr(raw, "astimezone"):
        dt = raw if raw.tzinfo else raw.replace(tzinfo=CST)
        return dt.astimezone(CST).strftime("%Y-%m-%d %H:%M:%S")
    return str(raw)


def scan_key(op_time: str, record_key: str) -> str:
    """扫描键（ORDER BY 对）：(OPERATION_TIME, 稳定业务键)。"""
    return f"{op_time}|{record_key}"
