"""ASGI entrypoint: `uvicorn slm_gateway.main:app --port 8000`."""

from slm_gateway.app import create_app

app = create_app()
