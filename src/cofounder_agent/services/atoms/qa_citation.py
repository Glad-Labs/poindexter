"""qa.citations — the dead-link / minimum-citation gate as a rail atom.

Restores the default-on ``citation_verifier`` gate that stopped running on the
live path when the #355 atom-cutover replaced ``MultiModelQA.review()`` with the
``qa.*`` atom chain (Glad-Labs/poindexter#659). ``review()`` ran it as step
"1b" — HTTP-HEAD-checking every external URL in the post and rejecting when the
dead-link ratio exceeded ``qa_citation_max_dead_ratio`` (default 0.30) or the
citation count fell below ``qa_citation_min_count``. It was enabled by default
(``qa_citation_verify_enabled=true``) and never marked advisory, so an
``approved=False`` was a hard veto. The cutover dropped it, so dead/fabricated
citations the old pipeline flagged now ship unchecked.

DISTINCT from ``url_verifier``: that rail was reconciled to advisory in the
2026-06-03 ``rewire_programmatic_validator_gate`` migration (the only URL check
on the live path is the deliberately non-halting ``stage.url_validation``).
``citation_verifier`` is a SEPARATE reviewer — ``provider='http_head'``, the
``qa_citation_*`` settings family, the dead-link RATIO + min-count logic — that
had no live replacement at all.

Design mirrors ``qa.programmatic``: delegates to the retained
``MultiModelQA._check_citations(content)`` rail method and appends the
``ReviewerResult`` to the ``qa_rail_reviews`` channel that ``qa.aggregate``
folds into the gate decision. Returns nothing when the feature is flagged off
(``qa_citation_verify_enabled=false``) or there are no external URLs to grade.

Advisory status is DB-driven via ``qa_gates.citation_verifier.required_to_pass``
(``_mark_advisory_if_configured``). The #659 fix seeds a NEW ``citation_verifier``
gate row (the gate had none). Per the advisory-first restoration posture it's
seeded ``required_to_pass=false`` so the dead-link rail SCORES + feeds the
weighted average on every pass but does not yet veto; an operator restores the
``dead_ratio > 0.30`` hard veto by flipping ``required_to_pass=true`` via the
poindexter#454 lever — no code deploy.
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec
from services.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict

ATOM_META = AtomMeta(
    name="qa.citations",
    type="atom",
    version="1.0.0",
    description=(
        "Dead-link / minimum-citation gate — HTTP-HEAD-checks every external "
        "URL and scores the dead-link ratio. Advisory is DB-driven via "
        "qa_gates.citation_verifier.required_to_pass (false at restore → scores "
        "but does not veto; flip true to restore the dead-link hard veto). "
        "Distinct from the advisory url_verifier rail."
    ),
    inputs=(FieldSpec(name="content", type="str", description="draft to verify citations in"),),
    outputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="citation-verifier review"),),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier=None,  # HTTP HEAD checks — no LLM tier
    cost_class="free",  # network I/O only — zero LLM/API spend in the budget model
    idempotent=False,
    side_effects=("HTTP HEAD requests to every external URL in the content",),
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
    # None when qa_citation_verify_enabled=false OR there were no external URLs
    # to grade (citation-free post with min_count=0) — both legacy no-ops.
    review = await qa._check_citations(content)
    if review is None:
        return {}
    # Advisory is DB-driven: required_to_pass=true → dead-link hard veto in
    # qa.aggregate; false (the restore default) → advisory (scores, never vetoes).
    MultiModelQA._mark_advisory_if_configured(review, gate_states, "citation_verifier")
    return {"qa_rail_reviews": [reviewer_to_dict(review)]}


__all__ = ["ATOM_META", "run"]
