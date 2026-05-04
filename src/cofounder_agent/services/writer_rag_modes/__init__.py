"""Writer RAG mode dispatcher.

The writer stage of the content pipeline delegates to one of four handlers
based on the niche's writer_rag_mode setting. Each handler produces a draft
with a different RAG strategy:

- TOPIC_ONLY        — single embedding query, dump top-N snippets in prompt
- CITATION_BUDGET   — same as TOPIC_ONLY but writer required to cite >= N
- STORY_SPINE       — outline-first pass, then expand to prose
- TWO_PASS          — internal-first draft, then conditional external augment
                      (LangGraph state machine; Glad Labs default)

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 9)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


async def dispatch_writer_mode(
    *,
    mode: str,
    topic: str,
    angle: str,
    niche_id: UUID | str,
    pool,
    **kwargs: Any,
) -> dict[str, Any]:
    """Route the writer call to the right mode handler. Each handler returns
    the writer's output dict (at minimum {"draft": "..."} plus any metadata).
    """
    if mode == "TOPIC_ONLY":
        from services.writer_rag_modes import topic_only
        return await topic_only.run(topic=topic, angle=angle, niche_id=niche_id, pool=pool, **kwargs)
    elif mode == "CITATION_BUDGET":
        from services.writer_rag_modes import citation_budget
        return await citation_budget.run(topic=topic, angle=angle, niche_id=niche_id, pool=pool, **kwargs)
    elif mode == "STORY_SPINE":
        from services.writer_rag_modes import story_spine
        return await story_spine.run(topic=topic, angle=angle, niche_id=niche_id, pool=pool, **kwargs)
    elif mode == "TWO_PASS":
        from services.writer_rag_modes import two_pass
        return await two_pass.run(topic=topic, angle=angle, niche_id=niche_id, pool=pool, **kwargs)
    elif mode == "DETERMINISTIC_COMPOSITOR":
        # Zero-LLM-call template render of the context_bundle. Used by
        # niches whose source-of-truth is a structured bundle (dev_diary)
        # and where any generative step risks hallucination. Bypasses
        # the entire RAG/embedding/snippet machinery.
        from services.writer_rag_modes import deterministic_compositor
        return await deterministic_compositor.run(topic=topic, angle=angle, niche_id=niche_id, pool=pool, **kwargs)
    else:
        raise ValueError(f"unknown writer_rag_mode: {mode!r}")
