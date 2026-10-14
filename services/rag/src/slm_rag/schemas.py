from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

VectorBackend = Literal["qdrant", "weaviate", "milvus"]
IndexStatus = Literal["creating", "ready", "failed"]

ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "text/plain",
        "text/markdown",
        "text/html",
        "text/csv",
        "application/json",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024


class IndexCreate(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    backend: VectorBackend = "qdrant"
    embedding_model: str = Field(min_length=1, max_length=256)
    embedding_dim: int = Field(ge=1, le=8192)
    chunk_size: int = Field(default=512, ge=64, le=8192)
    chunk_overlap: int = Field(default=64, ge=0)
    hybrid_search: bool = True
    reranker_model: str | None = Field(default=None, max_length=256)
    project_id: UUID | None = None

    @model_validator(mode="after")
    def _overlap(self) -> Self:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self


class IndexOut(BaseModel):
    id: UUID
    org_id: UUID
    project_id: UUID | None
    name: str
    backend: VectorBackend
    collection: str
    embedding_model: str
    embedding_dim: int
    chunk_size: int
    chunk_overlap: int
    hybrid_search: bool
    reranker_model: str | None
    status: IndexStatus
    doc_count: int
    chunk_count: int
    created_at: datetime


class DocumentOut(BaseModel):
    id: UUID
    index_id: UUID
    filename: str
    mime_type: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    chunk_count: int
    created_at: datetime


class QueryIn(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=5, ge=1, le=50)
    hybrid: bool | None = Field(default=None, description="Override the index default")
    rerank: bool = False
    filters: dict[str, Any] = Field(default_factory=dict)
    generate: bool = Field(default=False, description="Also answer with an LLM from the retrieved context")
    model: str | None = Field(default=None, max_length=256, description="Endpoint alias used when generate=true")

    @model_validator(mode="after")
    def _generate_needs_model(self) -> Self:
        if self.generate and not self.model:
            raise ValueError("model is required when generate=true")
        return self


class Hit(BaseModel):
    document_id: UUID
    chunk_index: int
    text: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class QueryOut(BaseModel):
    hits: list[Hit]
    answer: str | None = None
