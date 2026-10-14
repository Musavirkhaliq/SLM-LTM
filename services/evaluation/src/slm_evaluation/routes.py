from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from slm_common.auth import Principal
from slm_common.errors import ApiError
from slm_common.pagination import PageParams
from slm_common.rbac import Role, require_role
from slm_evaluation.schemas import (
    ComparisonOut,
    EvaluationCreate,
    EvaluationOut,
    JobStatus,
    ResultsOut,
    parse_ids,
)

router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluation"])


@router.get("")
async def list_evaluations(
    page: PageParams = Depends(),
    status: JobStatus | None = None,
    _: Principal = require_role(Role.viewer),
):
    raise ApiError.not_implemented("Listing evaluations")


@router.post("", response_model=EvaluationOut, status_code=201)
async def create_evaluation(body: EvaluationCreate, _: Principal = require_role(Role.ml_engineer)):
    raise ApiError.not_implemented("Creating evaluations")


@router.get("/compare", response_model=ComparisonOut)
async def compare_evaluations(
    ids: str = Query(description="Comma-separated evaluation ids (2-5)"),
    _: Principal = require_role(Role.viewer),
):
    try:
        parse_ids(ids)
    except ValueError as exc:
        raise ApiError(422, "validation_error", str(exc), param="ids") from exc
    raise ApiError.not_implemented("Comparing evaluations")


@router.get("/{evaluation_id}/results", response_model=ResultsOut)
async def evaluation_results(evaluation_id: UUID, _: Principal = require_role(Role.viewer)):
    raise ApiError.not_implemented("Reading evaluation results")
