"""content.detect_contradictions — find cross-section contradictions, change nothing.

First half of the self-review pass. Reads the draft and asks a reviewer model
to name claims that conflict across sections; writes the raw finding text to
``contradiction_review`` for ``content.revise_contradictions`` to act on.

Split from the revise half (2026-08-28) because the two calls have opposite
demands and are measurably better served by different models. Measured on real
published posts with an injected contradiction, n=4 per arm:

    gemma-4-31B-it-qat    detected 1/4, revised correctly when it did
    glm-4.7-5090 (think)  detected 4/4, every revision REJECTED as too long
                          (2.40x / 4.77x / 5.36x / 7.39x)

Before the split this ran inside one ``stage.writer_self_review`` node, so a
two-model composition would have been invisible to the graph — the thing the
atom contract exists to prevent. Two nodes make it the graph's truth, and let
an architect compose detection without revision.

Delegates to ``services/self_review.py`` (library), never to a sibling atom —
same shape as the ``qa.*`` atoms delegating to ``multi_model_qa``.

Produces: contradiction_review (str, empty when the draft is clean).
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.detect_contradictions",
    type="atom",
    version="1.0.0",
    description=(
        "LLM pass that names cross-section contradictions in the draft. "
        "Read-only — never edits the body."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft body"),
        FieldSpec(name="title", type="str", description="draft title"),
        FieldSpec(name="topic", type="str", description="assignment topic"),
    ),
    outputs=(
        FieldSpec(
            name="contradiction_review",
            type="str",
            description="raw detector output; empty string when clean",
        ),
    ),
    requires=("content",),
    produces=("contradiction_review",),
    capability_tier="standard",
    cost_class="local",
    idempotent=True,
    side_effects=(),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Detect contradictions; return them for the revise atom to apply."""
    content_text = (state.get("content") or "").strip()
    if not content_text:
        return {"contradiction_review": ""}

    site_config = state.get("site_config")
    if site_config is None:
        logger.warning("[DETECT_CONTRA] no site_config in state; skipping")
        return {"contradiction_review": ""}

    database_service = state.get("database_service")
    pool = getattr(database_service, "pool", None) if database_service else None

    from services.self_review import detect_contradictions

    try:
        review_text, stats = await detect_contradictions(
            content_text,
            state.get("title") or "",
            state.get("topic") or "",
            pool=pool,
            site_config=site_config,
        )
    except Exception as exc:  # noqa: BLE001 — non-fatal, mirrors the old stage
        logger.warning(
            "[DETECT_CONTRA] detection failed (non-fatal): %s",
            describe_exception(exc),
        )
        return {"contradiction_review": ""}

    found = int(stats.get("contradictions_found", 0) or 0)
    if found:
        logger.info("[DETECT_CONTRA] %d contradiction(s) found", found)
    return {"contradiction_review": review_text or ""}
