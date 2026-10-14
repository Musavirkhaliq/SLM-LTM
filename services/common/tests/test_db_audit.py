import uuid

import asyncpg
import pytest

from slm_common.audit import audit, compute_row_hash
from slm_common.auth import Principal
from slm_common.db import tenant_tx
from slm_common.rbac import Role


async def _project(conn: asyncpg.Connection, org: uuid.UUID, slug: str) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO projects (org_id, slug, name) VALUES ($1, $2::text, $2::text) RETURNING id", org, slug
    )


async def test_rls_isolates_tenants(pool, org_id):
    other = uuid.uuid4()
    async with tenant_tx(pool, other, bypass=True) as conn:
        await conn.execute(
            "INSERT INTO organizations (id, slug, name) VALUES ($1, $2::text, $2::text)", other, f"o-{other.hex[:10]}"
        )
    async with tenant_tx(pool, org_id) as conn:
        mine = await _project(conn, org_id, "mine")
    async with tenant_tx(pool, other) as conn:
        theirs = await _project(conn, other, "theirs")

    async with tenant_tx(pool, org_id) as conn:
        ids = {r["id"] for r in await conn.fetch("SELECT id FROM projects")}
        assert mine in ids and theirs not in ids
        with pytest.raises(asyncpg.PostgresError):  # WITH CHECK blocks writing into another org
            async with conn.transaction():
                await _project(conn, other, "smuggled")

    async with tenant_tx(pool, None) as conn:  # no tenant context -> nothing visible
        assert await conn.fetchval("SELECT count(*) FROM projects") == 0

    async with tenant_tx(pool, None, bypass=True) as conn:
        ids = {r["id"] for r in await conn.fetch("SELECT id FROM projects")}
        assert {mine, theirs} <= ids


async def test_tenant_context_does_not_leak_across_pooled_connections(pool, org_id):
    async with tenant_tx(pool, org_id) as conn:
        assert await conn.fetchval("SELECT current_org()") == org_id
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT current_org()") is None


async def test_global_catalogue_is_readable_but_not_writable(pool, org_id):
    async with tenant_tx(pool, org_id) as conn:
        names = {r["name"] for r in await conn.fetch("SELECT name FROM models WHERE org_id IS NULL")}
        assert {"phi-4", "phi-4-mini", "llama-3.1-8b"} <= names
        with pytest.raises(asyncpg.PostgresError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO models (org_id, name, source) VALUES (NULL, 'rogue', 'upload')"
                )


async def test_app_role_is_not_superuser(pool):
    row = await pool.fetchrow(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )
    assert not row["rolsuper"] and not row["rolbypassrls"]


async def test_audit_rows_are_chained_and_append_only(pool, org_id):
    actor = Principal(kind="user", user_id=uuid.uuid4(), org_id=org_id, role=Role.org_admin)
    async with tenant_tx(pool, org_id) as conn:
        await audit(conn, org_id=org_id, actor=actor, action="project.create", resource="project",
                    resource_id="p1", detail={"name": "alpha"})
        await audit(conn, org_id=org_id, actor=actor.user_id, action="project.delete", resource="project")
        await audit(conn, org_id=org_id, actor=None, action="quota.reset", resource="quota")
        rows = await conn.fetch("SELECT * FROM audit_logs ORDER BY id")
    assert [r["action"] for r in rows] == ["project.create", "project.delete", "quota.reset"]
    assert rows[0]["prev_hash"] is None and rows[1]["prev_hash"] == rows[0]["row_hash"]
    assert rows[2]["prev_hash"] == rows[1]["row_hash"] and rows[2]["actor_type"] == "system"
    assert rows[0]["detail"] == {"name": "alpha"}
    recomputed = compute_row_hash(
        None,
        {
            "org_id": org_id,
            "actor_id": actor.user_id,
            "actor_type": "user",
            "action": "project.create",
            "resource": "project",
            "resource_id": "p1",
            "detail": {"name": "alpha"},
            "created_at": rows[0]["created_at"].isoformat(),
        },
    )
    assert recomputed == rows[0]["row_hash"]

    async with tenant_tx(pool, org_id) as conn:
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("UPDATE audit_logs SET action = 'tampered'")
    async with tenant_tx(pool, org_id) as conn:
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("DELETE FROM audit_logs")
