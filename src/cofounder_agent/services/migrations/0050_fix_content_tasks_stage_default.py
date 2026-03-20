"""
Migration 0050: Fix content_tasks.stage nullable with no DEFAULT.

Addresses issue #914: after schema consolidation the `stage` column on
content_tasks is nullable with no DEFAULT value.  New rows get NULL stage,
which breaks stage-based queries and violates the CHECK constraint added
in migration 0029.

Allowed values (from migration 0029 chk_content_tasks_stage):
  ('pending','research','outline','draft','qa','image','publish','completed')

Changes:
- Backfill any NULL stage values to 'pending'
- Set column DEFAULT to 'pending'
- Add NOT NULL constraint

Rollback:
- Drop the NOT NULL constraint (make column nullable again)
- Drop the DEFAULT
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    """Backfill NULL stages, set DEFAULT 'pending', and add NOT NULL constraint."""
    async with pool.acquire() as conn:
        # Check table exists
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'content_tasks')"
        )
        if not exists:
            logger.warning("Table 'content_tasks' does not exist — skipping")
            return

        # Check column exists
        col_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'content_tasks' AND column_name = 'stage')"
        )
        if not col_exists:
            logger.warning("Column content_tasks.stage does not exist — skipping")
            return

        # Check if already NOT NULL (idempotent)
        is_nullable = await conn.fetchval(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'content_tasks' AND column_name = 'stage'"
        )

        # Step 1: Backfill any NULL stage values to 'pending'
        result = await conn.execute(
            "UPDATE content_tasks SET stage = 'pending' WHERE stage IS NULL"
        )
        logger.info(f"Backfilled NULL stage values to 'pending': {result}")

        # Step 2: Set DEFAULT to 'pending'
        await conn.execute(
            "ALTER TABLE content_tasks ALTER COLUMN stage SET DEFAULT 'pending'"
        )
        logger.info("Set DEFAULT 'pending' on content_tasks.stage")

        # Step 3: Add NOT NULL constraint (skip if already NOT NULL)
        if is_nullable == "YES":
            await conn.execute(
                "ALTER TABLE content_tasks ALTER COLUMN stage SET NOT NULL"
            )
            logger.info("Added NOT NULL constraint to content_tasks.stage")
        else:
            logger.debug("content_tasks.stage is already NOT NULL — skipping")


async def down(pool) -> None:
    """Revert: drop NOT NULL constraint and remove DEFAULT from content_tasks.stage."""
    async with pool.acquire() as conn:
        col_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'content_tasks' AND column_name = 'stage')"
        )
        if not col_exists:
            logger.warning("Column content_tasks.stage does not exist — skipping")
            return

        await conn.execute(
            "ALTER TABLE content_tasks ALTER COLUMN stage DROP NOT NULL"
        )
        logger.info("Dropped NOT NULL constraint from content_tasks.stage")

        await conn.execute(
            "ALTER TABLE content_tasks ALTER COLUMN stage DROP DEFAULT"
        )
        logger.info("Dropped DEFAULT from content_tasks.stage")
