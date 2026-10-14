import uuid

import httpx
import pytest

from slm_common.config import BaseServiceSettings
from slm_common.security import create_access_token
from slm_cost.app import create_app

SETTINGS = BaseServiceSettings(jwt_secret="cost-test-secret-cost-test-secret-cost-123")


def headers(role: str) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), org_id=uuid.uuid4(), role=role, settings=SETTINGS
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def client():
    app = create_app(SETTINGS)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    ) as c:
        yield c


async def test_probes(client):
    assert (await client.get("/healthz")).json()["service"] == "cost"
    assert (await client.get("/readyz")).status_code == 200
    assert (await client.get("/metrics")).status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/cost/summary", "/api/v1/cost/report.csv"])
async def test_valid_requests_are_501_problem_json(client, path):
    for group_by in ("project", "deployment", "model", "org"):
        r = await client.get(
            path,
            params={"group_by": group_by, "from": "2026-03-01", "to": "2026-03-31"},
            headers=headers("org_admin"),
        )
        assert r.status_code == 501, (path, group_by)
        assert r.headers["content-type"].startswith("application/problem+json")
    assert (await client.get(path, headers=headers("platform_admin"))).status_code == 501


@pytest.mark.parametrize("path", ["/api/v1/cost/summary", "/api/v1/cost/report.csv"])
async def test_rbac_org_admin_only(client, path):
    assert (await client.get(path)).status_code == 401
    for role in ("viewer", "developer", "ml_engineer"):
        assert (await client.get(path, headers=headers(role))).status_code == 403, role


@pytest.mark.parametrize(
    "params",
    [
        {"group_by": "region"},
        {"from": "yesterday"},
        {"from": "2026-04-01", "to": "2026-03-01"},
        {"from": "2020-01-01", "to": "2026-03-01"},
    ],
)
async def test_query_validation(client, params):
    r = await client.get("/api/v1/cost/summary", params=params, headers=headers("org_admin"))
    assert r.status_code == 422, params
