"""ASGI entrypoint: `uvicorn slm_finetune.main:app --port 8003`."""

from slm_finetune.app import create_app

app = create_app()
