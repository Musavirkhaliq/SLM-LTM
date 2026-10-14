from __future__ import annotations

import hashlib
import ipaddress
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import Request

from slm_common.auth import Principal


def compute_row_hash(prev_hash: bytes | None, record: dict[str, Any]) -> bytes:
    """sha256(prev_hash || canonical JSON of the record) - links each row to its predecessor."""
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev_hash or b"") + canonical.encode()).digest()


def _resolve_actor(actor: Principal | UUID | str | None) -> tuple[UUID | None, str]:
    if actor is None or actor == "system":
        return None, "system"
    if isinstance(actor, Principal):
        return actor.actor_id, actor.kind
    if isinstance(actor, UUID):
        return actor, "user"
    return UUID(actor), "user"


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    candidate = forwarded.split(",")[0].strip() if forwarded else None
    if not candidate and request.client:
        candidate = request.client.host
    try:
        return str(ipaddress.ip_address(candidate)) if candidate else None
    except ValueError:
        return None


async def audit(
    conn: asyncpg.Connection,
    *,
    org_id: UUID | None,
    actor: Principal | UUID | str | None,
    action: str,
    resource: str,
    resource_id: UUID | str | int | None = None,
    detail: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    """Append a hash-chained audit row on the caller's transaction (so it commits atomically
    with the change it describes)."""
    actor_id, actor_type = _resolve_actor(actor)
    ip = _client_ip(request)
    request_id = getattr(request.state, "request_id", None) if request is not None else None
    created_at = datetime.now(UTC)
    detail = detail or {}
    resource_id_s = str(resource_id) if resource_id is not None else None

    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", str(org_id))
    prev_hash = await conn.fetchval(
        "SELECT row_hash FROM audit_logs WHERE org_id IS NOT DISTINCT FROM $1 "
        "ORDER BY id DESC LIMIT 1",
        org_id,
    )
    row_hash = compute_row_hash(
        prev_hash,
        {
            "org_id": org_id,
            "actor_id": actor_id,
            "actor_type": actor_type,
            "action": action,
            "resource": resource,
            "resource_id": resource_id_s,
            "detail": detail,
            "created_at": created_at.isoformat(),
        },
    )
    await conn.execute(
        """
        INSERT INTO audit_logs (org_id, actor_id, actor_type, action, resource, resource_id,
                                ip, request_id, detail, prev_hash, row_hash, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7::text::inet, $8, $9, $10, $11, $12)
        """,
        org_id,
        actor_id,
        actor_type,
        action,
        resource,
        resource_id_s,
        ip,
        request_id,
        detail,
        prev_hash,
        row_hash,
        created_at,
    )
