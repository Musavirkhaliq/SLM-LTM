from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

DEPLOYMENT_COLS = """id, org_id, project_id, name, model_version_id, kind::text AS kind,
    target::text AS target, strategy::text AS strategy, status::text AS status,
    replicas_min, replicas_max, gpu_count, vllm_args, internal_url, previous_deployment_id,
    created_by, created_at, updated_at"""

ENDPOINT_COLS = "id, org_id, alias, kind::text AS kind, enabled, created_at"

_ROUTE_SELECT = """SELECT deployment_id, weight, label FROM endpoint_routes
                    WHERE endpoint_id = $1 ORDER BY weight DESC, deployment_id"""


async def list_deployments(
    conn: asyncpg.Connection,
    org_id: UUID,
    *,
    limit: int,
    after: tuple[datetime, UUID] | None,
    status: str | None,
) -> list[asyncpg.Record]:
    args: list[Any] = [org_id]
    where = ["org_id = $1"]

    def bind(value: Any) -> str:
        args.append(value)
        return f"${len(args)}"

    if status:
        where.append(f"status = {bind(status)}::deploy_status")
    if after:
        where.append(f"(created_at, id) < ({bind(after[0])}, {bind(after[1])})")
    limit_ref = bind(limit)
    return await conn.fetch(
        f"SELECT {DEPLOYMENT_COLS} FROM deployments WHERE {' AND '.join(where)} "
        f"ORDER BY created_at DESC, id DESC LIMIT {limit_ref}",
        *args,
    )


async def get_deployment(
    conn: asyncpg.Connection, deployment_id: UUID, *, lock: bool = False
) -> asyncpg.Record | None:
    if lock:
        locked = await conn.fetchval(
            "SELECT id FROM deployments WHERE id = $1 FOR UPDATE", deployment_id
        )
        if locked is None:
            return None
    return await conn.fetchrow(
        f"SELECT {DEPLOYMENT_COLS} FROM deployments WHERE id = $1", deployment_id
    )


async def insert_deployment(
    conn: asyncpg.Connection,
    *,
    org_id: UUID,
    project_id: UUID | None,
    name: str,
    model_version_id: UUID,
    kind: str,
    target: str,
    strategy: str,
    replicas_min: int,
    replicas_max: int,
    gpu_count: int,
    vllm_args: dict,
    internal_url: str | None,
    created_by: UUID | None,
) -> UUID:
    return await conn.fetchval(
        """INSERT INTO deployments (org_id, project_id, name, model_version_id, kind, target,
             strategy, replicas_min, replicas_max, gpu_count, vllm_args, internal_url, created_by)
           VALUES ($1, $2, $3, $4, $5::endpoint_kind, $6::deploy_target, $7::deploy_strategy,
                   $8, $9, $10, $11, $12, $13)
           RETURNING id""",
        org_id,
        project_id,
        name,
        model_version_id,
        kind,
        target,
        strategy,
        replicas_min,
        replicas_max,
        gpu_count,
        vllm_args,
        internal_url,
        created_by,
    )


async def update_deployment(
    conn: asyncpg.Connection, deployment_id: UUID, fields: dict[str, Any]
) -> None:
    casts = {"status": "::deploy_status"}
    sets = ", ".join(f"{col} = ${i}{casts.get(col, '')}" for i, col in enumerate(fields, start=2))
    await conn.execute(
        f"UPDATE deployments SET {sets}, updated_at = now() WHERE id = $1",
        deployment_id,
        *fields.values(),
    )


async def delete_deployment(conn: asyncpg.Connection, deployment_id: UUID) -> None:
    await conn.execute("DELETE FROM deployments WHERE id = $1", deployment_id)


async def model_version_exists(conn: asyncpg.Connection, version_id: UUID) -> bool:
    return bool(await conn.fetchval("SELECT 1 FROM model_versions WHERE id = $1", version_id))


async def insert_event(
    conn: asyncpg.Connection,
    *,
    org_id: UUID,
    deployment_id: UUID,
    event: str,
    detail: dict[str, Any] | None = None,
    actor_id: UUID | None = None,
) -> None:
    await conn.execute(
        """INSERT INTO deployment_events (org_id, deployment_id, event, detail, actor_id)
           VALUES ($1, $2, $3, $4, $5)""",
        org_id,
        deployment_id,
        event,
        detail or {},
        actor_id,
    )


# --------------------------------------------------------------------------------- endpoints

async def list_endpoints(
    conn: asyncpg.Connection, org_id: UUID, *, limit: int, after: tuple[datetime, UUID] | None
) -> list[asyncpg.Record]:
    args: list[Any] = [org_id]
    where = ["org_id = $1"]

    def bind(value: Any) -> str:
        args.append(value)
        return f"${len(args)}"

    if after:
        where.append(f"(created_at, id) < ({bind(after[0])}, {bind(after[1])})")
    limit_ref = bind(limit)
    return await conn.fetch(
        f"SELECT {ENDPOINT_COLS} FROM endpoints WHERE {' AND '.join(where)} "
        f"ORDER BY created_at DESC, id DESC LIMIT {limit_ref}",
        *args,
    )


async def get_endpoint(
    conn: asyncpg.Connection, endpoint_id: UUID, *, lock: bool = False
) -> asyncpg.Record | None:
    if lock:
        locked = await conn.fetchval(
            "SELECT id FROM endpoints WHERE id = $1 FOR UPDATE", endpoint_id
        )
        if locked is None:
            return None
    return await conn.fetchrow(f"SELECT {ENDPOINT_COLS} FROM endpoints WHERE id = $1", endpoint_id)


async def insert_endpoint(
    conn: asyncpg.Connection, *, org_id: UUID, alias: str, kind: str, enabled: bool
) -> UUID:
    return await conn.fetchval(
        """INSERT INTO endpoints (org_id, alias, kind, enabled)
           VALUES ($1, $2, $3::endpoint_kind, $4) RETURNING id""",
        org_id,
        alias,
        kind,
        enabled,
    )


async def list_routes(conn: asyncpg.Connection, endpoint_id: UUID) -> list[asyncpg.Record]:
    return await conn.fetch(_ROUTE_SELECT, endpoint_id)


async def replace_routes(
    conn: asyncpg.Connection, *, endpoint_id: UUID, org_id: UUID, routes: list[dict[str, Any]]
) -> None:
    await conn.execute("DELETE FROM endpoint_routes WHERE endpoint_id = $1", endpoint_id)
    await conn.executemany(
        """INSERT INTO endpoint_routes (endpoint_id, deployment_id, org_id, weight, label)
           VALUES ($1, $2, $3, $4, $5)""",
        [(endpoint_id, r["deployment_id"], org_id, r["weight"], r["label"]) for r in routes],
    )


async def deployments_exist(conn: asyncpg.Connection, org_id: UUID, ids: list[UUID]) -> set[UUID]:
    """Subset of `ids` that are real deployments belonging to `org_id`."""
    rows = await conn.fetch(
        "SELECT id FROM deployments WHERE org_id = $1 AND id = ANY($2::uuid[])", org_id, ids
    )
    return {r["id"] for r in rows}


async def repoint_routes(
    conn: asyncpg.Connection, *, from_deployment_id: UUID, to_deployment_id: UUID
) -> None:
    """Used by rollback: any endpoint route serving the old deployment now serves the new one."""
    await conn.execute(
        """UPDATE endpoint_routes SET deployment_id = $2
           WHERE deployment_id = $1
             AND NOT EXISTS (
               SELECT 1 FROM endpoint_routes r2
               WHERE r2.endpoint_id = endpoint_routes.endpoint_id AND r2.deployment_id = $2
             )""",
        from_deployment_id,
        to_deployment_id,
    )
    # an endpoint that already routes to both: drop the stale (old) row instead
    await conn.execute("DELETE FROM endpoint_routes WHERE deployment_id = $1", from_deployment_id)
