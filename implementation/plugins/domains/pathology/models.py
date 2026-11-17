"""病理领域表（04 §2.5）。核心不 import 本模块。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from core_db.engine import Base, utcnow

TS = DateTime()


def _pk() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True)


class PathologyCase(Base):
    __tablename__ = "pathology_case"
    __table_args__ = (UniqueConstraint("tenant_id", "registration_namespace", "source_case_id",
                                       name="uq_path_case"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    source_system_id: Mapped[str] = mapped_column(String(36))
    registration_namespace: Mapped[str] = mapped_column(String(128))  # 如 pis.pitaya.sqlserver@PITAYA.dbo
    source_case_id: Mapped[str] = mapped_column(String(128))          # PathologyID 原值
    pathology_no_raw: Mapped[str] = mapped_column(String(128))        # 原值字符串，不去前导零
    patient_ref_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="DISCOVERED")
    # 分列状态（02 v0.4 增补）：文件落盘/玻片关联/临床就绪/目标确认
    files_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    slides_linked: Mapped[bool] = mapped_column(Boolean, default=False)
    clinically_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    target_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class PathologyApplication(Base):
    __tablename__ = "pathology_application"
    __table_args__ = (UniqueConstraint("tenant_id", "source_system_id", "source_application_id",
                                       name="uq_path_app"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    source_system_id: Mapped[str] = mapped_column(String(36))
    source_application_id: Mapped[str] = mapped_column(String(128))
    external_no: Mapped[str] = mapped_column(String(128), default="")   # ReqNoteID 原值
    requested_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    requested_at_source: Mapped[str] = mapped_column(String(128), default="")  # 字段语义来源
    encounter_ref_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    encounter_kind_raw: Mapped[str] = mapped_column(String(64), default="")
    category_raw: Mapped[str] = mapped_column(String(64), default="")
    request_org_raw: Mapped[str] = mapped_column(String(128), default="")
    status_raw: Mapped[str] = mapped_column(String(64), default="")
    voided: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class PathologyCaseApplication(Base):
    __tablename__ = "pathology_case_application"
    __table_args__ = (UniqueConstraint("case_id", "application_id", "relation_type",
                                       name="uq_case_app"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("pathology_case.id"), index=True)
    application_id: Mapped[str] = mapped_column(String(36), ForeignKey("pathology_application.id"))
    relation_type: Mapped[str] = mapped_column(String(64), default="primary")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class PatientReference(Base):
    __tablename__ = "patient_reference"
    __table_args__ = (UniqueConstraint("tenant_id", "namespace", "source_patient_id",
                                       name="uq_pat_ref"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    namespace: Mapped[str] = mapped_column(String(128))
    source_patient_id: Mapped[str] = mapped_column(String(128))
    his_patient_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    display_name_masked: Mapped[str] = mapped_column(String(64), default="")
    identity_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)  # 脱敏快照+证据引用
    state: Mapped[str] = mapped_column(String(32), default="PENDING")
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class EncounterReference(Base):
    __tablename__ = "encounter_reference"
    __table_args__ = (UniqueConstraint("tenant_id", "namespace", "patient_ref_id", "visit_id",
                                       name="uq_enc_ref"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    namespace: Mapped[str] = mapped_column(String(128))
    patient_ref_id: Mapped[str] = mapped_column(String(36), ForeignKey("patient_reference.id"))
    visit_id: Mapped[str] = mapped_column(String(64))   # 已确认的 VISIT_ID；未确认为空→等待
    kind: Mapped[str] = mapped_column(String(32), default="inpatient")
    state: Mapped[str] = mapped_column(String(32), default="PENDING")
    candidates: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class Specimen(Base):
    __tablename__ = "pathology_specimen"
    __table_args__ = (UniqueConstraint("tenant_id", "case_id", "source_specimen_id",
                                       name="uq_specimen"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("pathology_case.id"), index=True)
    source_specimen_id: Mapped[str] = mapped_column(String(128))
    barcode_raw: Mapped[str] = mapped_column(String(128), default="")
    part: Mapped[str] = mapped_column(String(255), default="")


class Block(Base):
    __tablename__ = "pathology_block"
    __table_args__ = (UniqueConstraint("tenant_id", "case_id", "source_block_id",
                                       name="uq_block"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("pathology_case.id"), index=True)
    source_block_id: Mapped[str] = mapped_column(String(128))   # lkh 原值
    specimen_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # 外键未确认→空


class Slide(Base):
    __tablename__ = "pathology_slide"
    __table_args__ = (UniqueConstraint("tenant_id", "case_id", "source_slide_id",
                                       name="uq_slide"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("pathology_case.id"), index=True)
    source_slide_id: Mapped[str] = mapped_column(String(128))     # Pitaya_Slice.Id
    barcode_raw: Mapped[str] = mapped_column(String(128))         # qptmh 原值；无全局唯一约束
    block_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stain: Mapped[str] = mapped_column(String(128), default="")
    expected_state: Mapped[str] = mapped_column(String(64), default="UNKNOWN")  # 值域未批准
    status_raw: Mapped[dict] = mapped_column(JSON, default=dict)
    namespace: Mapped[str] = mapped_column(String(128), default="")


class ExpectedSlideSet(Base):
    __tablename__ = "expected_slide_set"
    __table_args__ = (UniqueConstraint("case_id", "revision", name="uq_expected_set"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("pathology_case.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    source_ref: Mapped[str] = mapped_column(String(255), default="")
    items: Mapped[list] = mapped_column(JSON, default=list)  # [{source_slide_id,barcode_raw,...}]
    closed_signal: Mapped[dict] = mapped_column(JSON, default=dict)  # 完成信号证据
    verified_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ScanAcquisition(Base):
    __tablename__ = "scan_acquisition"
    __table_args__ = (Index("ix_scan_acq_slide", "tenant_id", "slide_id"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    slide_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # 允许暂缺（未关联）
    acquisition_id: Mapped[str] = mapped_column(String(36), index=True)      # 通用采集
    source_scan_id: Mapped[str] = mapped_column(String(255), default="")
    scan_revision: Mapped[str] = mapped_column(String(64), default="1")
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    bundle_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    supersedes: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class SlideEffectiveVersion(Base):
    __tablename__ = "slide_effective_version"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    slide_id: Mapped[str] = mapped_column(String(36), ForeignKey("pathology_slide.id"), index=True)
    scan_acquisition_id: Mapped[str] = mapped_column(String(36), ForeignKey("scan_acquisition.id"))
    selection_policy: Mapped[str] = mapped_column(String(128), default="latest_confirmed")
    decision_ref: Mapped[str] = mapped_column(String(64), default="")
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class PathologyReportVersion(Base):
    __tablename__ = "pathology_report_version"
    __table_args__ = (UniqueConstraint("tenant_id", "case_id", "source_report_id", "revision",
                                       name="uq_report_ver"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("pathology_case.id"), index=True)
    source_report_id: Mapped[str] = mapped_column(String(255))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(64), default="ISSUED")
    issued_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    report_org_raw: Mapped[str] = mapped_column(String(128), default="")
    gross: Mapped[str] = mapped_column(Text, default="")
    microscopic: Mapped[str] = mapped_column(Text, default="")
    diagnosis: Mapped[str] = mapped_column(Text, default="")
    raw_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_url: Mapped[str] = mapped_column(String(1024), default="")
    voided: Mapped[bool] = mapped_column(Boolean, default=False)
    content_digest: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class PathologyIndexRecord(Base):
    __tablename__ = "pathology_index_record"
    __table_args__ = (Index("ix_index_case", "tenant_id", "case_id"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("pathology_case.id"), index=True)
    index_profile: Mapped[str] = mapped_column(String(64), default="pathology-internal-v1")
    segments: Mapped[dict] = mapped_column(JSON, default=dict)  # 8段原值+来源修订
    rule_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    dictionary_revisions: Mapped[dict] = mapped_column(JSON, default=dict)
    display_value: Mapped[str] = mapped_column(String(255), default="")
    serialized_value: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="CANDIDATE")  # CANDIDATE/APPROVED/BLOCKED
    supersedes: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class PathologyFeeLink(Base):
    __tablename__ = "pathology_fee_link"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    application_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    encounter_ref_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_fee_id: Mapped[str] = mapped_column(String(128), default="")
    settlement_ref: Mapped[str] = mapped_column(String(128), default="")


def migration(engine):
    """病理领域迁移（由应用装配时注册给 core_db.migrate.run）。

    表按拓扑序（父表先建）而不是字母序：PG 建表即校验外键目标存在，
    字母序会把 pathology_*（引用 patient_reference）排在前面而失败；
    SQLite 不在 DDL 期校验外键，因此该缺陷只在真实 PG 暴露（P2 实测）。
    """
    import inspect
    import sys
    from sqlalchemy import MetaData
    tables = {c.__table__ for _, c in inspect.getmembers(sys.modules[__name__], inspect.isclass)
              if hasattr(c, "__table__")}
    md = MetaData()
    for t in tables:
        t.to_metadata(md)                # RT-02：SQLAlchemy 2.0 API（tometadata 弃用）
    md.create_all(engine, tables=md.sorted_tables, checkfirst=True)


migration.step = 2
migration.name = "002_pathology_domain"
