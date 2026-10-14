"""Throwaway embedded PostgreSQL for local verification and tests (via the `pgserver` wheel).

The pgserver build ships without the `citext` and `pgcrypto` contrib extensions, so minimal
stand-ins are installed into its extension directory when missing. They only make
`CREATE EXTENSION` succeed: `citext` becomes a plain-text domain (so case-insensitive
uniqueness is NOT enforced by the database here; services lower-case emails themselves) and
`pgcrypto` is empty (`gen_random_uuid()` is core since PG13). Never use this against real data.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

_SHIMS = {
    "citext": "CREATE DOMAIN citext AS text;\n",
    "pgcrypto": "-- gen_random_uuid() is built in since PostgreSQL 13\nSELECT 1;\n",
}


def _extension_dir() -> Path:
    import pgserver

    return Path(pgserver.__file__).parent / "pginstall" / "share" / "postgresql" / "extension"


def install_extension_shims() -> list[str]:
    ext_dir = _extension_dir()
    installed: list[str] = []
    for name, body in _SHIMS.items():
        control = ext_dir / f"{name}.control"
        if control.exists():
            continue
        control.write_text(
            f"comment = 'dev shim for {name}'\ndefault_version = '1.0'\nrelocatable = true\n"
        )
        (ext_dir / f"{name}--1.0.sql").write_text(body)
        installed.append(name)
    return installed


def start(data_dir: str | Path | None = None):
    """Start (or attach to) an embedded server; returns the pgserver handle."""
    import pgserver

    install_extension_shims()
    path = Path(data_dir) if data_dir else Path(tempfile.mkdtemp(prefix="slm-pg-"))
    return pgserver.get_server(path, cleanup_mode="delete")


def admin_dsn(server, database: str = "postgres") -> str:
    base = server.get_uri()
    return base.rsplit("/", 1)[0] + "/" + database


def app_dsn(server, role: str = "slm_app", database: str = "postgres") -> str:
    admin = admin_dsn(server, database)
    return admin.replace("postgres:@", f"{role}:@", 1)
