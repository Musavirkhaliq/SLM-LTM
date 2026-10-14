"""Cost math: token and GPU pricing, daily roll-ups, grouping, budgets and forecasts.

Money and quantities use `Decimal` (never float). Results are rounded to the precision of the
`cost_records` columns: usd -> 6 places, quantity -> 4 places.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from uuid import UUID

USD_PLACES = Decimal("0.000001")
QTY_PLACES = Decimal("0.0001")
ZERO = Decimal(0)
TOKEN_UNIT = Decimal(1000)

Resource = Literal["gpu_hour", "input_token_1k", "output_token_1k", "storage_gb_month", "train_token_1k"]
GroupBy = Literal["project", "deployment", "model", "org"]


def usd(value: Decimal) -> Decimal:
    return value.quantize(USD_PLACES, rounding=ROUND_HALF_UP)


def qty(value: Decimal) -> Decimal:
    return value.quantize(QTY_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class Rate:
    resource: str
    usd: Decimal
    effective_from: date
    gpu_type: str | None = None


@dataclass(frozen=True, slots=True)
class UsageRow:
    """One slice of `usage_logs` already summed per (org, project, deployment, model version)."""

    org_id: UUID
    project_id: UUID | None
    deployment_id: UUID | None
    model_version_id: UUID | None
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True, slots=True)
class CostRecord:
    day: date
    org_id: UUID
    project_id: UUID | None
    deployment_id: UUID | None
    model_version_id: UUID | None
    resource: str
    quantity: Decimal
    usd: Decimal


@dataclass(slots=True)
class Group:
    key: str | None
    usd: Decimal = ZERO
    by_resource: dict[str, Decimal] = field(default_factory=dict)


def rate_at(
    rates: Iterable[Rate], resource: str, day: date, gpu_type: str | None = None
) -> Decimal | None:
    """Latest rate for `resource` effective on `day`. A rate for the exact `gpu_type` beats a
    generic (gpu_type=None) one regardless of date; None when nothing applies yet."""
    candidates = [r for r in rates if r.resource == resource and r.effective_from <= day]
    specific = [r for r in candidates if gpu_type is not None and r.gpu_type == gpu_type]
    pool = specific or [r for r in candidates if r.gpu_type is None]
    if not pool:
        return None
    return max(pool, key=lambda r: r.effective_from).usd


def token_cost(
    prompt_tokens: int,
    completion_tokens: int,
    input_per_1k: Decimal,
    output_per_1k: Decimal,
) -> Decimal:
    if prompt_tokens < 0 or completion_tokens < 0:
        raise ValueError("token counts must be non-negative")
    total = (Decimal(prompt_tokens) / TOKEN_UNIT) * input_per_1k
    total += (Decimal(completion_tokens) / TOKEN_UNIT) * output_per_1k
    return usd(total)


def gpu_cost(gpu_count: int, hours: Decimal | float, rate_per_gpu_hour: Decimal) -> Decimal:
    if gpu_count < 0 or Decimal(str(hours)) < 0:
        raise ValueError("gpu_count and hours must be non-negative")
    return usd(Decimal(gpu_count) * Decimal(str(hours)) * rate_per_gpu_hour)


def cost_per_1k_tokens(total_usd: Decimal, total_tokens: int) -> Decimal | None:
    """Blended price per 1k tokens, or None when nothing was processed."""
    if total_tokens <= 0:
        return None
    return usd(total_usd / (Decimal(total_tokens) / TOKEN_UNIT))


def gpu_hours_in_day(
    start: datetime, stop: datetime | None, day: date, gpu_count: int, *, now: datetime | None = None
) -> Decimal:
    """GPU-hours a deployment consumed during UTC `day` (still-running => until `now`)."""
    if gpu_count < 0:
        raise ValueError("gpu_count must be non-negative")
    window_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    window_end = window_start + timedelta(days=1)
    effective_stop = stop if stop is not None else (now or datetime.now(UTC))
    begin, end = max(start, window_start), min(effective_stop, window_end)
    seconds = (end - begin).total_seconds()
    if seconds <= 0:
        return ZERO
    return qty(Decimal(str(seconds)) / Decimal(3600) * gpu_count)


def rollup_usage(
    day: date, usage: Iterable[UsageRow], rates: Sequence[Rate]
) -> list[CostRecord]:
    """Daily `cost_records` for inference tokens, one row per (project, deployment, resource).

    A missing rate prices the resource at 0 rather than failing the whole roll-up.
    """
    input_rate = rate_at(rates, "input_token_1k", day) or ZERO
    output_rate = rate_at(rates, "output_token_1k", day) or ZERO
    buckets: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    owners: dict[tuple, UsageRow] = {}
    for row in usage:
        key = (row.org_id, row.project_id, row.deployment_id)
        buckets[key][0] += row.prompt_tokens
        buckets[key][1] += row.completion_tokens
        owners.setdefault(key, row)
    records: list[CostRecord] = []
    for key, (prompt, completion) in buckets.items():
        owner = owners[key]
        for resource, tokens, rate in (
            ("input_token_1k", prompt, input_rate),
            ("output_token_1k", completion, output_rate),
        ):
            if tokens == 0:
                continue
            quantity = qty(Decimal(tokens) / TOKEN_UNIT)
            records.append(
                CostRecord(
                    day, owner.org_id, owner.project_id, owner.deployment_id,
                    owner.model_version_id, resource, quantity, usd(quantity * rate),
                )
            )
    return sorted(records, key=lambda r: (str(r.project_id), str(r.deployment_id), r.resource))


def gpu_record(
    day: date,
    *,
    org_id: UUID,
    project_id: UUID | None,
    deployment_id: UUID | None,
    model_version_id: UUID | None,
    gpu_count: int,
    start: datetime,
    stop: datetime | None,
    rates: Sequence[Rate],
    gpu_type: str | None = None,
    now: datetime | None = None,
) -> CostRecord | None:
    hours = gpu_hours_in_day(start, stop, day, gpu_count, now=now)
    if hours == 0:
        return None
    rate = rate_at(rates, "gpu_hour", day, gpu_type) or ZERO
    return CostRecord(
        day, org_id, project_id, deployment_id, model_version_id, "gpu_hour", hours, usd(hours * rate)
    )


def aggregate(
    records: Iterable[CostRecord], group_by: GroupBy, *, model_names: Mapping[UUID, str] | None = None
) -> list[Group]:
    """Sum cost per project / deployment / model version / org, most expensive first.

    Records without the grouping dimension (e.g. org-level storage) fall under key None.
    """
    groups: dict[str | None, Group] = {}
    for rec in records:
        key = _group_key(rec, group_by, model_names)
        group = groups.setdefault(key, Group(key))
        group.usd += rec.usd
        group.by_resource[rec.resource] = group.by_resource.get(rec.resource, ZERO) + rec.usd
    result = list(groups.values())
    for g in result:
        g.usd = usd(g.usd)
        g.by_resource = {k: usd(v) for k, v in sorted(g.by_resource.items())}
    return sorted(result, key=lambda g: (-g.usd, g.key or ""))


def _group_key(
    rec: CostRecord, group_by: GroupBy, model_names: Mapping[UUID, str] | None
) -> str | None:
    if group_by == "org":
        return str(rec.org_id)
    if group_by == "project":
        return str(rec.project_id) if rec.project_id else None
    if group_by == "deployment":
        return str(rec.deployment_id) if rec.deployment_id else None
    if rec.model_version_id is None:
        return None
    if model_names and rec.model_version_id in model_names:
        return model_names[rec.model_version_id]
    return str(rec.model_version_id)


def total(records: Iterable[CostRecord]) -> Decimal:
    return usd(sum((r.usd for r in records), ZERO))


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    spent: Decimal
    budget: Decimal
    percent_used: Decimal
    remaining: Decimal
    over_budget: bool


def budget_status(spent: Decimal, budget: Decimal) -> BudgetStatus:
    if budget <= 0:
        raise ValueError("budget must be positive")
    return BudgetStatus(
        spent=usd(spent),
        budget=usd(budget),
        percent_used=(spent / budget * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        remaining=usd(max(budget - spent, ZERO)),
        over_budget=spent > budget,
    )


def forecast_month_end(spent_to_date: Decimal, days_elapsed: int, days_in_month: int) -> Decimal:
    """Linear projection of month-end spend from the spend so far."""
    if not 0 < days_elapsed <= days_in_month:
        raise ValueError("days_elapsed must be within 1..days_in_month")
    return usd(spent_to_date / days_elapsed * days_in_month)


def render_csv(groups: Iterable[Group], group_by: str) -> str:
    """CSV with one row per group and one column per resource seen (`cost/report.csv`)."""
    items = list(groups)
    resources = sorted({r for g in items for r in g.by_resource})
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([group_by, *resources, "total_usd"])
    for g in items:
        writer.writerow(
            [g.key or "", *(g.by_resource.get(r, ZERO) for r in resources), g.usd]
        )
    return out.getvalue()
