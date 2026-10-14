"""API-key authentication: sha256 lookup, shared cache, revoke / expiry / rotation-grace checks."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from slm_common.db import tenant_tx
from slm_common.errors import ApiError
from slm_common.security import API_KEY_CACHE_PREFIX, hash_api_key, looks_like_api_key
from slm_gateway.store import Store

_NEGATIVE = "null"

_LOOKUP_SQL = """
SELECT k.id, k.org_id, k.project_id, k.scope::text AS scope, k.rate_limit_rpm, k.expires_at,
       k.grace_until, k.revoked_at, q.rpm_limit, q.tpm_limit, q.monthly_token_limit
FROM api_keys k
JOIN organizations o ON o.id = k.org_id AND o.deleted_at IS NULL
LEFT JOIN quotas q ON q.org_id = k.org_id
WHERE k.key_hash = $1
"""


@dataclass(frozen=True, slots=True)
class KeyInfo:
    key_id: UUID
    org_id: UUID
    project_id: UUID | None
    scope: str
    rate_limit_rpm: int | None
    rpm_limit: int  # org quota
    tpm_limit: int
    monthly_token_limit: int | None
    expires_at: datetime | None
    grace_until: datetime | None
    revoked_at: datetime | None

    def rejection(self, now: datetime | None = None) -> ApiError | None:
        """Why this key must not be used right now, or None when it is valid."""
        now = now or datetime.now(UTC)
        if self.revoked_at is not None:
            return _auth_error("api_key_revoked", "This API key has been revoked.")
        if self.expires_at is not None and self.expires_at <= now:
            return _auth_error("api_key_expired", "This API key has expired.")
        if self.grace_until is not None and self.grace_until <= now:
            return _auth_error(
                "api_key_expired",
                "This API key was rotated and its grace period has ended; use the new key.",
            )
        return None

    def to_json(self) -> str:
        data = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in asdict(self).items()}
        data["key_id"], data["org_id"] = str(self.key_id), str(self.org_id)
        data["project_id"] = str(self.project_id) if self.project_id else None
        return json.dumps(data)

    @classmethod
    def from_json(cls, raw: str) -> KeyInfo:
        d = json.loads(raw)
        for field in ("expires_at", "grace_until", "revoked_at"):
            d[field] = datetime.fromisoformat(d[field]) if d[field] else None
        d["key_id"], d["org_id"] = UUID(d["key_id"]), UUID(d["org_id"])
        d["project_id"] = UUID(d["project_id"]) if d["project_id"] else None
        return cls(**d)


def _auth_error(code: str, message: str) -> ApiError:
    return ApiError(401, code, message, headers={"WWW-Authenticate": "Bearer"})


class KeyAuthenticator:
    def __init__(
        self, pool: asyncpg.Pool, store: Store, *, ttl: float = 60.0, negative_ttl: float = 5.0
    ) -> None:
        self._pool = pool
        self._store = store
        self._ttl = ttl
        self._negative_ttl = negative_ttl

    async def authenticate(self, authorization: str | None) -> KeyInfo:
        scheme, _, token = (authorization or "").partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            raise _auth_error(
                "missing_api_key",
                "You must provide an API key as 'Authorization: Bearer slm_...'.",
            )
        if not looks_like_api_key(token):  # malformed keys never reach the cache or the DB
            raise _auth_error("invalid_api_key", "Incorrect API key provided.")
        digest = hash_api_key(token)
        info = await self._load(digest)
        if info is None:
            raise _auth_error("invalid_api_key", "Incorrect API key provided.")
        if (problem := info.rejection()) is not None:
            raise problem
        return info

    async def _load(self, digest: bytes) -> KeyInfo | None:
        cache_key = API_KEY_CACHE_PREFIX + digest.hex()
        cached = await self._store.get(cache_key)
        if cached == _NEGATIVE:
            return None
        if cached:
            return KeyInfo.from_json(cached)
        async with tenant_tx(self._pool, None, bypass=True) as conn:
            row = await conn.fetchrow(_LOOKUP_SQL, digest)
        if row is None:
            await self._store.set(cache_key, _NEGATIVE, self._negative_ttl)
            return None
        info = KeyInfo(
            key_id=row["id"],
            org_id=row["org_id"],
            project_id=row["project_id"],
            scope=row["scope"],
            rate_limit_rpm=row["rate_limit_rpm"],
            rpm_limit=row["rpm_limit"] if row["rpm_limit"] is not None else 600,
            tpm_limit=row["tpm_limit"] if row["tpm_limit"] is not None else 200_000,
            monthly_token_limit=row["monthly_token_limit"],
            expires_at=row["expires_at"],
            grace_until=row["grace_until"],
            revoked_at=row["revoked_at"],
        )
        await self._store.set(cache_key, info.to_json(), self._ttl)
        return info
