"""Migration 20260711_000028_add_topic_candidates_grounding_ref_column: add topic_candidates grounding_ref column

ISSUE: Glad-Labs/poindexter#822

Adds ``topic_candidates.grounding_ref jsonb`` — the internal-corpus match
that justified a *grounded* external topic candidate (poindexter#822). The
column is nullable: internal candidates and ungrounded / fail-open external
candidates carry NULL. ``TopicBatchService._write_batch`` writes it, and
``_handoff_to_pipeline`` threads it into the pipeline task metadata so the
writer can later open on the first-party angle. Idempotent ADD COLUMN IF NOT
EXISTS — a no-op on fresh installs where the baseline already includes the
column, a real add on prod. stdlib-only so migrations-smoke applies it
without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration — add the nullable grounding_ref column."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE topic_candidates "
            "ADD COLUMN IF NOT EXISTS grounding_ref jsonb"
        )
    logger.info(
        "Migration add_topic_candidates_grounding_ref_column: column ready"
    )


async def down(pool) -> None:
    """Revert — drop the column."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE topic_candidates DROP COLUMN IF EXISTS grounding_ref"
        )
    logger.info(
        "Migration add_topic_candidates_grounding_ref_column: reverted"
    )
