"""Migration 20260808_035641_drop_orphan_pipeline_tasks_scheduled_at_column: drop orphan pipeline_tasks scheduled_at column

ISSUE: console "Schedule" button never scheduled anything.

``pipeline_tasks.scheduled_at`` was a write-only column. It was set in
exactly one place — the ``publish_at`` branch of ``POST
/api/tasks/{id}/approve`` — cleared to NULL in two (``unapprove_task``
and that same branch's rollback), and **read by nothing**: not by
``scheduled_publisher``, not by any job, dashboard, CLI command or MCP
tool.

The publish queue has always been keyed on ``posts``:
``scheduled_publisher`` polls for ``status='scheduled' AND published_at
<= NOW()``, and ``services/scheduling_service.py`` owns that pair. So an
approve carrying ``publish_at`` stamped a timestamp nobody honoured,
skipped the ``stage_only`` bridge that creates the ``posts`` row at all,
and reported success — the post simply never published. Zero rows ever
carried a non-NULL value, so there is nothing to migrate.

The approve route now routes ``publish_at`` through
``scheduling_service.assign_slot``, making ``posts`` the single source of
truth for a publish slot. This drops the decoy so the next reader can't
mistake it for one. Deliberately NOT kept as a denormalised mirror: it
would drift the moment an operator used ``PATCH /api/scheduling/shift``
or ``DELETE /api/scheduling``, and a schedule column that lies is worse
than no column.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Drop the orphan column and its partial index."""
    async with pool.acquire() as conn:
        # The index is defined ON the column, so DROP COLUMN would take it
        # with it — dropped explicitly first so the intent is legible and
        # the statement is safe to re-run against a partially-applied DB.
        await conn.execute(
            "DROP INDEX IF EXISTS idx_pipeline_tasks_scheduled"
        )
        await conn.execute(
            "ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS scheduled_at"
        )
    logger.info(
        "Migration drop_orphan_pipeline_tasks_scheduled_at_column: applied "
        "(pipeline_tasks.scheduled_at + idx_pipeline_tasks_scheduled dropped)"
    )


async def down(pool) -> None:
    """Restore the column + index shape.

    Restores structure only. The column carried no data on any known
    install (verified: 0 non-NULL rows on prod at drop time) and had no
    reader, so there is nothing to backfill — re-adding it reproduces the
    orphan exactly as it was.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks "
            "ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMP WITH TIME ZONE"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_scheduled "
            "ON public.pipeline_tasks USING btree (status, scheduled_at) "
            "WHERE (scheduled_at IS NOT NULL)"
        )
    logger.info(
        "Migration drop_orphan_pipeline_tasks_scheduled_at_column: reverted"
    )
