"""Workspace-wide pytest fixtures: an embedded, migrated PostgreSQL shared by DB-backed tests."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import asyncpg
import pytest

from slm_common.db import create_pool


@dataclass(frozen=True)
class PgInfo:
    admin_dsn: str
    app_dsn: str


@pytest.fixture(scope="session")
def pg() -> Iterator[PgInfo]:
    try:
        import dev_postgres
        import migrate

        server = dev_postgres.start()
        admin = dev_postgres.admin_dsn(server)
        asyncio.run(migrate.migrate(admin))
    except Exception as exc:
        pytest.skip(f"embedded PostgreSQL unavailable: {exc!r}")
    yield PgInfo(admin_dsn=admin, app_dsn=dev_postgres.app_dsn(server))
    server.cleanup()


@pytest.fixture
async def pool(pg: PgInfo) -> AsyncIterator[asyncpg.Pool]:
    """Pool connected as the non-superuser `slm_app`, so row-level security is enforced."""
    p = await create_pool(pg.app_dsn, min_size=1, max_size=5)
    yield p
    await p.close()


@pytest.fixture
async def admin_pool(pg: PgInfo) -> AsyncIterator[asyncpg.Pool]:
    p = await create_pool(pg.admin_dsn, min_size=1, max_size=3)
    yield p
    await p.close()


@pytest.fixture
async def org_id(admin_pool: asyncpg.Pool) -> uuid.UUID:
    oid = uuid.uuid4()
    await admin_pool.execute(
        "INSERT INTO organizations (id, slug, name) VALUES ($1, $2::text, $2::text)", oid, f"org-{oid.hex[:12]}"
    )
    return oid
