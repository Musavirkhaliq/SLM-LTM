"""Asynchronous, batched `usage_logs` writer. Request handlers only enqueue."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import astuple, dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
import structlog
from prometheus_client import Counter

from slm_common.db import tenant_tx

_log = structlog.get_logger(__name__)
USAGE_DROPPED = Counter("slm_gateway_usage_dropped_total", "Usage rows dropped", ["reason"])

_INSERT_SQL = """
INSERT INTO usage_logs (org_id, project_id, api_key_id, deployment_id, model_version_id,
                        endpoint_alias, route_label, request_id, status_code, prompt_tokens,
                        completion_tokens, latency_ms, ttft_ms, cached_tokens, created_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
"""
_TOUCH_SQL = """
UPDATE api_keys SET last_used_at = v.ts
FROM unnest($1::uuid[], $2::timestamptz[]) AS v(id, ts)
WHERE api_keys.id = v.id AND (api_keys.last_used_at IS NULL OR api_keys.last_used_at < v.ts - interval '30 seconds')
"""


@dataclass(slots=True)
class UsageRecord:
    org_id: UUID
    project_id: UUID | None
    api_key_id: UUID | None
    deployment_id: UUID | None
    model_version_id: UUID | None
    endpoint_alias: str | None
    route_label: str | None
    request_id: str
    status_code: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int | None = None
    ttft_ms: int | None = None
    cached_tokens: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class UsageWriter:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        batch_size: int = 200,
        flush_interval: float = 1.0,
        queue_size: int = 50_000,
    ) -> None:
        self._pool = pool
        self._batch_size = batch_size
        self._interval = flush_interval
        self._queue: asyncio.Queue[UsageRecord] = asyncio.Queue(maxsize=queue_size)
        self._touched: dict[UUID, datetime] = {}
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="usage-writer")

    def record(self, rec: UsageRecord) -> None:
        if rec.api_key_id is not None:
            self._touched[rec.api_key_id] = rec.created_at
        try:
            self._queue.put_nowait(rec)
        except asyncio.QueueFull:
            USAGE_DROPPED.labels("queue_full").inc()

    async def flush(self) -> None:
        """Write everything queued right now (also used on shutdown and in tests)."""
        while not self._queue.empty():
            await self._write(self._drain())
        await self._touch()

    def _drain(self) -> list[UsageRecord]:
        batch: list[UsageRecord] = []
        while len(batch) < self._batch_size and not self._queue.empty():
            batch.append(self._queue.get_nowait())
        return batch

    async def _run(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self._interval)
            try:
                await self.flush()
            except Exception:
                _log.exception("usage_flush_failed")

    async def _write(self, batch: list[UsageRecord]) -> None:
        if not batch:
            return
        rows = [astuple(r) for r in batch]
        for attempt in range(3):
            try:
                async with tenant_tx(self._pool, None, bypass=True) as conn:
                    await conn.executemany(_INSERT_SQL, rows)
                return
            except Exception as exc:
                _log.warning("usage_insert_failed", attempt=attempt + 1, error=str(exc))
                await asyncio.sleep(0.2 * (attempt + 1))
        USAGE_DROPPED.labels("db_error").inc(len(batch))

    async def _touch(self) -> None:
        if not self._touched:
            return
        touched, self._touched = self._touched, {}
        try:
            async with tenant_tx(self._pool, None, bypass=True) as conn:
                await conn.execute(_TOUCH_SQL, list(touched), list(touched.values()))
        except Exception as exc:
            _log.warning("last_used_update_failed", error=str(exc))

    async def aclose(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.flush()
