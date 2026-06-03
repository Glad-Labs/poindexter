"""qa.consistency — the internal self-contradiction gate as a rail atom.

Restores the ``internal_consistency`` reviewer (qa_gates name ``consistency``)
that stopped running on the live path when the #355 atom-cutover replaced
``MultiModelQA.review()`` with the ``qa.*`` atom chain
(Glad-Labs/poindexter#660). ``review()`` ran it as step "2c", gated by
``qa_gates.consistency``: it catches cross-section self-contradiction (section
1 says "no React", section 3 says "use Next.js" without acknowledging the
switch). The cutover ported the text rails but NOT this gate, so the score
never entered the weighted average and the low-score veto never fired — even
though the ``consistency`` gate row is still ``enabled=true`` in the DB.

Design mirrors ``qa.programmatic`` / ``qa.ragas``: delegates to the retained
``MultiModelQA._check_internal_consistency(content)`` rail method
(``provider='consistency_gate'`` → ``_qa_rail_common`` weights it at
``gate_weight``) and appends the ``ReviewerResult`` to the ``qa_rail_reviews``
channel that ``qa.aggregate`` folds into the gate decision. Returns nothing
when the content is empty or Ollama is unreachable.

Advisory status is DB-driven via ``qa_gates.consistency.required_to_pass``
(``_mark_advisory_if_configured``). The baseline already seeds ``consistency``
as ``enabled=true, required_to_pass=false`` (advisory), and the advisory-first
restoration keeps it there — the rail SCORES on every pass and feeds the
weighted average without vetoing.

NOTE on the legacy low-score hard-veto: the deleted ``review()`` had a
``_reviewer_vetoes`` carve-out that vetoed when the consistency score was
unambiguously low (``< qa_consistency_veto_threshold``, default 30/50) EVEN
when the gate was advisory. That carve-out lives in ``review()``, not in the
rail aggregator (``_qa_rail_common.aggregate_rail_reviews``), and is NOT
re-introduced here: per the advisory-first restoration, this rail scores but
does not veto until an operator graduates it via ``required_to_pass=true``.
Re-wiring the score-threshold escape into the aggregator is deliberately
deferred (it would be a NEW veto path, out of scope for the additive restore).
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec
from services.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict

ATOM_META = AtomMeta(
    name="qa.consistency",
    type="atom",
    version="1.0.0",
    description=(
        "Internal self-contradiction gate — catches sections that contradict "
        "each other. Advisory is DB-driven via qa_gates.consistency."
        "required_to_pass (advisory in baseline → scores, never vetoes)."
    ),
    inputs=(FieldSpec(name="content", type="str", description="draft to review"),),
    outputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="internal-consistency review"),),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier="cheap_critic",
    cost_class="compute",
    idempotent=False,
    side_effects=("calls ollama",),
    parallelizable=True,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        return {}

    pool = getattr(state.get("database_service"), "pool", None)
    settings_service = state.get("settings_service")

    from services.multi_model_qa import MultiModelQA

    qa = MultiModelQA(pool=pool, settings_service=settings_service, site_config=site_config)
    gate_states = await resolve_gate_states(qa)
    review = await qa._check_internal_consistency(content)
    if review is None:
        return {}
    # Gate row is named "consistency" (the reviewer is "internal_consistency").
    # Advisory in the baseline → review.advisory=True (scores, never vetoes).
    MultiModelQA._mark_advisory_if_configured(review, gate_states, "consistency")
    return {"qa_rail_reviews": [reviewer_to_dict(review)]}


__all__ = ["ATOM_META", "run"]
