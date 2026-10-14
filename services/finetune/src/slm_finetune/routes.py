from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile

from slm_common.auth import Principal
from slm_common.errors import ApiError
from slm_common.pagination import PageParams
from slm_common.rbac import Role, require_role
from slm_finetune.schemas import (
    DatasetOut,
    JobStatus,
    MetricPoint,
    TrainingJobCreate,
    TrainingJobOut,
)
from slm_finetune.validator import validate_dataset

router = APIRouter(prefix="/api/v1", tags=["finetune"])

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
_EXTENSIONS = {".jsonl": "jsonl", ".ndjson": "jsonl", ".csv": "csv"}


def _format_for(filename: str) -> Literal["jsonl", "csv"]:
    for ext, fmt in _EXTENSIONS.items():
        if filename.lower().endswith(ext):
            return fmt  # type: ignore[return-value]
    raise ApiError(
        415, "unsupported_media_type", "Datasets must be .jsonl or .csv files", param="file"
    )


@router.get("/datasets")
async def list_datasets(
    page: PageParams = Depends(),
    purpose: Literal["finetune", "eval"] | None = None,
    _: Principal = require_role(Role.viewer),
):
    raise ApiError.not_implemented("Listing datasets")


@router.post("/datasets", response_model=DatasetOut, status_code=201)
async def upload_dataset(
    name: Annotated[str, Form(min_length=1, max_length=200)],
    file: UploadFile = File(...),
    purpose: Annotated[Literal["finetune", "eval"], Form()] = "finetune",
    project_id: Annotated[UUID | None, Form()] = None,
    _: Principal = require_role(Role.ml_engineer),
):
    """Validates the file for real; persisting it (object storage + `datasets` row) is not built yet."""
    fmt = _format_for(file.filename or "")
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise ApiError(413, "dataset_too_large", f"Datasets are limited to {MAX_UPLOAD_BYTES} bytes")
    report = validate_dataset(await file.read(), fmt)
    if not report.ok:
        raise ApiError(
            422,
            "invalid_dataset",
            f"Dataset has {report.error_count} validation error(s)",
            param="file",
            extra={"report": report.to_dict()},
        )
    raise ApiError.not_implemented("Storing datasets")


@router.get("/training-jobs")
async def list_training_jobs(
    page: PageParams = Depends(),
    status: JobStatus | None = None,
    _: Principal = require_role(Role.viewer),
):
    raise ApiError.not_implemented("Listing training jobs")


@router.post("/training-jobs", response_model=TrainingJobOut, status_code=201)
async def create_training_job(
    body: TrainingJobCreate, _: Principal = require_role(Role.ml_engineer)
):
    raise ApiError.not_implemented("Creating training jobs")


@router.get("/training-jobs/{job_id}", response_model=TrainingJobOut)
async def get_training_job(job_id: UUID, _: Principal = require_role(Role.viewer)):
    raise ApiError.not_implemented("Reading a training job")


@router.get("/training-jobs/{job_id}/metrics", response_model=list[MetricPoint])
async def training_metrics(
    job_id: UUID,
    since_step: int = 0,
    _: Principal = require_role(Role.viewer),
):
    raise ApiError.not_implemented("Training metrics")


@router.post("/training-jobs/{job_id}/cancel", response_model=TrainingJobOut)
async def cancel_training_job(job_id: UUID, _: Principal = require_role(Role.ml_engineer)):
    raise ApiError.not_implemented("Cancelling training jobs")
