"""
Migration 0028: Standardise content_tasks timestamp columns to TIMESTAMP WITH TIME ZONE.

Addresses issue #246: content_tasks mixes TIMESTAMP WITH/WITHOUT TIME ZONE across
its columns (created_at, updated_at, completed_at, approval_timestamp are WITHOUT;
started_at, published_at are WITH). This makes cross-column interval arithmetic
unreliable and exposes timestamps to silent drift if the server timezone is not UTC.

Changes:
- Convert created_at, updated_at, completed_at, approval_timestamp to
  TIMESTAMP WITH TIME ZONE (USING ... AT TIME ZONE 'UTC' to preserve existing values).
- Add NOT NULL + DEFAULT NOW() to updated_at so the stale-task sweep
  (WHERE updated_at < NOW() - INTERVAL '30 min') never misses tasks with NULL updated_at.

Rollback: convert changed columns back to TIMESTAMP WITHOUT TIME ZONE and
drop the updated_at default/not-null constraint.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    """Standardise content_tasks timestamp columns to TIMESTAMPTZ."""
    async with pool.acquire() as conn:
        table_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'content_tasks')"
        )
        if not table_exists:
            logger.warning("Table 'content_tasks' does not exist — skipping timestamp migration")
            return

        # Check current types to be idempotent
        col_types = await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = 'content_tasks'
              AND column_name IN ('created_at', 'updated_at', 'completed_at', 'approval_timestamp')
            """
        )
        needs_fix = {
            row["column_name"]
            for row in col_types
            if row["data_type"] == "timestamp without time zone"
        }

        if not needs_fix:
            logger.info("content_tasks timestamp columns already TIMESTAMPTZ — no action needed")
            return

        logger.info(f"Fixing timestamp columns: {sorted(needs_fix)}")

        await conn.execute(
            """
            ALTER TABLE content_tasks
                ALTER COLUMN created_at
                    TYPE TIMESTAMP WITH TIME ZONE
                    USING COALESCE(created_at, NOW()) AT TIME ZONE 'UTC',
                ALTER COLUMN updated_at
                    TYPE TIMESTAMP WITH TIME ZONE
                    USING COALESCE(updated_at, NOW()) AT TIME ZONE 'UTC',
                ALTER COLUMN updated_at
                    SET NOT NULL,
                ALTER COLUMN updated_at
                    SET DEFAULT NOW(),
                ALTER COLUMN completed_at
                    TYPE TIMESTAMP WITH TIME ZONE
                    USING completed_at AT TIME ZONE 'UTC',
                ALTER COLUMN approval_timestamp
                    TYPE TIMESTAMP WITH TIME ZONE
                    USING approval_timestamp AT TIME ZONE 'UTC'
            """
        )

        logger.info("content_tasks timestamp columns standardised to TIMESTAMP WITH TIME ZONE")


async def down(pool) -> None:
    """Revert content_tasks timestamp columns to TIMESTAMP WITHOUT TIME ZONE."""
    async with pool.acquire() as conn:
        table_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'content_tasks')"
        )
        if not table_exists:
            return

        await conn.execute("ALTER TABLE content_tasks ALTER COLUMN updated_at DROP DEFAULT")
        await conn.execute("ALTER TABLE content_tasks ALTER COLUMN updated_at DROP NOT NULL")
        await conn.execute(
            """
            ALTER TABLE content_tasks
                ALTER COLUMN created_at
                    TYPE TIMESTAMP WITHOUT TIME ZONE
                    USING created_at AT TIME ZONE 'UTC',
                ALTER COLUMN updated_at
                    TYPE TIMESTAMP WITHOUT TIME ZONE
                    USING updated_at AT TIME ZONE 'UTC',
                ALTER COLUMN completed_at
                    TYPE TIMESTAMP WITHOUT TIME ZONE
                    USING completed_at AT TIME ZONE 'UTC',
                ALTER COLUMN approval_timestamp
                    TYPE TIMESTAMP WITHOUT TIME ZONE
                    USING approval_timestamp AT TIME ZONE 'UTC'
            """
        )
        logger.info("Reverted content_tasks timestamp columns to TIMESTAMP WITHOUT TIME ZONE")
