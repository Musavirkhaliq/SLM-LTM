"""ASGI entrypoint: `uvicorn slm_monitoring.main:app --port 8007`."""

from slm_monitoring.app import create_app

app = create_app()
