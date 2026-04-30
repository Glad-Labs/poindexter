#!/usr/bin/env python3
"""CI smoke-test: apply every migration to a fresh database (issue #229).

Spins up an asyncpg pool against ``DATABASE_URL`` (typically a throwaway
Postgres 16 + pgvector service container in CI), invokes the project's
``services.migrations.run_migrations`` runner end-to-end, and asserts that
exactly one ``schema_migrations`` row exists per migration file in
``src/cofounder_agent/services/migrations/`` (excluding ``__init__.py``).

The runner itself swallows per-migration exceptions and returns ``False``
when any failed, so we surface that as a non-zero exit. The row-count
assertion is what actually catches the "migration N silently dropped a
column migration M depended on" class of bug — the runner records a row
only on successful apply.

This script is dependency-light by design: it only imports asyncpg plus the
project's own migration runner. Run it from the repo root:

    DATABASE_URL=postgres://postgres:postgres@localhost:5432/poindexter_test \
        python scripts/ci/migrations_smoke.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "src" / "cofounder_agent"
MIGRATIONS_DIR = BACKEND_ROOT / "services" / "migrations"

# The migration runner imports ``from services.logger_config import get_logger``
# (relative to the backend package root), so make that importable before the
# import of the runner itself.
sys.path.insert(0, str(BACKEND_ROOT))

from services.migrations import run_migrations  # noqa: E402


class _PoolHolder:
    """Minimal stand-in for ``DatabaseService`` — the runner only touches ``.pool``."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool


def _migration_files() -> list[Path]:
    return sorted(p for p in MIGRATIONS_DIR.glob("*.py") if p.name != "__init__.py")


async def _run() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL must be set", file=sys.stderr)
        return 2

    files = _migration_files()
    expected = len(files)
    print(f"[smoke] discovered {expected} migration file(s) under {MIGRATIONS_DIR}")

    # asyncpg accepts both ``postgres://`` and ``postgresql://`` schemes.
    pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=4)
    try:
        runner_ok = await run_migrations(_PoolHolder(pool))

        async with pool.acquire() as conn:
            applied_rows = await conn.fetch(
                "SELECT name FROM schema_migrations ORDER BY name"
            )
    finally:
        await pool.close()

    applied_names = {row["name"] for row in applied_rows}
    file_names = {f.name for f in files}
    missing = sorted(file_names - applied_names)
    extra = sorted(applied_names - file_names)

    print(f"[smoke] runner returned ok={runner_ok}")
    print(f"[smoke] schema_migrations rows: {len(applied_names)} / files: {expected}")

    failed = False
    if not runner_ok:
        print("FAIL: run_migrations() reported one or more failures", file=sys.stderr)
        failed = True
    if missing:
        print(
            "FAIL: migrations did not record a schema_migrations row:\n  - "
            + "\n  - ".join(missing),
            file=sys.stderr,
        )
        failed = True
    if extra:
        print(
            "FAIL: schema_migrations contains rows with no matching file:\n  - "
            + "\n  - ".join(extra),
            file=sys.stderr,
        )
        failed = True
    if len(applied_names) != expected:
        print(
            f"FAIL: expected {expected} schema_migrations rows, got {len(applied_names)}",
            file=sys.stderr,
        )
        failed = True

    if failed:
        return 1

    print(f"[smoke] OK — all {expected} migrations applied cleanly")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
