"""qa.self_consistency — HalluCounter-style self-consistency rail as an atom.

Wires ``services.self_consistency_rail`` into the ``qa.*`` atom chain
(glad-labs-stack#621). Delegates to ``self_consistency_rail.evaluate()``
which samples the writer model N times, embeds each short summary, and
computes mean pairwise cosine similarity.

Default-off (``app_settings.self_consistency_enabled = false``). An
operator enables it by flipping the setting in the DB. The atom is
advisory-first (``required_to_pass=false`` in the qa_gates seed) — it
SCORES but never vetoes until the operator graduates it.

A rail that measured NOTHING appends NO review (poindexter#875). ``evaluate()``
returns ``score=None`` on every skip path; this atom treats that exactly like
its other "didn't run" cases — ``return {}`` — and emits a ``warn`` finding so
the disappearance is loud. Previously every skip returned ``score=1.0``, which
this atom recorded as ``100.0``: prod audit rows carried a PERFECT
self-consistency score whose feedback read "embedding step failed".

A check that did not run must never render as a passing measurement. While the
gate is advisory the fake 100 stayed out of the weighted mean (advisory rails
are excluded — see anti-hallucination.md), so it "only" inflated the QA Rails
pass-rate and hid embedding outages behind a green number. The teeth were on
graduation (``qa_gates.self_consistency.required_to_pass=true``): the 100 then
joins the mean and counts as a pass, lifting a lone 72.0 peer to 86.0 — +14
points from a check that never ran, enough to carry a sub-threshold post over
the prod bar of 80. Pinned by
``test_qa_self_consistency_atom.py::TestNoMeasurement``.

Chain position: after ``qa.consistency``, before ``qa.web_factcheck``.
"""

from __future__ import annotations

import logging
from typing import Any

from modules.content.atoms._pool import resolve_pool
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
) -> tuple[bool, float | None, str]:
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

    if score is None:
        # The rail measured nothing (no samples / embedding step down / caught
        # error). Recording a review here would fabricate a measurement — the
        # old code scored these 1.0 → 100.0. Drop the review instead, and make
        # the disappearance loud: a rail that silently stops reviewing is the
        # failure mode that looks like success on every dashboard.
        logger.warning("[qa.self_consistency] no measurement — %s", reason)
        from utils.findings import emit_finding

        emit_finding(
            source="qa.self_consistency",
            kind="qa_rail_no_measurement",
            title="self_consistency rail produced no measurement",
            body=(
                f"{reason}\n\nNo review was appended for this post — the rail "
                "is absent from the QA pass rather than scored. Repeated "
                "occurrences mean the rail is effectively off: check Ollama "
                "reachability, the embedding model, and "
                "`pipeline_local_writer_model`."
            ),
            # severity=warn routes to Discord (routine ops); the stable
            # dedup_key collapses a run of these into one ping while every
            # occurrence still lands on the Findings dashboard with its own
            # reason.
            severity="warn",
            dedup_key="qa_rail_no_measurement:self_consistency",
            extra={"rail": "self_consistency", "reason": reason},
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
    pool = resolve_pool(state, atom="qa.self_consistency")
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
