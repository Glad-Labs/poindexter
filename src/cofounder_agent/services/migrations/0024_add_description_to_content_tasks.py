"""
Migration: Add description column to content_tasks table.

Adds a human-written task description field (distinct from the AI-generated
excerpt) to support campaign briefs and task context (#116).
"""

from services.logger_config import get_logger
logger = get_logger(__name__)
async def up(pool) -> None:
    """Add description column to content_tasks if it doesn't already exist."""
    async with pool.acquire() as conn:
        table_exists = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT FROM information_schema.tables
                WHERE table_name = 'content_tasks'
            )
            """
        )
        if not table_exists:
            logger.warning("content_tasks table does not exist — skipping migration")
            return

        col_exists = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT FROM information_schema.columns
                WHERE table_name = 'content_tasks' AND column_name = 'description'
            )
            """
        )
        if col_exists:
            logger.info("description column already exists — skipping")
            return

        await conn.execute(
            "ALTER TABLE content_tasks ADD COLUMN description TEXT"
        )
        logger.info("Added description column to content_tasks")


async def down(pool) -> None:
    """Remove description column from content_tasks."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE content_tasks DROP COLUMN IF EXISTS description"
        )
        logger.info("Dropped description column from content_tasks")
