from __future__ import annotations

from fastapi import FastAPI

from slm_common.app import create_app as create_base_app
from slm_common.config import BaseServiceSettings, get_settings
from slm_finetune.routes import router


def create_app(settings: BaseServiceSettings | None = None) -> FastAPI:
    app = create_base_app("finetune", settings=settings or get_settings())
    app.include_router(router)
    return app
