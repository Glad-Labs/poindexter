"""Migration 20260615_202637: enforce pipeline_tasks.status values + app_settings.value NOT NULL.

ISSUE: Glad-Labs/poindexter#700

Zero CHECK constraints on enum-like columns meant a typo'd status write would
strand a task invisibly — it would never match the 'pending'/'rejected_retry'
claim filter, never error, and just sit there forever. Adds a CHECK constraint
covering all 15 live-or-code-reachable status values using NOT VALID + VALIDATE
to avoid a long table lock on the existing ~1 700 rows.

app_settings.value is nullable in the schema despite '' being the documented
unset sentinel and NULL crashing consumers. 0 NULLs exist on prod today, so the
ALTER COLUMN ... SET NOT NULL is a free no-op scan.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# All status values either present in the live DB or written by application code.
# Adding a new status requires a one-line migration extending this list rather
# than a silent string drift. Verified against prod 2026-06-15.
_VALID_STATUSES = (
    "pending",
    "in_progress",
    "approved",
    "awaiting_approval",
    "awaiting_gate",
    "rejected",
    "rejected_retry",
    "rejected_final",
    "failed",
    "completed",
    "published",
    "cancelled",
    "dry_run",
    "superseded",
    "archived",
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # 1. Disallow NULL in app_settings.value — '' is the documented unset
        #    sentinel; NULL silently crashes consumers. 0 NULLs exist on prod
        #    so this is a metadata-only change (no row scan).
        await conn.execute(
            "ALTER TABLE app_settings ALTER COLUMN value SET NOT NULL;"
        )

        # 2. Add the status CHECK without holding an AccessExclusiveLock on
        #    existing rows. NOT VALID skips the historical scan and lets
        #    concurrent writes proceed; VALIDATE then acquires only a
        #    ShareUpdateExclusiveLock to verify the existing rows.
        status_list = ", ".join(f"'{s}'" for s in _VALID_STATUSES)
        await conn.execute(
            f"""
            ALTER TABLE pipeline_tasks
                ADD CONSTRAINT pipeline_tasks_status_check
                CHECK (status IN ({status_list}))
                NOT VALID;
            """
        )
        await conn.execute(
            "ALTER TABLE pipeline_tasks VALIDATE CONSTRAINT pipeline_tasks_status_check;"
        )

        logger.info(
            "Migration 20260615_202637: applied status CHECK (%d values) + "
            "app_settings.value NOT NULL",
            len(_VALID_STATUSES),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks DROP CONSTRAINT IF EXISTS pipeline_tasks_status_check;"
        )
        await conn.execute(
            "ALTER TABLE app_settings ALTER COLUMN value DROP NOT NULL;"
        )
        logger.info("Migration 20260615_202637: reverted")
