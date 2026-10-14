import json
import uuid

import httpx
import pytest

from slm_common.config import BaseServiceSettings
from slm_common.security import create_access_token
from slm_finetune.app import create_app

SETTINGS = BaseServiceSettings(jwt_secret="finetune-test-secret-finetune-test-1234")
ID = uuid.uuid4()


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


def job(**over) -> dict:
    return {
        "name": "support-tune",
        "base_version_id": str(uuid.uuid4()),
        "dataset_id": str(uuid.uuid4()),
    } | over


def good_jsonl(n: int = 12) -> bytes:
    rows = [
        {"messages": [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]}
        for i in range(n)
    ]
    return "\n".join(json.dumps(r) for r in rows).encode()


async def test_probes(client):
    assert (await client.get("/healthz")).json()["service"] == "finetune"
    assert (await client.get("/readyz")).status_code == 200
    assert (await client.get("/metrics")).status_code == 200


async def test_contract_routes_return_501_problem_json(client):
    cases = [
        ("GET", "/api/v1/datasets", "viewer", {}),
        ("GET", "/api/v1/training-jobs", "viewer", {}),
        ("POST", "/api/v1/training-jobs", "ml_engineer", {"json": job()}),
        ("GET", f"/api/v1/training-jobs/{ID}", "viewer", {}),
        ("GET", f"/api/v1/training-jobs/{ID}/metrics", "viewer", {}),
        ("POST", f"/api/v1/training-jobs/{ID}/cancel", "ml_engineer", {}),
    ]
    for method, path, role, kwargs in cases:
        r = await client.request(method, path, headers=headers(role), **kwargs)
        assert r.status_code == 501, (path, r.text)
        assert r.headers["content-type"].startswith("application/problem+json")


async def test_rbac(client):
    assert (await client.get("/api/v1/training-jobs")).status_code == 401
    for role in ("viewer", "developer"):
        assert (await client.post("/api/v1/training-jobs", json=job(), headers=headers(role))).status_code == 403
        r = await client.post(
            "/api/v1/datasets",
            data={"name": "d"},
            files={"file": ("d.jsonl", good_jsonl(), "application/jsonl")},
            headers=headers(role),
        )
        assert r.status_code == 403


@pytest.mark.parametrize(
    "mutation",
    [
        {"name": ""},
        {"method": "prompt-tuning"},
        {"gpu_count": 0},
        {"base_version_id": "x"},
        {"hyperparams": {"learning_rate": 0}},
        {"hyperparams": {"epochs": 0}},
        {"hyperparams": {"typo_lr": 1}},
        {"hyperparams": {"warmup_ratio": 1.5}},
        {"method": "full", "hyperparams": {"lora_r": 8}},
    ],
)
async def test_training_job_validation(client, mutation):
    r = await client.post(
        "/api/v1/training-jobs", json=job(**mutation), headers=headers("ml_engineer")
    )
    assert r.status_code == 422, mutation


async def test_valid_training_jobs_reach_the_stub(client):
    h = headers("ml_engineer")
    for payload in (
        job(),
        job(method="lora", hyperparams={"lora_r": 8, "lora_alpha": 16, "learning_rate": 1e-4}),
        job(method="full", hyperparams={"learning_rate": 1e-5, "epochs": 1}),
    ):
        assert (await client.post("/api/v1/training-jobs", json=payload, headers=h)).status_code == 501


async def test_dataset_upload_is_validated_before_the_stub(client):
    h = headers("ml_engineer")
    ok = await client.post(
        "/api/v1/datasets",
        data={"name": "support"},
        files={"file": ("support.jsonl", good_jsonl(), "application/jsonl")},
        headers=h,
    )
    assert ok.status_code == 501  # valid, but storing is not built yet

    bad = await client.post(
        "/api/v1/datasets",
        data={"name": "broken"},
        files={"file": ("broken.jsonl", b'{"messages": []}\nnot json\n', "application/jsonl")},
        headers=h,
    )
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_dataset"
    report = bad.json()["report"]
    assert report["error_count"] == 2 and report["errors"][1]["message"] == "invalid JSON"

    csv_ok = await client.post(
        "/api/v1/datasets",
        data={"name": "csv", "purpose": "eval"},
        files={"file": ("eval.csv", b"prompt,completion\nhi,hello\n", "text/csv")},
        headers=h,
    )
    assert csv_ok.status_code == 501

    wrong_type = await client.post(
        "/api/v1/datasets", data={"name": "x"}, files={"file": ("x.parquet", b"PAR1", "application/octet-stream")}, headers=h
    )
    assert wrong_type.status_code == 415
    no_name = await client.post(
        "/api/v1/datasets", files={"file": ("x.jsonl", good_jsonl(), "application/jsonl")}, headers=h
    )
    assert no_name.status_code == 422
    bad_purpose = await client.post(
        "/api/v1/datasets",
        data={"name": "x", "purpose": "pretrain"},
        files={"file": ("x.jsonl", good_jsonl(), "application/jsonl")},
        headers=h,
    )
    assert bad_purpose.status_code == 422
