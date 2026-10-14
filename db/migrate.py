"""Apply db/migrations/*.sql in order and keep the `slm_app` role grants current.

Usage:
    uv run python db/migrate.py --dsn postgresql://postgres:...@host/slm [--app-password ...]

The DSN must belong to a role allowed to create extensions/roles (the owner/admin role).
Services themselves connect as the non-superuser `slm_app` so that row-level security applies.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
APP_ROLE = "slm_app"
_ADVISORY_LOCK = 7_265_001

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version    text PRIMARY KEY,
  checksum   text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
)
"""

_BEGIN_RE = re.compile(r"^\s*BEGIN\s*;\s*$", re.IGNORECASE | re.MULTILINE)
_COMMIT_RE = re.compile(r"^\s*COMMIT\s*;\s*$", re.IGNORECASE | re.MULTILINE)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def discover(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    return sorted(directory.glob("*.sql"), key=lambda p: p.name)


async def _apply_file(conn: asyncpg.Connection, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    checksum = hashlib.sha256(sql.encode()).hexdigest()
    # Migration files carry their own BEGIN/COMMIT; strip them so the file and its
    # bookkeeping row commit atomically in one transaction owned by this runner.
    body = _COMMIT_RE.sub("", _BEGIN_RE.sub("", sql, count=1))
    async with conn.transaction():
        await conn.execute(body)
        await conn.execute(
            "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
            path.name,
            checksum,
        )


async def ensure_app_role(
    conn: asyncpg.Connection, role: str = APP_ROLE, password: str | None = None
) -> None:
    """Create the non-superuser application role and (re)apply its grants. Idempotent."""
    r = _quote_ident(role)
    exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
    pw = f" PASSWORD {_quote_literal(password)}" if password else ""
    if not exists:
        await conn.execute(
            f"CREATE ROLE {r} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS{pw}"
        )
    elif password:
        await conn.execute(f"ALTER ROLE {r} NOSUPERUSER NOBYPASSRLS{pw}")
    db = _quote_ident(await conn.fetchval("SELECT current_database()"))
    await conn.execute(f"GRANT CONNECT ON DATABASE {db} TO {r}")
    await conn.execute(f"GRANT USAGE ON SCHEMA public TO {r}")
    await conn.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {r}")
    # audit_logs is append-only: the trigger blocks mutation, the grant makes intent explicit.
    await conn.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM {r}")
    await conn.execute(f"REVOKE ALL ON schema_migrations FROM {r}")
    await conn.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {r}")
    await conn.execute(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {r}")
    await conn.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {r}"
    )
    await conn.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {r}"
    )


async def migrate(
    dsn: str,
    *,
    app_role: str = APP_ROLE,
    app_password: str | None = None,
    directory: Path = MIGRATIONS_DIR,
) -> list[str]:
    """Apply pending migrations; returns the filenames applied in this run."""
    conn = await asyncpg.connect(dsn)
    applied: list[str] = []
    try:
        await conn.fetchval("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK)
        await conn.execute(_SCHEMA_MIGRATIONS_DDL)
        done = {
            r["version"]: r["checksum"]
            for r in await conn.fetch("SELECT version, checksum FROM schema_migrations")
        }
        for path in discover(directory):
            checksum = hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()
            if path.name in done:
                if done[path.name] != checksum:
                    raise RuntimeError(f"migration {path.name} was modified after being applied")
                continue
            await _apply_file(conn, path)
            applied.append(path.name)
        await ensure_app_role(conn, app_role, app_password)
    finally:
        await conn.fetchval("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK)
        await conn.close()
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dsn",
        default=os.environ.get("SLM_MIGRATE_DATABASE_URL") or os.environ.get("SLM_DATABASE_URL"),
        help="owner/admin DSN (env SLM_MIGRATE_DATABASE_URL)",
    )
    parser.add_argument("--app-role", default=APP_ROLE)
    parser.add_argument("--app-password", default=os.environ.get("SLM_APP_PASSWORD"))
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("--dsn or SLM_MIGRATE_DATABASE_URL is required")
    applied = asyncio.run(
        migrate(args.dsn, app_role=args.app_role, app_password=args.app_password)
    )
    print("applied: " + (", ".join(applied) if applied else "nothing (up to date)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
