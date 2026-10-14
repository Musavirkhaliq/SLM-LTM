from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from slm_common.auth import Principal
from slm_common.errors import ApiError
from slm_common.rbac import Role, require_role
from slm_cost.aggregation import GroupBy

router = APIRouter(prefix="/api/v1/cost", tags=["cost"])

MAX_RANGE = timedelta(days=366)


class CostGroupOut(BaseModel):
    key: str | None
    usd: str
    by_resource: dict[str, str]


class CostSummaryOut(BaseModel):
    group_by: GroupBy
    start: date
    end: date
    total_usd: str
    groups: list[CostGroupOut]


class DateRange:
    """`from` / `to` (inclusive dates), defaulting to the last 30 days."""

    def __init__(
        self,
        start: Annotated[date | None, Query(alias="from")] = None,
        end: Annotated[date | None, Query(alias="to")] = None,
    ) -> None:
        self.end = end or date.today()
        self.start = start or self.end - timedelta(days=29)
        if self.start > self.end:
            raise ApiError(422, "validation_error", "'from' must not be after 'to'", param="from")
        if self.end - self.start > MAX_RANGE:
            raise ApiError(422, "validation_error", "Range is limited to 366 days", param="to")


@router.get("/summary", response_model=CostSummaryOut)
async def cost_summary(
    group_by: GroupBy = "project",
    rng: DateRange = Depends(),
    _: Principal = require_role(Role.org_admin),
):
    raise ApiError.not_implemented("Cost summaries")


@router.get("/report.csv")
async def cost_report(
    group_by: GroupBy = "project",
    rng: DateRange = Depends(),
    _: Principal = require_role(Role.org_admin),
):
    raise ApiError.not_implemented("Cost reports")
