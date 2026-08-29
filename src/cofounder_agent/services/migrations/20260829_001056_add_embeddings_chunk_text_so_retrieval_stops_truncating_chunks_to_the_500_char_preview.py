"""Migration 20260829_001056: add ``embeddings.chunk_text``.

ISSUE: Glad-Labs/poindexter#1033

The tap runner chunks source documents at ``MAX_CHARS = 6000`` and embeds
each chunk **in full**, but ``MemoryClient.store`` persisted only
``text[:500]`` into ``text_preview`` (a ``varchar(500)``), and both
``rag_engine`` retriever paths built their ``TextNode`` from that column.
Retrieval therefore matched on ~4,300 median characters and handed the
consumer the first 500 — measured at **87.4% of indexed content discarded**
across the live 313-chunk post corpus, with the best-matching 500-char
window falling outside the preview in 40% of sampled queries. The
cross-encoder reranker scored the truncated preview too, unconditionally.

This adds a full-fidelity ``chunk_text`` column alongside ``text_preview``.
``text_preview`` is retained deliberately: console, CLI and voice surfaces
render an 80-180 char snippet and should not pull whole chunks into memory
to do it. Readers fall back to ``text_preview`` while rows are un-backfilled,
so the column is safe to deploy ahead of the re-tap.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Add the nullable ``chunk_text`` column.

    Nullable on purpose — existing rows carry NULL until the re-tap
    backfills them, and the read path falls back to ``text_preview``
    for exactly that window. No default, no table rewrite: this is a
    metadata-only ALTER on Postgres 11+, so it is safe on the live
    ``embeddings`` table (~77k rows) without a maintenance pause.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS chunk_text text"
        )
        await conn.execute(
            """
            COMMENT ON COLUMN embeddings.chunk_text IS
            'Full text of the embedded chunk (poindexter#1033). NULL for rows '
            'written before the column existed; readers fall back to '
            'text_preview. text_preview remains the short display field.'
            """
        )
    logger.info("Migration 20260829_001056: embeddings.chunk_text added")


async def down(pool) -> None:
    """Drop the column.

    Safe: nothing reads ``chunk_text`` without a ``text_preview`` fallback,
    so reverting degrades retrieval back to the 500-char payload rather
    than breaking it.
    """
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE embeddings DROP COLUMN IF EXISTS chunk_text")
    logger.info("Migration 20260829_001056: embeddings.chunk_text dropped")
