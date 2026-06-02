"""Migration 20260602_203200: drop redundant idx_posts_slug index

ISSUE: Glad-Labs/poindexter#627

The ``idx_posts_slug`` btree index on ``posts(slug)`` is redundant: the
``posts_slug_key`` UNIQUE constraint already maintains a unique btree
index over the same column, so any planner lookup on ``slug`` is served
by the constraint's index. Keeping both means every ``posts`` write pays
to maintain two identical structures for zero read benefit. This drops
the duplicate. Idempotent ``DROP INDEX IF EXISTS``; ``down()`` is a no-op
because the index is pure redundancy (the UNIQUE constraint still covers
``slug``).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Drop the redundant idx_posts_slug index.

    Safe to re-run — ``IF EXISTS`` no-ops when the index is already gone
    (or was never created, e.g. on a DB that started after this PR).
    """
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS idx_posts_slug")
    logger.info("drop_redundant_idx_posts_slug_index: dropped idx_posts_slug (if present)")


async def down(pool) -> None:
    # No-op: idx_posts_slug duplicated the posts_slug_key UNIQUE index, so
    # recreating it would just reintroduce the redundancy. Lookups on
    # ``slug`` remain covered by the UNIQUE constraint's index.
    logger.info("drop_redundant_idx_posts_slug_index down: no-op (redundant index)")
