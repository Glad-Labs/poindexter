"""STORY_SPINE writer mode.

Preprocess top snippets into a structured outline with one LLM call, then
expand the outline to full prose with the writer.

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 12)
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from services.logger_config import get_logger

logger = get_logger(__name__)


async def run(*, topic: str, angle: str, niche_id: UUID | str, pool, **kw: Any) -> dict[str, Any]:
    from services.topic_ranking import _ollama_chat_json, embed_text

    # DI seam (glad-labs-stack#330)
    site_config = kw.get("site_config")
    snippet_limit = (
        site_config.get_int("writer_rag_story_spine_snippet_limit", 15)
        if site_config is not None else 15
    )
    snippet_max_chars = (
        site_config.get_int("writer_rag_story_spine_snippet_max_chars", 600)
        if site_config is not None else 600
    )
    model = (
        (site_config.get("pipeline_writer_model", "glm-4.7-5090:latest")
            if site_config is not None else "glm-4.7-5090:latest")
        or "glm-4.7-5090:latest"
    ).removeprefix("ollama/")

    qvec = await embed_text(f"{topic} — {angle}")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT source_table, source_id, text_preview
              FROM embeddings
             ORDER BY embedding <=> $1::vector
             LIMIT $2
            """,
            qvec,
            snippet_limit,
        )
    snippets = [
        {"source": r["source_table"], "ref": str(r["source_id"]), "snippet": r["text_preview"]}
        for r in rows
    ]
    snippet_block = "\n---\n".join(
        s["snippet"][:snippet_max_chars] for s in snippets if s["snippet"]
    )

    spine_prompt = f"""Read these {snippet_limit} snippets from an AI-operated content business's records.
Produce a structured outline for a blog post about: "{topic}" (angle: "{angle}").

Outline format (return JSON):
{{
  "hook": "<opening hook, one sentence>",
  "what_happened": "<the timeline of events as drawn from snippets>",
  "why_it_matters": "<the lesson or insight>",
  "what_we_learned": "<concrete takeaways>",
  "close": "<call-to-action or final framing>"
}}

Snippets:
{snippet_block}
"""
    spine_raw = await _ollama_chat_json(spine_prompt, model=model)
    spine = json.loads(spine_raw)

    from services.ai_content_generator import generate_with_outline

    draft = await generate_with_outline(topic=topic, outline=spine, snippets=snippets)
    return {"draft": draft, "spine": spine, "snippets_used": snippets, "mode": "STORY_SPINE"}
