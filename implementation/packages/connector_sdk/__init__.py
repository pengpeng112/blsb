"""连接器SDK（02 §6、10 §1—§2）。

插件不持有任意SQL执行权限；对象名走允许列表；查询值全部参数绑定。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class RequestDeferred(Exception):
    """No external request was made; release this job until the shared cooldown."""

    def __init__(self, retry_after: float, reason: str = "platform cooldown"):
        import math
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__(reason)


@dataclass
class SourceScope:
    """已批准的读取作用域（10 §2.1）：锁定来源系统、库、schema、命名空间版本。"""
    tenant_id: str
    source_system_code: str
    database: str | None = None
    schema: str | None = None
    namespace: str = ""
    namespace_version: str = "1"

    def object_ref(self, obj: str) -> str:
        parts = [self.database, self.schema, obj]
        return ".".join(p for p in parts if p)


@dataclass
class SlideCandidate:
    source_object: str
    source_key: str          # Pitaya_Slice.Id 原值字符串
    barcode_raw: str         # qptmh 原值
    pathology_no: str        # PathologyID 原值
    block_key: str | None    # lkh 原值
    status_raw: dict = field(default_factory=dict)  # qpzt/zpzt/tmdyzt 原值，不解释
    observed_at: str = ""
    raw_digest: str = ""
    evidence_level: str = "SOURCE_RECORD"


@dataclass
class CaseRecord:
    source_object: str
    source_key: str
    pathology_no: str
    patient_source_id: str | None
    in_patient_id: str | None
    out_patient_id: str | None
    req_note_id: str | None
    identity_card: str | None
    name_masked: str | None
    create_time: str | None
    application_time: str | None      # 权威申请时间字段；未确认时为 None
    spec_recv_time: str | None
    report_time: str | None
    verify_report_time: str | None
    status_raw: str | None
    category_raw: str | None
    raw: dict = field(default_factory=dict)


@dataclass
class ApplicationCandidate:
    source_object: str
    source_key: str
    external_id: str
    requested_at: str | None
    encounter_kind_raw: str | None    # 病人类型原值（如'住院'/'门诊'）
    category_raw: str | None
    request_org_raw: str | None
    status_raw: str | None
    void_info: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


@dataclass
class ReportRecord:
    source_object: str
    source_key: str
    pathology_no: str
    report_serial: str
    gross: str | None
    microscopic: str | None
    diagnosis: str | None
    issued_at: str | None
    audit_time: str | None
    report_org_raw: str | None
    image_url: str | None
    voided: bool = False
    raw: dict = field(default_factory=dict)


@dataclass
class PatientCandidate:
    source_object: str
    source_key: str
    patient_id: str
    id_type_raw: str | None
    id_no_masked: str | None
    id_no_full_ref: str | None   # 敏感值不经API明文传递；指向证据对象
    name_masked: str | None
    birth_date: str | None
    gender_raw: str | None


@dataclass
class EncounterCandidate:
    source_object: str
    source_key: str
    patient_id: str
    visit_id: str
    kind: str  # inpatient / outpatient / special_outpatient（来源证实后填写）
    raw: dict = field(default_factory=dict)


class Warehouse(Protocol):
    """方言数据访问抽象：适配器只通过本协议取数。"""

    def fetch_all(self, template_id: str, params: dict, limit: int) -> list[dict]: ...
    def dialect(self) -> str: ...


class SourceConnector(Protocol):
    plugin_id: str

    def validate_config(self, config: dict) -> list[str]: ...
    def test_connection(self, config: dict) -> dict: ...


class CapabilityError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def capability_unsupported(message: str) -> CapabilityError:
    return CapabilityError("CAPABILITY_UNSUPPORTED", message)


def driver_unavailable(driver: str, detail: str = "") -> CapabilityError:
    return CapabilityError("DRIVER_UNAVAILABLE",
                           f"driver {driver} not installed/configured: {detail}")


# LT-03：统一目的地适配器协议（形状/回执规则/json_path 解析；不触网）
from .destination import (  # noqa: E402,F401
    OUTCOMES as DEST_OUTCOMES,
    SendResult as DestinationSendResult,
    QueryResult as DestinationQueryResult,
    ReceiptRule as DestinationReceiptRule,
    apply_receipt_rules as destination_apply_receipt_rules,
    json_path_get as destination_json_path_get,
)
