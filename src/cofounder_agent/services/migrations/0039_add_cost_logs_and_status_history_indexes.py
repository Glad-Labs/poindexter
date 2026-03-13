"""
Migration 0039: Add missing performance indexes.

Issue #506: cost_logs.created_at has no index, so every cost aggregation
query (daily/weekly/monthly windows) performs a full sequential scan.

Issue #527: task_status_history.task_id FK column has no index, causing
sequential scans on every status lookup by task_id.

Changes:
1. CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cost_logs_created_at
       ON cost_logs(created_at DESC)
2. CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_task_status_history_task_id
       ON task_status_history(task_id)

Note: CONCURRENTLY cannot run inside a transaction block. asyncpg executes
each conn.execute() in autocommit mode when not inside an explicit
transaction, so CONCURRENTLY is safe here.

Rollback: drop both indexes.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    """Add indexes on cost_logs.created_at and task_status_history.task_id."""
    async with pool.acquire() as conn:
        # --- cost_logs.created_at ---
        cost_logs_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'cost_logs')"
        )
        if cost_logs_exists:
            await conn.execute(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cost_logs_created_at
                    ON cost_logs(created_at DESC)
                """
            )
            logger.info("Index idx_cost_logs_created_at ensured")
        else:
            logger.warning("Table 'cost_logs' does not exist — skipping idx_cost_logs_created_at")

        # --- task_status_history.task_id ---
        history_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'task_status_history')"
        )
        if history_exists:
            await conn.execute(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_task_status_history_task_id
                    ON task_status_history(task_id)
                """
            )
            logger.info("Index idx_task_status_history_task_id ensured")
        else:
            logger.warning(
                "Table 'task_status_history' does not exist — skipping idx_task_status_history_task_id"
            )


async def down(pool) -> None:
    """Drop the indexes added by this migration."""
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_cost_logs_created_at")
        await conn.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_task_status_history_task_id")
        logger.info("Rolled back 0039: dropped cost_logs and task_status_history indexes")
