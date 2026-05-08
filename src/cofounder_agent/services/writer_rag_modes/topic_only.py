"""TOPIC_ONLY writer mode.

Single embedding query, dump top-N internal snippets into the writer prompt
as background context. Simplest of the four modes.

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 10)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from services.logger_config import get_logger

logger = get_logger(__name__)


async def run(*, topic: str, angle: str, niche_id: UUID | str, pool, **kw: Any) -> dict[str, Any]:
    from services.topic_ranking import embed_text

    # DI seam (glad-labs-stack#330) — site_config is threaded by the
    # dispatcher; falls through to the default when absent.
    site_config = kw.get("site_config")
    # Top-N internal snippets by cosine similarity (pgvector). N is
    # operator-tunable via writer_rag_topic_only_snippet_limit
    # (migration 0119); default 8 matches the prior hardcoded LIMIT.
    snippet_limit = (
        site_config.get_int("writer_rag_topic_only_snippet_limit", 8)
        if site_config is not None else 8
    )
    qvec = await embed_text(f"{topic} — {angle}")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT source_table, source_id, text_preview,
                   1 - (embedding <=> $1::vector) AS similarity
              FROM embeddings
             ORDER BY embedding <=> $1::vector
             LIMIT $2
            """,
            qvec,
            snippet_limit,
        )
    snippets = [
        {
            "source": r["source_table"],
            "ref": str(r["source_id"]),
            "snippet": r["text_preview"],
            "similarity": float(r["similarity"]),
        }
        for r in rows
    ]
    # Hand off to the existing writer with the snippets in the prompt context.
    from services.ai_content_generator import generate_with_context

    draft = await generate_with_context(topic=topic, angle=angle, snippets=snippets)
    return {"draft": draft, "snippets_used": snippets, "mode": "TOPIC_ONLY"}
