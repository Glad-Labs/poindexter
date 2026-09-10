"""content.revise_contradictions — apply detected fixes under the minimal-edit contract.

Second half of the self-review pass. Consumes ``contradiction_review`` from
``content.detect_contradictions`` and asks the REVISER model (always
``writer_self_review_model`` — this is the model whose text ships) to fix only
those contradictions.

A revision is accepted only if it stays inside the minimal-edit contract
(poindexter#1000): length ratio within
``writer_self_review_min/max_length_ratio`` AND no planning/deliberation dump.
On any rejection the ORIGINAL draft is kept and a per-model
``self_review_revision_rejected`` finding is emitted — the reviser is blamed,
not the detector, because it is the reviser's output that failed.

No-ops when ``contradiction_review`` is empty, so the node is safe on every
run and the graph needs no conditional edge.

Delegates to ``services/self_review.py`` (library), never to a sibling atom.

Produces: content (revised in place, or unchanged when rejected/clean).
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy
from utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.revise_contradictions",
    type="atom",
    version="1.0.0",
    description=(
        "Apply the detected contradiction fixes to the draft, accepting the "
        "revision only if it passes the minimal-edit contract."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft body"),
        FieldSpec(
            name="contradiction_review",
            type="str",
            description="detector output from content.detect_contradictions",
        ),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="revised draft body"),
    ),
    requires=("content", "contradiction_review"),
    produces=("content",),
    capability_tier="standard",
    cost_class="local",
    idempotent=True,
    side_effects=(),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Revise the draft for the detected contradictions, or keep the original."""
    content_text = (state.get("content") or "").strip()
    review_text = (state.get("contradiction_review") or "").strip()
    if not content_text or not review_text:
        return {}

    site_config = state.get("site_config")
    if site_config is None:
        logger.warning("[REVISE_CONTRA] no site_config in state; skipping")
        return {}

    database_service = state.get("database_service")
    pool = getattr(database_service, "pool", None) if database_service else None

    from services.self_review import revise_contradictions

    try:
        revised_text, stats = await revise_contradictions(
            content_text, review_text, pool=pool, site_config=site_config,
        )
    except Exception as exc:  # noqa: BLE001 — non-fatal, mirrors the old stage
        logger.warning(
            "[REVISE_CONTRA] revision failed (non-fatal): %s",
            describe_exception(exc),
        )
        return {}

    if not stats.get("revised"):
        # Rejected or no-op: the library already logged + emitted the finding.
        return {}
    return {"content": revised_text, "content_length": len(revised_text)}
