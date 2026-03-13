"""
Migration 0040: Align cost_logs.task_id type with content_tasks.task_id.

Issue #465: cost_logs.task_id is typed as UUID in the live DB while
content_tasks.task_id is VARCHAR(255).  Every JOIN between these tables
forces PostgreSQL to cast one side implicitly, which prevents the query
planner from using idx_cost_logs_task_id and causes a sequential scan on
cost_logs for every analytics query.

Fix: change cost_logs.task_id to VARCHAR(255) to match the FK source
column in content_tasks.  Indexes are dropped first, then the column is
cast to text, then re-created.

Safety check: if the column is already VARCHAR the migration is a no-op.

Rollback: the down() migration casts back to UUID (only safe when all
values are valid UUID strings).
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    """Change cost_logs.task_id from UUID to VARCHAR(255)."""
    async with pool.acquire() as conn:
        # Guard: check if cost_logs table exists
        table_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'cost_logs')"
        )
        if not table_exists:
            logger.warning("Table 'cost_logs' does not exist — skipping migration 0040")
            return

        # Check current data type — skip if already VARCHAR
        current_type = await conn.fetchval(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_name = 'cost_logs' AND column_name = 'task_id'
            """
        )
        if current_type and current_type.lower() in ("character varying", "text"):
            logger.info(
                f"cost_logs.task_id is already {current_type} — migration 0040 is a no-op"
            )
            return

        logger.info(
            f"cost_logs.task_id current type: {current_type!r} — migrating to VARCHAR(255)"
        )

        # Drop indexes first (they cannot be rebuilt on a different type in the same tx)
        await conn.execute("DROP INDEX IF EXISTS idx_cost_logs_task_id")
        await conn.execute("DROP INDEX IF EXISTS idx_cost_logs_task_phase")
        logger.info("Dropped cost_logs task_id indexes")

        # Alter column: cast UUID values to their text representation
        await conn.execute(
            "ALTER TABLE cost_logs ALTER COLUMN task_id TYPE VARCHAR(255) USING task_id::text"
        )
        logger.info("Altered cost_logs.task_id to VARCHAR(255)")

        # Recreate indexes on the new type
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cost_logs_task_id ON cost_logs(task_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cost_logs_task_phase ON cost_logs(task_id, phase)"
        )
        logger.info("Recreated cost_logs task_id indexes")

        logger.info(
            "Migration 0040 complete: cost_logs.task_id is now VARCHAR(255), "
            "index use on cost aggregation queries restored."
        )


async def down(pool) -> None:
    """Revert cost_logs.task_id from VARCHAR(255) back to UUID.

    WARNING: This will fail if any task_id values are not valid UUID strings
    (e.g. legacy test rows).  Verify with:
        SELECT task_id FROM cost_logs WHERE task_id !~ '^[0-9a-f-]{36}$';
    before running the rollback.
    """
    async with pool.acquire() as conn:
        table_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'cost_logs')"
        )
        if not table_exists:
            logger.warning("Table 'cost_logs' does not exist — skipping rollback 0040")
            return

        await conn.execute("DROP INDEX IF EXISTS idx_cost_logs_task_id")
        await conn.execute("DROP INDEX IF EXISTS idx_cost_logs_task_phase")

        await conn.execute(
            "ALTER TABLE cost_logs ALTER COLUMN task_id TYPE UUID USING task_id::uuid"
        )
        logger.info("Rolled back 0040: cost_logs.task_id restored to UUID")

        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cost_logs_task_id ON cost_logs(task_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cost_logs_task_phase ON cost_logs(task_id, phase)"
        )
        logger.info("Recreated cost_logs task_id indexes (UUID type)")
