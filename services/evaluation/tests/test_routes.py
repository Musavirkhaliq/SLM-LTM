import uuid

import httpx
import pytest

from slm_common.config import BaseServiceSettings
from slm_common.security import create_access_token
from slm_evaluation.app import create_app

SETTINGS = BaseServiceSettings(jwt_secret="evaluation-test-secret-evaluation-1234")
A, B = str(uuid.uuid4()), str(uuid.uuid4())


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


def body(**over) -> dict:
    return {
        "name": "phi vs llama",
        "dataset_id": str(uuid.uuid4()),
        "model_version_ids": [A, B],
        "metrics": ["accuracy", "rouge"],
    } | over


async def test_probes(client):
    assert (await client.get("/healthz")).json()["service"] == "evaluation"
    assert (await client.get("/readyz")).status_code == 200
    assert (await client.get("/metrics")).status_code == 200


async def test_routes_exist_and_return_501_problem_json(client):
    cases = [
        ("GET", "/api/v1/evaluations", "viewer", None),
        ("POST", "/api/v1/evaluations", "ml_engineer", body()),
        ("GET", f"/api/v1/evaluations/{A}/results", "viewer", None),
        ("GET", f"/api/v1/evaluations/compare?ids={A},{B}", "viewer", None),
    ]
    for method, path, role, payload in cases:
        r = await client.request(method, path, json=payload, headers=headers(role))
        assert r.status_code == 501, path
        assert r.headers["content-type"].startswith("application/problem+json")


async def test_rbac(client):
    assert (await client.get("/api/v1/evaluations")).status_code == 401
    r = await client.post("/api/v1/evaluations", json=body(), headers=headers("developer"))
    assert r.status_code == 403
    assert (await client.get("/api/v1/evaluations", headers=headers("viewer"))).status_code == 501


@pytest.mark.parametrize(
    "mutation",
    [
        {"model_version_ids": []},
        {"model_version_ids": [A, A]},
        {"metrics": []},
        {"metrics": ["accuracy", "accuracy"]},
        {"metrics": ["perplexity"]},
        {"metrics": ["hallucination"]},  # LLM judged without a judge model
        {"dataset_id": "x"},
        {"name": ""},
    ],
)
async def test_create_validation(client, mutation):
    r = await client.post(
        "/api/v1/evaluations", json=body(**mutation), headers=headers("ml_engineer")
    )
    assert r.status_code == 422, mutation


async def test_llm_judged_metrics_accepted_with_judge(client):
    r = await client.post(
        "/api/v1/evaluations",
        json=body(metrics=["faithfulness", "json_validity"], judge_model="llama-3.1-8b"),
        headers=headers("ml_engineer"),
    )
    assert r.status_code == 501


@pytest.mark.parametrize(
    "ids", ["", A, f"{A},{A}", f"{A},nope", ",".join(str(uuid.uuid4()) for _ in range(6))]
)
async def test_compare_ids_validation(client, ids):
    r = await client.get(
        "/api/v1/evaluations/compare", params={"ids": ids}, headers=headers("viewer")
    )
    assert r.status_code == 422, ids


async def test_compare_requires_ids(client):
    r = await client.get("/api/v1/evaluations/compare", headers=headers("viewer"))
    assert r.status_code == 422
