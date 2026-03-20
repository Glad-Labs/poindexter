"""
Migration 0044: Add missing index on workflow_executions.owner_id.

Addresses issue #707: workflow history queries filter exclusively by owner_id
but no index exists on that column in workflow_executions — every history page
load performs a full sequential scan.

Note: workflow_history.py references 'user_id' in queries but the physical column
in workflow_executions is 'owner_id'. This migration indexes the correct column
(owner_id) and a separate code fix is needed to align the service query column names.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    """Add index on workflow_executions.owner_id."""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "workflow_executions"):
            logger.warning("Table 'workflow_executions' does not exist — skipping")
            return

        # Check if column exists (it's owner_id, not user_id)
        col_exists = await conn.fetchval(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'workflow_executions' AND column_name = 'owner_id'
            """
        )
        if not col_exists:
            logger.warning("workflow_executions.owner_id column not found — skipping")
            return

        idx_exists = await conn.fetchval(
            """
            SELECT 1 FROM pg_indexes
            WHERE tablename = 'workflow_executions'
              AND indexname = 'idx_workflow_executions_owner_id'
            """
        )
        if idx_exists:
            logger.debug("idx_workflow_executions_owner_id already exists — skipping")
            return

        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_workflow_executions_owner_id
            ON workflow_executions (owner_id)
            """
        )
        logger.info("Created idx_workflow_executions_owner_id on workflow_executions.owner_id")


async def down(pool) -> None:
    """Drop the index added by this migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DROP INDEX IF EXISTS idx_workflow_executions_owner_id"
        )
    logger.info("Dropped idx_workflow_executions_owner_id")
