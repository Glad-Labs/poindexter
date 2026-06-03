"""Re-seed canonical_blog graph_def with the qa.vision rail (version 5).

WHY (Glad-Labs/poindexter#563): the #355 atom-cutover replaced the monolithic
``cross_model_qa`` stage — which ran two vision-model gates inline — with the
``qa.*`` atom chain, but only ported the text rails. The two vision gates went
cold:

1. **Image relevance** (``_check_image_relevance`` → ``vision_gate`` qa_gates
   row). ``qa_vision_check_enabled=true`` but nothing on the live path ran it.
2. **Rendered-preview screenshot** (``_check_rendered_preview``).
   ``qa_preview_screenshot_enabled=true`` but no ``preview_url`` ever reached
   the gate on the live path (the deleted ``review()`` had an ``if preview_url:``
   guard and was never called with one).

This migration re-seeds ``pipeline_templates.graph_def`` (canonical_blog) from
the updated authoritative spec, which now wires a ``qa.vision`` rail between
``qa.ragas`` and ``qa.aggregate``. ``qa.vision`` runs both vision checks and
appends their reviews to ``qa_rail_reviews`` so ``qa.aggregate`` scores them —
restoring a ``vision_gate`` score on every pass. The ``preview_url`` it reads is
minted early by ``stage.verify_task`` (#563 also threads ``preview_token`` /
``preview_url`` through the pipeline state so the rail, which runs BEFORE
``finalize_task``, has a URL to screenshot). Version bumps 4 → 5.

Both checks stay opt-in via their existing app_settings flags, so a fresh
install with the flags off gets a no-op rail. No qa_gates flag changes — the
``vision_gate`` row is already ``enabled=true`` (advisory) in the baseline.

Imports only ``canonical_blog_spec`` (pure data — no LangGraph) + stdlib so it
stays light for the migrations-smoke env.
"""
from __future__ import annotations

import json
import logging

from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    payload = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE pipeline_templates
               SET graph_def = $1::jsonb, version = 5, updated_at = NOW()
             WHERE slug = 'canonical_blog'
            """,
            payload,
        )
    logger.info("reseed_canonical_blog_qa_vision: applied (graph_def v5)")


async def down(pool) -> None:
    logger.info(
        "reseed_canonical_blog_qa_vision: no-op down (forward-only reseed)"
    )
