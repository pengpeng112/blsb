"""Storage Service 请求/响应模型（03 §4、契约 acquisition-manifest.schema.json）。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

FILE_ROLES = Literal["primary", "index", "tile_data", "label", "thumbnail",
                     "metadata", "report", "other"]


class ManifestFile(BaseModel):
    client_asset_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1, max_length=1024)
    role: FILE_ROLES = "primary"
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: str = "application/octet-stream"
    format_hint: str = ""


class CompletionEvidence(BaseModel):
    method: Literal["vendor_api", "vendor_manifest", "manual_confirmation"]
    confirmed_at: str
    evidence_ref: str
    source_immutable_assertion: bool


class IdentityHints(BaseModel):
    pathology_no: Optional[str] = None
    slide_id: Optional[str] = None
    barcode_raw: Optional[str] = None


class AcquisitionManifest(BaseModel):
    schema_version: Literal["1.0"]
    event_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    source_system_code: str = Field(min_length=1)
    root_id: str = Field(min_length=1)
    captured_at: str
    source_scan_id: str = Field(min_length=1)
    source_revision: str = Field(min_length=1, default="1")
    identity_hints: IdentityHints = Field(default_factory=IdentityHints)
    completion_evidence: CompletionEvidence
    files: list[ManifestFile] = Field(min_length=1)


class CreateUploadSession(BaseModel):
    acquisition_id: str
    asset_id: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    chunk_size: int | None = Field(default=None, gt=0)
    allow_empty: bool = False


class RegisterVolume(BaseModel):
    name: str
    root_ref: str
    capacity_bytes: int = 0
    safety_margin_bytes: int = 2 * 1024 * 1024 * 1024
