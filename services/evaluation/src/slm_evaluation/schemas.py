from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

MetricName = Literal[
    "accuracy",
    "f1",
    "rouge",
    "rouge_l",
    "bleu",
    "hallucination",
    "faithfulness",
    "groundedness",
    "toxicity",
    "json_validity",
]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
LLM_JUDGED: frozenset[str] = frozenset({"hallucination", "faithfulness", "groundedness", "toxicity"})


class EvaluationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dataset_id: UUID
    model_version_ids: list[UUID] = Field(
        min_length=1, max_length=10, description="Two or more makes it an A/B comparison"
    )
    metrics: list[MetricName] = Field(min_length=1)
    judge_model: str | None = Field(default=None, max_length=256)
    project_id: UUID | None = None

    @model_validator(mode="after")
    def _consistency(self) -> Self:
        if len(set(self.model_version_ids)) != len(self.model_version_ids):
            raise ValueError("model_version_ids must be unique")
        if len(set(self.metrics)) != len(self.metrics):
            raise ValueError("metrics must be unique")
        judged = LLM_JUDGED.intersection(self.metrics)
        if judged and not self.judge_model:
            raise ValueError(f"judge_model is required for: {', '.join(sorted(judged))}")
        return self


class EvaluationOut(BaseModel):
    id: UUID
    org_id: UUID
    project_id: UUID | None = None
    name: str
    dataset_id: UUID
    model_version_ids: list[UUID]
    metrics: list[MetricName]
    judge_model: str | None = None
    status: JobStatus
    created_at: datetime
    finished_at: datetime | None = None
    dataset_name: str | None = None
    error: str | None = None


class ResultOut(BaseModel):
    evaluation_id: UUID | None = None
    model_version_id: UUID
    scores: dict[str, float]
    latency_p50_ms: int | None = None
    latency_p95_ms: int | None = None
    cost_per_1k_tokens_usd: float | None = None
    sample_count: int | None = None
    model_name: str | None = None
    model_version: int | None = None


class ResultsOut(BaseModel):
    evaluation_id: UUID
    status: JobStatus
    results: list[ResultOut] = Field(default_factory=list)
    items: list[ResultOut] = Field(default_factory=list)


class ComparisonOut(BaseModel):
    evaluation_ids: list[UUID] = Field(default_factory=list)
    metrics: dict[str, dict[str, Any]] = Field(default_factory=dict)
    items: list[ResultOut] = Field(default_factory=list)


def parse_ids(raw: str) -> list[UUID]:
    """`?ids=a,b` -> 2..5 distinct UUIDs."""
    try:
        ids = [UUID(part.strip()) for part in raw.split(",") if part.strip()]
    except ValueError as exc:
        raise ValueError("ids must be comma-separated UUIDs") from exc
    if not 2 <= len(ids) <= 5:
        raise ValueError("provide between 2 and 5 evaluation ids")
    if len(set(ids)) != len(ids):
        raise ValueError("ids must be distinct")
    return ids
