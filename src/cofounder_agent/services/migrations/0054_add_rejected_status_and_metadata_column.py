"""
Migration 0054: Add 'rejected' to status CHECK + add metadata JSONB column.

Fixes two issues from the log:
1. Bulk-rejecting tasks fails because 'rejected' is not in chk_content_tasks_status
2. Individual task rejection fails because content_tasks.metadata column doesn't exist

Extends the CHECK constraint from migration 0052 and adds the metadata column.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)

_EXTENDED_STATUSES = (
    "('pending','queued','in_progress','completed','failed','cancelled',"
    "'awaiting_approval','approved','validation_failed','validation_error',"
    "'published','rejected')"
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # 1. Extend CHECK constraint to include 'rejected'
        await conn.execute(
            "ALTER TABLE content_tasks "
            "DROP CONSTRAINT IF EXISTS chk_content_tasks_status"
        )
        await conn.execute(
            f"ALTER TABLE content_tasks "
            f"ADD CONSTRAINT chk_content_tasks_status "
            f"CHECK (status IN {_EXTENDED_STATUSES})"
        )
        logger.info("Added 'rejected' to chk_content_tasks_status")

        # 2. Add metadata JSONB column if it doesn't exist
        exists = await conn.fetchval(
            "SELECT EXISTS("
            "  SELECT 1 FROM information_schema.columns "
            "  WHERE table_name = 'content_tasks' AND column_name = 'metadata'"
            ")"
        )
        if not exists:
            await conn.execute(
                "ALTER TABLE content_tasks "
                "ADD COLUMN metadata JSONB DEFAULT '{}'"
            )
            logger.info("Added metadata JSONB column to content_tasks")
        else:
            logger.info("metadata column already exists on content_tasks — skipping")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        # Revert CHECK constraint to 0052's version (without 'rejected')
        await conn.execute(
            "ALTER TABLE content_tasks "
            "DROP CONSTRAINT IF EXISTS chk_content_tasks_status"
        )
        _previous = (
            "('pending','queued','in_progress','completed','failed','cancelled',"
            "'awaiting_approval','approved','validation_failed','validation_error','published')"
        )
        await conn.execute(
            f"ALTER TABLE content_tasks "
            f"ADD CONSTRAINT chk_content_tasks_status "
            f"CHECK (status IN {_previous})"
        )
        logger.info("Reverted chk_content_tasks_status (removed 'rejected')")

        # Drop metadata column
        await conn.execute(
            "ALTER TABLE content_tasks "
            "DROP COLUMN IF EXISTS metadata"
        )
        logger.info("Dropped metadata column from content_tasks")
