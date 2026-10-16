from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI

from slm_common.app import create_app as create_base_app
from slm_common.app import pool_ready_check
from slm_common.config import BaseServiceSettings
from slm_common.db import create_pool
from slm_finetune.config import FinetuneSettings, get_finetune_settings
from slm_finetune.routes import internal_router, router
from slm_finetune.runner import DockerTrainerRunner
from slm_finetune.s3 import ensure_buckets


def create_app(
    settings: BaseServiceSettings | None = None,
    *,
    pool: asyncpg.Pool | None = None,
) -> FastAPI:
    finetune_settings = (
        settings if isinstance(settings, FinetuneSettings) else get_finetune_settings()
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.pool = pool or await create_pool(finetune_settings.database_url)
        app.state.settings = finetune_settings
        app.state.runner = DockerTrainerRunner(finetune_settings)
        try:
            # Ensure MinIO/S3 buckets exist in the background
            try:
                await ensure_buckets(finetune_settings)
            except Exception:
                pass
            yield
        finally:
            if pool is None and hasattr(app.state, "pool") and app.state.pool:
                await app.state.pool.close()

    app = create_base_app("finetune", settings=finetune_settings, lifespan=lifespan)
    app.state.pool = pool
    app.state.settings = finetune_settings
    app.state.runner = DockerTrainerRunner(finetune_settings)
    if pool is not None:
        app.state.ready_checks["db"] = pool_ready_check(lambda: app.state.pool)
    app.include_router(router)
    app.include_router(internal_router)
    return app
