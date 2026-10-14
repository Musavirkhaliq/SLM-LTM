from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import httpx
import pytest

from slm_auth.app import create_app
from slm_auth.settings import AuthSettings

SECRET = "auth-test-secret-auth-test-secret-123456"


@dataclass
class Session:
    email: str
    user_id: str
    org_id: str
    access: str
    refresh: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access}"}


class Upstream:
    """Scriptable fake OAuth provider reachable through httpx.MockTransport."""

    def __init__(self) -> None:
        self.token_status = 200
        self.userinfo: dict = {}
        self.emails: list[dict] = []
        self.token_requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/token") or "access_token" in url:
            self.token_requests.append(request)
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "bad_code"})
            return httpx.Response(200, json={"access_token": "upstream-token", "token_type": "bearer"})
        if url.endswith("/user/emails"):
            return httpx.Response(200, json=self.emails)
        return httpx.Response(200, json=self.userinfo)


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


@pytest.fixture
def settings() -> AuthSettings:
    return AuthSettings(
        jwt_secret=SECRET,
        public_base_url="http://test",
        oauth={
            "google": {"client_id": "g-id", "client_secret": "g-secret"},
            "github": {"client_id": "gh-id", "client_secret": "gh-secret"},
            "azure_ad": {"client_id": "az-id", "client_secret": "az-secret", "tenant": "contoso"},
        },
    )


@pytest.fixture
async def client(pool, settings, upstream) -> AsyncIterator[httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = create_app(settings, pool=pool, http_client=http)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await http.aclose()


@pytest.fixture
def register(client: httpx.AsyncClient) -> Callable[..., object]:
    async def _register(org_name: str | None = None, display_name: str = "Ada") -> Session:
        email = f"{uuid.uuid4().hex[:10]}@example.com"
        r = await client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "correct-horse-battery",
                "display_name": display_name,
                "org_name": org_name,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        return Session(email, body["user"]["id"], body["org"]["id"], body["access_token"], body["refresh_token"])

    return _register
