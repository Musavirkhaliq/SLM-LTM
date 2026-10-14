from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import asyncpg
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from slm_common import __version__
from slm_common.config import BaseServiceSettings, get_settings
from slm_common.errors import install_exception_handlers
from slm_common.logging import RequestIdMiddleware, configure_logging
from slm_common.telemetry import MetricsMiddleware, metrics_endpoint, setup_otel

ReadyCheck = Callable[[], Awaitable[bool]]
_log = structlog.get_logger(__name__)


def create_app(
    service: str,
    *,
    settings: BaseServiceSettings | None = None,
    title: str | None = None,
    lifespan: Any = None,
    ready_checks: Mapping[str, ReadyCheck] | None = None,
    error_style: str = "problem",
) -> FastAPI:
    """FastAPI app with the shared platform plumbing: JSON logs, request ids, Prometheus
    metrics, OTLP tracing, error format, and `/healthz` `/readyz` `/metrics`."""
    settings = settings or get_settings()
    configure_logging(service, settings.log_level)
    app = FastAPI(
        title=title or f"SLM Studio {service}",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.env != "prod" else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.service = service
    app.state.ready_checks = dict(ready_checks or {})
    install_exception_handlers(app, error_style)
    app.add_middleware(MetricsMiddleware, service=service)
    app.add_middleware(RequestIdMiddleware)
    setup_otel(app, service, settings)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": service}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request) -> JSONResponse:
        results: dict[str, bool] = {}
        for name, check in request.app.state.ready_checks.items():
            try:
                results[name] = bool(await check())
            except Exception as exc:  # readiness must never raise
                _log.warning("readiness_check_failed", check=name, error=str(exc))
                results[name] = False
        ok = all(results.values())
        body = {"status": "ready" if ok else "unavailable", "checks": results}
        return JSONResponse(body, status_code=200 if ok else 503)

    app.add_route("/metrics", metrics_endpoint, include_in_schema=False)
    return app


def pool_ready_check(pool_getter: Callable[[], asyncpg.Pool | None]) -> ReadyCheck:
    async def check() -> bool:
        pool = pool_getter()
        if pool is None:
            return False
        async with pool.acquire() as conn:
            return await conn.fetchval("SELECT 1") == 1

    return check
