from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from slm_common.app import create_app as create_base_app
from slm_common.app import pool_ready_check
from slm_common.db import create_pool
from slm_common.errors import ApiError, openai_error_handler, problem_json_handler
from slm_gateway import controlplane, inference
from slm_gateway.keys import KeyAuthenticator
from slm_gateway.limits import Limiter
from slm_gateway.routing import Router
from slm_gateway.settings import GatewaySettings, get_gateway_settings
from slm_gateway.state import Gateway
from slm_gateway.store import Store, build_store
from slm_gateway.usage import UsageWriter


async def _by_path_handler(request: Request, exc: Exception):
    """`/v1/*` speaks the OpenAI error format; everything else is RFC 7807 problem+json."""
    handler = openai_error_handler if request.url.path.startswith("/v1/") else problem_json_handler
    return await handler(request, exc)


def build_gateway(
    settings: GatewaySettings,
    pool: asyncpg.Pool,
    store: Store,
    inference_http: httpx.AsyncClient,
    control_http: httpx.AsyncClient,
    router: Router | None = None,
) -> Gateway:
    return Gateway(
        settings=settings,
        store=store,
        keys=KeyAuthenticator(
            pool,
            store,
            ttl=settings.api_key_cache_ttl_seconds,
            negative_ttl=settings.api_key_negative_cache_seconds,
        ),
        limiter=Limiter(store, pool),
        router=router or Router(pool, ttl=settings.route_cache_ttl_seconds),
        usage=UsageWriter(
            pool,
            batch_size=settings.usage_batch_size,
            flush_interval=settings.usage_flush_interval_seconds,
            queue_size=settings.usage_queue_size,
        ),
        inference_http=inference_http,
        control_http=control_http,
    )


def create_app(
    settings: GatewaySettings | None = None,
    *,
    pool: asyncpg.Pool | None = None,
    store: Store | None = None,
    inference_http: httpx.AsyncClient | None = None,
    control_http: httpx.AsyncClient | None = None,
    router: Router | None = None,
) -> FastAPI:
    """Collaborators may be injected (tests); anything not injected is built in the lifespan."""
    settings = settings or get_gateway_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = {
            "pool": pool is None,
            "store": store is None,
            "inference": inference_http is None,
            "control": control_http is None,
        }
        the_pool = pool or await create_pool(settings.database_url, max_size=20)
        the_store = store or build_store(settings.redis_url)
        the_inference = inference_http or httpx.AsyncClient(
            timeout=httpx.Timeout(
                settings.upstream_read_timeout, connect=settings.upstream_connect_timeout
            ),
            limits=httpx.Limits(max_connections=1000, max_keepalive_connections=200),
        )
        the_control = control_http or httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=5.0), follow_redirects=False
        )
        gateway = build_gateway(settings, the_pool, the_store, the_inference, the_control, router)
        app.state.pool = the_pool
        app.state.gateway = gateway
        gateway.usage.start()
        try:
            yield
        finally:
            await gateway.drain()
            await gateway.usage.aclose()
            if owned["inference"]:
                await the_inference.aclose()
            if owned["control"]:
                await the_control.aclose()
            if owned["store"]:
                await the_store.aclose()
            if owned["pool"]:
                await the_pool.close()

    app = create_base_app("gateway", settings=settings, lifespan=lifespan)
    for exc_type in (ApiError, RequestValidationError, StarletteHTTPException, Exception):
        app.add_exception_handler(exc_type, _by_path_handler)

    app.state.ready_checks["db"] = pool_ready_check(lambda: getattr(app.state, "pool", None))
    app.include_router(inference.router)
    app.include_router(controlplane.router)
    return app
