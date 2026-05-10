"""Seed the master switch for the LlamaIndex retriever path.

Lane D sub-issue 4 of ``glad-labs-stack#329`` activates the dormant
``services/rag_engine.py`` scaffold by routing
``poindexter.memory.MemoryClient.search`` through the LlamaIndex
``BaseRetriever`` when ``app_settings.rag_engine_enabled = 'true'``.

Default-disabled because the retriever's value-add (hybrid BM25 + RRF
fusion, cross-encoder rerank, query rewriting) is gated on additional
settings the operator opts into separately:

- ``rag_hybrid_enabled`` (already seeded — controls BM25 + vector
  Reciprocal Rank Fusion)
- ``rag_rerank_enabled`` (already seeded — controls cross-encoder
  rerank against ``rag_rerank_model``)
- ``rag_default_top_k`` / ``rag_min_similarity`` (already seeded
  with sane defaults)

So the new master switch is purely a chain-of-trust question: the
operator says "yes, route my queries through LlamaIndex" once via
this flag, and from then on whichever extras they've turned on
(hybrid, rerank) take effect transparently. Without flipping this,
the legacy inline-pgvector path runs unchanged.

Idempotent — ``ON CONFLICT DO NOTHING``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SETTINGS = [
    (
        "rag_engine_enabled",
        "false",
        "rag",
        "Master switch for the LlamaIndex retriever path "
        "(services/rag_engine.py wired into MemoryClient.search per "
        "Lane D #329 sub-issue 4). Default 'false' — the legacy "
        "inline-pgvector path runs. Flip to 'true' to route every "
        "MemoryClient.search through the LlamaIndex BaseRetriever; "
        "whichever rag_hybrid_enabled / rag_rerank_enabled extras "
        "are turned on then take effect transparently. Writer-"
        "filtered queries always fall through to the legacy path "
        "(the retriever has no writer-filter parameter today).",
    ),
]


async def run_migration(conn) -> None:
    for key, value, category, description in _SETTINGS:
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active)
            VALUES ($1, $2, $3, $4, false, true)
            ON CONFLICT (key) DO NOTHING
            """,
            key,
            value,
            category,
            description,
        )
    logger.info(
        "Migration 20260510_040315: rag_engine_enabled master switch "
        "seeded (default false — opt-in)."
    )
