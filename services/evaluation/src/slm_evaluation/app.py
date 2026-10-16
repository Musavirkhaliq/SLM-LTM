from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI

from slm_common.app import create_app as create_base_app
from slm_common.app import pool_ready_check
from slm_common.config import BaseServiceSettings
from slm_common.db import create_pool
from slm_evaluation.config import EvaluationSettings, get_evaluation_settings
from slm_evaluation.evaluator import EvaluationEngine
from slm_evaluation.routes import router


def create_app(
    settings: BaseServiceSettings | None = None,
    *,
    pool: asyncpg.Pool | None = None,
) -> FastAPI:
    if isinstance(settings, EvaluationSettings):
        eval_settings = settings
    elif isinstance(settings, BaseServiceSettings):
        eval_settings = EvaluationSettings(**settings.model_dump())
    else:
        eval_settings = get_evaluation_settings()


    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.pool = pool or await create_pool(eval_settings.database_url)
        app.state.settings = eval_settings
        app.state.engine = EvaluationEngine(eval_settings)
        try:
            yield
        finally:
            if pool is None and hasattr(app.state, "pool") and app.state.pool:
                await app.state.pool.close()

    app = create_base_app("evaluation", settings=eval_settings, lifespan=lifespan)
    app.state.pool = pool
    app.state.settings = eval_settings
    app.state.engine = EvaluationEngine(eval_settings)
    if pool is not None:
        app.state.ready_checks["db"] = pool_ready_check(lambda: app.state.pool)
    app.include_router(router)
    return app
