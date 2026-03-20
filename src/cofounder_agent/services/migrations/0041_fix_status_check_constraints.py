"""
Migration 0041: Fix status CHECK constraints to include awaiting_approval and approved.

Migration 0029 incorrectly omitted 'awaiting_approval' and 'approved' from the
content_tasks.status and task_status_history.old_status/new_status allowed lists.
Those values are actively written by:
  - task_executor.py
  - unified_orchestrator.py
  - content_router_service.py
  - approval_routes.py

On environments where 0029 has already run, the constraints exist with the wrong
allowed list. This migration drops the four affected constraints and re-adds them
with the complete allowed list (issue #764).

The two constraints on approval_status, publish_mode, and stage are not changed.

Rollback: drops the corrected constraints (leaves the table without those constraints).
"""

from services.logger_config import get_logger

logger = get_logger(__name__)

# The complete set of allowed status values — including the two that were missing.
_FULL_STATUS_VALUES = (
    "'pending','queued','in_progress','completed','failed','cancelled',"
    "'awaiting_approval','approved'"
)

_CONSTRAINTS_TO_FIX = {
    "content_tasks": {
        "chk_content_tasks_status": (
            "status",
            f"({_FULL_STATUS_VALUES})",
        ),
    },
    "task_status_history": {
        "chk_status_history_old_status": (
            "old_status",
            f"({_FULL_STATUS_VALUES})",
        ),
        "chk_status_history_new_status": (
            "new_status",
            f"({_FULL_STATUS_VALUES})",
        ),
    },
}


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    """Drop the incorrect status constraints and re-add them with the full value list."""
    async with pool.acquire() as conn:
        for table, constraints in _CONSTRAINTS_TO_FIX.items():
            if not await _table_exists(conn, table):
                logger.warning(f"Table '{table}' does not exist — skipping")
                continue

            for constraint_name, (column, allowed) in constraints.items():
                # Drop the old constraint if it exists (may have the wrong value list).
                await conn.execute(
                    f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint_name}"
                )

                # Add the corrected constraint.
                await conn.execute(
                    f"""
                    ALTER TABLE {table}
                        ADD CONSTRAINT {constraint_name}
                        CHECK ({column} IN {allowed})
                    """
                )
                logger.info(
                    f"Re-added constraint {constraint_name} on {table}.{column} "
                    f"with full status value list"
                )


async def down(pool) -> None:
    """Remove the constraints added by this migration (leaves the columns unconstrained)."""
    async with pool.acquire() as conn:
        for table, constraints in _CONSTRAINTS_TO_FIX.items():
            for constraint_name in constraints:
                await conn.execute(
                    f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint_name}"
                )
    logger.info("Removed corrected status CHECK constraints (migration 0041 rollback)")
