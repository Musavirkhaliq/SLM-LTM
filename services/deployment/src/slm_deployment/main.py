"""ASGI entrypoint: `uvicorn slm_deployment.main:app --port 8006`."""

from slm_deployment.app import create_app

app = create_app()
