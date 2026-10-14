"""Shared-state backend (key cache, sliding-window limits, counters).

One interface, two implementations: Redis for multi-replica correctness and an in-process
fallback. `ResilientStore` degrades from the first to the second when Redis errors, so a Redis
outage weakens limits to per-replica enforcement instead of taking inference down.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import structlog

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WindowResult:
    allowed: bool
    used: int  # total in the window after this call (unchanged when denied)
    retry_after: float  # seconds until enough capacity frees up (0 when allowed)


class Store(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: float) -> None: ...
    async def delete(self, *keys: str) -> None: ...
    async def window_hit(self, key: str, *, limit: int, window: float, cost: int = 1) -> WindowResult:
        """Atomically add `cost` to the sliding window unless that would exceed `limit`."""
        ...
    async def window_used(self, key: str, *, window: float) -> tuple[int, float]:
        """(total in window, seconds until the oldest entry expires)."""
        ...
    async def window_add(self, key: str, *, cost: int, window: float) -> None: ...
    async def counter_get(self, key: str) -> int | None: ...
    async def counter_add(self, key: str, amount: int, ttl: float) -> int: ...
    async def counter_seed(self, key: str, value: int, ttl: float) -> None:
        """Set the counter only if it does not exist yet."""
        ...
    async def aclose(self) -> None: ...


class MemoryStore:
    """Single-process store. Sliding-window *log*: exact, O(entries in window)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._kv: dict[str, tuple[float, str]] = {}
        self._windows: dict[str, deque[tuple[float, int]]] = defaultdict(deque)
        self._counters: dict[str, tuple[float, int]] = {}

    async def get(self, key: str) -> str | None:
        entry = self._kv.get(key)
        if entry is None:
            return None
        if entry[0] <= self._clock():
            del self._kv[key]
            return None
        return entry[1]

    async def set(self, key: str, value: str, ttl: float) -> None:
        if len(self._kv) > 10_000:  # bound memory: drop expired entries opportunistically
            now = self._clock()
            self._kv = {k: v for k, v in self._kv.items() if v[0] > now}
        self._kv[key] = (self._clock() + ttl, value)

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._kv.pop(key, None)

    def _trim(self, key: str, window: float) -> deque[tuple[float, int]]:
        entries = self._windows[key]
        horizon = self._clock() - window
        while entries and entries[0][0] <= horizon:
            entries.popleft()
        return entries

    async def window_hit(self, key: str, *, limit: int, window: float, cost: int = 1) -> WindowResult:
        entries = self._trim(key, window)
        total = sum(c for _, c in entries)
        if total + cost > limit:
            return WindowResult(False, total, self._retry_after(entries, total + cost - limit, window))
        entries.append((self._clock(), cost))
        return WindowResult(True, total + cost, 0.0)

    def _retry_after(self, entries: deque[tuple[float, int]], needed: int, window: float) -> float:
        """Time until expiring the oldest entries frees `needed` units."""
        freed = 0
        for ts, cost in entries:
            freed += cost
            if freed >= needed:
                return max(ts + window - self._clock(), 0.0)
        return window

    async def window_used(self, key: str, *, window: float) -> tuple[int, float]:
        entries = self._trim(key, window)
        total = sum(c for _, c in entries)
        reset = max(entries[0][0] + window - self._clock(), 0.0) if entries else 0.0
        return total, reset

    async def window_add(self, key: str, *, cost: int, window: float) -> None:
        self._trim(key, window).append((self._clock(), cost))

    async def counter_get(self, key: str) -> int | None:
        entry = self._counters.get(key)
        if entry is None or entry[0] <= self._clock():
            self._counters.pop(key, None)
            return None
        return entry[1]

    async def counter_add(self, key: str, amount: int, ttl: float) -> int:
        current = await self.counter_get(key) or 0
        self._counters[key] = (self._clock() + ttl, current + amount)
        return current + amount

    async def counter_seed(self, key: str, value: int, ttl: float) -> None:
        if await self.counter_get(key) is None:
            self._counters[key] = (self._clock() + ttl, value)

    async def aclose(self) -> None:
        return None


# Redis time (not the client's) is the single clock, so replicas cannot skew each other.
# Each window is a sorted set: score = timestamp in ms, member = "<uuid>:<signed cost>".
_LUA_PRELUDE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local window = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local entries = redis.call('ZRANGE', KEYS[1], 0, -1, 'WITHSCORES')
local total = 0
for i = 1, #entries, 2 do
  total = total + tonumber(string.match(entries[i], ':(-?%d+)$'))
end
"""

_LUA_HIT = (
    _LUA_PRELUDE
    + """
local limit = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
if total + cost > limit then
  local needed = total + cost - limit
  local freed = 0
  local retry = window
  for i = 1, #entries, 2 do
    freed = freed + tonumber(string.match(entries[i], ':(-?%d+)$'))
    if freed >= needed then retry = tonumber(entries[i + 1]) + window - now; break end
  end
  if retry < 0 then retry = 0 end
  return {0, total, retry}
end
redis.call('ZADD', KEYS[1], now, ARGV[4] .. ':' .. cost)
redis.call('PEXPIRE', KEYS[1], window + 1000)
return {1, total + cost, 0}
"""
)

_LUA_USED = (
    _LUA_PRELUDE
    + """
local reset = 0
if #entries > 0 then reset = tonumber(entries[2]) + window - now end
if reset < 0 then reset = 0 end
return {total, reset}
"""
)

_LUA_ADD = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZADD', KEYS[1], now, ARGV[2] .. ':' .. ARGV[3])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[1]) + 1000)
return 1
"""


class RedisStore:
    def __init__(self, client) -> None:  # redis.asyncio.Redis
        self._r = client
        self._hit = client.register_script(_LUA_HIT)
        self._used = client.register_script(_LUA_USED)
        self._add = client.register_script(_LUA_ADD)

    async def get(self, key: str) -> str | None:
        value = await self._r.get(key)
        return value.decode() if isinstance(value, bytes) else value

    async def set(self, key: str, value: str, ttl: float) -> None:
        await self._r.set(key, value, px=max(int(ttl * 1000), 1))

    async def delete(self, *keys: str) -> None:
        if keys:
            await self._r.delete(*keys)

    async def window_hit(self, key: str, *, limit: int, window: float, cost: int = 1) -> WindowResult:
        allowed, used, retry_ms = await self._hit(
            keys=[key], args=[int(window * 1000), limit, cost, uuid.uuid4().hex]
        )
        return WindowResult(bool(allowed), int(used), int(retry_ms) / 1000)

    async def window_used(self, key: str, *, window: float) -> tuple[int, float]:
        total, reset_ms = await self._used(keys=[key], args=[int(window * 1000)])
        return int(total), int(reset_ms) / 1000

    async def window_add(self, key: str, *, cost: int, window: float) -> None:
        await self._add(keys=[key], args=[int(window * 1000), uuid.uuid4().hex, cost])

    async def counter_get(self, key: str) -> int | None:
        value = await self._r.get(key)
        return int(value) if value is not None else None

    async def counter_add(self, key: str, amount: int, ttl: float) -> int:
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.incrby(key, amount)
            pipe.expire(key, max(int(ttl), 1))
            value, _ = await pipe.execute()
        return int(value)

    async def counter_seed(self, key: str, value: int, ttl: float) -> None:
        await self._r.set(key, value, ex=max(int(ttl), 1), nx=True)

    async def aclose(self) -> None:
        await self._r.aclose()


class ResilientStore:
    """Primary (Redis) with automatic fallback to a local store when the primary raises."""

    def __init__(self, primary: Store, fallback: Store) -> None:
        self._primary = primary
        self._fallback = fallback
        self._last_warning = 0.0

    def _warn(self, op: str, exc: Exception) -> None:
        now = time.monotonic()
        if now - self._last_warning > 30:  # one line per 30 s, not one per request
            self._last_warning = now
            _log.warning("redis_unavailable_using_local_fallback", op=op, error=str(exc))

    async def _call(self, op: str, *args, **kwargs):
        try:
            return await getattr(self._primary, op)(*args, **kwargs)
        except Exception as exc:
            self._warn(op, exc)
            return await getattr(self._fallback, op)(*args, **kwargs)

    async def get(self, key: str) -> str | None:
        return await self._call("get", key)

    async def set(self, key: str, value: str, ttl: float) -> None:
        await self._call("set", key, value, ttl)

    async def delete(self, *keys: str) -> None:
        await self._call("delete", *keys)

    async def window_hit(self, key: str, *, limit: int, window: float, cost: int = 1) -> WindowResult:
        return await self._call("window_hit", key, limit=limit, window=window, cost=cost)

    async def window_used(self, key: str, *, window: float) -> tuple[int, float]:
        return await self._call("window_used", key, window=window)

    async def window_add(self, key: str, *, cost: int, window: float) -> None:
        await self._call("window_add", key, cost=cost, window=window)

    async def counter_get(self, key: str) -> int | None:
        return await self._call("counter_get", key)

    async def counter_add(self, key: str, amount: int, ttl: float) -> int:
        return await self._call("counter_add", key, amount, ttl)

    async def counter_seed(self, key: str, value: int, ttl: float) -> None:
        await self._call("counter_seed", key, value, ttl)

    async def aclose(self) -> None:
        await self._primary.aclose()
        await self._fallback.aclose()


def build_store(redis_url: str | None) -> Store:
    memory = MemoryStore()
    if not redis_url:
        return memory
    from redis import asyncio as aioredis

    return ResilientStore(RedisStore(aioredis.from_url(redis_url)), memory)
