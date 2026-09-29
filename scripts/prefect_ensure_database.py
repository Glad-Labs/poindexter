#!/usr/bin/env python3
"""Create the Prefect server's database when it does not exist yet.

The prefect-server container keeps its state in a ``prefect`` database on the
stack's ``postgres-local``. Nothing else ever created that database: a fresh
volume holds only ``poindexter_brain``, so on every fresh install the server
died with ``InvalidCatalogNameError: database "prefect" does not exist``, the
Prefect worker never started, and no task was ever dispatched
(quickstart-e2e, 2026-09-28). Existing installs only had it because an
operator once created it by hand.

docker-compose.consumer.yml runs this before ``prefect server start``. It reads
``PREFECT_API_DATABASE_CONNECTION_URL`` (the same URL the server uses),
connects to the ``postgres`` maintenance database as that user — the stack's
Postgres superuser — and issues ``CREATE DATABASE`` only when the database is
missing. It is idempotent, so it runs on every start, and it exits non-zero
when Postgres cannot be reached so the container restarts visibly instead of
starting a server with no database.

Runs inside the Prefect image (Python + asyncpg, no Poindexter code), so it is
stdlib + asyncpg only.
"""

from __future__ import annotations

import asyncio
import os
import sys
from urllib.parse import unquote, urlsplit

URL_ENV = "PREFECT_API_DATABASE_CONNECTION_URL"
MAINTENANCE_DB = "postgres"
CONNECT_ATTEMPTS = 30
CONNECT_DELAY_S = 2.0


def parse_database_url(url: str) -> dict[str, object]:
    """Connection kwargs + target database name from a SQLAlchemy-style URL.

    ``postgresql+asyncpg://user:pass@host:5432/prefect`` ->
    ``{"host", "port", "user", "password", "database"}``.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.split("+", 1)[0]
    if scheme not in ("postgresql", "postgres"):
        raise ValueError(f"{URL_ENV} is not a Postgres URL (scheme {parts.scheme!r})")
    database = unquote(parts.path.lstrip("/"))
    if not database:
        raise ValueError(f"{URL_ENV} names no database")
    return {
        "host": parts.hostname or "localhost",
        "port": parts.port or 5432,
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "database": database,
    }


def quote_ident(name: str) -> str:
    """A Postgres identifier, double-quoted (CREATE DATABASE takes no bind params)."""
    return '"' + name.replace('"', '""') + '"'


async def ensure_database(url: str, *, attempts: int = CONNECT_ATTEMPTS, delay_s: float = CONNECT_DELAY_S) -> str:
    """Return ``"exists"`` or ``"created"``; raise RuntimeError if unreachable."""
    import asyncpg

    cfg = parse_database_url(url)
    target = str(cfg.pop("database"))
    conn = None
    last: BaseException | None = None
    for _ in range(max(1, attempts)):
        try:
            conn = await asyncpg.connect(database=MAINTENANCE_DB, **cfg)
            break
        except (OSError, TimeoutError, asyncpg.CannotConnectNowError) as exc:
            last = exc
            await asyncio.sleep(delay_s)
    if conn is None:
        raise RuntimeError(
            f"could not reach Postgres at {cfg['host']}:{cfg['port']} to check for "
            f"database {target!r}: {type(last).__name__}: {last}"
        )
    try:
        if await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", target):
            return "exists"
        try:
            await conn.execute(f"CREATE DATABASE {quote_ident(target)}")
        except asyncpg.DuplicateDatabaseError:
            return "exists"  # created concurrently by another starter
        return "created"
    finally:
        await conn.close()


def main() -> int:
    url = os.environ.get(URL_ENV, "").strip()
    if not url:
        print(f"[prefect-db] {URL_ENV} is unset; nothing to ensure", file=sys.stderr)
        return 1
    try:
        outcome = asyncio.run(ensure_database(url))
    except (RuntimeError, ValueError) as exc:
        print(f"[prefect-db] {exc}", file=sys.stderr)
        return 1
    print(f"[prefect-db] database {parse_database_url(url)['database']!r} {outcome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
