from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
import structlog

from slm_common.audit import audit
from slm_common.auth import Principal
from slm_common.db import tenant_tx
from slm_common.errors import ApiError
from slm_common.pagination import Page, PageParams, build_page
from slm_common.rbac import Role, require_role
from slm_finetune.config import FinetuneSettings
from slm_finetune.runner import DockerTrainerRunner
from slm_finetune.s3 import upload_bytes
from slm_finetune.schemas import (
    DatasetOut,
    JobStatus,
    MetricPoint,
    TrainerMetricUpdate,
    TrainerStatusUpdate,
    TrainingJobCreate,
    TrainingJobOut,
)
from slm_finetune.validator import validate_dataset

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["finetune"])
internal_router = APIRouter(prefix="/internal/v1", tags=["trainer-internal"])

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
_EXTENSIONS = {".jsonl": "jsonl", ".ndjson": "jsonl", ".csv": "csv", ".parquet": "parquet"}


def _format_for(filename: str) -> Literal["jsonl", "csv", "parquet"]:
    for ext, fmt in _EXTENSIONS.items():
        if filename.lower().endswith(ext):
            return fmt  # type: ignore[return-value]
    raise ApiError(
        415, "unsupported_media_type", "Datasets must be .jsonl, .csv, or .parquet files", param="file"
    )


def _pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def _settings(request: Request) -> FinetuneSettings:
    return request.app.state.settings


async def require_trainer_auth(request: Request) -> None:
    settings = _settings(request)
    auth_header = request.headers.get("authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not settings.trainer_token or token != settings.trainer_token:
        raise ApiError.unauthorized("Invalid trainer token")


# ------------------------------------------------------------------ Datasets
@router.get("/datasets", response_model=Page[DatasetOut])
async def list_datasets(
    request: Request,
    page: PageParams = Depends(),
    purpose: Literal["finetune", "eval"] | None = None,
    principal: Principal = require_role(Role.viewer),
):
    pool = _pool(request)
    if not pool:
        return {"items": [], "next_cursor": None}

    async with tenant_tx(pool, principal.org_id) as conn:
        rows = await conn.fetch(
            """
            SELECT id, org_id, project_id, name, purpose, format, storage_uri,
                   row_count, token_count, size_bytes, schema_info, created_at
            FROM datasets
            WHERE ($1::text IS NULL OR purpose = $1)
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            purpose,
            page.fetch,
        )
    return build_page(
        [DatasetOut.model_validate(dict(r)) for r in rows],
        page.limit,
        lambda d: {"created_at": d.created_at, "id": d.id},
    )


@router.post("/datasets", response_model=DatasetOut, status_code=201)
async def upload_dataset(
    request: Request,
    file: UploadFile = File(...),
    name: Annotated[str | None, Form(max_length=200)] = None,
    purpose: Annotated[Literal["finetune", "eval"], Form()] = "finetune",
    project_id: Annotated[UUID | None, Form()] = None,
    principal: Principal = require_role(Role.ml_engineer),
):
    dataset_name = (name or file.filename or "dataset").strip() or "dataset"
    fmt = _format_for(file.filename or "")
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise ApiError(413, "dataset_too_large", f"Datasets are limited to {MAX_UPLOAD_BYTES} bytes")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise ApiError(413, "dataset_too_large", f"Datasets are limited to {MAX_UPLOAD_BYTES} bytes")

    report = validate_dataset(content, fmt)
    if not report.ok:
        raise ApiError(
            422,
            "invalid_dataset",
            f"Dataset has {report.error_count} validation error(s)",
            param="file",
            extra={"report": report.to_dict()},
        )

    pool = _pool(request)
    settings = _settings(request)
    dataset_id = uuid4()
    key = f"{principal.org_id}/{dataset_id}/data.{fmt}"
    if fmt == "jsonl":
        content_type = "application/jsonl"
    elif fmt == "csv":
        content_type = "text/csv"
    else:
        content_type = "application/vnd.apache.parquet"

    # Upload to MinIO/S3
    storage_uri = await upload_bytes(
        settings=settings,
        bucket=settings.s3_datasets_bucket,
        key=key,
        data=content,
        content_type=content_type,
    )

    row_count = report.valid_rows
    token_count = report.estimated_tokens
    size_bytes = len(content)
    schema_info = report.to_dict()

    if not pool:
        # Mock response when DB pool is not configured (e.g. lightweight isolated tests)
        return DatasetOut(
            id=dataset_id,
            org_id=principal.org_id,
            project_id=project_id,
            name=dataset_name,
            purpose=purpose,
            format=fmt,
            storage_uri=storage_uri,
            row_count=row_count,
            token_count=token_count,
            size_bytes=size_bytes,
            schema_info=schema_info,
            created_at=datetime.now(UTC),
        )

    async with tenant_tx(pool, principal.org_id) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO datasets (
                id, org_id, project_id, name, purpose, format,
                storage_uri, row_count, token_count, size_bytes, schema_info, created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING id, org_id, project_id, name, purpose, format,
                      storage_uri, row_count, token_count, size_bytes, schema_info, created_at
            """,
            dataset_id,
            principal.org_id,
            project_id,
            dataset_name,
            purpose,
            fmt,
            storage_uri,
            row_count,
            token_count,
            size_bytes,
            schema_info,
            principal.user_id,
        )
        await audit(
            conn,
            org_id=principal.org_id,
            actor=principal,
            action="dataset.uploaded",
            resource="datasets",
            resource_id=dataset_id,
            detail={"name": dataset_name, "purpose": purpose, "size_bytes": size_bytes, "rows": row_count},
            request=request,
        )

    return DatasetOut.model_validate(dict(row))


# ------------------------------------------------------------------ Training Jobs
@router.get("/training-jobs", response_model=Page[TrainingJobOut])
async def list_training_jobs(
    request: Request,
    page: PageParams = Depends(),
    status: JobStatus | None = None,
    principal: Principal = require_role(Role.viewer),
):
    pool = _pool(request)
    if not pool:
        return {"items": [], "next_cursor": None}

    async with tenant_tx(pool, principal.org_id) as conn:
        rows = await conn.fetch(
            """
            SELECT tj.*,
                   m.name AS base_model_name,
                   d.name AS dataset_name,
                   COALESCE((SELECT MAX(step) FROM training_metrics tm WHERE tm.job_id = tj.id), 0) AS current_step,
                   COALESCE(d.token_count * (tj.hyperparams->>'epochs')::bigint, 0) AS total_tokens
            FROM training_jobs tj
            LEFT JOIN model_versions mv ON tj.base_version_id = mv.id
            LEFT JOIN models m ON mv.model_id = m.id
            LEFT JOIN datasets d ON tj.dataset_id = d.id
            WHERE ($1::job_status IS NULL OR tj.status = $1)
            ORDER BY tj.created_at DESC, tj.id DESC
            LIMIT $2
            """,
            status,
            page.fetch,
        )
    return build_page(
        [TrainingJobOut.model_validate(dict(r)) for r in rows],
        page.limit,
        lambda j: {"created_at": j.created_at, "id": j.id},
    )


@router.post("/training-jobs", response_model=TrainingJobOut, status_code=201)
async def create_training_job(
    request: Request,
    body: TrainingJobCreate,
    principal: Principal = require_role(Role.ml_engineer),
):
    pool = _pool(request)
    settings = _settings(request)
    runner: DockerTrainerRunner = request.app.state.runner

    job_id = uuid4()
    hp_dict = body.hyperparams.model_dump(exclude_none=True)

    if not pool:
        return TrainingJobOut(
            id=job_id,
            org_id=principal.org_id,
            project_id=body.project_id,
            name=body.name,
            base_version_id=body.base_version_id,
            dataset_id=body.dataset_id,
            method=body.method,
            hyperparams=hp_dict,
            gpu_count=body.gpu_count,
            status="queued",
            created_at=datetime.now(UTC),
        )

    async with tenant_tx(pool, principal.org_id) as conn:
        # Check base_version_id exists (either org's model or global catalogue where org_id is null)
        base_mv = await conn.fetchrow(
            """
            SELECT mv.id, mv.artifact_uri, m.name, mv.format, m.family, m.hf_repo
            FROM model_versions mv
            JOIN models m ON mv.model_id = m.id
            WHERE mv.id = $1 AND (mv.org_id = $2 OR mv.org_id IS NULL)
            """,
            body.base_version_id,
            principal.org_id,
        )
        if not base_mv:
            raise ApiError.not_found("Base model version not found", "model_version_not_found")

        # Check dataset_id exists and belongs to org
        ds = await conn.fetchrow(
            """
            SELECT id, name, format, storage_uri, token_count, row_count
            FROM datasets
            WHERE id = $1 AND org_id = $2
            """,
            body.dataset_id,
            principal.org_id,
        )
        if not ds:
            raise ApiError.not_found("Dataset not found", "dataset_not_found")

        # Rough cost estimate ($2.90 / GPU-hour)
        tokens = (ds["token_count"] or 1_000_000) * body.hyperparams.epochs
        hours = (tokens / (30000 * body.gpu_count) + 90) / 3600
        est_cost = round(hours * 2.90 * body.gpu_count, 4)

        row = await conn.fetchrow(
            """
            INSERT INTO training_jobs (
                id, org_id, project_id, name, base_version_id, dataset_id,
                method, hyperparams, gpu_count, status, est_cost_usd, created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'queued', $10, $11)
            RETURNING *
            """,
            job_id,
            principal.org_id,
            body.project_id,
            body.name,
            body.base_version_id,
            body.dataset_id,
            body.method,
            hp_dict,
            body.gpu_count,
            est_cost,
            principal.user_id,
        )
        await audit(
            conn,
            org_id=principal.org_id,
            actor=principal,
            action="training_job.created",
            resource="training_jobs",
            resource_id=job_id,
            detail={"name": body.name, "method": body.method, "base_version_id": str(body.base_version_id)},
            request=request,
        )

    # Launch trainer
    base_uri = base_mv["artifact_uri"]
    if not base_uri and base_mv["hf_repo"]:
        base_uri = f"hf://{base_mv['hf_repo']}"

    async def _run_job():
        try:
            container_id = await runner.launch(
                job_id=job_id,
                org_id=principal.org_id,
                job_name=body.name,
                method=body.method,
                base_model_uri=base_uri,
                dataset_uri=ds["storage_uri"],
                dataset_format=ds["format"],
                hyperparams=hp_dict,
            )
            async with tenant_tx(pool, principal.org_id) as c:
                await c.execute(
                    """
                    UPDATE training_jobs
                    SET k8s_job_name = $1, status = 'running', started_at = now()
                    WHERE id = $2 AND status = 'queued'
                    """,
                    container_id,
                    job_id,
                )
        except Exception as exc:
            logger.exception("launch_trainer_failed", job_id=str(job_id), error=str(exc))
            async with tenant_tx(pool, principal.org_id) as c:
                await c.execute(
                    """
                    UPDATE training_jobs
                    SET status = 'failed', error = $1, finished_at = now()
                    WHERE id = $2
                    """,
                    f"Launch error: {exc}"[:1000],
                    job_id,
                )

    asyncio.create_task(_run_job())

    job_data = dict(row)
    job_data["base_model_name"] = base_mv["name"]
    job_data["dataset_name"] = ds["name"]
    return TrainingJobOut.model_validate(job_data)


@router.get("/training-jobs/{job_id}", response_model=TrainingJobOut)
async def get_training_job(
    request: Request,
    job_id: UUID,
    principal: Principal = require_role(Role.viewer),
):
    pool = _pool(request)
    if not pool:
        raise ApiError.not_found("Training job not found")

    async with tenant_tx(pool, principal.org_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT tj.*,
                   m.name AS base_model_name,
                   d.name AS dataset_name,
                   COALESCE((SELECT MAX(step) FROM training_metrics tm WHERE tm.job_id = tj.id), 0) AS current_step
            FROM training_jobs tj
            LEFT JOIN model_versions mv ON tj.base_version_id = mv.id
            LEFT JOIN models m ON mv.model_id = m.id
            LEFT JOIN datasets d ON tj.dataset_id = d.id
            WHERE tj.id = $1 AND tj.org_id = $2
            """,
            job_id,
            principal.org_id,
        )
    if not row:
        raise ApiError.not_found("Training job not found", "job_not_found")
    return TrainingJobOut.model_validate(dict(row))


@router.get("/training-jobs/{job_id}/metrics", response_model=list[MetricPoint])
async def training_metrics(
    request: Request,
    job_id: UUID,
    since_step: int = 0,
    principal: Principal = require_role(Role.viewer),
):
    pool = _pool(request)
    if not pool:
        return []

    async with tenant_tx(pool, principal.org_id) as conn:
        rows = await conn.fetch(
            """
            SELECT step, loss, eval_loss, lr, tokens, gpu_util, recorded_at
            FROM training_metrics
            WHERE job_id = $1 AND org_id = $2 AND step >= $3
            ORDER BY step ASC
            """,
            job_id,
            principal.org_id,
            since_step,
        )
    return [MetricPoint.model_validate(dict(r)) for r in rows]


@router.post("/training-jobs/{job_id}/cancel", response_model=TrainingJobOut)
async def cancel_training_job(
    request: Request,
    job_id: UUID,
    principal: Principal = require_role(Role.ml_engineer),
):
    pool = _pool(request)
    runner: DockerTrainerRunner = request.app.state.runner

    if not pool:
        raise ApiError.not_found("Training job not found")

    async with tenant_tx(pool, principal.org_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT tj.*, m.name AS base_model_name, d.name AS dataset_name
            FROM training_jobs tj
            LEFT JOIN model_versions mv ON tj.base_version_id = mv.id
            LEFT JOIN models m ON mv.model_id = m.id
            LEFT JOIN datasets d ON tj.dataset_id = d.id
            WHERE tj.id = $1 AND tj.org_id = $2
            """,
            job_id,
            principal.org_id,
        )
        if not row:
            raise ApiError.not_found("Training job not found", "job_not_found")

        current_status = row["status"]
        if current_status in ("queued", "running"):
            if row["k8s_job_name"]:
                await runner.cancel(row["k8s_job_name"])

            updated = await conn.fetchrow(
                """
                UPDATE training_jobs
                SET status = 'cancelled', finished_at = now()
                WHERE id = $1 AND org_id = $2
                RETURNING *
                """,
                job_id,
                principal.org_id,
            )
            await audit(
                conn,
                org_id=principal.org_id,
                actor=principal,
                action="training_job.cancelled",
                resource="training_jobs",
                resource_id=job_id,
                request=request,
            )
            data = dict(updated)
            data["base_model_name"] = row["base_model_name"]
            data["dataset_name"] = row["dataset_name"]
            return TrainingJobOut.model_validate(data)

    return TrainingJobOut.model_validate(dict(row))


# ------------------------------------------------------------------ Internal Callbacks
@internal_router.post("/training-jobs/{job_id}/metrics")
async def report_metrics(
    request: Request,
    job_id: UUID,
    body: TrainerMetricUpdate,
    _: None = Depends(require_trainer_auth),
):
    pool = _pool(request)
    if not pool:
        return {"ok": True}

    async with tenant_tx(pool, org_id=None, bypass=True) as conn:
        job = await conn.fetchrow("SELECT id, org_id FROM training_jobs WHERE id = $1", job_id)
        if not job:
            raise ApiError.not_found("Training job not found", "job_not_found")

        await conn.execute(
            """
            INSERT INTO training_metrics (job_id, org_id, step, loss, eval_loss, lr, tokens, gpu_util, recorded_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
            ON CONFLICT (job_id, step) DO UPDATE SET
                loss = EXCLUDED.loss,
                eval_loss = EXCLUDED.eval_loss,
                lr = EXCLUDED.lr,
                tokens = EXCLUDED.tokens,
                gpu_util = EXCLUDED.gpu_util
            """,
            job_id,
            job["org_id"],
            body.step,
            body.loss,
            body.eval_loss,
            body.lr,
            body.tokens,
            body.gpu_util,
        )

        if body.tokens is not None:
            await conn.execute(
                "UPDATE training_jobs SET tokens_processed = $1 WHERE id = $2",
                body.tokens,
                job_id,
            )

    return {"ok": True}


@internal_router.post("/training-jobs/{job_id}/status")
async def report_status(
    request: Request,
    job_id: UUID,
    body: TrainerStatusUpdate,
    _: None = Depends(require_trainer_auth),
):
    pool = _pool(request)
    if not pool:
        return {"ok": True}

    async with tenant_tx(pool, org_id=None, bypass=True) as conn:
        job = await conn.fetchrow(
            """
            SELECT tj.*, mv.model_id, mv.format, mv.param_count, mv.context_length, m.family
            FROM training_jobs tj
            JOIN model_versions mv ON tj.base_version_id = mv.id
            JOIN models m ON mv.model_id = m.id
            WHERE tj.id = $1
            """,
            job_id,
        )
        if not job:
            raise ApiError.not_found("Training job not found", "job_not_found")

        output_version_id = None
        if body.status == "succeeded" and body.artifact_uri:
            # Check if there is an org-specific fine-tuned model or create a new version
            model_id = job["model_id"]
            org_id = job["org_id"]

            # If the base model was a global catalogue model (org_id is null), create a new model entry for this org
            existing_model = await conn.fetchrow(
                "SELECT id FROM models WHERE id = $1 AND org_id = $2",
                model_id,
                org_id,
            )
            if not existing_model:
                # Create fine-tuned model for this tenant
                ft_model = await conn.fetchrow(
                    """
                    INSERT INTO models (org_id, name, family, description, source, created_by)
                    VALUES ($1, $2, $3, $4, 'finetune', $5)
                    ON CONFLICT (COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid), name)
                    DO UPDATE SET updated_at = now()
                    RETURNING id
                    """,
                    org_id,
                    job["name"],
                    job["family"],
                    f"Fine-tuned model based on {job['base_version_id']}",
                    job["created_by"],
                )
                model_id = ft_model["id"]

            next_v = await conn.fetchval(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM model_versions WHERE model_id = $1",
                model_id,
            )

            await conn.execute(
                "UPDATE model_versions SET status = 'registered' WHERE model_id = $1 AND status = 'active'",
                model_id,
            )

            v_row = await conn.fetchrow(
                """
                INSERT INTO model_versions (
                    id, model_id, org_id, version, status, format, param_count,
                    context_length, artifact_uri, base_version_id, training_job_id,
                    mlflow_run_id, created_by
                )
                VALUES (
                    gen_random_uuid(), $1, $2, $3, 'active', $4, $5,
                    $6, $7, $8, $9,
                    $10, $11
                )
                RETURNING id
                """,
                model_id,
                org_id,
                next_v,
                job["format"],
                job["param_count"],
                job["context_length"],
                body.artifact_uri,
                job["base_version_id"],
                job["id"],
                body.mlflow_run_id or job["mlflow_run_id"],
                job["created_by"],
            )
            output_version_id = v_row["id"]

        await conn.execute(
            """
            UPDATE training_jobs
            SET status = $1::job_status,
                mlflow_run_id = COALESCE($2, mlflow_run_id),
                tokens_processed = COALESCE($3, tokens_processed),
                error = COALESCE($4, error),
                output_version_id = COALESCE($5, output_version_id),
                started_at = CASE WHEN $1::text = 'running' AND started_at IS NULL THEN now() ELSE started_at END,
                finished_at = CASE WHEN $1::text IN ('succeeded', 'failed', 'cancelled') THEN now() ELSE finished_at END
            WHERE id = $6
            """,
            body.status,
            body.mlflow_run_id,
            body.tokens_processed,
            body.error,
            output_version_id,
            job_id,
        )

    return {"ok": True, "output_version_id": str(output_version_id) if output_version_id else None}
