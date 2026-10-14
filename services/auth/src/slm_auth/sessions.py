"""Access/refresh token issuance with rotation and reuse detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
from fastapi import Request

from slm_auth import repo
from slm_auth.schemas import TokenResponse
from slm_auth.settings import AuthSettings
from slm_common.audit import audit
from slm_common.db import tenant_tx
from slm_common.errors import ApiError
from slm_common.security import create_access_token, generate_refresh_token, hash_token

_NO_ORG = "0" * 32


def new_refresh_token(org_id: UUID | None) -> tuple[str, bytes]:
    """Opaque token that carries the active org (the schema has no column for it).

    The org is part of the hashed secret, so it cannot be altered without invalidating the token.
    """
    raw, _ = generate_refresh_token()
    token = f"slr1.{org_id.hex if org_id else _NO_ORG}.{raw}"
    return token, hash_token(token)


def org_from_refresh_token(token: str) -> UUID | None:
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "slr1" or parts[1] == _NO_ORG:
        return None
    try:
        return UUID(parts[1])
    except ValueError:
        return None


async def resolve_role(conn: asyncpg.Connection, user_id: UUID, org_id: UUID | None) -> str:
    user = await repo.get_user(conn, user_id)
    if user is None or not user["is_active"]:
        raise ApiError(403, "account_disabled", "Account is disabled")
    if user["is_platform_admin"]:
        return "platform_admin"
    if org_id is None:
        return "viewer"
    membership = await repo.get_membership(conn, org_id, user_id)
    if membership is None:
        raise ApiError.forbidden("You are not a member of that organization", "not_a_member")
    return membership["role"]


async def issue_tokens(
    conn: asyncpg.Connection,
    settings: AuthSettings,
    *,
    user_id: UUID,
    org_id: UUID | None,
    family_id: UUID | None = None,
) -> TokenResponse:
    role = await resolve_role(conn, user_id, org_id)
    refresh, refresh_hash = new_refresh_token(org_id)
    await conn.execute(
        "INSERT INTO refresh_tokens (user_id, token_hash, family_id, expires_at) "
        "VALUES ($1, $2, $3, $4)",
        user_id,
        refresh_hash,
        family_id or uuid4(),
        datetime.now(UTC) + timedelta(seconds=settings.jwt_refresh_ttl_seconds),
    )
    access = create_access_token(user_id=user_id, org_id=org_id, role=role, settings=settings)
    return TokenResponse(
        access_token=access, refresh_token=refresh, expires_in=settings.jwt_access_ttl_seconds
    )


async def default_org(conn: asyncpg.Connection, user_id: UUID) -> UUID | None:
    memberships = await repo.memberships_for_user(conn, user_id)
    return memberships[0]["org_id"] if memberships else None


async def rotate_refresh_token(
    pool: asyncpg.Pool, settings: AuthSettings, token: str, request: Request
) -> TokenResponse:
    """Single-use refresh: the presented token is revoked and a successor issued in the same
    family. Presenting an already-revoked token means it leaked, so the whole family dies."""
    invalid = ApiError(401, "invalid_refresh_token", "Refresh token is invalid or expired")
    org_id = org_from_refresh_token(token)
    reused = False
    async with tenant_tx(pool, None, bypass=True) as conn:
        row = await conn.fetchrow(
            "SELECT id, user_id, family_id, expires_at, revoked_at FROM refresh_tokens "
            "WHERE token_hash = $1 FOR UPDATE",
            hash_token(token),
        )
        if row is None:
            raise invalid
        if row["revoked_at"] is not None:
            await conn.execute(
                "UPDATE refresh_tokens SET revoked_at = now() "
                "WHERE family_id = $1 AND revoked_at IS NULL",
                row["family_id"],
            )
            await audit(
                conn,
                org_id=org_id,
                actor=row["user_id"],
                action="auth.refresh_reuse_detected",
                resource="refresh_token_family",
                resource_id=row["family_id"],
                request=request,
            )
            reused = True
        elif row["expires_at"] <= datetime.now(UTC):
            raise invalid
        else:
            if org_id is not None and await repo.get_membership(conn, org_id, row["user_id"]) is None:
                org_id = await default_org(conn, row["user_id"])  # membership removed meanwhile
            await conn.execute(
                "UPDATE refresh_tokens SET revoked_at = now() WHERE id = $1", row["id"]
            )
            tokens = await issue_tokens(
                conn, settings, user_id=row["user_id"], org_id=org_id, family_id=row["family_id"]
            )
            await audit(
                conn,
                org_id=org_id,
                actor=row["user_id"],
                action="auth.refresh",
                resource="refresh_token_family",
                resource_id=row["family_id"],
                request=request,
            )
    if reused:  # raised after the commit so the family revocation persists
        raise ApiError(
            401, "refresh_token_reused", "Refresh token reuse detected; all sessions were revoked"
        )
    return tokens


async def revoke_family_of(pool: asyncpg.Pool, token: str) -> UUID | None:
    async with tenant_tx(pool, None, bypass=True) as conn:
        row = await conn.fetchrow(
            "SELECT user_id, family_id FROM refresh_tokens WHERE token_hash = $1", hash_token(token)
        )
        if row is None:
            return None
        await conn.execute(
            "UPDATE refresh_tokens SET revoked_at = now() WHERE family_id = $1 AND revoked_at IS NULL",
            row["family_id"],
        )
        return row["user_id"]
