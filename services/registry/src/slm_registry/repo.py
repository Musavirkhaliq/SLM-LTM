from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

MODEL_SELECT = """
SELECT m.id, m.org_id, CASE WHEN m.org_id IS NULL THEN 'global' ELSE 'org' END AS scope,
       m.name, m.family, m.description, m.license, m.source::text AS source, m.hf_repo,
       m.created_by, m.created_at, m.updated_at, m.archived_at,
       (SELECT v.version FROM model_versions v
         WHERE v.model_id = m.id AND v.status = 'active') AS active_version,
       (SELECT count(*)::int FROM model_versions v WHERE v.model_id = m.id) AS version_count
FROM models m
"""

VERSION_COLS = """id, model_id, org_id, version, status::text AS status, format::text AS format,
    param_count, quantization, context_length, artifact_uri, artifact_sha256, base_version_id,
    training_job_id, mlflow_run_id, benchmark_scores, metadata, created_by, created_at"""


async def list_models(
    conn: asyncpg.Connection,
    org_id: UUID,
    *,
    limit: int,
    after: tuple[datetime, UUID] | None,
    scope: str,
    family: str | None,
    source: str | None,
    query: str | None,
    include_archived: bool,
) -> list[asyncpg.Record]:
    args: list[Any] = []
    where: list[str] = []

    def bind(value: Any) -> str:
        args.append(value)
        return f"${len(args)}"

    if scope == "org":
        where.append(f"m.org_id = {bind(org_id)}")
    elif scope == "global":
        where.append("m.org_id IS NULL")
    if family:
        where.append(f"m.family = {bind(family)}")
    if source:
        where.append(f"m.source = {bind(source)}::model_source")
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append(f"m.name ILIKE {bind('%' + escaped + '%')}")
    if not include_archived:
        where.append("m.archived_at IS NULL")
    if after:
        where.append(f"(m.created_at, m.id) < ({bind(after[0])}, {bind(after[1])})")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    limit_ref = bind(limit)
    return await conn.fetch(
        f"{MODEL_SELECT} {clause} ORDER BY m.created_at DESC, m.id DESC LIMIT {limit_ref}", *args
    )


async def get_model(
    conn: asyncpg.Connection, model_id: UUID, *, lock: bool = False
) -> asyncpg.Record | None:
    if lock:
        locked = await conn.fetchval("SELECT id FROM models WHERE id = $1 FOR UPDATE", model_id)
        if locked is None:
            return None
    return await conn.fetchrow(f"{MODEL_SELECT} WHERE m.id = $1", model_id)


async def insert_model(
    conn: asyncpg.Connection,
    *,
    org_id: UUID | None,
    name: str,
    family: str | None,
    description: str | None,
    license: str | None,
    source: str,
    hf_repo: str | None,
    created_by: UUID | None,
) -> UUID:
    return await conn.fetchval(
        """INSERT INTO models (org_id, name, family, description, license, source, hf_repo, created_by)
           VALUES ($1, $2, $3, $4, $5, $6::model_source, $7, $8) RETURNING id""",
        org_id,
        name,
        family,
        description,
        license,
        source,
        hf_repo,
        created_by,
    )


async def update_model(conn: asyncpg.Connection, model_id: UUID, fields: dict[str, Any]) -> None:
    sets = ", ".join(f"{col} = ${i}" for i, col in enumerate(fields, start=2))
    await conn.execute(f"UPDATE models SET {sets} WHERE id = $1", model_id, *fields.values())


async def archive_model(conn: asyncpg.Connection, model_id: UUID) -> None:
    await conn.execute("UPDATE models SET archived_at = now() WHERE id = $1", model_id)
    await conn.execute(
        "UPDATE model_versions SET status = 'archived' WHERE model_id = $1 AND status <> 'archived'",
        model_id,
    )


async def live_deployments(conn: asyncpg.Connection, model_id: UUID) -> int:
    return await conn.fetchval(
        """SELECT count(*) FROM deployments d JOIN model_versions v ON v.id = d.model_version_id
           WHERE v.model_id = $1 AND d.status IN ('pending', 'provisioning', 'running', 'rolling_back')""",
        model_id,
    )


async def list_versions(conn: asyncpg.Connection, model_id: UUID) -> list[asyncpg.Record]:
    return await conn.fetch(
        f"SELECT {VERSION_COLS} FROM model_versions WHERE model_id = $1 ORDER BY version DESC",
        model_id,
    )


async def get_version(
    conn: asyncpg.Connection, model_id: UUID, version: int, *, lock: bool = False
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"SELECT {VERSION_COLS} FROM model_versions WHERE model_id = $1 AND version = $2"
        + (" FOR UPDATE" if lock else ""),
        model_id,
        version,
    )


async def active_version(conn: asyncpg.Connection, model_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"SELECT {VERSION_COLS} FROM model_versions WHERE model_id = $1 AND status = 'active' "
        "FOR UPDATE",
        model_id,
    )


async def next_version_number(conn: asyncpg.Connection, model_id: UUID) -> int:
    return await conn.fetchval(
        "SELECT COALESCE(max(version), 0) + 1 FROM model_versions WHERE model_id = $1", model_id
    )


async def insert_version(
    conn: asyncpg.Connection,
    *,
    model_id: UUID,
    org_id: UUID | None,
    version: int,
    status: str,
    created_by: UUID | None,
    fields: dict[str, Any],
) -> asyncpg.Record:
    return await conn.fetchrow(
        f"""INSERT INTO model_versions (model_id, org_id, version, status, format, param_count,
              quantization, context_length, artifact_uri, artifact_sha256, base_version_id,
              training_job_id, mlflow_run_id, benchmark_scores, metadata, created_by)
            VALUES ($1, $2, $3, $4::version_status, $5::model_format, $6, $7, $8, $9, $10, $11,
                    $12, $13, $14, $15, $16)
            RETURNING {VERSION_COLS}""",
        model_id,
        org_id,
        version,
        status,
        fields["format"],
        fields["param_count"],
        fields["quantization"],
        fields["context_length"],
        fields["artifact_uri"],
        fields["artifact_sha256"],
        fields["base_version_id"],
        fields["training_job_id"],
        fields["mlflow_run_id"],
        fields["benchmark_scores"],
        fields["metadata"],
        created_by,
    )


async def set_version_status(
    conn: asyncpg.Connection, version_id: UUID, status: str
) -> asyncpg.Record:
    return await conn.fetchrow(
        f"UPDATE model_versions SET status = $2::version_status WHERE id = $1 RETURNING {VERSION_COLS}",
        version_id,
        status,
    )


async def merge_version_fields(
    conn: asyncpg.Connection,
    version_id: UUID,
    *,
    benchmark_scores: dict[str, float] | None,
    metadata: dict[str, Any] | None,
) -> asyncpg.Record:
    return await conn.fetchrow(
        f"""UPDATE model_versions
            SET benchmark_scores = benchmark_scores || COALESCE($2::jsonb, '{{}}'::jsonb),
                metadata = metadata || COALESCE($3::jsonb, '{{}}'::jsonb)
            WHERE id = $1 RETURNING {VERSION_COLS}""",
        version_id,
        benchmark_scores,
        metadata,
    )


async def visible(conn: asyncpg.Connection, table: str, row_id: UUID) -> bool:
    """Existence check under the caller's RLS context (FKs themselves ignore RLS)."""
    assert table in {"model_versions", "training_jobs"}
    return bool(await conn.fetchval(f"SELECT 1 FROM {table} WHERE id = $1", row_id))
