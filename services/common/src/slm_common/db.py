from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg


def _dumps(value: object) -> str:
    return json.dumps(value, default=str)  # UUID / datetime / Decimal become strings


async def _init_connection(conn: asyncpg.Connection) -> None:
    for typ in ("jsonb", "json"):
        await conn.set_type_codec(typ, encoder=_dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(
    dsn: str, *, min_size: int = 1, max_size: int = 10, command_timeout: float = 30.0
) -> asyncpg.Pool:
    """Pool whose connections transparently (de)serialise json/jsonb to Python objects."""
    return await asyncpg.create_pool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=command_timeout,
        init=_init_connection,
    )


@asynccontextmanager
async def tenant_tx(
    pool: asyncpg.Pool, org_id: UUID | None, *, bypass: bool = False
) -> AsyncIterator[asyncpg.Connection]:
    """Open a transaction scoped to a tenant for row-level security.

    `app.org_id` / `app.bypass_rls` are transaction-local (`set_config(..., true)`), so they
    cannot leak to the next user of a pooled connection. `bypass=True` is for the narrow
    paths that legitimately cross tenants (login, API-key lookup, platform admin).
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT set_config('app.org_id', $1, true), set_config('app.bypass_rls', $2, true)",
            str(org_id) if org_id else "",
            "on" if bypass else "off",
        )
        yield conn
