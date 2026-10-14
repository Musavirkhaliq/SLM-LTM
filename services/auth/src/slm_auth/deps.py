from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated

import asyncpg
import httpx
import structlog
from fastapi import Depends, Request

from slm_auth.settings import AuthSettings
from slm_common.security import API_KEY_CACHE_PREFIX

_log = structlog.get_logger(__name__)


def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def get_auth_settings(request: Request) -> AuthSettings:
    return request.app.state.settings


def get_http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


Pool = Annotated[asyncpg.Pool, Depends(get_pool)]
Settings = Annotated[AuthSettings, Depends(get_auth_settings)]
Http = Annotated[httpx.AsyncClient, Depends(get_http)]


async def invalidate_key_cache(request: Request, key_hashes: Iterable[bytes]) -> None:
    """Best effort: drop gateway-cached API-key entries so a revoke takes effect immediately
    instead of after the cache TTL. Failures only delay the revoke by at most that TTL."""
    redis = getattr(request.app.state, "redis", None)
    hashes = list(key_hashes)
    if redis is None or not hashes:
        return
    try:
        await redis.delete(*(API_KEY_CACHE_PREFIX + h.hex() for h in hashes))
    except Exception as exc:
        _log.warning("api_key_cache_invalidation_failed", error=str(exc))
