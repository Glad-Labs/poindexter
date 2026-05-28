"""Writer RAG mode dispatcher — TWO_PASS only.

The other modes (TOPIC_ONLY, CITATION_BUDGET, STORY_SPINE, DETERMINISTIC_
COMPOSITOR) were deleted 2026-05-28 — none had carried live traffic for
months. TWO_PASS is the only writer mode glad-labs niche uses; dev_diary
moved to ``services/atoms/narrate_bundle.py`` and doesn't use this
dispatcher at all (its template skips ``generate_content``).

Kept as a dispatcher (rather than inlining TWO_PASS) so adding a new
mode remains a one-elif change. If TWO_PASS is ever the only writer
forever, this module can collapse to a direct import.
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
    if mode == "TWO_PASS":
        from services.writer_rag_modes import two_pass
        return await two_pass.run(topic=topic, angle=angle, niche_id=niche_id, pool=pool, **kwargs)
    raise ValueError(
        f"unknown writer_rag_mode: {mode!r} — only TWO_PASS is supported "
        f"(other modes were deleted 2026-05-28 as dead code)"
    )
