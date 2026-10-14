"""SQL for the auth service. Functions take a connection already inside `tenant_tx`.

`citext` columns are compared through `$n::text::citext` and read back as `::text` so the
queries behave identically whether or not the driver knows the extension type.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

USER_COLS = "id, email::text AS email, display_name, is_platform_admin, is_active, password_hash, created_at"


def slugify(text: str, fallback: str = "org") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].strip("-")
    return slug or fallback


# ------------------------------------------------------------------ users --
async def get_user_by_email(conn: asyncpg.Connection, email: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"SELECT {USER_COLS} FROM users WHERE email = $1::text::citext AND deleted_at IS NULL",
        email.lower(),
    )


async def get_user(conn: asyncpg.Connection, user_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"SELECT {USER_COLS} FROM users WHERE id = $1 AND deleted_at IS NULL", user_id
    )


async def create_user(
    conn: asyncpg.Connection, *, email: str, display_name: str, password_hash: str | None
) -> asyncpg.Record:
    return await conn.fetchrow(
        f"""INSERT INTO users (email, display_name, password_hash)
            VALUES ($1::text, $2, $3) RETURNING {USER_COLS}""",
        email.lower(),
        display_name,
        password_hash,
    )


async def touch_login(conn: asyncpg.Connection, user_id: UUID) -> None:
    await conn.execute("UPDATE users SET last_login_at = now() WHERE id = $1", user_id)


async def find_identity(
    conn: asyncpg.Connection, provider: str, subject: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT user_id FROM user_identities WHERE provider = $1 AND subject = $2",
        provider,
        subject,
    )


async def link_identity(
    conn: asyncpg.Connection, user_id: UUID, provider: str, subject: str
) -> None:
    await conn.execute(
        "INSERT INTO user_identities (user_id, provider, subject) VALUES ($1, $2, $3) "
        "ON CONFLICT (provider, subject) DO NOTHING",
        user_id,
        provider,
        subject,
    )


# ------------------------------------------------------------ orgs/members --
async def create_org(conn: asyncpg.Connection, name: str) -> asyncpg.Record:
    base = slugify(name)
    slug = base
    while await conn.fetchval("SELECT 1 FROM organizations WHERE slug = $1::text::citext", slug):
        slug = f"{base[:43]}-{secrets.token_hex(2)}"
    return await conn.fetchrow(
        "INSERT INTO organizations (slug, name) VALUES ($1::text, $2) "
        "RETURNING id, slug::text AS slug, name, plan",
        slug,
        name,
    )


async def ensure_quota(conn: asyncpg.Connection, org_id: UUID) -> None:
    await conn.execute(
        "INSERT INTO quotas (org_id) VALUES ($1) ON CONFLICT (org_id) DO NOTHING", org_id
    )


async def memberships_for_user(conn: asyncpg.Connection, user_id: UUID) -> list[asyncpg.Record]:
    return await conn.fetch(
        """SELECT m.org_id, o.name AS org_name, o.slug::text AS org_slug, m.role::text AS role
           FROM org_memberships m JOIN organizations o ON o.id = m.org_id
           WHERE m.user_id = $1 AND o.deleted_at IS NULL
           ORDER BY m.created_at, m.org_id""",
        user_id,
    )


async def get_membership(
    conn: asyncpg.Connection, org_id: UUID, user_id: UUID
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """SELECT m.org_id, m.user_id, m.role::text AS role FROM org_memberships m
           JOIN organizations o ON o.id = m.org_id AND o.deleted_at IS NULL
           WHERE m.org_id = $1 AND m.user_id = $2""",
        org_id,
        user_id,
    )


async def org_exists(conn: asyncpg.Connection, org_id: UUID) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT 1 FROM organizations WHERE id = $1 AND deleted_at IS NULL", org_id
        )
    )


async def add_member(conn: asyncpg.Connection, org_id: UUID, user_id: UUID, role: str) -> None:
    await conn.execute(
        "INSERT INTO org_memberships (org_id, user_id, role) VALUES ($1, $2, $3::org_role)",
        org_id,
        user_id,
        role,
    )


async def count_admins(conn: asyncpg.Connection, org_id: UUID) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM org_memberships WHERE org_id = $1 AND role = 'org_admin'", org_id
    )


async def list_members(
    conn: asyncpg.Connection,
    org_id: UUID,
    limit: int,
    after: tuple[datetime, UUID] | None,
) -> list[asyncpg.Record]:
    args: list[Any] = [org_id, limit]
    keyset = ""
    if after:
        keyset = "AND (m.created_at, m.user_id) > ($3, $4)"
        args += [after[0], after[1]]
    return await conn.fetch(
        f"""SELECT m.user_id, u.email::text AS email, u.display_name, m.role::text AS role,
                   m.created_at
            FROM org_memberships m JOIN users u ON u.id = m.user_id
            WHERE m.org_id = $1 {keyset}
            ORDER BY m.created_at, m.user_id LIMIT $2""",
        *args,
    )


async def get_member(
    conn: asyncpg.Connection, org_id: UUID, user_id: UUID
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """SELECT m.user_id, u.email::text AS email, u.display_name, m.role::text AS role,
                  m.created_at
           FROM org_memberships m JOIN users u ON u.id = m.user_id
           WHERE m.org_id = $1 AND m.user_id = $2""",
        org_id,
        user_id,
    )


async def set_member_role(
    conn: asyncpg.Connection, org_id: UUID, user_id: UUID, role: str
) -> None:
    await conn.execute(
        "UPDATE org_memberships SET role = $3::org_role WHERE org_id = $1 AND user_id = $2",
        org_id,
        user_id,
        role,
    )


async def remove_member(conn: asyncpg.Connection, org_id: UUID, user_id: UUID) -> None:
    await conn.execute(
        "DELETE FROM org_memberships WHERE org_id = $1 AND user_id = $2", org_id, user_id
    )


# --------------------------------------------------------------- projects --
PROJECT_COLS = (
    "id, org_id, slug::text AS slug, name, description, created_by, created_at, updated_at"
)


async def list_projects(
    conn: asyncpg.Connection,
    org_id: UUID,
    limit: int,
    after: tuple[datetime, UUID] | None,
) -> list[asyncpg.Record]:
    args: list[Any] = [org_id, limit]
    keyset = ""
    if after:
        keyset = "AND (created_at, id) < ($3, $4)"
        args += [after[0], after[1]]
    return await conn.fetch(
        f"""SELECT {PROJECT_COLS} FROM projects
            WHERE org_id = $1 AND deleted_at IS NULL {keyset}
            ORDER BY created_at DESC, id DESC LIMIT $2""",
        *args,
    )


async def get_project(
    conn: asyncpg.Connection, org_id: UUID, project_id: UUID
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"SELECT {PROJECT_COLS} FROM projects WHERE org_id = $1 AND id = $2 AND deleted_at IS NULL",
        org_id,
        project_id,
    )


async def insert_project(
    conn: asyncpg.Connection,
    org_id: UUID,
    *,
    slug: str,
    name: str,
    description: str | None,
    created_by: UUID | None,
) -> asyncpg.Record:
    return await conn.fetchrow(
        f"""INSERT INTO projects (org_id, slug, name, description, created_by)
            VALUES ($1, $2::text, $3, $4, $5) RETURNING {PROJECT_COLS}""",
        org_id,
        slug,
        name,
        description,
        created_by,
    )


async def update_project(
    conn: asyncpg.Connection,
    org_id: UUID,
    project_id: UUID,
    fields: dict[str, Any],
) -> asyncpg.Record | None:
    sets = ", ".join(f"{col} = ${i}" for i, col in enumerate(fields, start=3))
    return await conn.fetchrow(
        f"""UPDATE projects SET {sets} WHERE org_id = $1 AND id = $2 AND deleted_at IS NULL
            RETURNING {PROJECT_COLS}""",
        org_id,
        project_id,
        *fields.values(),
    )


async def soft_delete_project(conn: asyncpg.Connection, org_id: UUID, project_id: UUID) -> bool:
    # Free the slug for reuse and revoke project-scoped keys so they stop authenticating.
    row = await conn.fetchrow(
        """UPDATE projects SET deleted_at = now(),
                  slug = (slug::text || '~' || substr(id::text, 1, 8))::citext
           WHERE org_id = $1 AND id = $2 AND deleted_at IS NULL RETURNING id""",
        org_id,
        project_id,
    )
    return row is not None


async def revoke_project_keys(
    conn: asyncpg.Connection, org_id: UUID, project_id: UUID
) -> list[bytes]:
    rows = await conn.fetch(
        """UPDATE api_keys SET revoked_at = now()
           WHERE org_id = $1 AND project_id = $2 AND revoked_at IS NULL RETURNING key_hash""",
        org_id,
        project_id,
    )
    return [r["key_hash"] for r in rows]


# ---------------------------------------------------------------- api keys --
KEY_COLS = """id, org_id, project_id, scope::text AS scope, name, prefix, rate_limit_rpm,
              expires_at, rotated_from, grace_until, last_used_at, revoked_at, created_by,
              created_at, key_hash,
              CASE WHEN revoked_at IS NOT NULL THEN 'revoked'
                   WHEN expires_at IS NOT NULL AND expires_at <= now() THEN 'expired'
                   WHEN grace_until IS NOT NULL AND grace_until <= now() THEN 'expired'
                   WHEN grace_until IS NOT NULL THEN 'grace'
                   ELSE 'active' END AS status"""


async def insert_api_key(
    conn: asyncpg.Connection,
    *,
    org_id: UUID,
    project_id: UUID | None,
    scope: str,
    name: str,
    prefix: str,
    key_hash: bytes,
    created_by: UUID | None,
    rate_limit_rpm: int | None,
    expires_at: datetime | None,
    rotated_from: UUID | None = None,
) -> asyncpg.Record:
    return await conn.fetchrow(
        f"""INSERT INTO api_keys (org_id, project_id, scope, name, prefix, key_hash, created_by,
                                  rate_limit_rpm, expires_at, rotated_from)
            VALUES ($1, $2, $3::api_key_scope, $4, $5, $6, $7, $8, $9, $10)
            RETURNING {KEY_COLS}""",
        org_id,
        project_id,
        scope,
        name,
        prefix,
        key_hash,
        created_by,
        rate_limit_rpm,
        expires_at,
        rotated_from,
    )


async def get_api_key(
    conn: asyncpg.Connection, org_id: UUID, key_id: UUID, *, lock: bool = False
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"SELECT {KEY_COLS} FROM api_keys WHERE org_id = $1 AND id = $2"
        + (" FOR UPDATE" if lock else ""),
        org_id,
        key_id,
    )


async def list_api_keys(
    conn: asyncpg.Connection,
    org_id: UUID,
    limit: int,
    after: tuple[datetime, UUID] | None,
    *,
    visible_to: UUID | None,
) -> list[asyncpg.Record]:
    """`visible_to` restricts non-admins to project keys plus keys they created."""
    args: list[Any] = [org_id, limit]
    clauses = ["org_id = $1"]
    if after:
        args += [after[0], after[1]]
        clauses.append(f"(created_at, id) < (${len(args) - 1}, ${len(args)})")
    if visible_to is not None:
        args.append(visible_to)
        clauses.append(f"(scope = 'project' OR created_by = ${len(args)})")
    return await conn.fetch(
        f"SELECT {KEY_COLS} FROM api_keys WHERE {' AND '.join(clauses)} "
        "ORDER BY created_at DESC, id DESC LIMIT $2",
        *args,
    )


async def revoke_api_key(conn: asyncpg.Connection, org_id: UUID, key_id: UUID) -> None:
    await conn.execute(
        "UPDATE api_keys SET revoked_at = now() WHERE org_id = $1 AND id = $2 AND revoked_at IS NULL",
        org_id,
        key_id,
    )


async def set_grace(
    conn: asyncpg.Connection, org_id: UUID, key_id: UUID, grace_seconds: int
) -> datetime:
    return await conn.fetchval(
        """UPDATE api_keys SET grace_until = now() + make_interval(secs => $3)
           WHERE org_id = $1 AND id = $2 RETURNING grace_until""",
        org_id,
        key_id,
        float(grace_seconds),
    )


async def key_usage(
    conn: asyncpg.Connection, org_id: UUID, key_id: UUID, start: datetime, end: datetime
) -> tuple[asyncpg.Record, list[asyncpg.Record]]:
    totals = await conn.fetchrow(
        """SELECT count(*)::int AS requests,
                  COALESCE(sum(prompt_tokens), 0)::bigint AS prompt_tokens,
                  COALESCE(sum(completion_tokens), 0)::bigint AS completion_tokens,
                  (count(*) FILTER (WHERE status_code >= 400))::int AS errors,
                  avg(latency_ms)::float8 AS avg_latency_ms,
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)::float8 AS p95_latency_ms
           FROM usage_logs
           WHERE org_id = $1 AND api_key_id = $2 AND created_at >= $3 AND created_at < $4""",
        org_id,
        key_id,
        start,
        end,
    )
    daily = await conn.fetch(
        """SELECT date_trunc('day', created_at) AS day, count(*)::int AS requests,
                  COALESCE(sum(prompt_tokens), 0)::bigint AS prompt_tokens,
                  COALESCE(sum(completion_tokens), 0)::bigint AS completion_tokens,
                  (count(*) FILTER (WHERE status_code >= 400))::int AS errors
           FROM usage_logs
           WHERE org_id = $1 AND api_key_id = $2 AND created_at >= $3 AND created_at < $4
           GROUP BY 1 ORDER BY 1""",
        org_id,
        key_id,
        start,
        end,
    )
    return totals, daily
