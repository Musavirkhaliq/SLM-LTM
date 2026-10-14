from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from slm_cost.aggregation import (
    CostRecord,
    Rate,
    UsageRow,
    aggregate,
    budget_status,
    cost_per_1k_tokens,
    forecast_month_end,
    gpu_cost,
    gpu_hours_in_day,
    gpu_record,
    rate_at,
    render_csv,
    rollup_usage,
    token_cost,
    total,
)

D = Decimal
ORG = UUID(int=1)
P1, P2 = UUID(int=11), UUID(int=12)
DEP1, DEP2 = UUID(int=21), UUID(int=22)
MV1, MV2 = UUID(int=31), UUID(int=32)
DAY = date(2026, 3, 10)
RATES = [
    Rate("input_token_1k", D("0.10"), date(2026, 1, 1)),
    Rate("output_token_1k", D("0.30"), date(2026, 1, 1)),
    Rate("input_token_1k", D("0.20"), date(2026, 3, 1)),  # price change
    Rate("gpu_hour", D("2.50"), date(2026, 1, 1)),
    Rate("gpu_hour", D("4.00"), date(2026, 1, 1), gpu_type="h100"),
    Rate("gpu_hour", D("9.00"), date(2026, 6, 1), gpu_type="h100"),  # future
]


def at(h: int, m: int = 0, day: int = 10) -> datetime:
    return datetime(2026, 3, day, h, m, tzinfo=UTC)


def test_token_cost_is_exact_decimal_math():
    assert token_cost(1_500, 500, D("0.10"), D("0.30")) == D("0.300000")
    assert token_cost(1, 0, D("0.10"), D("0.30")) == D("0.000100")
    assert token_cost(0, 0, D("1"), D("1")) == D("0.000000")
    # classic float trap: 3 * 0.1 must be exactly 0.3
    assert token_cost(3_000, 0, D("0.1"), D("0")) == D("0.300000")
    with pytest.raises(ValueError):
        token_cost(-1, 0, D("1"), D("1"))


def test_gpu_cost_and_blended_price():
    assert gpu_cost(4, D("1.5"), D("2.50")) == D("15.000000")
    assert gpu_cost(1, 0.25, D("4")) == D("1.000000")
    assert cost_per_1k_tokens(D("0.30"), 2_000) == D("0.150000")
    assert cost_per_1k_tokens(D("1"), 0) is None
    with pytest.raises(ValueError):
        gpu_cost(-1, 1, D("1"))


def test_rate_lookup_respects_effective_date_and_gpu_type():
    assert rate_at(RATES, "input_token_1k", date(2026, 2, 15)) == D("0.10")
    assert rate_at(RATES, "input_token_1k", DAY) == D("0.20")
    assert rate_at(RATES, "input_token_1k", date(2025, 12, 31)) is None
    assert rate_at(RATES, "gpu_hour", DAY) == D("2.50")  # generic
    assert rate_at(RATES, "gpu_hour", DAY, "h100") == D("4.00")  # specific beats generic
    assert rate_at(RATES, "gpu_hour", DAY, "a100") == D("2.50")  # unknown type -> generic
    assert rate_at(RATES, "gpu_hour", date(2026, 7, 1), "h100") == D("9.00")
    assert rate_at(RATES, "storage_gb_month", DAY) is None


def test_gpu_hours_clipped_to_the_day():
    assert gpu_hours_in_day(at(8), at(20), DAY, 2) == D("24.0000")  # 12 h x 2 GPUs
    assert gpu_hours_in_day(at(22, day=9), at(2), DAY, 1) == D("2.0000")  # started yesterday
    assert gpu_hours_in_day(at(22), at(6, day=11), DAY, 1) == D("2.0000")  # runs into tomorrow
    assert gpu_hours_in_day(at(1, day=5), at(1, day=8), DAY, 1) == D("0.0000")  # stopped before
    assert gpu_hours_in_day(at(0, day=9), at(0, day=12), DAY, 1) == D("24.0000")  # whole day
    assert gpu_hours_in_day(at(6), None, DAY, 1, now=at(9, 30)) == D("3.5000")  # still running
    assert gpu_hours_in_day(at(6), None, DAY, 1, now=at(5)) == D("0.0000")


def test_gpu_record_prices_with_gpu_type_and_skips_idle_deployments():
    rec = gpu_record(
        DAY, org_id=ORG, project_id=P1, deployment_id=DEP1, model_version_id=MV1,
        gpu_count=2, start=at(0), stop=at(12), rates=RATES, gpu_type="h100",
    )
    assert rec.resource == "gpu_hour" and rec.quantity == D("24.0000") and rec.usd == D("96.000000")
    idle = gpu_record(
        DAY, org_id=ORG, project_id=P1, deployment_id=DEP1, model_version_id=MV1,
        gpu_count=1, start=at(1, day=1), stop=at(2, day=1), rates=RATES,
    )
    assert idle is None


def test_rollup_usage_groups_and_prices_tokens():
    usage = [
        UsageRow(ORG, P1, DEP1, MV1, 10_000, 2_000),
        UsageRow(ORG, P1, DEP1, MV1, 5_000, 1_000),  # same bucket: summed
        UsageRow(ORG, P2, DEP2, MV2, 1_000, 0),  # no output tokens -> no output row
        UsageRow(ORG, None, DEP2, MV2, 0, 0),  # nothing consumed -> nothing emitted
    ]
    records = rollup_usage(DAY, usage, RATES)
    by_key = {(r.project_id, r.resource): r for r in records}
    assert by_key[(P1, "input_token_1k")].quantity == D("15.0000")
    assert by_key[(P1, "input_token_1k")].usd == D("3.000000")  # 15k x 0.20 (March rate)
    assert by_key[(P1, "output_token_1k")].usd == D("0.900000")  # 3k x 0.30
    assert by_key[(P2, "input_token_1k")].usd == D("0.200000")
    assert (P2, "output_token_1k") not in by_key and all(r.project_id is not None for r in records)
    assert len(records) == 3
    assert all(r.day == DAY and r.org_id == ORG for r in records)
    # before the March price change the old input rate applies
    early = rollup_usage(date(2026, 2, 1), [UsageRow(ORG, P1, DEP1, MV1, 1_000, 0)], RATES)
    assert early[0].usd == D("0.100000")


def test_rollup_with_missing_rates_prices_zero_instead_of_failing():
    records = rollup_usage(DAY, [UsageRow(ORG, P1, DEP1, MV1, 1_000, 1_000)], [])
    assert [r.usd for r in records] == [D("0"), D("0")] and records[0].quantity == D("1.0000")


def record(project=P1, deployment=DEP1, model=MV1, resource="gpu_hour", amount="1.00", day=DAY):
    return CostRecord(day, ORG, project, deployment, model, resource, D("1"), D(amount))


def test_aggregate_by_each_dimension():
    records = [
        record(P1, DEP1, MV1, "gpu_hour", "10.00"),
        record(P1, DEP1, MV1, "input_token_1k", "2.50"),
        record(P2, DEP2, MV2, "gpu_hour", "30.00"),
        record(None, None, None, "storage_gb_month", "1.00"),
    ]
    by_project = aggregate(records, "project")
    assert [(g.key, g.usd) for g in by_project] == [(str(P2), D("30.000000")), (str(P1), D("12.500000")), (None, D("1.000000"))]
    assert by_project[1].by_resource == {"gpu_hour": D("10.000000"), "input_token_1k": D("2.500000")}
    assert [g.key for g in aggregate(records, "deployment")][:2] == [str(DEP2), str(DEP1)]
    names = {MV1: "phi-4-mini v1", MV2: "llama v3"}
    assert [g.key for g in aggregate(records, "model", model_names=names)][:2] == ["llama v3", "phi-4-mini v1"]
    assert [g.key for g in aggregate(records, "model")][0] == str(MV2)
    org = aggregate(records, "org")
    assert len(org) == 1 and org[0].usd == D("43.500000")
    assert total(records) == D("43.500000")


def test_aggregate_is_exact_for_many_small_amounts():
    records = [record(amount="0.000001") for _ in range(1_000_000 // 1000)]
    assert aggregate(records, "org")[0].usd == D("0.001000")


def test_budget_status_and_forecast():
    ok = budget_status(D("250"), D("1000"))
    assert (ok.percent_used, ok.remaining, ok.over_budget) == (D("25.00"), D("750.000000"), False)
    over = budget_status(D("1200.5"), D("1000"))
    assert over.over_budget and over.remaining == D("0.000000") and over.percent_used == D("120.05")
    with pytest.raises(ValueError):
        budget_status(D("1"), D("0"))
    assert forecast_month_end(D("100"), 10, 30) == D("300.000000")
    assert forecast_month_end(D("100"), 30, 30) == D("100.000000")
    for elapsed in (0, 31):
        with pytest.raises(ValueError):
            forecast_month_end(D("1"), elapsed, 30)


def test_render_csv():
    records = [
        record(P1, resource="gpu_hour", amount="10"),
        record(P1, resource="input_token_1k", amount="2.5"),
        record(P2, resource="gpu_hour", amount="30"),
    ]
    text = render_csv(aggregate(records, "project"), "project")
    assert text.splitlines() == [
        "project,gpu_hour,input_token_1k,total_usd",
        f"{P2},30.000000,0,30.000000",
        f"{P1},10.000000,2.500000,12.500000",
    ]
