"""One-off: return topic_pool rows leaked by batch refresh/expiry to 'pooled'.

poindexter#1042. ``TopicBatchService`` flipped pool rows to ``batched`` when
they won a ranking, but a batch **refresh** deleted the previous candidate
set wholesale and an **expired** batch kept its candidates' rows batched —
so a row that lost a re-rank, or sat in a batch nobody resolved, stayed
``batched`` forever with no candidate and no task. Prod on 2026-09-08 held
1,002 such rows (internal_rag 512, hackernews 384, web_search 54, devto 29,
rss 23), and the leak took the best-scored fresh rows first: every
``search_autocomplete`` row — the only demand-measuring source — was burned
in a single refresh.

The service now re-pools on refresh and on reject (same PR). This migration
is the backlog: rows ingested within the pool retention window (30 days)
that are ``batched`` but referenced by no candidate in either table and by
no task with their title. Older rows are past the TTL and left for the
retention policy. Idempotent — a second run matches nothing.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_REPOOL = """
    UPDATE topic_pool tp
       SET status = 'pooled', batched_at = NULL
     WHERE tp.status = 'batched'
       AND tp.ingested_at > NOW() - INTERVAL '30 days'
       AND NOT EXISTS (
             SELECT 1 FROM topic_candidates tc
              WHERE tc.source_ref = tp.id::text
           )
       AND NOT EXISTS (
             SELECT 1 FROM internal_topic_candidates ic
              WHERE ic.primary_ref = tp.id::text
           )
       AND NOT EXISTS (
             SELECT 1 FROM pipeline_tasks t
              WHERE lower(t.topic) = lower(tp.title)
           )
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for table in ("topic_pool", "topic_candidates", "internal_topic_candidates", "pipeline_tasks"):
            if not await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table):
                logger.info("Migration repool_topic_pool_rows_leaked_by_batch_refresh: %s absent, nothing to do", table)
                return
        status = await conn.execute(_REPOOL)
    logger.info("Migration repool_topic_pool_rows_leaked_by_batch_refresh: %s", status)


async def down(pool) -> None:
    """One-way: the rows were leaked, not deliberately batched — there is no
    state worth restoring. Explicit no-op."""
    return
