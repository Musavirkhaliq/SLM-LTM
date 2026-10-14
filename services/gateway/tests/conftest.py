from __future__ import annotations

import random
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from gw_helpers import FakeControlPlane, FakeVLLM, HostTransport, Tenant

from slm_gateway.app import create_app
from slm_gateway.routing import Router
from slm_gateway.settings import GatewaySettings
from slm_gateway.store import MemoryStore


@pytest.fixture
def upstreams() -> dict[str, FakeVLLM]:
    return {}


@pytest.fixture
def transport() -> HostTransport:
    return HostTransport()


@pytest.fixture
def control() -> FakeControlPlane:
    return FakeControlPlane()


@pytest.fixture
async def make_tenant(admin_pool):
    async def _make(*, rpm: int = 100_000, tpm: int = 10_000_000, monthly: int | None = None) -> Tenant:
        org_id = uuid.uuid4()
        await admin_pool.execute(
            "INSERT INTO organizations (id, slug, name) VALUES ($1, $2::text, $2::text)",
            org_id, f"gw-{org_id.hex[:12]}",
        )
        await admin_pool.execute(
            "INSERT INTO quotas (org_id, rpm_limit, tpm_limit, monthly_token_limit) VALUES ($1, $2, $3, $4)",
            org_id, rpm, tpm, monthly,
        )
        return Tenant(org_id, admin_pool)

    return _make


@pytest.fixture
def settings() -> GatewaySettings:
    return GatewaySettings(
        jwt_secret="gateway-test-secret-gateway-test-1234",
        usage_flush_interval_seconds=3600,  # tests flush explicitly
        route_cache_ttl_seconds=0,
    )


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
async def gw(pool, settings, store, transport, control) -> AsyncIterator[FastAPI]:
    app = create_app(
        settings,
        pool=pool,
        store=store,
        inference_http=httpx.AsyncClient(transport=transport),
        control_http=httpx.AsyncClient(transport=control),
        router=Router(pool, ttl=0, rng=random.Random(1234)),
    )
    async with LifespanManager(app):
        yield app


@pytest.fixture
async def client(gw) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=gw, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as c:
        yield c


@pytest.fixture
def flush(gw):
    async def _flush() -> None:
        await gw.state.gateway.drain()
        await gw.state.gateway.usage.flush()

    return _flush


@pytest.fixture
def register_upstream(upstreams, transport):
    def _register(host: str, **kwargs) -> FakeVLLM:
        fake = FakeVLLM(host, **kwargs)
        upstreams[host] = fake
        transport.add(host, fake.app)
        return fake

    return _register


