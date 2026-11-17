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
    # LT-06A（R08）：用户类主体的上报业务授权范围。None=未初始化（按无业务
    # fail-closed）；["*"]=全部（仅 018 迁移对既有账号兼容赋值）；[]=无业务
    # （新建账号默认）；具体 code 列表=仅这些业务。机器主体（agent/connector/
    # collector）不受 scope 约束（既有生产鉴权零改变）。
    target_scopes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    connector_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ConsoleAccount(Base):
    """Password login for a human subject; usernames are globally unique."""
    __tablename__ = "console_account"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    subject_id: Mapped[str] = mapped_column(String(36), ForeignKey("auth_subject.id"), unique=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ConsoleSession(Base):
    """Opaque, expiring browser credential; only its digest is persisted."""
    __tablename__ = "console_session"
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = _tenant_fk("tenant")
    subject_id: Mapped[str] = mapped_column(String(36), ForeignKey("auth_subject.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TS, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


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
    """上线监控告警（07 §6）控制台展示。LT-04 扩列：fingerprint 去重键
    （tenant+rule+target+对象）、first/last_seen、occurrences、ACK/恢复、
    rule_code/target_code/detail。旧列（alert_type 小写 severity/state）保留
    兼容；severity 新值统一大写（INFO/WARN/CRITICAL），读取时归一。"""
    __tablename__ = "alert_event"
    __table_args__ = (Index("ix_alert_state", "tenant_id", "state"),
                      Index("ix_alert_fp", "tenant_id", "fingerprint"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    alert_type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    message: Mapped[str] = mapped_column(Text, default="")
    target_ref: Mapped[str] = mapped_column(String(255), default="")
    state: Mapped[str] = mapped_column(String(32), default="OPEN")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    # ---- LT-04 扩列（旧库由 016 ALTER 补列；列可空+ORM 默认——裸 SQL 旧行
    # 插入不因新列 NOT NULL 失败；读取处对 NULL 做 or 默认兜底）
    fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    rule_code: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    target_code: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    first_seen_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    occurrences: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)
    acked_by: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    acked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)


class ReportTarget(Base):
    """上报业务目录（LT-01）：本院要上报的每一项业务的一级台账实体。

    只做目录/台账/展示与生产闸门判定，不驱动任何调度或发送；现有药品追溯/
    传染病生产链路不受本表任何字段影响（闸门 assert_target_can_send_real
    只供 LT-03 之后的新通用发送路径调用，不接入既有生产链路）。
    """
    __tablename__ = "report_target"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_report_target_code"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32), default="")
    authority: Mapped[str] = mapped_column(String(128), default="")
    plugin_key: Mapped[str] = mapped_column(String(64), default="")
    destination_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="PUSH")
    lifecycle: Mapped[str] = mapped_column(String(16), default="PLANNED")
    runtime_kind: Mapped[str] = mapped_column(String(16), default="PLATFORM")
    owner_dept: Mapped[str] = mapped_column(String(64), default="")
    contact: Mapped[str] = mapped_column(String(128), default="")
    sole_pusher: Mapped[str] = mapped_column(String(16), default="UNCONFIRMED")
    sole_pusher_note: Mapped[str] = mapped_column(Text, default="")
    spec_docs: Mapped[list] = mapped_column(JSON, default=list)
    readme_path: Mapped[str] = mapped_column(String(255), default="")
    schedule_note: Mapped[str] = mapped_column(String(255), default="")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str] = mapped_column(String(64), default="")


# ================================================================ LT-04 调度/告警
class Schedule(Base):
    """平台调度定义（LT-04 A 段）。

    红线（R01/R04）：所有新调度（含系统任务）初始 enabled=False；**observe_only=
    True 的镜像调度由服务端强制只观测**——enable/run-now/改 job_type/payload/
    复制后入队一律拒绝（不以 note 文本判定）；tick 永不为 observe_only 入队。
    本表不改变药品宿主 timer / 传染病 crontab 的任何现有行为。
    """
    __tablename__ = "schedule"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_schedule_code"),
                      Index("ix_schedule_fire", "tenant_id", "enabled", "next_fire_at"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128), default="")
    target_code: Mapped[str] = mapped_column(String(64), default="")
    job_type: Mapped[str] = mapped_column(String(64))
    payload_template: Mapped[dict] = mapped_column(JSON, default=dict)
    cron: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    observe_only: Mapped[bool] = mapped_column(Boolean, default=False)
    misfire_policy: Mapped[str] = mapped_column(String(16), default="SKIP")
    concurrency: Mapped[str] = mapped_column(String(16), default="FORBID")
    max_runtime_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    blackout: Mapped[list] = mapped_column(JSON, default=list)
    next_fire_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_fire_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str] = mapped_column(String(64), default="")


class ScheduleRun(Base):
    """调度运行记录（LT-04 A/B 段）。

    run_key 稳定标识（cron:{fire_time} / manual:{幂等键}）+ (tenant, schedule,
    run_key) 唯一——唯一约束只是幂等兜底；抢占/run/Job 入队在同一事务（R04）。
    监测状态（TIMED_OUT/STALLED）是观测异常，不是执行终止：原 Job 可能仍在
    运行，FORBID 并发保护不因观测状态放行（run_guard 只标记只告警不 kill）。
    """
    __tablename__ = "schedule_run"
    __table_args__ = (
        UniqueConstraint("tenant_id", "schedule_id", "run_key",
                         name="uq_schedule_run_key"),
        Index("ix_schedule_run_state", "tenant_id", "state"),
        Index("ix_schedule_run_sched", "schedule_id", "fire_time"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    schedule_id: Mapped[str] = mapped_column(String(36), index=True)
    run_key: Mapped[str] = mapped_column(String(128))
    fire_time: Mapped[datetime] = mapped_column(TS)
    state: Mapped[str] = mapped_column(String(32), default="QUEUED")
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(String(500), default="")
    trigger: Mapped[str] = mapped_column(String(16), default="CRON")
    triggered_by: Mapped[str] = mapped_column(String(64), default="scheduler")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AlertRule(Base):
    """告警规则（LT-04 C 段）。enabled=True 只表示参与评估；系统调度
    system.alert_evaluate 自身默认停用——启用是独立合成环境操作。"""
    __tablename__ = "alert_rule"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_alert_rule_code"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128), default="")
    target_code: Mapped[str] = mapped_column(String(64), default="")
    kind: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String(16), default="WARN")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=60)
    notify_channels: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str] = mapped_column(String(64), default="")


class NotificationChannel(Base):
    """通知通道（LT-04 C 段）。config 禁止内联秘密：smtp 用 password_env、
    webhook 用 url_env（环境变量名引用）；服务端校验拒绝明文。"""
    __tablename__ = "notification_channel"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_notify_channel_code"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    code: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(16))           # file/smtp/webhook
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str] = mapped_column(String(64), default="")


class NotificationLog(Base):
    """通知外发日志（LT-04 C 段）。"""
    __tablename__ = "notification_log"
    __table_args__ = (Index("ix_notify_log_time", "tenant_id", "sent_at"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    channel_code: Mapped[str] = mapped_column(String(64))
    alert_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(String(500), default="")
    kind: Mapped[str] = mapped_column(String(16), default="ALERT")  # ALERT/RECOVERED/DIGEST/TEST
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


# ================================================================ LT-05 外部台账
class ExternalImportBatch(Base):
    """外部台账导入批次（LT-05 A 段；只读导入，不改生产 state）。

    source_instance：来源实例标识（如 "host-<prod-host>-state"）——幂等身份组成之一，
    不同来源互不去重；source_tz：无偏移时间戳的解释时区（UTC|CST，导入时显式
    提供，入库统一 UTC）。
    """
    __tablename__ = "external_import_batch"
    __table_args__ = (Index("ix_eib_target", "tenant_id", "target_code",
                            "started_at"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    target_code: Mapped[str] = mapped_column(String(64))
    source_label: Mapped[str] = mapped_column(String(255), default="")
    source_instance: Mapped[str] = mapped_column(String(128), default="")
    source_tz: Mapped[str] = mapped_column(String(8), default="")
    files: Mapped[list] = mapped_column(JSON, default=list)   # [{name,sha256,rows}]
    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    rows_applied: Mapped[int] = mapped_column(Integer, default=0)
    rows_skipped_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    imported_by: Mapped[str] = mapped_column(String(64), default="")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    report: Mapped[dict] = mapped_column(JSON, default=dict)  # 前20拒绝原因（无原文）


class ExternalImportLine(Base):
    """导入行幂等账（LT-05 A2）：联合来源身份 = tenant+target+source_instance+
    logical_file+行偏移；存 line_digest。同身份同 digest 再导入→skipped_duplicate；
    同身份不同 digest→内容冲突（保留新行并标 conflict 待核，不覆盖旧证据）。"""
    __tablename__ = "external_import_line"
    __table_args__ = (
        UniqueConstraint("tenant_id", "target_code", "source_instance",
                         "logical_file", "line_offset",
                         name="uq_eil_source_pos"),
        Index("ix_eil_digest", "tenant_id", "line_digest"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    target_code: Mapped[str] = mapped_column(String(64))
    source_instance: Mapped[str] = mapped_column(String(128), default="")
    logical_file: Mapped[str] = mapped_column(String(255))
    line_offset: Mapped[int] = mapped_column(Integer)
    line_digest: Mapped[str] = mapped_column(String(64))
    batch_id: Mapped[str] = mapped_column(String(36), index=True)
    imported_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ExternalSubmissionRecord(Base):
    """外部提交台账（LT-05 A 段；任何 EXTERNAL 类上报业务通用）。

    证据级别（R06）：result=true 只证明前置机该次受理 → last_result=ACCEPTED、
    evidence_level=FRONTEND_ACCEPTED，**不升 CONFIRMED**（国家平台业务确认缺证据）；
    sent.jsonl 是本地成功记账、watermark 不是回执；Mock 永远单列（MOCK）。
    """
    __tablename__ = "external_submission_record"
    __table_args__ = (
        # RT-04 A3（迁移022）：记录唯一键含 source_instance——跨实例同
        # record_key 不合并（旧行 source_instance 沿用原值，约束只会放宽）
        UniqueConstraint("tenant_id", "target_code", "source_instance",
                         "record_key", name="uq_esr_key_src"),
        Index("ix_esr_last_attempt", "tenant_id", "target_code",
              "last_attempt_at"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    target_code: Mapped[str] = mapped_column(String(64))
    record_key: Mapped[str] = mapped_column(String(255))     # <表key>/<记录ID>
    business_type: Mapped[str] = mapped_column(String(64), default="")
    doc_type_code: Mapped[str] = mapped_column(String(8), default="")
    first_seen_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_attempt_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_result: Mapped[str] = mapped_column(String(32), default="")
    bucket: Mapped[str] = mapped_column(String(32), default="")
    evidence_level: Mapped[str] = mapped_column(String(32), default="")
    last_error_code: Mapped[str] = mapped_column(String(64), default="")
    last_error_message: Mapped[str] = mapped_column(String(200), default="")
    op_time: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    source_file: Mapped[str] = mapped_column(String(255), default="")
    import_batch_id: Mapped[str] = mapped_column(String(36), default="")
    # RT-04 A3（迁移022）：记录身份含来源实例——跨实例同 record_key 不合并
    source_instance: Mapped[str] = mapped_column(String(128), default="")


# ================================================================ LT-07 联调记录
class DebugExchange(Base):
    """医保联调记录（LT-07 迁移019）：request/response 只存打码版
    （sensitive 字段一律打码；export-request 的真实值由用户页面输入、
    不落库）。record-result 回填为人工证据（保留操作者与观察时间，
    不等同机器确认）。"""
    __tablename__ = "debug_exchange"
    __table_args__ = (Index("ix_dbg_txn", "tenant_id", "target_code",
                            "transaction_code", "created_at"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    target_code: Mapped[str] = mapped_column(String(64), default="")
    transaction_code: Mapped[str] = mapped_column(String(16), default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    mode: Mapped[str] = mapped_column(String(16),
                                      default="VALIDATE_ONLY")  # VALIDATE_ONLY/MOCK/EXPORT
    request_digest: Mapped[str] = mapped_column(String(64), default="")
    request_masked: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_masked: Mapped[dict] = mapped_column(JSON, default=dict)
    outcome: Mapped[str] = mapped_column(String(16), default="")  # PASS/FAIL/ERROR
    note: Mapped[str] = mapped_column(Text, default="")


class InfectiousShadowCursor(Base):
    """传染病影子流水位（RT-04 A1 迁移021；替代写 PUBLISHED ConfigRevision）。

    流水位键 = tenant+target+source_instance+stream（表键）。语义见
    domains/infectious/evidence.py A0 契约：source_cursor 只在表内全部记录
    终态处理后推进（保守推进，不越过失败记录）；last_success_version 与
    max_seen 分离——已发版本=得到明确投递结果的版本。
    """
    __tablename__ = "infectious_shadow_cursor"
    __table_args__ = (
        UniqueConstraint("tenant_id", "target_code", "source_instance",
                         "stream_key", name="uq_ifc_stream"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    target_code: Mapped[str] = mapped_column(String(64), default="inf_cdc_frontend")
    source_instance: Mapped[str] = mapped_column(String(128), default="default")
    stream_key: Mapped[str] = mapped_column(String(64))     # TABLES 键（表/流）
    source_cursor: Mapped[str] = mapped_column(String(64), default="")
    max_seen: Mapped[str] = mapped_column(String(64), default="")
    last_success_version: Mapped[str] = mapped_column(String(128), default="")
    legacy_imported: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class InfectiousShadowRecord(Base):
    """传染病影子记录版本状态（RT-04 A1 迁移021）。

    版本状态键 = tenant+target+source_instance+table+source_record_key。
    每行记录当前已知源版本（op_time+source_version_digest）与投递结果；
    行级唯一+条件更新防并发回退。delivered_result 非空=明确投递结果
    （ACCEPTED/CONFIRMED/PARTIAL）；UNKNOWN/RETRYABLE/质量失败/冲突为空。
    """
    __tablename__ = "infectious_shadow_record"
    __table_args__ = (
        UniqueConstraint("tenant_id", "target_code", "source_instance",
                         "table_key", "source_record_key",
                         name="uq_ifr_record"),
        Index("ix_ifr_state", "tenant_id", "target_code", "state"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    target_code: Mapped[str] = mapped_column(String(64), default="inf_cdc_frontend")
    source_instance: Mapped[str] = mapped_column(String(128), default="default")
    table_key: Mapped[str] = mapped_column(String(64))
    source_record_key: Mapped[str] = mapped_column(String(255))
    source_op_time: Mapped[str] = mapped_column(String(32), default="")
    source_version_digest: Mapped[str] = mapped_column(String(64), default="")
    # PENDING/QUALITY_WAIT/DELIVERED/CONFLICT/FAILED（快照级状态）
    state: Mapped[str] = mapped_column(String(32), default="PENDING")
    delivered_result: Mapped[str] = mapped_column(String(32), default="")
    submission_id: Mapped[str] = mapped_column(String(36), default="")
    snapshot_digest: Mapped[str] = mapped_column(String(64), default="")
    mock: Mapped[bool] = mapped_column(Boolean, default=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(200), default="")
    # A0/A2：采集即冻结的不可变源快照（mapped payload；上传时间重写前）。
    # 快照随采集事务提交后水位才推进（可靠持久化到本地待处理队列）。
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    quality_issues: Mapped[list] = mapped_column(JSON, default=list)
    frozen_at: Mapped[str] = mapped_column(String(32), default="")
    tables_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class InfectiousTableTelemetry(Base):
    """传染病逐表遥测快照（PT-03 迁移024）：每表最新水位/最近班次/最近日终对账。

    唯一键 = tenant+target+source_instance+table_key；数据源=状态副本导入
    （watermark.json / last-run-real.json / reconcile-YYYYMMDD.json 白名单解析）
    与只读目录流式同步（monitor_sync）。全部为聚合计数与源时间——零患者字段。
    """
    __tablename__ = "infectious_table_telemetry"
    __table_args__ = (
        UniqueConstraint("tenant_id", "target_code", "source_instance",
                         "table_key", name="uq_itt_key"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    target_code: Mapped[str] = mapped_column(String(64), default="inf_cdc_frontend")
    source_instance: Mapped[str] = mapped_column(String(128), default="live")
    table_key: Mapped[str] = mapped_column(String(64))
    doc_type_code: Mapped[str] = mapped_column(String(8), default="")
    # 水位（源端 opTime，naive UTC 存储；源为 CST 解释）
    watermark_op_time: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    watermark_lag_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 最近班次（last-run-real.json 每表行）
    last_shift_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    shift_rows: Mapped[int] = mapped_column(Integer, default=0)
    shift_sent: Mapped[int] = mapped_column(Integer, default=0)
    shift_parent_wait: Mapped[int] = mapped_column(Integer, default=0)
    shift_skipped_invalid: Mapped[int] = mapped_column(Integer, default=0)
    shift_failed: Mapped[int] = mapped_column(Integer, default=0)
    shift_truncated: Mapped[int] = mapped_column(Integer, default=0)
    shift_error: Mapped[str] = mapped_column(String(500), default="")
    # 最近日终对账（reconcile-YYYYMMDD.json 每表行）
    reconcile_day: Mapped[str] = mapped_column(String(8), default="")
    reconcile_repushed: Mapped[int] = mapped_column(Integer, default=0)
    reconcile_parent_wait: Mapped[int] = mapped_column(Integer, default=0)
    reconcile_failed: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow,
                                                 onupdate=utcnow)


class InfectiousWatchdogSnapshot(Base):
    """守护进程状态快照（PT-03 迁移024）：status.json C1–C11 + 晨报 + 告警流水。

    唯一键 = tenant+target+source_instance。checks_json/driver_errors_json/
    recent_alerts_json 全部来自守护白名单输出（计数/表名/级别/时间戳），
    daily_text 晨报全文经 redact_text 脱敏——零患者字段。
    """
    __tablename__ = "infectious_watchdog_snapshot"
    __table_args__ = (
        UniqueConstraint("tenant_id", "target_code", "source_instance",
                         name="uq_iws_key"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    target_code: Mapped[str] = mapped_column(String(64), default="inf_cdc_frontend")
    source_instance: Mapped[str] = mapped_column(String(128), default="live")
    checked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    host_label: Mapped[str] = mapped_column(String(64), default="")
    overall_level: Mapped[str] = mapped_column(String(16), default="")
    checks_json: Mapped[dict] = mapped_column(JSON, default=dict)
    relay_reachable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    driver_errors_json: Mapped[dict] = mapped_column(JSON, default=dict)
    daily_ymd: Mapped[str] = mapped_column(String(8), default="")
    daily_text: Mapped[str] = mapped_column(Text, default="")
    recent_alerts_json: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow,
                                                 onupdate=utcnow)


class ExternalFileCursor(Base):
    """外部只读文件流式尾读游标（PT-03 迁移024）：按字节偏移增量吸收生产
    状态目录（100% O_RDONLY 零写源目录）。

    唯一键 = tenant+target+source_instance+file_name。byte_offset 只推进到
    完整换行边界（防读到写了一半的尾行）；head_sha256 存文件头 4096 字节
    摘要——变化即轮转重建，从 0 重读；abs_line_start 为该偏移前累计行数
    （导入行账全局行号，跨批次零重复）。"""
    __tablename__ = "external_file_cursor"
    __table_args__ = (
        UniqueConstraint("tenant_id", "target_code", "source_instance",
                         "file_name", name="uq_efc_key"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    target_code: Mapped[str] = mapped_column(String(64), default="inf_cdc_frontend")
    source_instance: Mapped[str] = mapped_column(String(128), default="live")
    file_name: Mapped[str] = mapped_column(String(255))
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    byte_offset: Mapped[int] = mapped_column(BigInteger, default=0)
    abs_line_start: Mapped[int] = mapped_column(BigInteger, default=0)
    head_sha256: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow,
                                                 onupdate=utcnow)
