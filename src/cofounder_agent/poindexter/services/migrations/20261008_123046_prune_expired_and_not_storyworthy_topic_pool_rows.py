"""Migration: prune expired and not_storyworthy topic_pool rows

The topic_pool retention policy pruned only ``status = 'pooled'``. Two
statuses now need the same 30-day horizon:

- ``not_storyworthy``: internal_rag records the distiller's non-story verdict
  as a pool row so the snippet is not judged again every 30 minutes. The
  verdict only has to outlive the 30-day snippet selection window.
- ``expired``: 1,503 reworded internal_rag repeats were expired by
  ``20261008_032728`` and would otherwise stay forever.

The baseline seed carries the new filter for fresh installs. This converges an
existing row, and only when it still holds the shipped default, so an
operator's own filter is left alone. Idempotent.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_POLICY_ID = "4d1be84b-4dfc-41db-9bfa-4d919011c372"
_OLD = "status = 'pooled'"
_NEW = "status IN ('pooled', 'expired', 'not_storyworthy')"


async def up(pool) -> None:
    async with pool.acquire() as conn:
        status = await conn.execute(
            "UPDATE retention_policies SET filter_sql = $1 "
            "WHERE id = $2::uuid AND filter_sql = $3",
            _NEW, _POLICY_ID, _OLD,
        )
    logger.info("Migration prune_expired_and_not_storyworthy_topic_pool_rows: %s", status)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE retention_policies SET filter_sql = $1 "
            "WHERE id = $2::uuid AND filter_sql = $3",
            _OLD, _POLICY_ID, _NEW,
        )
