from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, Query, Request

from slm_common.audit import audit
from slm_common.auth import Principal
from slm_common.db import tenant_tx
from slm_common.errors import ApiError
from slm_common.pagination import Page, PageParams, build_page
from slm_common.rbac import Role, require_role
from slm_evaluation.config import EvaluationSettings, get_evaluation_settings
from slm_evaluation.evaluator import EvaluationEngine
from slm_evaluation.schemas import (
    ComparisonOut,
    EvaluationCreate,
    EvaluationOut,
    JobStatus,
    ResultOut,
    ResultsOut,
    parse_ids,
)

router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluation"])


def _pool(request: Request) -> asyncpg.Pool | None:
    return getattr(request.app.state, "pool", None)


def _settings(request: Request) -> EvaluationSettings:
    return getattr(request.app.state, "settings", get_evaluation_settings())


@router.get("", response_model=Page[EvaluationOut])
async def list_evaluations(
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
            SELECT e.*, d.name AS dataset_name
            FROM evaluations e
            LEFT JOIN datasets d ON e.dataset_id = d.id
            WHERE ($1::job_status IS NULL OR e.status = $1)
            ORDER BY e.created_at DESC, e.id DESC
            LIMIT $2
            """,
            status,
            page.fetch,
        )
    return build_page(
        [EvaluationOut.model_validate(dict(r)) for r in rows],
        page.limit,
        lambda e: {"created_at": e.created_at, "id": e.id},
    )


@router.post("", response_model=EvaluationOut, status_code=201)
async def create_evaluation(
    request: Request,
    body: EvaluationCreate,
    principal: Principal = require_role(Role.ml_engineer),
):
    pool = _pool(request)
    eval_id = uuid4()

    if not pool:
        return EvaluationOut(
            id=eval_id,
            org_id=principal.org_id,
            project_id=body.project_id,
            name=body.name,
            dataset_id=body.dataset_id,
            model_version_ids=body.model_version_ids,
            metrics=body.metrics,
            judge_model=body.judge_model,
            status="queued",
            created_at=datetime.now(UTC),
        )

    async with tenant_tx(pool, principal.org_id) as conn:
        # Validate dataset exists
        ds = await conn.fetchrow(
            "SELECT id, name FROM datasets WHERE id = $1 AND (org_id = $2 OR org_id IS NULL)",
            body.dataset_id,
            principal.org_id,
        )
        if not ds:
            raise ApiError.not_found("Dataset not found", "dataset_not_found")

        # Validate all model_version_ids exist
        for mv_id in body.model_version_ids:
            mv = await conn.fetchrow(
                "SELECT id FROM model_versions WHERE id = $1 AND (org_id = $2 OR org_id IS NULL)",
                mv_id,
                principal.org_id,
            )
            if not mv:
                raise ApiError.not_found(f"Model version {mv_id} not found", "model_version_not_found")

        row = await conn.fetchrow(
            """
            INSERT INTO evaluations (
                id, org_id, project_id, name, dataset_id, model_version_ids, metrics, judge_model, status, created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'queued', $9)
            RETURNING *
            """,
            eval_id,
            principal.org_id,
            body.project_id,
            body.name,
            body.dataset_id,
            body.model_version_ids,
            body.metrics,
            body.judge_model,
            principal.user_id,
        )

        await audit(
            conn,
            org_id=principal.org_id,
            actor=principal,
            action="evaluation.created",
            resource="evaluations",
            resource_id=eval_id,
            detail={"name": body.name, "dataset_id": str(body.dataset_id), "model_versions": [str(m) for m in body.model_version_ids]},
            request=request,
        )

    # Spawn evaluation in background
    engine: EvaluationEngine = getattr(request.app.state, "engine", None)
    if engine and pool:
        asyncio.create_task(engine.run_evaluation(eval_id, principal.org_id, pool))

    out = dict(row)
    out["dataset_name"] = ds["name"]
    return EvaluationOut.model_validate(out)


@router.get("/compare", response_model=ComparisonOut)
async def compare_evaluations(
    request: Request,
    ids: str = Query(description="Comma-separated evaluation ids (2-5)"),
    principal: Principal = require_role(Role.viewer),
):
    try:
        eval_ids = parse_ids(ids)
    except ValueError as exc:
        raise ApiError(422, "validation_error", str(exc), param="ids") from exc

    pool = _pool(request)
    if not pool:
        return ComparisonOut(evaluation_ids=eval_ids, metrics={}, items=[])

    async with tenant_tx(pool, principal.org_id) as conn:
        rows = await conn.fetch(
            """
            SELECT er.*, m.name AS model_name, mv.version AS model_version
            FROM evaluation_results er
            JOIN model_versions mv ON er.model_version_id = mv.id
            JOIN models m ON mv.model_id = m.id
            WHERE er.evaluation_id = ANY($1::uuid[]) AND er.org_id = $2
            """,
            eval_ids,
            principal.org_id,
        )

    items: list[ResultOut] = []
    metrics_map: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        if isinstance(d.get("scores"), str):
            d["scores"] = json.loads(d["scores"])
        item = ResultOut.model_validate(d)
        items.append(item)

        key = f"{item.model_name or 'model'} v{item.model_version or 1}"
        metrics_map[key] = {
            **item.scores,
            "latency_p50_ms": item.latency_p50_ms,
            "latency_p95_ms": item.latency_p95_ms,
            "cost_per_1k_tokens_usd": item.cost_per_1k_tokens_usd,
        }

    return ComparisonOut(
        evaluation_ids=eval_ids,
        metrics=metrics_map,
        items=items,
    )


@router.get("/{evaluation_id}", response_model=EvaluationOut)
async def get_evaluation(
    request: Request,
    evaluation_id: UUID,
    principal: Principal = require_role(Role.viewer),
):
    pool = _pool(request)
    if not pool:
        raise ApiError.not_found("Evaluation not found")

    async with tenant_tx(pool, principal.org_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT e.*, d.name AS dataset_name
            FROM evaluations e
            LEFT JOIN datasets d ON e.dataset_id = d.id
            WHERE e.id = $1 AND e.org_id = $2
            """,
            evaluation_id,
            principal.org_id,
        )
    if not row:
        raise ApiError.not_found("Evaluation not found", "evaluation_not_found")
    return EvaluationOut.model_validate(dict(row))


@router.get("/{evaluation_id}/results", response_model=ResultsOut)
async def evaluation_results(
    request: Request,
    evaluation_id: UUID,
    principal: Principal = require_role(Role.viewer),
):
    pool = _pool(request)
    if not pool:
        return ResultsOut(evaluation_id=evaluation_id, status="succeeded", results=[], items=[])

    async with tenant_tx(pool, principal.org_id) as conn:
        ev = await conn.fetchrow(
            "SELECT id, status FROM evaluations WHERE id = $1 AND org_id = $2",
            evaluation_id,
            principal.org_id,
        )
        if not ev:
            raise ApiError.not_found("Evaluation not found", "evaluation_not_found")

        rows = await conn.fetch(
            """
            SELECT er.*, m.name AS model_name, mv.version AS model_version
            FROM evaluation_results er
            JOIN model_versions mv ON er.model_version_id = mv.id
            JOIN models m ON mv.model_id = m.id
            WHERE er.evaluation_id = $1 AND er.org_id = $2
            """,
            evaluation_id,
            principal.org_id,
        )

    results: list[ResultOut] = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("scores"), str):
            d["scores"] = json.loads(d["scores"])
        results.append(ResultOut.model_validate(d))

    return ResultsOut(
        evaluation_id=evaluation_id,
        status=ev["status"],
        results=results,
        items=results,
    )


@router.post("/{evaluation_id}/cancel", response_model=EvaluationOut)
async def cancel_evaluation(
    request: Request,
    evaluation_id: UUID,
    principal: Principal = require_role(Role.ml_engineer),
):
    pool = _pool(request)
    if not pool:
        raise ApiError.not_found("Evaluation not found")

    async with tenant_tx(pool, principal.org_id) as conn:
        row = await conn.fetchrow(
            """
            UPDATE evaluations
            SET status = 'cancelled', finished_at = now()
            WHERE id = $1 AND org_id = $2 AND status IN ('queued', 'running')
            RETURNING *
            """,
            evaluation_id,
            principal.org_id,
        )
        if not row:
            existing = await conn.fetchrow("SELECT id FROM evaluations WHERE id = $1 AND org_id = $2", evaluation_id, principal.org_id)
            if not existing:
                raise ApiError.not_found("Evaluation not found", "evaluation_not_found")
            raise ApiError.conflict("Evaluation is not in a cancellable state")

    return EvaluationOut.model_validate(dict(row))
