"""通用契约：错误码、状态枚举、响应包装、ID/时间工具。

对应设计 04 第4、6、7节。所有状态以字符串常量表达，避免枚举散落各处。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------- 错误码（04 §7）
ERROR_CODES = {
    "SOURCE_UNAVAILABLE": (503, True),
    "NETWORK_TIMEOUT": (504, True),
    "SOURCE_AMBIGUOUS": (409, False),
    "IDENTITY_CONFLICT": (409, False),
    "REQUIRED_FIELD_MISSING": (422, False),
    "DICTIONARY_UNMAPPED": (422, False),
    "SOURCE_FILE_CHANGED": (409, False),
    "CHUNK_HASH_MISMATCH": (422, False),
    "FILE_HASH_MISMATCH": (422, False),
    "STORAGE_CAPACITY_LOW": (507, False),
    "VOLUME_OFFLINE": (503, False),
    "IDEMPOTENCY_CONFLICT": (409, False),
    "VERSION_CONFLICT": (412, False),
    "PRECONDITION_REQUIRED": (428, False),
    "CAPABILITY_UNSUPPORTED": (501, False),
    "PROFILE_UNAPPROVED": (409, False),
    "CLOSURE_DRIFTED": (409, False),
    "REMOTE_REJECTED": (502, False),
    "REMOTE_RESULT_UNKNOWN": (202, True),
    "NOT_FOUND": (404, False),
    "VALIDATION_FAILED": (422, False),
    "FORBIDDEN": (403, False),
    "UNAUTHORIZED": (401, False),
    "REQUEST_TOO_LARGE": (413, False),
    "RATE_LIMITED": (429, True),
    "BARCODE_NOT_FOUND": (404, False),
    "WAITING_EXTERNAL": (202, True),
    "WAITING_HUMAN": (202, True),
    "DRIVER_UNAVAILABLE": (501, False),
    "SESSION_EXPIRED": (410, False),
    "CONFLICT": (409, False),
    # LT-03 统一台账
    "TARGET_REQUIRED": (400, False),        # 台账查询缺 target 参数
    "USE_DOMAIN_WORKFLOW": (400, False),    # 药品处置必须走领域专页受控流程
    # LT-03-R1
    "CURSOR_CONTEXT_MISMATCH": (400, False),  # 游标与筛选上下文不匹配（换条件须重新首页）
    # RT-03 S08
    "INVALID_CURSOR": (400, False),          # 游标非法（base64/版本/字段/时间/ID/大小）
    # LT-04
    "JOB_TYPE_UNKNOWN": (400, False),        # 调度引用未注册 job 类型
    "OBSERVE_ONLY_REJECTED": (403, False),   # 镜像调度服务端只观测（enable/run-now/改类型拒绝）
    "BLACKOUT": (400, False),                # 维护窗口内拒绝 run-now（本批无绕过）
    "SECRET_INLINE_FORBIDDEN": (400, False), # 通道配置内联秘密拒绝（只许 *_env 引用）
    "NOT_ALLOWED_THIS_BATCH": (400, False),  # 本批服务端强制停用（smtp/webhook 启用/测试外发）
}


class ApiError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool | None = None,
                 field_errors: list[dict] | None = None, details: dict | None = None):
        super().__init__(message)
        status, default_retryable = ERROR_CODES.get(code, (500, False))
        self.code = code
        self.message = message
        self.status = status
        self.retryable = default_retryable if retryable is None else retryable
        self.field_errors = field_errors or []
        self.details = details or {}

    def to_body(self, request_id: str) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "field_errors": self.field_errors,
                "details_ref": self.details or None,
            },
            "request_id": request_id,
        }


# ---------------------------------------------------------------- 状态常量（04 §6）
class UploadSessionState:
    CREATED = "CREATED"
    RECEIVING = "RECEIVING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class AssetState:
    STAGING = "STAGING"
    VERIFYING = "VERIFYING"
    AVAILABLE = "AVAILABLE"
    QUARANTINED = "QUARANTINED"
    UNAVAILABLE = "UNAVAILABLE"


class BlobState:
    VERIFYING = "VERIFYING"
    AVAILABLE = "AVAILABLE"
    QUARANTINED = "QUARANTINED"


class PackageRevisionState:
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    FROZEN = "FROZEN"
    WAITING = "WAITING"
    INVALID = "INVALID"
    SUPERSEDED = "SUPERSEDED"


class StepRunState:
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    WAITING_HUMAN = "WAITING_HUMAN"
    RETRY_WAIT = "RETRY_WAIT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkflowRunState:
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    WAITING = "WAITING"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobState:
    PENDING = "PENDING"
    LEASED = "LEASED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRY_WAIT = "RETRY_WAIT"
    CANCELLED = "CANCELLED"


class SubmissionState:
    PREPARED = "PREPARED"
    SENDING = "SENDING"
    ACCEPTED = "ACCEPTED"
    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class PublicationState:
    PREPARING = "PREPARING"
    READY = "READY"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"
    REVOKED = "REVOKED"


class OperationState:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class QualityIssueState:
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


# 等待原因（WAITING 状态的 reason_code，10 §7）
WAIT_REASONS = {
    "BARCODE_NOT_FOUND", "SOURCE_AMBIGUOUS", "IDENTITY_CONFLICT",
    "VISIT_UNRESOLVED", "DICTIONARY_UNMAPPED", "CATEGORY_UNCONFIRMED",
    "ORG_CODE_UNBOUND", "APPLICATION_TIME_UNCONFIRMED", "COMPLETENESS_UNKNOWN",
    "EXTERNAL_UNAVAILABLE", "AWAITING_HUMAN_DECISION",
}

# 质量规则码（10 §5）
QUALITY_RULES = {
    "QF-DATE-FUTURE", "QF-DATE-BEFORE-BIRTH", "QF-DATE-ORDER", "QF-TIME-SEMANTICS",
    "QF-ID-TYPE", "QF-ID-CONFLICT", "QF-CATEGORY", "QF-ORG", "QF-STATUS",
    "QF-SOURCE-KEY", "QF-ENCOUNTER", "QF-COMPLETENESS",
}


# ---------------------------------------------------------------- 工具
def new_uuid() -> str:
    return str(uuid.uuid4())


def uuid7ish() -> str:
    """时间有序 UUID（用于审计/排序友好；非安全敏感）。

    注意（LT-09）：当前实现实际返回 uuid4（随机），**排序语义不可依赖**；
    名字保留为兼容层。如需真有序 ID 另立任务实现 uuid7 并做迁移评估。
    """
    return str(uuid.uuid4())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------- 响应包装（04 §4）
def ok(data: Any = None, request_id: str = "") -> dict:
    return {"data": data, "request_id": request_id}


def sha256_hex_of_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def is_sha256(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


# ---------------------------------------------------------------- LT-01 上报业务目录
class TargetLifecycle:
    """上报业务生命周期（ReportTarget.lifecycle；目录台账语义，不驱动发送）。"""
    PLANNED = "PLANNED"          # 已登记需求，未开发
    DEVELOPING = "DEVELOPING"    # 开发中
    MOCK_ONLY = "MOCK_ONLY"      # 只能发 Mock
    PILOT = "PILOT"              # 试点（真实发送须逐次授权）
    PRODUCTION = "PRODUCTION"    # 正式
    SUSPENDED = "SUSPENDED"      # 暂停
    RETIRED = "RETIRED"          # 下线
    ALL = (PLANNED, DEVELOPING, MOCK_ONLY, PILOT, PRODUCTION, SUSPENDED, RETIRED)


class TargetRuntimeKind:
    """上报运行形态（ReportTarget.runtime_kind）。"""
    PLATFORM = "PLATFORM"        # 由本平台插件推送
    EXTERNAL = "EXTERNAL"        # 由平台外程序推送（如 inf_pusher），平台只读观测
    MANUAL = "MANUAL"            # 人工上报，平台只登记
    ALL = (PLATFORM, EXTERNAL, MANUAL)


# 统一状态桶：所有插件把自己的状态映射到这 8 个桶，供驾驶舱/台账统一展示
REPORT_BUCKETS = ("PENDING", "SENDING", "ACCEPTED", "CONFIRMED",
                  "PARTIAL", "REJECTED", "UNKNOWN", "NOT_REPORTABLE")
REPORT_BUCKET_LABELS = {
    "PENDING": "待发送", "SENDING": "发送中", "ACCEPTED": "已受理(待确认)",
    "CONFIRMED": "已确认", "PARTIAL": "部分成功", "REJECTED": "被拒绝",
    "UNKNOWN": "结果未知", "NOT_REPORTABLE": "不上报",
}
# 通用 Submission 状态 → 桶（SubmissionState 全部 7 个取值均在内；
# 插件自有状态由各插件映射表补充，未映射值统计时计入 UNKNOWN 并在
# unmapped_states 列出，绝不静默丢弃）
SUBMISSION_STATE_TO_BUCKET = {
    "PREPARED": "PENDING", "SENDING": "SENDING", "ACCEPTED": "ACCEPTED",
    "CONFIRMED": "CONFIRMED", "PARTIAL": "PARTIAL", "REJECTED": "REJECTED",
    "UNKNOWN": "UNKNOWN",
}
