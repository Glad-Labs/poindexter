"""
Migration 0064: Drop the abandoned `settings` table.

The `settings` table was replaced by `app_settings` (migration 0058).
All 4 rows in `settings` have equivalent entries in `app_settings`.
The SQLAlchemy model `Setting` in admin.py has been removed.

This migration drops the table to eliminate confusion between the two.
"""
from services.logger_config import get_logger

logger = get_logger(__name__)


async def run_migration(conn):
    """Drop the abandoned settings table if it exists."""
    exists = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'settings')"
    )
    if not exists:
        logger.info("[migration-0064] settings table does not exist, skipping")
        return

    # Safety: log remaining rows before dropping
    count = await conn.fetchval("SELECT COUNT(*) FROM settings")
    logger.info("[migration-0064] Dropping settings table (%d rows — all migrated to app_settings)", count)

    await conn.execute("DROP TABLE IF EXISTS settings CASCADE")
    logger.info("[migration-0064] settings table dropped")
