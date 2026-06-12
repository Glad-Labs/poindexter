"""qa.self_consistency — HalluCounter-style self-consistency rail as an atom.

Wires ``services.self_consistency_rail`` into the ``qa.*`` atom chain
(glad-labs-stack#621). Delegates to ``self_consistency_rail.evaluate()``
which samples the writer model N times, embeds each short summary, and
computes mean pairwise cosine similarity.

Default-off (``app_settings.self_consistency_enabled = false``). An
operator enables it by flipping the setting in the DB. The atom is
advisory-first (``required_to_pass=false`` in the qa_gates seed) — it
SCORES but never vetoes until the operator graduates it.

Chain position: after ``qa.consistency``, before ``qa.web_factcheck``.
"""

from __future__ import annotations

import logging
from typing import Any

from modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from modules.content.multi_model_qa import MultiModelQA, ReviewerResult
from plugins.atom import AtomMeta, FieldSpec

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="qa.self_consistency",
    type="atom",
    version="1.0.0",
    description=(
        "HalluCounter self-consistency gate — samples writer N times, embeds "
        "each summary, flags low mean cosine similarity as potential "
        "hallucination. Advisory-first (DB-driven via qa_gates.self_consistency)."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft to review"),
        FieldSpec(name="topic", type="str", description="article topic for summary prompt"),
    ),
    outputs=(
        FieldSpec(
            name="qa_rail_reviews",
            type="list[dict]",
            description="self-consistency review result",
        ),
    ),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier="compute",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama N+1 times (N summaries + N embeddings)",),
    parallelizable=True,
)


async def _rail_evaluate(
    *, content: str, topic: str, site_config: Any
) -> tuple[bool, float, str]:
    """Thin indirection so tests can monkeypatch without touching the rail."""
    from services.self_consistency_rail import evaluate
    return await evaluate(content=content, topic=topic, site_config=site_config)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        return {}

    from services.self_consistency_rail import is_enabled
    if not is_enabled(site_config):
        return {}

    topic = (state.get("topic") or "").strip()

    try:
        passed, score, reason = await _rail_evaluate(
            content=content, topic=topic, site_config=site_config,
        )
    except Exception as exc:
        logger.warning(
            "[qa.self_consistency] evaluate() raised unexpectedly: %s — skipping",
            exc, exc_info=True,
        )
        return {}

    review = ReviewerResult(
        reviewer="self_consistency",
        approved=passed,
        score=round(score * 100, 1),   # store as 0-100 for consistency with other rails
        feedback=reason,
        provider="self_consistency_gate",
    )

    # Advisory status is DB-driven via qa_gates.self_consistency.required_to_pass,
    # and we MUST apply it to the review here — mirroring qa.citations /
    # qa.topic_delivery. qa.aggregate vetoes on any review with approved=False
    # AND advisory=False; it does NOT read required_to_pass back onto the review.
    # So without this call a FAILING run hard-vetoes the post even though the
    # gate is seeded advisory (required_to_pass=false) — the exact bug this
    # restores. required_to_pass=true leaves it a real veto.
    pool = getattr(state.get("database_service"), "pool", None)
    settings_service = state.get("settings_service")
    qa = MultiModelQA(
        pool=pool,
        settings_service=settings_service,
        site_config=site_config,
        platform=state.get("platform"),
    )
    gate_states = await resolve_gate_states(qa)
    MultiModelQA._mark_advisory_if_configured(review, gate_states, "self_consistency")

    return {"qa_rail_reviews": [reviewer_to_dict(review)]}


__all__ = ["ATOM_META", "run"]
