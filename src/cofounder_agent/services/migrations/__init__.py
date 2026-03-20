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

from services.logger_config import get_logger

logger = get_logger(__name__)

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
        migration_files = sorted(
            [f for f in migrations_dir.glob("*.py") if f.name != "__init__.py"]
        )

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
