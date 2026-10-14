"""ASGI entrypoint: `uvicorn slm_registry.main:app --port 8002`."""

from slm_registry.app import create_app

app = create_app()
