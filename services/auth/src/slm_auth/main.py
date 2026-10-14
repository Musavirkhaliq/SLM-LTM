"""ASGI entrypoint: `uvicorn slm_auth.main:app --port 8001`."""

from slm_auth.app import create_app

app = create_app()
