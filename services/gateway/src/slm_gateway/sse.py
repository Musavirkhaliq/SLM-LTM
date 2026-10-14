"""Incremental Server-Sent-Events inspector for OpenAI-style streams.

Events are forwarded byte-for-byte. The sniffer only reads them to learn the token usage and
the number of content chunks, and can withhold the final usage-only chunk when the gateway
(not the client) asked the upstream for it.
"""

from __future__ import annotations

import json
import re
from typing import Any

_BOUNDARY = re.compile(rb"(?:\r?\n){2}")


class SSEInspector:
    def __init__(self, *, drop_usage_chunk: bool) -> None:
        self._drop_usage_chunk = drop_usage_chunk
        self._buf = b""
        self.usage: dict[str, Any] | None = None
        self.content_events = 0
        self.saw_done = False

    def feed(self, chunk: bytes) -> list[bytes]:
        """Return the complete events from `chunk` that should be sent to the client."""
        self._buf += chunk
        out: list[bytes] = []
        while (m := _BOUNDARY.search(self._buf)) is not None:
            event, self._buf = self._buf[: m.end()], self._buf[m.end() :]
            if self._inspect(event):
                out.append(event)
        return out

    def tail(self) -> bytes:
        """Bytes of an unterminated final event, forwarded as-is."""
        rest, self._buf = self._buf, b""
        return rest

    def _inspect(self, event: bytes) -> bool:
        keep = True
        for line in event.splitlines():
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                self.saw_done = True
                continue
            try:
                parsed = json.loads(data)
            except ValueError:
                continue
            if not isinstance(parsed, dict):
                continue
            usage = parsed.get("usage")
            if isinstance(usage, dict):
                self.usage = usage
                if self._drop_usage_chunk and not parsed.get("choices"):
                    keep = False
            elif parsed.get("choices"):
                self.content_events += 1
        return keep
