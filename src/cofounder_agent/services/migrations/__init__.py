"""
Database Migrations Runner

Dynamically discovers and runs all async migrations in the migrations/ directory.

Two interface conventions are supported:

Convention A (pool-based):
- async def up(pool): Apply the migration
- async def down(pool): Revert the migration

Convention B (connection-based):
- async def run_migration(conn): Apply the migration
- async def rollback_migration(conn): Revert the migration

The runner checks for `up` first; if absent it falls back to `run_migration`,
acquiring a connection from the pool automatically.

Migration files are run in alphabetical order. Each migration is tracked in a
`schema_migrations` table and will only be applied once (idempotent).
"""

import importlib.util
from pathlib import Path
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


def _collect_migration_files(migrations_dir: Path) -> list[Path]:
    """Return migration .py files in ``migrations_dir``, sorted alphabetically.

    Excludes ``__init__.py`` and any file whose name starts with ``_``
    (private helpers such as ``_module_runner.py``).  Matches the rule in
    ``services/module_runner.py`` and ``scripts/ci/migrations_smoke.py``.
    """
    return sorted(
        (f for f in migrations_dir.glob("*.py")
         if f.name != "__init__.py" and not f.name.startswith("_")),
        key=lambda p: p.name,
    )


async def get_migration_status(pool: Any) -> dict[str, Any]:
    """Return a snapshot of migration state for ``/api/health`` consumers.

    Shape::

        {
            "applied": <int>,            # count of migrations recorded in schema_migrations
            "pending": <int>,            # on-disk files not yet recorded as applied
            "latest_applied": <str|None>, # filename of the most-recently applied migration
            "pending_files": [str, ...]   # filenames of pending migrations (empty when clean)
        }

    Brain's :mod:`brain.migration_drift_probe` reads this block from
    ``/api/health`` to detect schema drift (the case where a worker has
    shipped a new migration file but the runner hasn't applied it).
    Before this helper landed, the block was missing entirely — every
    probe cycle emitted ``probe.migration_drift_unknown`` audit events
    that the brain's triage LLM then mistook for evidence of an
    "outdated worker build" on every unrelated incident. See
    ``feedback_verify_brain_triage_before_acting`` for the misdiagnosis
    pattern this fix interrupts.

    Returns an ``{"error": "..."}`` dict instead of raising — the
    health endpoint must NEVER throw out of a component check. The
    probe handles the error branch as "can't tell" rather than drift.
    """
    if pool is None:
        return {"error": "pool unavailable"}
    try:
        migrations_dir = Path(__file__).parent
        on_disk = [f.name for f in _collect_migration_files(migrations_dir)]
        async with pool.acquire() as conn:
            # Tolerate the table not existing yet — fresh DB during
            # startup race. Empty applied set = everything is pending.
            try:
                rows = await conn.fetch(
                    "SELECT name FROM schema_migrations",
                )
                applied_names = {r["name"] for r in rows}
            except Exception:
                applied_names = set()
            try:
                latest = await conn.fetchval(
                    "SELECT name FROM schema_migrations "
                    "ORDER BY applied_at DESC NULLS LAST, id DESC LIMIT 1",
                )
            except Exception:
                latest = None
        pending = [name for name in on_disk if name not in applied_names]
        return {
            "applied": len(applied_names),
            "pending": len(pending),
            "latest_applied": latest,
            "pending_files": pending,
        }
    except Exception as exc:  # noqa: BLE001 — health endpoint must never raise
        return {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}

_MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) UNIQUE NOT NULL,
    applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
)
"""


async def run_migrations(database_service) -> bool:
    """
    Run all pending migrations in the services/migrations/ directory.

    Each migration is recorded in the `schema_migrations` table so it only
    runs once, even across multiple server restarts.

    Args:
        database_service: The DatabaseService instance with pool access

    Returns:
        bool: True if all migrations completed successfully, False otherwise
    """
    try:
        if not database_service or not database_service.pool:
            logger.warning("Database service or pool not available, skipping migrations")
            return False

        pool = database_service.pool

        # Ensure tracking table exists
        async with pool.acquire() as conn:
            await conn.execute(_MIGRATIONS_TABLE_SQL)

        migrations_dir = Path(__file__).parent
        migration_files = _collect_migration_files(migrations_dir)

        if not migration_files:
            logger.info("No migrations found")
            return True

        logger.info(f"Found {len(migration_files)} migration file(s)")

        applied_count = 0
        skipped_count = 0
        failed_count = 0

        for migration_file in migration_files:
            migration_name = migration_file.name
            try:
                # Check if already applied
                async with pool.acquire() as conn:
                    already_applied = await conn.fetchval(
                        "SELECT id FROM schema_migrations WHERE name = $1",
                        migration_name,
                    )

                if already_applied:
                    logger.debug(f"Skipping (already applied): {migration_name}")
                    skipped_count += 1
                    continue

                # Dynamically import the migration module
                spec = importlib.util.spec_from_file_location(migration_name[:-3], migration_file)
                migration_module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
                spec.loader.exec_module(migration_module)  # type: ignore[union-attr]

                has_up = hasattr(migration_module, "up")
                has_run_migration = hasattr(migration_module, "run_migration")

                if not has_up and not has_run_migration:
                    logger.warning(f"Migration {migration_name} missing up() or run_migration() function — skipping")
                    continue

                logger.info(f"Applying migration: {migration_name}")

                if has_up:
                    await migration_module.up(pool)
                else:
                    async with pool.acquire() as migration_conn:
                        await migration_module.run_migration(migration_conn)

                # Record as applied
                async with pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO schema_migrations (name) VALUES ($1) ON CONFLICT (name) DO NOTHING",
                        migration_name,
                    )

                logger.info(f"Migration completed: {migration_name}")
                applied_count += 1

            except Exception:
                # Log and continue — do NOT halt on first failure so subsequent migrations can apply
                logger.error(f"Migration failed: {migration_name}", exc_info=True)
                failed_count += 1

        logger.info(
            f"Migrations finished — {applied_count} applied, "
            f"{skipped_count} already up-to-date, {failed_count} failed"
        )
        # Return False only if any migration failed, so callers know the DB may be in a partial state
        return failed_count == 0

    except Exception:
        logger.error("Migration runner error", exc_info=True)
        return False
