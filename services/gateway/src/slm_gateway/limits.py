"""Rate limits (RPM + TPM, sliding window) and monthly token quota."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from slm_common.db import tenant_tx
from slm_common.errors import ApiError
from slm_gateway.keys import KeyInfo
from slm_gateway.store import Store

WINDOW_SECONDS = 60.0
_MONTH_TTL = 40 * 24 * 3600


@dataclass(frozen=True, slots=True)
class RateInfo:
    limit_requests: int
    remaining_requests: int
    reset_requests: float
    limit_tokens: int
    remaining_tokens: int
    reset_tokens: float

    def headers(self) -> dict[str, str]:
        return {
            "x-ratelimit-limit-requests": str(self.limit_requests),
            "x-ratelimit-remaining-requests": str(max(self.remaining_requests, 0)),
            "x-ratelimit-reset-requests": f"{math.ceil(self.reset_requests)}s",
            "x-ratelimit-limit-tokens": str(self.limit_tokens),
            "x-ratelimit-remaining-tokens": str(max(self.remaining_tokens, 0)),
            "x-ratelimit-reset-tokens": f"{math.ceil(self.reset_tokens)}s",
        }


def _month_key(org_id: UUID, now: datetime) -> str:
    return f"slm:month_tokens:{org_id}:{now:%Y%m}"


class Limiter:
    def __init__(self, store: Store, pool: asyncpg.Pool) -> None:
        self._store = store
        self._pool = pool

    async def admit(self, key: KeyInfo, estimated_tokens: int) -> RateInfo:
        """Enforce the monthly quota, the org+key request rate and the org token rate.

        Requests are counted atomically at admission. Tokens are checked against an estimate
        here and charged with the real count afterwards (`charge`), so a burst may overshoot
        the TPM limit by what is in flight, never by more.
        """
        await self._check_monthly(key)

        # The per-key limit (if any) is checked first so rejected requests do not eat org budget.
        checks: list[tuple[str, int]] = []
        if key.rate_limit_rpm is not None:
            checks.append((f"slm:rpm:key:{key.key_id}", key.rate_limit_rpm))
        checks.append((f"slm:rpm:org:{key.org_id}", key.rpm_limit))
        req_limit, req_remaining = key.rpm_limit, key.rpm_limit
        for redis_key, limit in checks:
            hit = await self._store.window_hit(redis_key, limit=limit, window=WINDOW_SECONDS)
            if not hit.allowed:
                raise _too_many("requests", limit, 0, hit.retry_after)
            if limit - hit.used <= req_remaining:
                req_limit, req_remaining = limit, limit - hit.used

        tpm_key = f"slm:tpm:org:{key.org_id}"
        used, reset = await self._store.window_used(tpm_key, window=WINDOW_SECONDS)
        if used + estimated_tokens > key.tpm_limit:
            raise _too_many("tokens", key.tpm_limit, key.tpm_limit - used, reset or 1.0)
        return RateInfo(
            limit_requests=req_limit,
            remaining_requests=req_remaining,
            reset_requests=WINDOW_SECONDS,
            limit_tokens=key.tpm_limit,
            remaining_tokens=key.tpm_limit - used - estimated_tokens,
            reset_tokens=reset,
        )

    async def charge(self, key: KeyInfo, total_tokens: int) -> None:
        if total_tokens <= 0:
            return
        await self._store.window_add(
            f"slm:tpm:org:{key.org_id}", cost=total_tokens, window=WINDOW_SECONDS
        )
        await self._store.counter_add(
            _month_key(key.org_id, datetime.now(UTC)), total_tokens, _MONTH_TTL
        )

    async def _check_monthly(self, key: KeyInfo) -> None:
        if key.monthly_token_limit is None:
            return
        used = await self.month_tokens(key.org_id)
        if used >= key.monthly_token_limit:
            raise ApiError(
                429,
                "insufficient_quota",
                "You exceeded your monthly token quota. Raise the quota or wait for the next period.",
            )

    async def month_tokens(self, org_id: UUID) -> int:
        now = datetime.now(UTC)
        redis_key = _month_key(org_id, now)
        current = await self._store.counter_get(redis_key)
        if current is not None:
            return current
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        async with tenant_tx(self._pool, org_id) as conn:
            seeded = await conn.fetchval(
                "SELECT COALESCE(sum(prompt_tokens + completion_tokens), 0)::bigint "
                "FROM usage_logs WHERE org_id = $1 AND created_at >= $2",
                org_id,
                start,
            )
        await self._store.counter_seed(redis_key, int(seeded), _MONTH_TTL)
        return await self._store.counter_get(redis_key) or int(seeded)


def _too_many(kind: str, limit: int, remaining: int, retry_after: float) -> ApiError:
    seconds = max(math.ceil(retry_after), 1)
    return ApiError(
        429,
        "rate_limit_exceeded",
        f"Rate limit reached for {kind}: limit {limit} per minute. Retry in {seconds}s.",
        headers={
            "retry-after": str(seconds),
            f"x-ratelimit-limit-{kind}": str(limit),
            f"x-ratelimit-remaining-{kind}": str(max(remaining, 0)),
            f"x-ratelimit-reset-{kind}": f"{seconds}s",
        },
    )
