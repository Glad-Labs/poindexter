#!/usr/bin/env python3
# scan-floor-exempt: applies migrations against a live DB, not a tree scan
# mirror-tree-exempt: needs the live Postgres its workflow starts as a service
"""CI smoke-test: apply every migration to a fresh database (issue #229).

Spins up an asyncpg pool against ``DATABASE_URL`` (typically a throwaway
Postgres 16 + pgvector service container in CI), invokes the project's
``services.migrations.run_migrations`` runner end-to-end, and asserts that
exactly one ``schema_migrations`` row exists per migration file in
``src/cofounder_agent/poindexter/services/migrations/`` (excluding ``__init__.py``).

The runner itself swallows per-migration exceptions and returns ``False``
when any failed, so we surface that as a non-zero exit. The row-count
assertion is what actually catches the "migration N silently dropped a
column migration M depended on" class of bug — the runner records a row
only on successful apply.

This script is dependency-light by design: it only imports asyncpg plus the
project's own migration runner. Run it from the repo root:

    DATABASE_URL=postgres://postgres:postgres@localhost:5432/poindexter_test \
        python scripts/ci/migrations_smoke.py

**Both real install orders (poindexter#1097).** The default run is the
``poindexter setup`` order: migrations on an empty database. A compose-first
install runs a different order. ``docker compose up`` starts the brain before
the worker, and the brain seeds ``app_settings`` (creating the table itself)
before any migration runs. That order crashed the baseline on every fresh
compose install for months while this check stayed green, because it only ever
ran the first order. ``--brain-first`` runs the brain's real boot seed on an
empty database, then every migration. ``--compare-schema-to <dsn>`` then
requires the result to match a migrations-first database object for object:
columns (position, type, nullability, default), constraints, indexes,
triggers, sequences, views, functions and types.

    DATABASE_URL=postgres://.../poindexter_test_brain_first \
        python scripts/ci/migrations_smoke.py --brain-first \
            --compare-schema-to postgres://.../poindexter_test
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
MIGRATIONS_DIR = BACKEND_ROOT / "poindexter" / "services" / "migrations"


class _PoolHolder:
    """Minimal stand-in for ``DatabaseService`` — the runner only touches ``.pool``."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool


def _migration_files() -> list[Path]:
    return sorted(
        p for p in MIGRATIONS_DIR.glob("*.py")
        if p.name != "__init__.py" and not p.name.startswith("_")
    )


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
    migratable" signal. (poindexter#441 — see poindexter/brain/restore_test_probe.py.)
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


# One query per object kind in the public schema, each row rendered to a
# comparable string. Positions are among live columns, so a dropped column's
# attnum gap cannot read as a difference.
_SNAPSHOT_QUERIES: dict[str, str] = {
    "column": """
        SELECT format('%s.%s #%s %s%s%s%s', c.relname, a.attname,
                      row_number() OVER (PARTITION BY c.oid ORDER BY a.attnum),
                      format_type(a.atttypid, a.atttypmod),
                      CASE WHEN a.attnotnull THEN ' NOT NULL' ELSE '' END,
                      COALESCE(' DEFAULT ' || pg_get_expr(d.adbin, d.adrelid), ''),
                      CASE WHEN a.attgenerated <> '' THEN ' GENERATED' ELSE '' END)
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
          AND a.attnum > 0 AND NOT a.attisdropped
    """,
    "constraint": """
        SELECT format('%s %s %s', c.relname, k.conname, pg_get_constraintdef(k.oid))
        FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
        WHERE k.connamespace = 'public'::regnamespace
    """,
    "index": """
        SELECT pg_get_indexdef(i.indexrelid)
        FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
        WHERE c.relnamespace = 'public'::regnamespace
    """,
    "trigger": """
        SELECT pg_get_triggerdef(t.oid)
        FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        WHERE c.relnamespace = 'public'::regnamespace AND NOT t.tgisinternal
    """,
    "sequence": """
        SELECT format('%s %s start=%s increment=%s min=%s max=%s cycle=%s owned_by=%s',
                      s.sequencename, s.data_type, s.start_value, s.increment_by,
                      s.min_value, s.max_value, s.cycle,
                      COALESCE((SELECT t.relname || '.' || a.attname
                                FROM pg_depend dep
                                JOIN pg_class t ON t.oid = dep.refobjid
                                JOIN pg_attribute a ON a.attrelid = t.oid
                                                   AND a.attnum = dep.refobjsubid
                                WHERE dep.objid = format('public.%I', s.sequencename)::regclass
                                  AND dep.classid = 'pg_class'::regclass
                                  AND dep.deptype = 'a'), '-'))
        FROM pg_sequences s WHERE s.schemaname = 'public'
    """,
    "view": """
        SELECT format('%s %s', c.relname, pg_get_viewdef(c.oid))
        FROM pg_class c
        WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('v', 'm')
    """,
    "function": """
        SELECT format('%s(%s) %s', p.proname, pg_get_function_identity_arguments(p.oid),
                      md5(pg_get_functiondef(p.oid)))
        FROM pg_proc p
        WHERE p.pronamespace = 'public'::regnamespace AND p.prokind IN ('f', 'p')
          AND NOT EXISTS (SELECT 1 FROM pg_depend e
                          WHERE e.objid = p.oid AND e.deptype = 'e')
    """,
    "type": """
        SELECT format('%s %s %s', t.typname, t.typtype,
                      COALESCE((SELECT string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder)
                                FROM pg_enum e WHERE e.enumtypid = t.oid),
                               format_type(t.typbasetype, t.typtypmod)))
        FROM pg_type t
        WHERE t.typnamespace = 'public'::regnamespace AND t.typtype IN ('e', 'd')
    """,
}


async def schema_snapshot(conn: asyncpg.Connection) -> set[str]:
    """Describe the public schema as a set of ``"<kind> <definition>"`` strings.

    Data is deliberately left out: on a compose-first install the brain's seed
    values win for the keys it shares with the baseline, so the two install
    orders legitimately hold different ``app_settings`` rows.
    """
    snapshot: set[str] = set()
    for kind, query in _SNAPSHOT_QUERIES.items():
        snapshot.update(f"{kind} {row[0]}" for row in await conn.fetch(query))
    return snapshot


def diff_snapshots(
    actual: set[str], reference: set[str], *, actual_label: str, reference_label: str
) -> list[str]:
    """Lines naming every object that is in one snapshot but not the other."""
    return [f"only in {actual_label}: {item}" for item in sorted(actual - reference)] + [
        f"only in {reference_label}: {item}" for item in sorted(reference - actual)
    ]


async def _seed_like_the_brain(pool: asyncpg.Pool) -> list[str]:
    """Run the brain daemon's boot seed against the (empty) target database.

    This is ``brain_daemon.main()``'s first act, and on a compose-first install
    it happens before the worker has run a single migration. Returns FAIL lines;
    empty means the seed ran against an empty database and inserted every row.
    """
    from poindexter.brain.seed_loader import seed_app_settings

    async with pool.acquire() as conn:
        tables = await conn.fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
        )
        if tables:
            return [
                f"FAIL: --brain-first needs an empty database, found {tables} "
                "table(s) — it models a first boot"
            ]
        result = await seed_app_settings(conn)
    print(f"[smoke] brain seed before any migration: {result}")
    if result["inserted"] != result["total_seed"]:
        return [f"FAIL: the brain seed inserted {result['inserted']} of {result['total_seed']} rows"]
    return []


async def _run(
    allow_historical: bool = False,
    brain_first: bool = False,
    compare_schema_to: str | None = None,
) -> int:
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
    from poindexter.services.migrations import run_migrations

    files = _migration_files()
    expected = len(files)
    print(f"[smoke] discovered {expected} migration file(s) under {MIGRATIONS_DIR}")

    # asyncpg accepts both ``postgres://`` and ``postgresql://`` schemes.
    pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=4)
    try:
        if brain_first:
            seed_failures = await _seed_like_the_brain(pool)
            if seed_failures:
                for msg in seed_failures:
                    print(msg, file=sys.stderr)
                return 1

        runner_ok = await run_migrations(_PoolHolder(pool))

        async with pool.acquire() as conn:
            applied_rows = await conn.fetch(
                "SELECT name FROM schema_migrations ORDER BY name"
            )
            snapshot = await schema_snapshot(conn) if compare_schema_to else set()
    finally:
        await pool.close()

    schema_diff: list[str] = []
    if compare_schema_to:
        reference_conn = await asyncpg.connect(dsn=compare_schema_to)
        try:
            reference = await schema_snapshot(reference_conn)
        finally:
            await reference_conn.close()
        if not reference:
            # A reference with nothing in it would pass any database with
            # nothing in it too; a comparison has to compare something.
            schema_diff = ["the reference database has no schema — was it migrated?"]
        else:
            schema_diff = diff_snapshots(
                snapshot, reference,
                actual_label="this database", reference_label="the reference",
            )
        print(
            f"[smoke] schema compared against the reference: {len(snapshot)} "
            f"object(s) here, {len(reference)} there, {len(schema_diff)} difference(s)"
        )

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
    if schema_diff:
        messages.append(
            "FAIL: the schema differs from the reference database:\n  - "
            + "\n  - ".join(schema_diff)
        )
        failed = True
    for msg in messages:
        # FAIL lines go to stderr (CI greps them); NOTE lines are informational.
        print(msg, file=sys.stderr if msg.startswith("FAIL") else sys.stdout)

    if failed:
        return 1

    mode = " (restored-backup mode)" if allow_historical else ""
    if brain_first:
        mode += " after the brain's boot seed"
    if compare_schema_to:
        mode += ", schema identical to the reference"
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
    parser.add_argument(
        "--brain-first",
        action="store_true",
        help=(
            "Run the brain daemon's boot seed on the (empty) database before "
            "the migrations: the order `docker compose up` produces on a fresh "
            "volume (poindexter#1097)."
        ),
    )
    parser.add_argument(
        "--compare-schema-to",
        metavar="DSN",
        help=(
            "After migrating, require the public schema to match this "
            "already-migrated reference database object for object."
        ),
    )
    args = parser.parse_args()
    return asyncio.run(
        _run(
            allow_historical=args.restored_backup,
            brain_first=args.brain_first,
            compare_schema_to=args.compare_schema_to,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
