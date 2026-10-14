"""ASGI entrypoint: `uvicorn slm_cost.main:app --port 8008`."""

from slm_cost.app import create_app

app = create_app()
