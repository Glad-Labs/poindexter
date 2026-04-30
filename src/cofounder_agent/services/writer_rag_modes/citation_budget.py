"""CITATION_BUDGET writer mode.

Same as TOPIC_ONLY but the writer is REQUIRED to cite at least N internal
sources by [source/ref] tag. content_validator extension (follow-up) enforces
the count post-write.

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 11)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from services.logger_config import get_logger

logger = get_logger(__name__)

DEFAULT_MIN_CITATIONS = 3


async def run(
    *,
    topic: str,
    angle: str,
    niche_id: UUID | str,
    pool,
    min_citations: int = DEFAULT_MIN_CITATIONS,
    **kw: Any,
) -> dict[str, Any]:
    from services.topic_ranking import embed_text

    qvec = await embed_text(f"{topic} — {angle}")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT source_table, source_id, text_preview
              FROM embeddings
             ORDER BY embedding <=> $1::vector
             LIMIT 12
            """,
            qvec,
        )
    snippets = [
        {"source": r["source_table"], "ref": str(r["source_id"]), "snippet": r["text_preview"]}
        for r in rows
    ]

    citation_instruction = (
        f"You MUST cite at least {min_citations} of the provided internal sources by their "
        f"source/ref pair (e.g. [claude_sessions/abc123]). Failing to cite that many will "
        f"cause the post to be rejected by the validator."
    )
    from services.ai_content_generator import generate_with_context

    draft = await generate_with_context(
        topic=topic, angle=angle, snippets=snippets,
        extra_instructions=citation_instruction,
    )
    return {
        "draft": draft,
        "snippets_used": snippets,
        "min_citations": min_citations,
        "mode": "CITATION_BUDGET",
    }
