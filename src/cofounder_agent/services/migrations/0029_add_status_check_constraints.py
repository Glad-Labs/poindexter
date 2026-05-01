"""
Migration 0029: Add CHECK constraints to enum-like status columns.

Addresses issue #260: content_tasks.status, stage, approval_status, publish_mode
have no CHECK constraints — any string is accepted, so typos and stale code paths
produce rows permanently invisible to status-based queries.

The same gap exists on task_status_history.old_status and new_status.

Changes:
- Add CHECK constraints to content_tasks: status, stage, approval_status, publish_mode.
- Add CHECK constraints to task_status_history: old_status, new_status.

All constraints use IF NOT EXISTS equivalents (drop-then-add pattern with
existence checks) to be idempotent.

Rollback: drop all constraints added by this migration.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)

_CONTENT_TASKS_CONSTRAINTS = {
    "chk_content_tasks_status": (
        "status",
        # awaiting_approval and approved are written by task_executor, unified_orchestrator,
        # content_router_service, and approval_routes — must be included (issue #764).
        "('pending','queued','in_progress','completed','failed','cancelled','awaiting_approval','approved')",
    ),
    "chk_content_tasks_approval_status": (
        "approval_status",
        "('pending','approved','rejected')",
    ),
    "chk_content_tasks_publish_mode": (
        "publish_mode",
        "('draft','published')",
    ),
    "chk_content_tasks_stage": (
        "stage",
        "('pending','research','outline','draft','qa','image','publish','completed')",
    ),
}

_STATUS_HISTORY_CONSTRAINTS = {
    "chk_status_history_old_status": (
        "old_status",
        # awaiting_approval and approved are written by log_status_change (issue #764).
        "('pending','queued','in_progress','completed','failed','cancelled','awaiting_approval','approved')",
    ),
    "chk_status_history_new_status": (
        "new_status",
        "('pending','queued','in_progress','completed','failed','cancelled','awaiting_approval','approved')",
    ),
}


async def _constraint_exists(conn, table: str, name: str) -> bool:
    return bool(
        await conn.fetchval(
            """
            SELECT 1 FROM information_schema.table_constraints
            WHERE table_name = $1 AND constraint_name = $2
            """,
            table,
            name,
        )
    )


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    """Add CHECK constraints for status columns on content_tasks and task_status_history."""
    async with pool.acquire() as conn:
        # --- content_tasks ---
        if not await _table_exists(conn, "content_tasks"):
            logger.warning("Table 'content_tasks' does not exist — skipping")
        else:
            for constraint_name, (column, allowed) in _CONTENT_TASKS_CONSTRAINTS.items():
                if await _constraint_exists(conn, "content_tasks", constraint_name):
                    logger.debug(f"Constraint {constraint_name} already exists — skipping")
                    continue

                # Normalise any out-of-range values to NULL before adding constraint
                # to avoid constraint-violation on existing dirty data.
                await conn.execute(
                    f"""
                    UPDATE content_tasks
                    SET {column} = NULL
                    WHERE {column} IS NOT NULL
                      AND {column} NOT IN {allowed}
                    """  # nosec B608  # column + allowed come from _CONTENT_TASKS_CONSTRAINTS module-level constant
                )

                await conn.execute(
                    f"""
                    ALTER TABLE content_tasks
                        ADD CONSTRAINT {constraint_name}
                        CHECK ({column} IN {allowed})
                    """  # nosec B608  # constraint_name + column + allowed come from _CONTENT_TASKS_CONSTRAINTS module-level constant
                )
                logger.info(f"Added constraint {constraint_name} to content_tasks")

        # --- task_status_history ---
        if not await _table_exists(conn, "task_status_history"):
            logger.warning("Table 'task_status_history' does not exist — skipping")
        else:
            for constraint_name, (column, allowed) in _STATUS_HISTORY_CONSTRAINTS.items():
                if await _constraint_exists(conn, "task_status_history", constraint_name):
                    logger.debug(f"Constraint {constraint_name} already exists — skipping")
                    continue

                await conn.execute(
                    f"""
                    UPDATE task_status_history
                    SET {column} = NULL
                    WHERE {column} IS NOT NULL
                      AND {column} NOT IN {allowed}
                    """  # nosec B608  # column + allowed come from _STATUS_HISTORY_CONSTRAINTS module-level constant
                )

                await conn.execute(
                    f"""
                    ALTER TABLE task_status_history
                        ADD CONSTRAINT {constraint_name}
                        CHECK ({column} IN {allowed})
                    """  # nosec B608  # constraint_name + column + allowed come from _STATUS_HISTORY_CONSTRAINTS module-level constant
                )
                logger.info(f"Added constraint {constraint_name} to task_status_history")


async def down(pool) -> None:
    """Remove CHECK constraints added by this migration."""
    async with pool.acquire() as conn:
        for name in _CONTENT_TASKS_CONSTRAINTS:
            await conn.execute(
                f"ALTER TABLE content_tasks DROP CONSTRAINT IF EXISTS {name}"
            )
        for name in _STATUS_HISTORY_CONSTRAINTS:
            await conn.execute(
                f"ALTER TABLE task_status_history DROP CONSTRAINT IF EXISTS {name}"
            )
    logger.info("Removed status CHECK constraints from content_tasks and task_status_history")
