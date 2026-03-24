"""
Migration 0052: Add 'published' to content_tasks status CHECK constraint.

The publish handler sets status='published' but the CHECK constraint
from migration 0048 only allows: pending, queued, in_progress, completed,
failed, cancelled, awaiting_approval, approved, validation_failed, validation_error.

This migration drops and re-creates the constraint with 'published' added.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)

_EXTENDED_STATUSES = (
    "('pending','queued','in_progress','completed','failed','cancelled',"
    "'awaiting_approval','approved','validation_failed','validation_error','published')"
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE content_tasks "
            "DROP CONSTRAINT IF EXISTS chk_content_tasks_status"
        )
        await conn.execute(
            f"ALTER TABLE content_tasks "
            f"ADD CONSTRAINT chk_content_tasks_status "
            f"CHECK (status IN {_EXTENDED_STATUSES})"
        )
        logger.info("Added 'published' to chk_content_tasks_status")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE content_tasks "
            "DROP CONSTRAINT IF EXISTS chk_content_tasks_status"
        )
        # Revert to 0048's constraint (without 'published')
        _original = (
            "('pending','queued','in_progress','completed','failed','cancelled',"
            "'awaiting_approval','approved','validation_failed','validation_error')"
        )
        await conn.execute(
            f"ALTER TABLE content_tasks "
            f"ADD CONSTRAINT chk_content_tasks_status "
            f"CHECK (status IN {_original})"
        )
        logger.info("Reverted chk_content_tasks_status (removed 'published')")
