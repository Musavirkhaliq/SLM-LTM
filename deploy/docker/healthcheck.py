"""Container HEALTHCHECK helper for the Python services (stdlib only, no curl in the image)."""
import os
import sys
import urllib.request

port = os.environ.get("PORT", "8000")
path = os.environ.get("HEALTHCHECK_PATH", "/healthz")
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as resp:  # noqa: S310
        sys.exit(0 if resp.status == 200 else 1)
except Exception:
    sys.exit(1)
