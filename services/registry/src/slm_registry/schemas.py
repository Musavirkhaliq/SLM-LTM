from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ModelSource = Literal["huggingface", "upload", "finetune"]
ModelFormat = Literal["hf", "safetensors", "gguf"]
VersionStatus = Literal["registered", "active", "archived"]
ModelScope = Literal["global", "org"]

NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
ARTIFACT_URI_PATTERN = r"^(s3|hf|gs|azure|https)://\S+$"


class ModelCreateIn(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    family: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=4000)
    license: str | None = Field(default=None, max_length=128)
    source: ModelSource
    hf_repo: str | None = Field(default=None, max_length=256)
    global_catalogue: bool = Field(
        default=False, description="Publish to the global catalogue (platform_admin only)"
    )


class ModelPatchIn(BaseModel):
    family: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=4000)
    license: str | None = Field(default=None, max_length=128)
    hf_repo: str | None = Field(default=None, max_length=256)


class VersionCreateIn(BaseModel):
    format: ModelFormat = "hf"
    param_count: int | None = Field(default=None, ge=0)
    quantization: str | None = Field(default=None, max_length=32)
    context_length: int | None = Field(default=None, ge=1)
    artifact_uri: str = Field(pattern=ARTIFACT_URI_PATTERN, max_length=2048)
    artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    base_version_id: UUID | None = None
    training_job_id: UUID | None = None
    mlflow_run_id: str | None = Field(default=None, max_length=128)
    benchmark_scores: dict[str, float] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    activate: bool = False


class VersionPatchIn(BaseModel):
    benchmark_scores: dict[str, float] | None = Field(
        default=None, description="Merged into the existing scores; keys are never removed"
    )
    status: VersionStatus | None = None
    metadata: dict[str, Any] | None = None


class VersionOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: UUID
    model_id: UUID
    org_id: UUID | None
    version: int
    status: VersionStatus
    format: ModelFormat
    param_count: int | None
    quantization: str | None
    context_length: int | None
    artifact_uri: str
    artifact_sha256: str | None
    base_version_id: UUID | None
    training_job_id: UUID | None
    mlflow_run_id: str | None
    benchmark_scores: dict[str, Any]
    metadata: dict[str, Any]
    created_by: UUID | None
    created_at: datetime


class ModelOut(BaseModel):
    id: UUID
    org_id: UUID | None
    scope: ModelScope
    name: str
    family: str | None
    description: str | None
    license: str | None
    source: ModelSource
    hf_repo: str | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    active_version: int | None
    version_count: int
    latest_version: VersionOut | None = None
