"""Migration 20261008_032728: expire pooled internal_rag topics keyed by their reworded titles

internal_rag re-ranks the same embedded snippets every run (a stable vector
ranking over a 30-day window) and an LLM distils each into a NEW title, so
the title-keyed pool took every rewording as a new row: ~40 glad-labs rows
in eight days ("Reranker Update", "Re-ranker Update Impact", "Quality
Reranker Enhancements" ...) from two snippets about one re-ranker fix. The
sweep reads only the best 20 rows per source, so one story filled the
internal slots of every batch.

Candidates are now keyed on their snippet (``topic_pool.dedup_key`` with a
``ref:internal_rag:...`` key) and an already-pooled snippet is not distilled
again. The rows pooled before that change carry title keys the new ones can
never collide with, so they are expired here; the next runs pool each
current snippet exactly once. Rows referenced by a batch candidate are left
alone, and nothing but ``pooled`` rows is touched (``batched`` history
stays). Idempotent — a second run matches nothing.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_EXPIRE = """
    UPDATE topic_pool tp
       SET status = 'expired'
     WHERE tp.source = 'internal_rag'
       AND tp.status = 'pooled'
       AND tp.dedup_key NOT LIKE 'ref:%'
       AND NOT EXISTS (
             SELECT 1 FROM internal_topic_candidates ic
              WHERE ic.primary_ref = tp.id::text
           )
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        status = await conn.execute(_EXPIRE)
    logger.info(
        "Migration expire_pooled_internal_rag_topics_keyed_by_their_reworded_titles: %s",
        status,
    )


async def down(pool) -> None:
    # One-way: the expired rows were re-distillations of snippets the source
    # now pools once under a ref key; restoring them would bring the
    # duplicates back. They stay in the table (status 'expired'), untouched.
    return
