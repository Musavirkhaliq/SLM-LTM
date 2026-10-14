from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from slm_common.app import create_app as create_base_app
from slm_common.app import pool_ready_check
from slm_common.config import BaseServiceSettings, get_settings
from slm_common.db import create_pool

from slm_deployment.routes import router


def create_app(
    settings: BaseServiceSettings | None = None, *, pool: asyncpg.Pool | None = None
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.pool = pool or await create_pool(settings.database_url)
        try:
            yield
        finally:
            if pool is None:
                await app.state.pool.close()

    app = create_base_app("deployment", settings=settings, lifespan=lifespan)
    app.state.pool = pool
    app.state.ready_checks["db"] = pool_ready_check(lambda: app.state.pool)
    app.include_router(router)
    return app
