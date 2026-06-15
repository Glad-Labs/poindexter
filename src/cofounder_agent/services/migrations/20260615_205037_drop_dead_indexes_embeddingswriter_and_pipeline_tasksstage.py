"""Migration 20260615_205037: drop two zero-scan indexes confirmed dead by live stats.

ISSUE: Glad-Labs/poindexter#701

Two indexes confirmed unused by live pg_stat_user_indexes as of 2026-06-15,
with no code path that would ever hit them:

  idx_embeddings_writer  — btree on embeddings.writer (0 scans, 456 kB).
    The writer column exists but every retrieval path queries by source_table /
    source_id / embedding similarity — never by writer alone. Dead since the
    column was added; now confirmed by 0 lifetime scans.

  idx_pipeline_tasks_stage  — btree on pipeline_tasks.stage (1 scan, 56 kB).
    The `stage` column was populated by the legacy StageRunner, which was
    deleted 2026-05-16 (cleanup sweep Stage 4). No call site sets or queries
    stage post-deletion. The 1 lifetime scan is likely a one-off historical
    query from before the deletion.

Indexes with 0 scans that were NOT dropped (and why):
  cost_logs_pkey / embeddings_pkey / pipeline_tasks_pkey — PRIMARY KEY backing
    indexes. pg_stat_user_indexes.idx_scan only counts SELECT scans; constraint
    enforcement (INSERT/UPDATE uniqueness checks) is not counted, so 0 scans on
    a pkey is expected and safe — do not confuse this with "unused".
  idx_pipeline_tasks_scheduled — partial on (status, scheduled_at) WHERE
    scheduled_at IS NOT NULL; 8 kB. Serves the scheduled publisher even when
    no posts are currently scheduled.

Dropping an index is instant (no table scan, no AccessExclusiveLock on rows).
IF EXISTS guards make the up() body idempotent.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DROP INDEX IF EXISTS idx_embeddings_writer;"
        )
        await conn.execute(
            "DROP INDEX IF EXISTS idx_pipeline_tasks_stage;"
        )
        logger.info(
            "Migration 20260615_205037: dropped idx_embeddings_writer + idx_pipeline_tasks_stage"
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_embeddings_writer ON embeddings (writer);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_stage ON pipeline_tasks (stage);"
        )
        logger.info(
            "Migration 20260615_205037: restored idx_embeddings_writer + idx_pipeline_tasks_stage"
        )
