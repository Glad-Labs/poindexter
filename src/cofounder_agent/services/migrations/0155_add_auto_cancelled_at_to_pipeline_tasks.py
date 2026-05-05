"""Migration 0155: pipeline_tasks.auto_cancelled_at — pipeline_events split phase 2.

The brain daemon's stale-task sweeper writes one ``task.auto_cancelled``
row to ``pipeline_events`` per cancellation, and ``metrics_exporter``
reads ``COUNT(*)`` from that table to populate the
``AUTO_CANCELLED_TOTAL`` Prometheus gauge. The reason it had to live
in ``pipeline_events`` (not in-memory) is restart survivability — a
process-local counter resets to zero on every brain restart, breaking
``rate()`` queries on Grafana panels for several minutes.

Adding a column on ``pipeline_tasks`` itself eliminates the cross-table
denormalization. The brain already runs an UPDATE on the row to flip
``status='failed'``; setting ``auto_cancelled_at = NOW()`` in the same
UPDATE is one cheap append, and ``COUNT(*) WHERE auto_cancelled_at IS
NOT NULL`` is the new metric query.

Backfill from existing ``error_message`` content — the sweeper has
been writing ``'Auto-cancelled: stuck in_progress > Nm'`` for every
auto-cancel since GH-90 shipped, so the historical signal can be
recovered without losing the gauge baseline.

Spec: poindexter#366. Phase 2 of the pipeline_events split-migration.
After this lands and the brain + metrics_exporter PRs ship, the only
remaining ``pipeline_events`` writers are template_runner progress
events (Langfuse-replaceable, Phase 4) + the dead-write in
``scheduling_service`` (Phase 3 cleanup).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE pipeline_tasks
            ADD COLUMN IF NOT EXISTS auto_cancelled_at TIMESTAMPTZ
            """
        )
        # Partial index — fast COUNT(*) and the column is rarely-non-NULL.
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_auto_cancelled
                ON pipeline_tasks (auto_cancelled_at)
                WHERE auto_cancelled_at IS NOT NULL
            """
        )
        # Backfill: tasks the sweeper auto-cancelled previously have
        # ``error_message`` starting with 'Auto-cancelled:' and
        # ``status='failed'``. Use ``updated_at`` as the cancel time —
        # the sweeper's UPDATE bumped it in the same statement.
        result = await conn.execute(
            """
            UPDATE pipeline_tasks
               SET auto_cancelled_at = updated_at
             WHERE status = 'failed'
               AND error_message LIKE 'Auto-cancelled:%'
               AND auto_cancelled_at IS NULL
            """
        )
        logger.info(
            "Migration 0155: backfilled auto_cancelled_at for "
            "previously cancelled tasks: %s",
            result,
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DROP INDEX IF EXISTS idx_pipeline_tasks_auto_cancelled"
        )
        await conn.execute(
            "ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS auto_cancelled_at"
        )
        logger.info("Migration 0155 down: removed auto_cancelled_at column")
