from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

TuneMethod = Literal["lora", "qlora", "full"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class Hyperparams(BaseModel):
    """Training hyper-parameters; unknown keys are rejected so typos fail fast."""

    model_config = ConfigDict(extra="forbid")

    learning_rate: float = Field(default=2e-4, gt=0, le=1)
    epochs: int = Field(default=3, ge=1, le=100)
    batch_size: int = Field(default=8, ge=1, le=1024)
    gradient_accumulation_steps: int = Field(default=1, ge=1, le=1024)
    max_seq_len: int = Field(default=2048, ge=64, le=131072)
    warmup_ratio: float = Field(default=0.03, ge=0, lt=1)
    weight_decay: float = Field(default=0.0, ge=0, le=1)
    lora_r: int | None = Field(default=None, ge=1, le=512)
    lora_alpha: int | None = Field(default=None, ge=1, le=1024)
    lora_dropout: float | None = Field(default=None, ge=0, lt=1)
    seed: int = 42


class TrainingJobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    base_version_id: UUID
    dataset_id: UUID
    method: TuneMethod = "qlora"
    hyperparams: Hyperparams = Field(default_factory=Hyperparams)
    gpu_count: int = Field(default=1, ge=1, le=16)
    project_id: UUID | None = None

    @model_validator(mode="after")
    def _method_consistency(self) -> Self:
        hp = self.hyperparams
        adapter_fields = (hp.lora_r, hp.lora_alpha, hp.lora_dropout)
        if self.method == "full" and any(v is not None for v in adapter_fields):
            raise ValueError("lora_* hyperparameters do not apply to full fine-tuning")
        if self.method in ("lora", "qlora") and hp.lora_r is None:
            self.hyperparams = hp.model_copy(update={"lora_r": 16, "lora_alpha": hp.lora_alpha or 32})
        return self


class TrainingJobOut(BaseModel):
    id: UUID
    org_id: UUID
    project_id: UUID | None
    name: str
    base_version_id: UUID
    dataset_id: UUID
    method: TuneMethod
    hyperparams: dict
    gpu_count: int
    status: JobStatus
    output_version_id: UUID | None
    tokens_processed: int
    est_cost_usd: float | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class MetricPoint(BaseModel):
    step: int
    loss: float | None
    eval_loss: float | None
    lr: float | None
    tokens: int | None
    gpu_util: float | None
    recorded_at: datetime


class DatasetOut(BaseModel):
    id: UUID
    org_id: UUID
    project_id: UUID | None
    name: str
    purpose: Literal["finetune", "eval"]
    format: Literal["jsonl", "csv", "parquet"]
    storage_uri: str
    row_count: int | None
    token_count: int | None
    size_bytes: int | None
    schema_info: dict
    created_at: datetime
