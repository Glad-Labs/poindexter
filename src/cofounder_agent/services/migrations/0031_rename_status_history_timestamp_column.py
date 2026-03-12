"""
Migration 0031: Rename task_status_history.timestamp to created_at and fix type.

Addresses issue #309:
- The column is named 'timestamp', which is a reserved word in SQL and a
  PostgreSQL type name. Using it as a column name requires quoting in raw SQL.
- The column is TIMESTAMP WITHOUT TIME ZONE, making audit entries ambiguous
  on non-UTC servers.

Changes:
1. Rename 'timestamp' column to 'created_at'.
2. Change type to TIMESTAMP WITH TIME ZONE (USING ... AT TIME ZONE 'UTC').
3. Keep DEFAULT NOW() (already set via CURRENT_TIMESTAMP).
4. Drop the old index on 'timestamp' and recreate it on 'created_at'.

Code changes: tasks_db.py references 'timestamp' in INSERT and SELECT statements.
Those are updated to use 'created_at' in the same PR.

Rollback: reverse the rename and type change.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    """Rename task_status_history.timestamp -> created_at and convert to TIMESTAMPTZ."""
    async with pool.acquire() as conn:
        table_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'task_status_history')"
        )
        if not table_exists:
            logger.warning("Table 'task_status_history' does not exist — skipping")
            return

        # Check if column is already renamed
        col_exists = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT FROM information_schema.columns
                WHERE table_name = 'task_status_history' AND column_name = 'timestamp'
            )
            """
        )
        if not col_exists:
            logger.info("Column 'timestamp' already renamed or does not exist — skipping rename")
        else:
            # Rename timestamp -> created_at
            await conn.execute(
                'ALTER TABLE task_status_history RENAME COLUMN "timestamp" TO created_at'
            )
            logger.info("Renamed task_status_history.timestamp -> created_at")

        # Check if type is already TIMESTAMPTZ
        col_type = await conn.fetchval(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_name = 'task_status_history' AND column_name = 'created_at'
            """
        )
        if col_type and col_type != "timestamp with time zone":
            await conn.execute(
                """
                ALTER TABLE task_status_history
                    ALTER COLUMN created_at
                        TYPE TIMESTAMP WITH TIME ZONE
                        USING created_at AT TIME ZONE 'UTC'
                """
            )
            logger.info("Converted task_status_history.created_at to TIMESTAMP WITH TIME ZONE")
        else:
            logger.info("task_status_history.created_at already TIMESTAMPTZ — skipping type change")

        # Drop old index on 'timestamp' column if it exists
        old_idx = await conn.fetchval(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'idx_task_status_history_timestamp'"
        )
        if old_idx:
            await conn.execute("DROP INDEX IF EXISTS idx_task_status_history_timestamp")
            logger.info("Dropped old index idx_task_status_history_timestamp")

        # Recreate index on 'created_at'
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_task_status_history_created_at
                ON task_status_history(created_at DESC)
            """
        )
        logger.info("Created idx_task_status_history_created_at")


async def down(pool) -> None:
    """Reverse rename of task_status_history.created_at -> timestamp."""
    async with pool.acquire() as conn:
        table_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'task_status_history')"
        )
        if not table_exists:
            return

        created_at_exists = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT FROM information_schema.columns
                WHERE table_name = 'task_status_history' AND column_name = 'created_at'
            )
            """
        )
        if created_at_exists:
            await conn.execute(
                'ALTER TABLE task_status_history RENAME COLUMN created_at TO "timestamp"'
            )
            await conn.execute(
                """
                ALTER TABLE task_status_history
                    ALTER COLUMN "timestamp"
                        TYPE TIMESTAMP WITHOUT TIME ZONE
                        USING "timestamp" AT TIME ZONE 'UTC'
                """
            )

        await conn.execute("DROP INDEX IF EXISTS idx_task_status_history_created_at")
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_task_status_history_timestamp
                ON task_status_history("timestamp" DESC)
            """
        )
    logger.info("Reverted task_status_history column rename (0031 down)")
