"""Migration 20260527_183209_add_retry_count_to_pipeline_tasks_for_stale_sweep.

Cycle-5 audit finding (#253): ``sweep_stale_tasks`` in
``services/tasks_db.py`` reads + writes the ``content_tasks`` *view*.
That view is a JOIN of ``pipeline_tasks`` x ``pipeline_versions`` —
PostgreSQL refuses UPDATE on non-trivially-derivable views, and the
``task_metadata`` column the sweeper tries to update is a computed
expression (``pv.stage_data->'task_metadata'``). The sweeper has been
silently failing on every fire — stale rows pile up in ``in_progress``
forever, retry counts never increment, and the operator never gets the
"max retries exceeded -> mark failed" signal.

Fix: add a real ``retry_count`` column directly on ``pipeline_tasks``
so the rewritten sweeper can read + UPDATE the table without going
through a view. The companion code change in PR moves the sweeper to
target ``pipeline_tasks`` directly and increments the new column.

Why an integer column and not a JSON field: the old code stored
``retry_count`` inside ``pipeline_versions.stage_data->'task_metadata'``
which is the wrong scope (versions are per-attempt, retries span all
versions for one task) and forced JSON arithmetic instead of an atom.

Tested invariant: NOT NULL DEFAULT 0 so every existing row starts at
0 retries; consumer code does the bounds check.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE pipeline_tasks
                ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0
            """
        )
        logger.info(
            "Migration 20260527_183209_add_retry_count_to_pipeline_tasks_for_stale_sweep: "
            "applied (retry_count INTEGER NOT NULL DEFAULT 0 added)"
        )


async def down(pool) -> None:
    """Revert: drop the column. Losing the retry_count is acceptable on
    rollback - every row resets to 0 retries via the column default
    when re-added.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS retry_count"
        )
        logger.info(
            "Migration 20260527_183209_add_retry_count_to_pipeline_tasks_for_stale_sweep down: "
            "reverted (column dropped)"
        )
