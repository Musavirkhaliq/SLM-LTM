import uuid

import httpx
import pytest

from slm_common.config import BaseServiceSettings
from slm_common.security import create_access_token
from slm_rag.app import create_app

SETTINGS = BaseServiceSettings(jwt_secret="rag-test-secret-rag-test-secret-rag-12345")
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


def index_body(**over) -> dict:
    return {"name": "handbook", "embedding_model": "bge-m3", "embedding_dim": 1024} | over


async def test_probes(client):
    assert (await client.get("/healthz")).json()["service"] == "rag"
    assert (await client.get("/readyz")).status_code == 200
    assert (await client.get("/metrics")).status_code == 200


async def test_routes_return_501_problem_json(client):
    cases = [
        ("GET", "/api/v1/rag/indexes", "viewer", {}),
        ("POST", "/api/v1/rag/indexes", "ml_engineer", {"json": index_body()}),
        (
            "POST",
            f"/api/v1/rag/indexes/{ID}/documents",
            "ml_engineer",
            {"files": {"file": ("a.pdf", b"%PDF-1.4", "application/pdf")}},
        ),
        ("POST", f"/api/v1/rag/indexes/{ID}/query", "developer", {"json": {"query": "refund policy?"}}),
    ]
    for method, path, role, kwargs in cases:
        r = await client.request(method, path, headers=headers(role), **kwargs)
        assert r.status_code == 501, (path, r.text)
        assert r.headers["content-type"].startswith("application/problem+json")


async def test_rbac(client):
    assert (await client.get("/api/v1/rag/indexes")).status_code == 401
    r = await client.post("/api/v1/rag/indexes", json=index_body(), headers=headers("developer"))
    assert r.status_code == 403
    r = await client.post(
        f"/api/v1/rag/indexes/{ID}/query", json={"query": "q"}, headers=headers("viewer")
    )
    assert r.status_code == 403  # querying is inference-adjacent: developer and up
    r = await client.post(
        f"/api/v1/rag/indexes/{ID}/query", json={"query": "q"}, headers=headers("developer")
    )
    assert r.status_code == 501


@pytest.mark.parametrize(
    "mutation",
    [
        {"name": "bad name"},
        {"embedding_dim": 0},
        {"embedding_dim": 100_000},
        {"chunk_size": 10},
        {"chunk_size": 256, "chunk_overlap": 256},
        {"backend": "pinecone"},
        {"embedding_model": ""},
    ],
)
async def test_index_validation(client, mutation):
    r = await client.post(
        "/api/v1/rag/indexes", json=index_body(**mutation), headers=headers("ml_engineer")
    )
    assert r.status_code == 422, mutation


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": ""},
        {"query": "x", "top_k": 0},
        {"query": "x", "top_k": 51},
        {"query": "x", "generate": True},  # needs a model alias
    ],
)
async def test_query_validation(client, payload):
    r = await client.post(
        f"/api/v1/rag/indexes/{ID}/query", json=payload, headers=headers("developer")
    )
    assert r.status_code == 422, payload


async def test_query_with_generation_is_valid(client):
    payload = {"query": "x", "generate": True, "model": "support-bot", "top_k": 3, "rerank": True}
    r = await client.post(
        f"/api/v1/rag/indexes/{ID}/query", json=payload, headers=headers("developer")
    )
    assert r.status_code == 501


async def test_document_upload_validation(client):
    url = f"/api/v1/rag/indexes/{ID}/documents"
    h = headers("ml_engineer")
    r = await client.post(url, files={"file": ("a.exe", b"MZ", "application/x-msdownload")}, headers=h)
    assert r.status_code == 415 and r.json()["code"] == "unsupported_media_type"
    r = await client.post(url, headers=h)
    assert r.status_code == 422  # no file part
    r = await client.post(url, files={"file": ("notes.md", b"# hi", "text/markdown")}, headers=h)
    assert r.status_code == 501  # accepted by validation, body not implemented
    r = await client.post(url, files={"file": ("a.txt", b"x", "text/plain")}, headers=headers("developer"))
    assert r.status_code == 403
