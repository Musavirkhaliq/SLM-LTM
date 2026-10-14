from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import httpx
from fastapi import FastAPI

from slm_auth.routes import audit, auth, keys, oauth, orgs, projects
from slm_auth.settings import AuthSettings, get_auth_settings
from slm_common.app import create_app as create_base_app
from slm_common.app import pool_ready_check
from slm_common.db import create_pool


def create_app(
    settings: AuthSettings | None = None,
    *,
    pool: asyncpg.Pool | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    """`pool` / `http_client` may be injected (tests); otherwise they are created in lifespan."""
    settings = settings or get_auth_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned_pool = pool is None
        app.state.pool = pool or await create_pool(settings.database_url)
        owned_http = http_client is None
        app.state.http = http_client or httpx.AsyncClient(timeout=10.0)
        app.state.redis = None
        if settings.redis_url:
            from redis import asyncio as aioredis

            app.state.redis = aioredis.from_url(settings.redis_url)
        try:
            yield
        finally:
            if app.state.redis is not None:
                await app.state.redis.aclose()
            if owned_http:
                await app.state.http.aclose()
            if owned_pool:
                await app.state.pool.close()

    app = create_base_app("auth", settings=settings, lifespan=lifespan)
    app.state.pool = pool
    app.state.http = http_client
    app.state.redis = None
    app.state.ready_checks["db"] = pool_ready_check(lambda: app.state.pool)
    for module in (auth, orgs, projects, keys, oauth, audit):
        app.include_router(module.router)
    return app
