"""
Migration 0048: Extend task_status_history CHECK constraint to include validation statuses.

Addresses issue #867: get_validation_failures() queries for 'validation_failed' and
'validation_error' statuses, but the CHECK constraint on task_status_history.new_status
(added in migration 0029) does not include them. Any INSERT with these values silently
violates the constraint.

Also extends content_tasks.status to include 'validation_failed'.

Changes:
- Drop and re-create chk_status_history_new_status with validation statuses added
- Drop and re-create chk_status_history_old_status with validation statuses added
- Drop and re-create chk_content_tasks_status with 'validation_failed' added

Rollback: revert to the original constraints from migration 0029.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)

_EXTENDED_TASK_STATUSES = (
    "('pending','queued','in_progress','completed','failed','cancelled',"
    "'awaiting_approval','approved','validation_failed','validation_error')"
)

_ORIGINAL_TASK_STATUSES = (
    "('pending','queued','in_progress','completed','failed','cancelled',"
    "'awaiting_approval','approved')"
)


async def up(pool) -> None:
    """Extend CHECK constraints to allow validation statuses."""
    async with pool.acquire() as conn:
        # --- task_status_history.new_status ---
        await conn.execute(
            "ALTER TABLE task_status_history "
            "DROP CONSTRAINT IF EXISTS chk_status_history_new_status"
        )
        await conn.execute(
            f"ALTER TABLE task_status_history "
            f"ADD CONSTRAINT chk_status_history_new_status "
            f"CHECK (new_status IN {_EXTENDED_TASK_STATUSES})"
        )
        logger.info("Extended chk_status_history_new_status with validation statuses")

        # --- task_status_history.old_status ---
        await conn.execute(
            "ALTER TABLE task_status_history "
            "DROP CONSTRAINT IF EXISTS chk_status_history_old_status"
        )
        await conn.execute(
            f"ALTER TABLE task_status_history "
            f"ADD CONSTRAINT chk_status_history_old_status "
            f"CHECK (old_status IN {_EXTENDED_TASK_STATUSES})"
        )
        logger.info("Extended chk_status_history_old_status with validation statuses")

        # --- content_tasks.status ---
        await conn.execute(
            "ALTER TABLE content_tasks "
            "DROP CONSTRAINT IF EXISTS chk_content_tasks_status"
        )
        await conn.execute(
            f"ALTER TABLE content_tasks "
            f"ADD CONSTRAINT chk_content_tasks_status "
            f"CHECK (status IN {_EXTENDED_TASK_STATUSES})"
        )
        logger.info("Extended chk_content_tasks_status with validation statuses")


async def down(pool) -> None:
    """Revert to original constraints without validation statuses."""
    async with pool.acquire() as conn:
        for table, constraint in [
            ("task_status_history", "chk_status_history_new_status"),
            ("task_status_history", "chk_status_history_old_status"),
            ("content_tasks", "chk_content_tasks_status"),
        ]:
            await conn.execute(
                f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}"
            )

        await conn.execute(
            f"ALTER TABLE task_status_history "
            f"ADD CONSTRAINT chk_status_history_new_status "
            f"CHECK (new_status IN {_ORIGINAL_TASK_STATUSES})"
        )
        await conn.execute(
            f"ALTER TABLE task_status_history "
            f"ADD CONSTRAINT chk_status_history_old_status "
            f"CHECK (old_status IN {_ORIGINAL_TASK_STATUSES})"
        )
        await conn.execute(
            f"ALTER TABLE content_tasks "
            f"ADD CONSTRAINT chk_content_tasks_status "
            f"CHECK (status IN {_ORIGINAL_TASK_STATUSES})"
        )
    logger.info("Reverted status CHECK constraints to original (no validation statuses)")
