from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any

import httpx

from slm_gateway.keys import KeyAuthenticator
from slm_gateway.limits import Limiter
from slm_gateway.routing import Router
from slm_gateway.settings import GatewaySettings
from slm_gateway.store import Store
from slm_gateway.usage import UsageWriter


@dataclass
class Gateway:
    """Everything a request handler needs, assembled once in the app lifespan."""

    settings: GatewaySettings
    store: Store
    keys: KeyAuthenticator
    limiter: Limiter
    router: Router
    usage: UsageWriter
    inference_http: httpx.AsyncClient
    control_http: httpx.AsyncClient
    _tasks: set[asyncio.Task[Any]] = field(default_factory=set)

    def background(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Run post-response bookkeeping without blocking the stream and without losing the task."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
