"""ASGI entrypoint: `uvicorn slm_evaluation.main:app --port 8005`."""

from slm_evaluation.app import create_app

app = create_app()
