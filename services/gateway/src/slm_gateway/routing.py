"""Alias -> weighted deployment targets (A/B tests and canaries live in `endpoint_routes`)."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from slm_common.db import tenant_tx
from slm_common.errors import ApiError

_RESOLVE_SQL = """
SELECT e.id AS endpoint_id, e.alias, e.kind::text AS kind, e.enabled, e.org_id,
       r.weight, r.label, d.id AS deployment_id, d.name AS deployment_name,
       d.status::text AS status, d.internal_url, d.model_version_id, d.vllm_args
FROM endpoints e
LEFT JOIN endpoint_routes r ON r.endpoint_id = e.id
LEFT JOIN deployments d ON d.id = r.deployment_id
WHERE e.alias = $2 AND (e.org_id = $1 OR (e.kind = 'shared' AND e.enabled))
ORDER BY (e.org_id = $1) DESC, e.created_at, e.id
"""

_LIST_SQL = """
SELECT e.alias, e.kind::text AS kind, e.org_id, e.created_at
FROM endpoints e
WHERE e.enabled AND (e.org_id = $1 OR e.kind = 'shared')
  AND EXISTS (SELECT 1 FROM endpoint_routes r JOIN deployments d ON d.id = r.deployment_id
              WHERE r.endpoint_id = e.id AND d.status = 'running' AND d.internal_url IS NOT NULL)
ORDER BY (e.org_id = $1) DESC, e.alias, e.created_at
"""


@dataclass(frozen=True, slots=True)
class Target:
    endpoint_id: UUID
    alias: str
    deployment_id: UUID
    deployment_name: str
    model_version_id: UUID
    base_url: str
    served_name: str  # the model name the vLLM instance answers to
    weight: int
    label: str


@dataclass(frozen=True, slots=True)
class Resolved:
    alias: str
    targets: tuple[Target, ...]


class Router:
    def __init__(
        self, pool: asyncpg.Pool, *, ttl: float = 5.0, rng: random.Random | None = None
    ) -> None:
        self._pool = pool
        self._ttl = ttl
        self._rng = rng or random.Random()  # noqa: S311 - load spreading, not security
        self._cache: dict[tuple[UUID, str], tuple[float, Resolved | ApiError]] = {}

    async def resolve(self, org_id: UUID, alias: str) -> Resolved:
        cache_key = (org_id, alias)
        hit = self._cache.get(cache_key)
        if hit and hit[0] > time.monotonic():
            if isinstance(hit[1], ApiError):
                raise hit[1]
            return hit[1]
        try:
            resolved: Resolved | ApiError = await self._load(org_id, alias)
        except ApiError as exc:
            resolved = exc
        if len(self._cache) > 5000:
            self._cache.clear()
        self._cache[cache_key] = (time.monotonic() + self._ttl, resolved)
        if isinstance(resolved, ApiError):
            raise resolved
        return resolved

    def invalidate(self) -> None:
        self._cache.clear()

    async def _load(self, org_id: UUID, alias: str) -> Resolved:
        async with tenant_tx(self._pool, None, bypass=True) as conn:
            rows = await conn.fetch(_RESOLVE_SQL, org_id, alias)
        if not rows or not rows[0]["enabled"]:
            raise ApiError(
                404,
                "model_not_found",
                f"The model '{alias}' does not exist or you do not have access to it.",
                param="model",
            )
        first = rows[0]["endpoint_id"]
        targets = []
        for r in rows:
            if r["endpoint_id"] != first or r["deployment_id"] is None:
                continue
            if r["status"] != "running" or not r["internal_url"] or r["weight"] <= 0:
                continue
            args = r["vllm_args"] or {}
            targets.append(
                Target(
                    endpoint_id=first,
                    alias=alias,
                    deployment_id=r["deployment_id"],
                    deployment_name=r["deployment_name"],
                    model_version_id=r["model_version_id"],
                    base_url=_normalise_base(r["internal_url"]),
                    served_name=args.get("served_model_name") or r["deployment_name"],
                    weight=r["weight"],
                    label=r["label"] or "default",
                )
            )
        if not targets:
            raise ApiError(
                503,
                "no_healthy_deployment",
                f"No running deployment is currently available for '{alias}'.",
            )
        return Resolved(alias, tuple(targets))

    def pick(self, resolved: Resolved, exclude: frozenset[UUID] = frozenset()) -> Target | None:
        """Weighted random choice, skipping deployments that already failed this request."""
        candidates = [t for t in resolved.targets if t.deployment_id not in exclude]
        if not candidates:
            return None
        return self._rng.choices(candidates, weights=[t.weight for t in candidates], k=1)[0]

    async def list_aliases(self, org_id: UUID) -> list[asyncpg.Record]:
        async with tenant_tx(self._pool, None, bypass=True) as conn:
            rows = await conn.fetch(_LIST_SQL, org_id)
        seen: set[str] = set()
        unique = []
        for r in rows:  # the org's own alias shadows a shared alias of the same name
            if r["alias"] not in seen:
                seen.add(r["alias"])
                unique.append(r)
        return unique


def _normalise_base(url: str) -> str:
    base = url.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base
