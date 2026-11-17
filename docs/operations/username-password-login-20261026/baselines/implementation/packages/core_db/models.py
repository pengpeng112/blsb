"""核心数据模型（04 §2 全量 + 10 §7 增补 + 运行支撑表）。

- 内部ID为UUID字符串；外部标识一律字符串，不去前导零。
- 所有业务表带 tenant_id；子表外键按 (tenant_id,parent_id) 组合。
- barcode_raw 无全局唯一约束（10 §2）。
- 病理领域表不在此文件：由 plugins/domains/pathology/models.py 提供，核心不得 import。
"""
from __future__ import annotations

from datetime import datetime

import uuid

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, Float, ForeignKey,
                        Index, Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from .engine import Base, utcnow

TS = DateTime()
STR = String(64)


def _pk() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True,
                         default=lambda: str(uuid.uuid4()))


def _tenant_fk(table: str) -> Mapped[str]:
    return mapped_column(String(36), ForeignKey(f"{table}.id"), index=True)


# ================================================================ 2.1 配置与设备
class Tenant(Base):
    __tablename__ = "tenant"
    id: Mapped[str] = _pk()
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class SourceSystem(Base):
    __tablename__ = "source_system"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_source_system_code"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    code: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(64))  # scanner / pis / his / csv_import ...
    name: Mapped[str] = mapped_column(String(255), default="")
    identifier_namespace: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")


class ConnectorInstance(Base):
    __tablename__ = "connector_instance"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    direction: Mapped[str] = mapped_column(String(16))  # source / destination
    plugin_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255), default="")
    source_system_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active_config_revision: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")  # DRAFT/ENABLED/DISABLED
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ConfigRevision(Base):
    __tablename__ = "config_revision"
    __table_args__ = (UniqueConstraint("tenant_id", "config_type", "owner_id", "revision",
                                       name="uq_config_rev"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    config_type: Mapped[str] = mapped_column(String(64))  # connector/workflow/mapping/pack...
    owner_id: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    content_json: Mapped[dict] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="DRAFT")  # DRAFT/PUBLISHED/RETIRED
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ConfigRelease(Base):
    __tablename__ = "config_release"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    revision_ids: Mapped[list] = mapped_column(JSON, default=list)
    digest: Mapped[str] = mapped_column(String(64), default="")  # W10聚合摘要（迁移006）
    test_report: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    released_by: Mapped[str] = mapped_column(String(64), default="")
    released_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    rolled_back_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class SecretRef(Base):
    __tablename__ = "secret_ref"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    provider: Mapped[str] = mapped_column(String(64), default="local-keyring")
    locator: Mapped[str] = mapped_column(String(255))  # 不存明文；本地开发仅存环境变量名
    rotation_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Agent(Base):
    __tablename__ = "agent"
    __table_args__ = (UniqueConstraint("tenant_id", "device_code", name="uq_agent_device"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    source_system_id: Mapped[str] = mapped_column(String(36), ForeignKey("source_system.id"))
    device_code: Mapped[str] = mapped_column(String(64))
    device_name: Mapped[str] = mapped_column(String(255), default="")
    cert_fingerprint: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    desired_config_revision: Mapped[str | None] = mapped_column(String(36), nullable=True)
    applied_config_revision: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AgentCommand(Base):
    __tablename__ = "agent_command"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent.id"), index=True)
    type: Mapped[str] = mapped_column(String(64))  # 白名单: apply_config/pause/resume/rescan/diagnostic/upgrade
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    config_revision: Mapped[str | None] = mapped_column(String(36), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="PENDING")  # PENDING/SENT/ACKED/FAILED/EXPIRED
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class SchemaRevision(Base):
    __tablename__ = "schema_revision"
    __table_args__ = (UniqueConstraint("tenant_id", "schema_key", "version", name="uq_schema_rev"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    schema_key: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(64))
    json_schema: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")


class MappingRevision(Base):
    __tablename__ = "mapping_revision"
    __table_args__ = (UniqueConstraint("tenant_id", "mapping_key", "revision", name="uq_mapping_rev"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    mapping_key: Mapped[str] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(Integer)
    rules: Mapped[dict] = mapped_column(JSON)
    dictionary_refs: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(32), default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class DictionaryRevision(Base):
    __tablename__ = "dictionary_revision"
    __table_args__ = (UniqueConstraint("tenant_id", "namespace", "version", name="uq_dict_rev"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    namespace: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(64))
    items: Mapped[list] = mapped_column(JSON, default=list)  # [{code,label,...}]
    source: Mapped[str] = mapped_column(String(64), default="source")  # source / target
    effective_from: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    unmapped_values: Mapped[list] = mapped_column(JSON, default=list)
    approved_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# ================================================================ 2.2 通用采集与数据包
class InboxEvent(Base):
    __tablename__ = "inbox_event"
    __table_args__ = (UniqueConstraint("tenant_id", "connector_id", "external_event_id",
                                       "external_revision", name="uq_inbox_event"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    connector_id: Mapped[str] = mapped_column(String(36), ForeignKey("connector_instance.id"), index=True)
    external_event_id: Mapped[str] = mapped_column(String(255))
    external_revision: Mapped[str] = mapped_column(String(64), default="1")
    raw_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    digest: Mapped[str] = mapped_column(String(64), default="")
    received_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    processed_state: Mapped[str] = mapped_column(String(32), default="RECEIVED")


class SourceCheckpoint(Base):
    __tablename__ = "source_checkpoint"
    __table_args__ = (UniqueConstraint("tenant_id", "connector_id", "stream", "partition",
                                       name="uq_checkpoint"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    connector_id: Mapped[str] = mapped_column(String(36), ForeignKey("connector_instance.id"))
    stream: Mapped[str] = mapped_column(String(128))
    partition: Mapped[str] = mapped_column(String(64), default="0")
    cursor_json: Mapped[dict] = mapped_column(JSON, default=dict)
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class SourceRecord(Base):
    __tablename__ = "source_record"
    __table_args__ = (UniqueConstraint("tenant_id", "namespace", "entity_type", "external_id",
                                       name="uq_source_record"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    namespace: Mapped[str] = mapped_column(String(128))
    entity_type: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(255))  # 经确认的稳定键，原值含前导零
    current_revision: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class SourceRecordRevision(Base):
    __tablename__ = "source_record_revision"
    __table_args__ = (UniqueConstraint("record_id", "source_version", name="uq_srr"),
                      Index("ix_srr_record", "record_id"))
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    record_id: Mapped[str] = mapped_column(String(36), ForeignKey("source_record.id"))
    source_version: Mapped[str] = mapped_column(String(128))
    source_change_time: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))  # 带版本的规范化序列化 hash（10 §6.4）
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    observed_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    observed_by: Mapped[str] = mapped_column(String(128), default="")  # 通道: created_time/partition_scan/...
    supersedes: Mapped[str | None] = mapped_column(String(36), nullable=True)


class DataPackage(Base):
    __tablename__ = "data_package"
    __table_args__ = (UniqueConstraint("tenant_id", "domain", "business_key", name="uq_package"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    domain: Mapped[str] = mapped_column(String(64))  # pathology / metrics
    business_key: Mapped[str] = mapped_column(String(255))  # 由 DomainPlugin 定义
    current_revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class PackageRevision(Base):
    __tablename__ = "package_revision"
    __table_args__ = (UniqueConstraint("package_id", "revision", name="uq_pkg_rev"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    package_id: Mapped[str] = mapped_column(String(36), ForeignKey("data_package.id"))
    revision: Mapped[int] = mapped_column(Integer)
    schema_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    digest: Mapped[str] = mapped_column(String(64), default="")
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(32), default="DRAFT")
    wait_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class PackageRelation(Base):
    __tablename__ = "package_relation"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    package_revision_id: Mapped[str] = mapped_column(String(36), ForeignKey("package_revision.id"), index=True)
    relation_type: Mapped[str] = mapped_column(String(64))
    from_ref: Mapped[str] = mapped_column(String(255))
    to_ref: Mapped[str] = mapped_column(String(255))
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class QualityIssue(Base):
    __tablename__ = "quality_issue"
    __table_args__ = (Index("ix_quality_issue_open", "tenant_id", "state"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    entity_ref: Mapped[str] = mapped_column(String(255))  # 如 pathology_case:{id}
    rule_code: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), default="BLOCKING")
    blocking_scope: Mapped[str] = mapped_column(String(64), default="package")  # index/package/readiness/destination
    field_path: Mapped[str] = mapped_column(String(255), default="")
    evidence_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rule_revision: Mapped[str] = mapped_column(String(64), default="")
    evaluated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    context_json: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(32), default="OPEN")
    resolved_by_decision: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ManualDecision(Base):
    __tablename__ = "manual_decision"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    entity_ref: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64))
    before_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    after_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# ================================================================ 2.3 文件与存储
class Acquisition(Base):
    __tablename__ = "acquisition"
    __table_args__ = (UniqueConstraint("tenant_id", "source_system_id", "external_event_id",
                                       "source_revision", name="uq_acquisition"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    agent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("agent.id"), nullable=True)
    source_system_id: Mapped[str] = mapped_column(String(36), ForeignKey("source_system.id"))
    external_event_id: Mapped[str] = mapped_column(String(255))
    source_revision: Mapped[str] = mapped_column(String(64), default="1")
    source_scan_id: Mapped[str] = mapped_column(String(255), default="")
    manifest: Mapped[dict] = mapped_column(JSON)
    manifest_digest: Mapped[str] = mapped_column(String(64))
    completion_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(32), default="REGISTERED")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Asset(Base):
    __tablename__ = "asset"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    acquisition_id: Mapped[str] = mapped_column(String(36), ForeignKey("acquisition.id"), index=True)
    client_asset_id: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(32), default="primary")
    original_name: Mapped[str] = mapped_column(String(512), default="")
    relative_path: Mapped[str] = mapped_column(String(1024), default="")
    media_type: Mapped[str] = mapped_column(String(128), default="")
    format_hint: Mapped[str] = mapped_column(String(128), default="")
    blob_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("blob.id"), nullable=True)
    expected_size: Mapped[int] = mapped_column(BigInteger, default=0)  # R23：BIGINT（>2GB目标）
    expected_sha256: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(32), default="STAGING")
    metadata_state: Mapped[str] = mapped_column(String(32), default="PENDING")  # PENDING/UNSUPPORTED/PARSED
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Blob(Base):
    __tablename__ = "blob"
    __table_args__ = (UniqueConstraint("tenant_id", "sha256", "size_bytes", name="uq_blob_content"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="VERIFYING")
    verified_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class StorageVolume(Base):
    __tablename__ = "storage_volume"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    backend: Mapped[str] = mapped_column(String(32), default="filesystem")
    name: Mapped[str] = mapped_column(String(128))
    root_ref: Mapped[str] = mapped_column(String(1024))  # 绝对路径，来自部署配置
    capacity_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, default=0)  # 事务预留
    safety_margin_bytes: Mapped[int] = mapped_column(BigInteger,
                                                     default=2 * 1024 * 1024 * 1024)
    state: Mapped[str] = mapped_column(String(32), default="ACTIVE")  # ACTIVE/DRAINING/READ_ONLY/OFFLINE
    health: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AssetLocation(Base):
    __tablename__ = "asset_location"
    __table_args__ = (UniqueConstraint("volume_id", "physical_key", name="uq_location_key"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    blob_id: Mapped[str] = mapped_column(String(36), ForeignKey("blob.id"), index=True)
    volume_id: Mapped[str] = mapped_column(String(36), ForeignKey("storage_volume.id"))
    physical_key: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(32), default="primary")
    state: Mapped[str] = mapped_column(String(32), default="VERIFYING")  # VERIFYING/AVAILABLE/DELETED
    verified_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class AssetBundle(Base):
    __tablename__ = "asset_bundle"
    __table_args__ = (UniqueConstraint("acquisition_id", "revision", name="uq_bundle_rev"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    acquisition_id: Mapped[str] = mapped_column(String(36), ForeignKey("acquisition.id"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    manifest_digest: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(32), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class BundleMember(Base):
    __tablename__ = "bundle_member"
    __table_args__ = (UniqueConstraint("bundle_id", "relative_path_normalized", name="uq_bundle_member"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    bundle_id: Mapped[str] = mapped_column(String(36), ForeignKey("asset_bundle.id"), index=True)
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("asset.id"))
    relative_path: Mapped[str] = mapped_column(String(1024))
    relative_path_normalized: Mapped[str] = mapped_column(String(1024))
    role: Mapped[str] = mapped_column(String(32), default="primary")


class AssetDerivation(Base):
    __tablename__ = "asset_derivation"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    source_asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("asset.id"), index=True)
    derived_asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("asset.id"))
    processor: Mapped[str] = mapped_column(String(128))
    parameters_digest: Mapped[str] = mapped_column(String(64), default="")
    qc_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class UploadSession(Base):
    __tablename__ = "upload_session"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_upload_idem"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    acquisition_id: Mapped[str] = mapped_column(String(36), ForeignKey("acquisition.id"), index=True)
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("asset.id"))
    volume_id: Mapped[str] = mapped_column(String(36), ForeignKey("storage_volume.id"))
    expected_size: Mapped[int] = mapped_column(BigInteger)
    expected_sha256: Mapped[str] = mapped_column(String(64))
    chunk_size: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(TS)
    epoch: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(32), default="CREATED")
    idempotency_key: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class UploadChunk(Base):
    __tablename__ = "upload_chunk"
    __table_args__ = (UniqueConstraint("session_id", "chunk_index", name="uq_chunk"),
                      Index("ix_chunk_session", "session_id"))
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("upload_session.id"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512), default="")
    committed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class StorageCommitIntent(Base):
    __tablename__ = "storage_commit_intent"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("upload_session.id"), index=True)
    blob_id: Mapped[str] = mapped_column(String(36), nullable=True)
    target_key: Mapped[str] = mapped_column(String(512))
    digest: Mapped[str] = mapped_column(String(64), default="")
    phase: Mapped[str] = mapped_column(String(32), default="CREATED")  # CREATED/RENAMED/DB_COMMITTED
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class StorageReservation(Base):
    """预留账本（W07扩大）：每会话至多一行，CAS 状态转换保证释放恰好一次。

    释放路径（cancel/expire/fail/complete/recovery）都先走
    ACTIVE→RELEASED 条件UPDATE，只有影响行数=1 才扣减卷 reserved_bytes。

    T5 起账本泛化：session_id 不再限上传会话——存储迁移以确定性派生键
    复用同一账本（迁移008移除FK；SQLite本就不强制）。
    """
    __tablename__ = "storage_reservation"
    __table_args__ = (UniqueConstraint("session_id", name="uq_reservation_session"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    volume_id: Mapped[str] = mapped_column(String(36), ForeignKey("storage_volume.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    bytes_amount: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(16), default="ACTIVE")  # ACTIVE/RELEASED
    reason: Mapped[str] = mapped_column(String(16), default="")  # CANCEL/EXPIRE/FAIL/COMPLETE/RECOVERY
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    released_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class AssetReference(Base):
    __tablename__ = "asset_reference"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("asset.id"), index=True)
    owner_type: Mapped[str] = mapped_column(String(64))  # package_revision/submission/publication
    owner_id: Mapped[str] = mapped_column(String(36))
    retention_until: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    hold: Mapped[bool] = mapped_column(Boolean, default=False)


class DownloadEvidence(Base):
    __tablename__ = "download_evidence"
    __table_args__ = (Index("ix_dl_ev", "tenant_id", "publication_item_id"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    publication_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    principal: Mapped[str] = mapped_column(String(128))
    range_header: Mapped[str] = mapped_column(String(255), default="")
    bytes_sent: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[str] = mapped_column(String(32), default="COMPLETE")  # COMPLETE/PARTIAL/ABORTED
    at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# ================================================================ 2.4 任务、提交、发布与审计
class WorkflowRevision(Base):
    __tablename__ = "workflow_revision"
    __table_args__ = (UniqueConstraint("tenant_id", "workflow_key", "revision", name="uq_wf_rev"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    workflow_key: Mapped[str] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(Integer)
    dsl: Mapped[dict] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")  # DRAFT/PUBLISHED/RETIRED
    production_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class WorkflowRun(Base):
    __tablename__ = "workflow_run"
    __table_args__ = (Index("ix_wfr_state", "tenant_id", "state"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_revision_id: Mapped[str] = mapped_column(String(36), ForeignKey("workflow_revision.id"))
    package_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    trigger: Mapped[str] = mapped_column(String(32), default="package_changed")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(32), default="RUNNING")
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class StepRun(Base):
    __tablename__ = "step_run"
    __table_args__ = (UniqueConstraint("run_id", "node_id", name="uq_step_run"),
                      Index("ix_step_lease", "state", "lease_until"))
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("workflow_run.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(32), default="PENDING")
    input_refs: Mapped[dict] = mapped_column(JSON, default=dict)
    output_refs: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    lease_epoch: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str] = mapped_column(String(128), default="")
    wait_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (UniqueConstraint("tenant_id", "job_type", "dedup_key", name="uq_job_dedup"),
                      Index("ix_job_poll", "state", "available_at"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    job_type: Mapped[str] = mapped_column(String(128))
    dedup_key: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    available_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    state: Mapped[str] = mapped_column(String(32), default="PENDING")
    lease_until: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    lease_epoch: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str] = mapped_column(String(128), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    last_error: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class OutboxEvent(Base):
    __tablename__ = "outbox_event"
    __table_args__ = (Index("ix_outbox_unpublished", "published_at"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    aggregate_ref: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Destination(Base):
    __tablename__ = "destination"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255), default="")
    profile_revision: Mapped[str] = mapped_column(String(64), default="")  # profile key@version
    mode: Mapped[str] = mapped_column(String(32))  # PUSH / PULL_PUBLICATION / HYBRID
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    config_revision: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="ENABLED")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Submission(Base):
    __tablename__ = "submission"
    __table_args__ = (UniqueConstraint("destination_id", "idempotency_key", name="uq_submission_idem"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    package_revision_id: Mapped[str] = mapped_column(String(36), ForeignKey("package_revision.id"), index=True)
    destination_id: Mapped[str] = mapped_column(String(36), ForeignKey("destination.id"))
    submission_revision: Mapped[int] = mapped_column(Integer, default=1)
    snapshot_digest: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(32), default="PREPARED")
    idempotency_key: Mapped[str] = mapped_column(String(128))
    supersedes_submission: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class SubmissionItem(Base):
    __tablename__ = "submission_item"
    __table_args__ = (UniqueConstraint("submission_id", "item_key", name="uq_submission_item"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    submission_id: Mapped[str] = mapped_column(String(36), ForeignKey("submission.id"), index=True)
    item_key: Mapped[str] = mapped_column(String(255))
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    data_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(32), default="PENDING")
    remote_id: Mapped[str] = mapped_column(String(255), default="")


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempt"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    submission_id: Mapped[str] = mapped_column(String(36), ForeignKey("submission.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    request_digest: Mapped[str] = mapped_column(String(64), default="")
    started_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body_digest: Mapped[str] = mapped_column(String(64), default="")
    response_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    outcome: Mapped[str] = mapped_column(String(32), default="UNKNOWN")


class ReceiptEvent(Base):
    __tablename__ = "receipt_event"
    __table_args__ = (UniqueConstraint("tenant_id", "destination_id", "external_receipt_id",
                                       name="uq_receipt"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    destination_id: Mapped[str] = mapped_column(String(36), ForeignKey("destination.id"))
    external_receipt_id: Mapped[str] = mapped_column(String(255))
    submission_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    publication_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)  # W06：回执精确关联发布
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    parsed_result: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    received_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Publication(Base):
    __tablename__ = "publication"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    package_revision_id: Mapped[str] = mapped_column(String(36), ForeignKey("package_revision.id"), index=True)
    destination_id: Mapped[str] = mapped_column(String(36), ForeignKey("destination.id"))
    generation_id: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="PREPARING")
    manifest_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)  # T4撤回时点（迁移008回填列）


class PublicationItem(Base):
    __tablename__ = "publication_item"
    __table_args__ = (Index("ix_pub_item", "publication_id"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    publication_id: Mapped[str] = mapped_column(String(36), ForeignKey("publication.id"))
    entity_type: Mapped[str] = mapped_column(String(64))
    external_key: Mapped[str] = mapped_column(String(255))
    payload_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="PENDING")


class ExternalIdentifier(Base):
    __tablename__ = "external_identifier"
    __table_args__ = (UniqueConstraint("profile_scope", "entity_type", "internal_id", name="uq_ext_ident"),
                      UniqueConstraint("profile_scope", "entity_type", "external_id", name="uq_ext_ident2"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    profile_scope: Mapped[str] = mapped_column(String(128))
    entity_type: Mapped[str] = mapped_column(String(64))
    internal_id: Mapped[str] = mapped_column(String(255))
    external_id: Mapped[str] = mapped_column(String(255))


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_run"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    destination_id: Mapped[str] = mapped_column(String(36), ForeignKey("destination.id"))
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(32), default="RUNNING")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class ReconciliationItem(Base):
    __tablename__ = "reconciliation_item"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("reconciliation_run.id"), index=True)
    item_key: Mapped[str] = mapped_column(String(255))
    expected_state: Mapped[str] = mapped_column(String(64), default="")
    actual_state: Mapped[str] = mapped_column(String(64), default="")
    disposition: Mapped[str] = mapped_column(String(64), default="PENDING")  # PENDING/ACCEPTED/COMPENSATE
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = (Index("ix_audit_tenant_time", "tenant_id", "at"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(128))
    target: Mapped[str] = mapped_column(String(255))
    old_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    new_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# ================================================================ 10 §7 首院增补
class SourceCapabilityRevision(Base):
    __tablename__ = "source_capability_revision"
    __table_args__ = (UniqueConstraint("tenant_id", "source_system_id", "entity_stream", "revision",
                                       name="uq_cap_rev"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    source_system_id: Mapped[str] = mapped_column(String(36), ForeignKey("source_system.id"))
    entity_stream: Mapped[str] = mapped_column(String(64))  # slide / case / application / report / ...
    source_keys: Mapped[list] = mapped_column(JSON, default=list)
    object_list: Mapped[list] = mapped_column(JSON, default=list)
    change_coverage: Mapped[dict] = mapped_column(JSON, default=dict)  # {insert:bool,update:bool,...}
    dictionary_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    query_templates: Mapped[dict] = mapped_column(JSON, default=dict)
    approval_evidence: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class SourceReconciliationRun(Base):
    __tablename__ = "source_reconciliation_run"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    connector_id: Mapped[str] = mapped_column(String(36), ForeignKey("connector_instance.id"))
    stream: Mapped[str] = mapped_column(String(64))
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(32), default="RUNNING")
    complete_coverage: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class SourceReconciliationPartition(Base):
    __tablename__ = "source_reconciliation_partition"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("source_reconciliation_run.id"), index=True)
    key_range: Mapped[dict] = mapped_column(JSON, default=dict)
    cursor_start: Mapped[str] = mapped_column(String(255), default="")
    cursor_end: Mapped[str] = mapped_column(String(255), default="")
    pages_read: Mapped[int] = mapped_column(Integer, default=0)
    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    failed_pages: Mapped[list] = mapped_column(JSON, default=list)
    content_changes: Mapped[int] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(32), default="PENDING")
    started_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class AssociationCandidate(Base):
    __tablename__ = "association_candidate"
    __table_args__ = (Index("ix_assoc_acq", "tenant_id", "acquisition_id"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    acquisition_id: Mapped[str] = mapped_column(String(36), ForeignKey("acquisition.id"))
    source_ref: Mapped[str] = mapped_column(String(255))  # source_system:{id}#slide:{source_key}
    candidate_kind: Mapped[str] = mapped_column(String(32), default="slide")
    candidate_ref: Mapped[str] = mapped_column(String(255))
    match_basis: Mapped[dict] = mapped_column(JSON, default=dict)
    candidate_state: Mapped[str] = mapped_column(String(32), default="PROPOSED")  # PROPOSED/CONFIRMED/REJECTED
    rule_revision: Mapped[str] = mapped_column(String(64), default="")
    raw_digest: Mapped[str] = mapped_column(String(64), default="")
    evidence_level: Mapped[str] = mapped_column(String(32), default="SOURCE_RECORD")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class SourceFieldEvidence(Base):
    __tablename__ = "source_field_evidence"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    object_ref: Mapped[str] = mapped_column(String(255))  # pathology_case:{id}
    field_path: Mapped[str] = mapped_column(String(255))
    source_revision_ref: Mapped[str] = mapped_column(String(36))
    value_ref: Mapped[str] = mapped_column(String(255), default="")  # 敏感值不落此表，指向证据对象
    effective_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    extract_rule: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# ================================================================ 运行支撑（设计04目录之外的实现表）
class Operation(Base):
    """异步操作（202 + operation_id 的落点，04 §5.1）。"""
    __tablename__ = "operation"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    op_type: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(32), default="PENDING")
    subject_ref: Mapped[str] = mapped_column(String(255), default="")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_record"
    __table_args__ = (UniqueConstraint("tenant_id", "subject", "idempotency_key", name="uq_idem"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36))
    subject: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_digest: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AuthSubject(Base):
    """认证主体（用户/Agent/Connector/Collector）。04 §4 的实现支撑表。"""
    __tablename__ = "auth_subject"
    __table_args__ = (UniqueConstraint("tenant_id", "subject_code", name="uq_auth_subject"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    kind: Mapped[str] = mapped_column(String(16))  # user/agent/connector/collector
    subject_code: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(128), default="")
    token_hash: Mapped[str] = mapped_column(String(64), default="")  # sha256(token)，仅本地开发令牌
    roles: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    connector_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ExternalBlocker(Base):
    """09 §3 的 B01—B09 阻断登记（控制台展示来源）。"""
    __tablename__ = "external_blocker"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    blocker_code: Mapped[str] = mapped_column(String(16))  # B01..B09
    title: Mapped[str] = mapped_column(String(255))
    owner_party: Mapped[str] = mapped_column(String(128), default="")
    impact: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    evidence_ref: Mapped[str] = mapped_column(String(255), default="")
    confirmed_by: Mapped[str] = mapped_column(String(64), default="")
    confirmed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AlertEvent(Base):
    """上线监控告警（07 §6）控制台展示。"""
    __tablename__ = "alert_event"
    __table_args__ = (Index("ix_alert_state", "tenant_id", "state"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    alert_type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    message: Mapped[str] = mapped_column(Text, default="")
    target_ref: Mapped[str] = mapped_column(String(255), default="")
    state: Mapped[str] = mapped_column(String(32), default="OPEN")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
