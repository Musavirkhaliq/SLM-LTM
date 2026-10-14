"""ASGI entrypoint: `uvicorn slm_rag.main:app --port 8004`."""

from slm_rag.app import create_app

app = create_app()
