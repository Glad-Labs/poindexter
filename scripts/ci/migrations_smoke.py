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

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[2]
# poindexter#441: the brain's restore-test probe runs this script inside the
# worker container, where the backend is mounted at /app (not under a repo-root
# tree). Honor an explicit override so the split-mount layout resolves; CI
# leaves the env unset and keeps the repo-root default.
_BACKEND_ROOT_ENV = os.environ.get("POINDEXTER_BACKEND_ROOT")
BACKEND_ROOT = (
    Path(_BACKEND_ROOT_ENV).resolve()
    if _BACKEND_ROOT_ENV
    else REPO_ROOT / "src" / "cofounder_agent"
)
MIGRATIONS_DIR = BACKEND_ROOT / "services" / "migrations"


class _PoolHolder:
    """Minimal stand-in for ``DatabaseService`` — the runner only touches ``.pool``."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool


def _migration_files() -> list[Path]:
    return sorted(p for p in MIGRATIONS_DIR.glob("*.py") if p.name != "__init__.py")


def _evaluate(
    *,
    runner_ok: bool,
    applied_names: set[str],
    file_names: set[str],
    allow_historical: bool,
) -> tuple[bool, list[str]]:
    """Decide pass/fail from the runner result and the applied-vs-files sets.

    Returns ``(failed, messages)``. In the default (CI / fresh-DB) mode every
    discrepancy is fatal. With ``allow_historical=True`` — a restored
    *production* backup, whose ``schema_migrations`` legitimately carries rows
    for migrations whose files were later squashed into ``0000_baseline.py`` or
    renamed — EXTRA rows and the exact-count check are tolerated. Only a runner
    failure or a MISSING current migration (one that should apply but didn't)
    stays fatal there: that is the real "this backup isn't restorable /
    migratable" signal. (poindexter#441 — see brain/restore_test_probe.py.)
    """
    missing = sorted(file_names - applied_names)
    extra = sorted(applied_names - file_names)
    messages: list[str] = []
    failed = False

    if not runner_ok:
        messages.append("FAIL: run_migrations() reported one or more failures")
        failed = True
    if missing:
        messages.append(
            "FAIL: migrations did not record a schema_migrations row:\n  - "
            + "\n  - ".join(missing)
        )
        failed = True

    if allow_historical:
        if extra:
            messages.append(
                f"NOTE: tolerating {len(extra)} historical schema_migrations "
                "row(s) with no matching file (restored-backup mode)"
            )
    else:
        if extra:
            messages.append(
                "FAIL: schema_migrations contains rows with no matching file:\n  - "
                + "\n  - ".join(extra)
            )
            failed = True
        if len(applied_names) != len(file_names):
            messages.append(
                f"FAIL: expected {len(file_names)} schema_migrations rows, "
                f"got {len(applied_names)}"
            )
            failed = True

    return failed, messages


async def _run(allow_historical: bool = False) -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL must be set", file=sys.stderr)
        return 2

    # The migration runner imports ``from services.logger_config import
    # get_logger`` (relative to the backend package root), so make that
    # importable before importing the runner. Done lazily here (not at module
    # load) so importing this script stays light — keeps the migrations-smoke
    # CI env minimal and lets unit tests import it under a fake BACKEND_ROOT.
    sys.path.insert(0, str(BACKEND_ROOT))
    from services.migrations import run_migrations

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

    print(f"[smoke] runner returned ok={runner_ok}")
    print(f"[smoke] schema_migrations rows: {len(applied_names)} / files: {expected}")

    failed, messages = _evaluate(
        runner_ok=runner_ok,
        applied_names=applied_names,
        file_names=file_names,
        allow_historical=allow_historical,
    )
    for msg in messages:
        # FAIL lines go to stderr (CI greps them); NOTE lines are informational.
        print(msg, file=sys.stderr if msg.startswith("FAIL") else sys.stdout)

    if failed:
        return 1

    mode = " (restored-backup mode)" if allow_historical else ""
    print(f"[smoke] OK — all {expected} migrations applied cleanly{mode}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply every migration to a DB and verify.")
    parser.add_argument(
        "--restored-backup",
        action="store_true",
        help=(
            "Tolerate historical schema_migrations rows that have no matching "
            "file, and skip the exact-count check. Use when running against a "
            "restored PRODUCTION backup (brain restore-test probe, "
            "poindexter#441): its DB carries the full migration history while "
            "the repo only ships the post-squash files. Default (CI / fresh DB) "
            "keeps the strict 1-row-per-file check."
        ),
    )
    args = parser.parse_args()
    return asyncio.run(_run(allow_historical=args.restored_backup))


if __name__ == "__main__":
    sys.exit(main())
