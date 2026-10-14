import httpx
import pytest
from fastapi.responses import JSONResponse, Response

from slm_gateway.controlplane import resolve_service
from slm_gateway.settings import GatewaySettings

SERVICES = ("auth", "registry", "finetune", "rag", "evaluation", "deployment", "monitoring", "cost")


@pytest.mark.parametrize(
    ("path", "service"),
    [
        ("/api/v1/auth/login", "auth"),
        ("/api/v1/me", "auth"),
        ("/api/v1/orgs/members/abc", "auth"),
        ("/api/v1/projects", "auth"),
        ("/api/v1/api-keys/1/rotate", "auth"),
        ("/api/v1/audit-logs", "auth"),
        ("/api/v1/models/1/versions", "registry"),
        ("/api/v1/datasets", "finetune"),
        ("/api/v1/training-jobs/1/cancel", "finetune"),
        ("/api/v1/rag/indexes/1/query", "rag"),
        ("/api/v1/evaluations/compare", "evaluation"),
        ("/api/v1/deployments/1/scale", "deployment"),
        ("/api/v1/endpoints/1/routes", "deployment"),
        ("/api/v1/monitoring/overview", "monitoring"),
        ("/api/v1/cost/report.csv", "cost"),
    ],
)
def test_prefix_routing_covers_every_contract_route(path, service):
    settings = GatewaySettings(**{f"{s}_url": f"http://{s}:1" for s in SERVICES})
    assert resolve_service(settings, path) == f"http://{service}:1"


def test_prefix_must_match_whole_segments():
    settings = GatewaySettings()
    for path in ("/api/v1/models-evil", "/api/v1/authx/login", "/api/v1/", "/api/v2/models"):
        assert resolve_service(settings, path) is None


async def test_get_is_forwarded_with_query_and_headers(client, control, settings):
    r = await client.get(
        "/api/v1/models?limit=5&family=phi&family=qwen",
        headers={"Authorization": "Bearer jwt-token", "X-Request-ID": "rid-9"},
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    [call] = control.calls
    assert call["url"] == f"{settings.registry_url}/api/v1/models?limit=5&family=phi&family=qwen"
    assert call["headers"]["authorization"] == "Bearer jwt-token"
    assert call["headers"]["x-request-id"] == "rid-9"
    assert call["headers"]["x-forwarded-proto"] == "http"
    assert call["method"] == "GET"


async def test_client_supplied_forwarded_for_is_not_trusted_by_default(client, control):
    await client.get("/api/v1/me", headers={"X-Forwarded-For": "6.6.6.6"})
    assert control.calls[0]["headers"]["x-forwarded-for"] != "6.6.6.6"


async def test_body_methods_and_response_passthrough(client, control, settings):
    def respond(request, body: bytes) -> Response:
        response = JSONResponse({"echo": body.decode()}, status_code=201)
        response.headers.append("set-cookie", "a=1; Path=/")
        response.headers.append("set-cookie", "b=2; Path=/")
        response.headers["x-custom"] = "yes"
        return response

    control.respond = respond
    r = await client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "pw"})
    assert r.status_code == 201
    assert "a@example.com" in r.json()["echo"]
    assert r.headers.get_list("set-cookie") == ["a=1; Path=/", "b=2; Path=/"]
    assert r.headers["x-custom"] == "yes" and r.headers["x-request-id"]
    assert control.calls[0]["url"].startswith(settings.auth_url)
    for method in ("PUT", "PATCH", "DELETE"):
        r = await client.request(method, "/api/v1/projects/1", json={"name": "x"})
        assert r.status_code == 201 and control.calls[-1]["method"] == method
        assert b'"name"' in control.calls[-1]["body"]


async def test_large_body_is_streamed_through_intact(client, control):
    payload = b"0123456789abcdef" * 200_000  # 3.2 MB multipart-style upload
    r = await client.post(
        "/api/v1/datasets", content=payload, headers={"content-type": "application/octet-stream"}
    )
    assert r.status_code == 200
    assert control.calls[0]["body"] == payload


async def test_upstream_status_codes_pass_through_untouched(client, control):
    control.respond = lambda request, body: Response(
        b'{"code":"dup"}', status_code=409, media_type="application/problem+json"
    )
    r = await client.post("/api/v1/projects", json={"name": "x"})
    assert r.status_code == 409 and r.headers["content-type"] == "application/problem+json"
    control.respond = lambda request, body: Response(
        status_code=302, headers={"location": "https://idp/authorize"}
    )
    r = await client.get("/api/v1/auth/oauth/google/start")
    assert r.status_code == 302 and r.headers["location"] == "https://idp/authorize"  # not followed


async def test_unknown_prefix_is_problem_json_404(client):
    r = await client.get("/api/v1/nothing-here")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "route_not_found"


async def test_unreachable_and_slow_upstreams(client, control):
    control.fail = httpx.ConnectError
    r = await client.get("/api/v1/models")
    assert r.status_code == 503 and r.json()["code"] == "upstream_unavailable"
    assert r.headers["content-type"].startswith("application/problem+json")
    control.fail = httpx.ReadTimeout
    assert (await client.get("/api/v1/models")).status_code == 504


async def test_probe_endpoints(client):
    assert (await client.get("/healthz")).json()["service"] == "gateway"
    r = await client.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"] == {"db": True}
    assert "slm_http_requests_total" in (await client.get("/metrics")).text
