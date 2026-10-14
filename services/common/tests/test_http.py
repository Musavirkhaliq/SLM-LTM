import base64
import json
import uuid

import httpx
import pytest
from fastapi import Depends
from pydantic import BaseModel

from slm_common.app import create_app
from slm_common.auth import Principal
from slm_common.config import BaseServiceSettings
from slm_common.errors import ApiError
from slm_common.pagination import PageParams, build_page, decode_cursor, encode_cursor
from slm_common.rbac import Role, require_role
from slm_common.security import create_access_token

SETTINGS = BaseServiceSettings(jwt_secret="unit-test-secret-unit-test-secret-1234")


class Body(BaseModel):
    name: str
    size: int


def make_app(style: str = "problem"):
    app = create_app("unit", settings=SETTINGS, error_style=style)

    @app.get("/admin")
    async def admin(p: Principal = require_role(Role.org_admin)):
        return {"role": p.role.name, "org": str(p.org_id)}

    @app.get("/boom")
    async def boom():
        raise ApiError(409, "dup", "already there")

    @app.get("/crash")
    async def crash():
        raise RuntimeError("secret internals")

    @app.post("/things")
    async def things(body: Body):
        return body

    @app.get("/page")
    async def page(params: PageParams = Depends()):
        rows = list(range(params.cursor["after"] + 1 if params.cursor else 0, 7))[: params.fetch]
        return build_page(rows, params.limit, lambda last: {"after": last})

    return app


def client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def bearer(role: str, org: uuid.UUID | None = None) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), org_id=org or uuid.uuid4(), role=role, settings=SETTINGS
    )
    return {"Authorization": f"Bearer {token}"}


async def test_standard_endpoints_and_request_id():
    async with client(make_app()) as c:
        r = await c.get("/healthz", headers={"X-Request-ID": "abc-123"})
        assert r.json() == {"status": "ok", "service": "unit"}
        assert r.headers["x-request-id"] == "abc-123"
        assert (await c.get("/healthz")).headers["x-request-id"]
        assert (await c.get("/readyz")).status_code == 200
        metrics = (await c.get("/metrics")).text
        assert 'slm_http_requests_total{method="GET",route="/healthz",service="unit"' in metrics


async def test_readyz_reports_failing_dependency():
    app = make_app()

    async def down() -> bool:
        return False

    app.state.ready_checks["db"] = down
    async with client(app) as c:
        r = await c.get("/readyz")
        assert r.status_code == 503
        assert r.json()["checks"] == {"db": False}


async def test_rbac_matrix():
    async with client(make_app()) as c:
        assert (await c.get("/admin")).status_code == 401
        assert (await c.get("/admin", headers={"Authorization": "Bearer junk"})).status_code == 401
        for role, expected in [
            ("viewer", 403),
            ("developer", 403),
            ("ml_engineer", 403),
            ("org_admin", 200),
            ("platform_admin", 200),
        ]:
            r = await c.get("/admin", headers=bearer(role))
            assert r.status_code == expected, role


async def test_problem_json_shapes():
    async with client(make_app()) as c:
        r = await c.get("/boom", headers={"X-Request-ID": "rid-1"})
        assert r.status_code == 409
        assert r.headers["content-type"].startswith("application/problem+json")
        body = r.json()
        assert body["code"] == "dup" and body["detail"] == "already there"
        assert body["status"] == 409 and body["request_id"] == "rid-1"

        r = await c.post("/things", json={"name": "x", "size": "nope"})
        assert r.status_code == 422
        assert r.json()["code"] == "validation_error"
        assert r.json()["errors"][0]["loc"][-1] == "size"

        r = await c.get("/crash")
        assert r.status_code == 500
        assert "secret internals" not in r.text


async def test_openai_error_shape():
    async with client(make_app("openai")) as c:
        r = await c.get("/boom")
        assert r.headers["content-type"].startswith("application/json")
        assert r.json()["error"] == {
            "message": "already there",
            "type": "invalid_request_error",
            "param": None,
            "code": "dup",
        }
        r = await c.post("/things", json={"name": "x"})
        assert r.status_code == 400
        assert r.json()["error"]["type"] == "invalid_request_error"


async def test_pagination_dependency_walks_all_pages():
    seen: list[int] = []
    async with client(make_app()) as c:
        cursor = None
        while True:
            params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
            body = (await c.get("/page", params=params)).json()
            seen += body["items"]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        assert seen == list(range(7))
        assert (await c.get("/page", params={"cursor": "!!!"})).status_code == 400
        assert (await c.get("/page", params={"limit": 0})).status_code == 422


def test_cursor_roundtrip_and_garbage():
    token = encode_cursor({"id": uuid.UUID(int=5), "n": 3})
    assert decode_cursor(token) == {"id": str(uuid.UUID(int=5)), "n": 3}
    for bad in ("", "###", base64.urlsafe_b64encode(json.dumps([1]).encode()).decode()):
        with pytest.raises(ApiError):
            decode_cursor(bad)
