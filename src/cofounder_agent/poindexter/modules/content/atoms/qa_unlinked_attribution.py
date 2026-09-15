"""qa.unlinked_attribution — advisory rail for named sources cited without links.

The gap (poindexter#765): the writer attributes claims to named sources
("as noted by M. Huzaifa Rizwan", "(Ai Insights)") without linking them, and
nothing catches it — ``qa.citations`` only dead-link-checks URLs that already
exist, ``content_validator``'s unlinked rule is defanged + misses these
phrasings, and the LLM critic reads right past them.

This rail runs AFTER ``content.reconcile_citations`` (which auto-links the
subjects it can ground against the research corpus by domain handle), so it sees
only the RESIDUAL: attribution subjects that match no corpus source and aren't
already linked — i.e. author-name and unknown-brand attributions a deterministic
linker can't safely repair. It scores that residual density and lists the
offenders in its feedback (which lands in qa_feedback + the QA Rails dashboard).

Advisory at birth (Matt's call on #765), **required since 2026-09-15**
(migration ``20260915_013131``, poindexter#1052): once the frames covered the
shapes real fabrications take ("the VRLA Tech piece", "according to the
breakdown at Tutorials Point", "per a recent LinkedIn analysis"), an unlinked
subject that matches no corpus source is a fabricated citation until proven
otherwise, so a hit now VETOES. The QA rescue cycle gets the offender list in
``qa_feedback`` and can link or drop the phrase before the terminal reject.
Status stays DB-driven via ``qa_gates.unlinked_attribution.required_to_pass``
— the poindexter#454 lever demotes it back to advisory with no deploy. The
score penalty is still gentle (a few points per offender, floored).

Returns nothing (no review) when disabled, when there's no research corpus to
match against (can't tell real from fabricated without one — that's the deferred
grounded-LLM pass's job), or when the content is empty.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.modules.content.atoms._pool import resolve_pool
from poindexter.modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from poindexter.plugins.atom import AtomMeta, FieldSpec

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="qa.unlinked_attribution",
    type="atom",
    version="1.0.0",
    description=(
        "Fabricated-citation rail (#765, hard gate since 2026-09-15): flags "
        "attribution-shaped phrases naming a source with no inline link and no "
        "research-corpus match (author names / unknown brands / phantom pieces). "
        "Runs after content.reconcile_citations so it sees only the residual. "
        "Gate status is DB-driven via qa_gates.unlinked_attribution.required_to_pass."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft to scan"),
        FieldSpec(name="research_context", type="str", description="research corpus to match against", required=False),
    ),
    outputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="unlinked-attribution review"),),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier=None,  # pure string ops — no LLM tier
    cost_class="free",
    idempotent=True,
    side_effects=(),
    parallelizable=True,
)


def _score(count: int, *, penalty_per: int, floor: int) -> float:
    """Map the unmatched-attribution count to a 0-100 advisory score.

    100 when none; each offender shaves ``penalty_per`` points down to ``floor``
    so the rail nudges the weighted QA mean without sinking an otherwise-good
    post on a single missing link.
    """
    if count <= 0:
        return 100.0
    return float(max(floor, 100 - penalty_per * count))


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        return {}

    try:
        if not site_config.get_bool("unlinked_attribution_enabled", True):
            return {}
    except Exception:  # noqa: BLE001 — config read must never break the pipeline
        # silent-ok: falling through RUNS the rail (the setting defaults to
        # True), so a failed read errs toward more checking, not less. The
        # rail is advisory, so the worst case is an extra advisory review.
        pass

    research_context = state.get("research_context") or ""
    if not research_context.strip():
        # No corpus → can't distinguish real-but-unlinked from fabricated.
        # Defer to the future grounded-LLM pass rather than flag blindly.
        return {}

    from poindexter.modules.content.atoms._citation_match import (
        find_unmatched_attributions,
        parse_corpus,
    )
    from poindexter.modules.content.multi_model_qa import MultiModelQA, ReviewerResult

    sources = parse_corpus(research_context)
    if not sources:
        return {}

    unmatched = find_unmatched_attributions(content, sources)

    try:
        penalty_per = site_config.get_int("unlinked_attribution_penalty_per", 8)
        floor = site_config.get_int("unlinked_attribution_score_floor", 60)
    except Exception:  # noqa: BLE001
        penalty_per, floor = 8, 60

    count = len(unmatched)
    if count:
        preview = "; ".join(unmatched[:5])
        feedback = (
            f"{count} named source(s) cited without a link or corpus match: "
            f"{preview}"
        )
        logger.info(
            "[qa.unlinked_attribution] %d unmatched attribution(s) (task=%s): %s",
            count, str(state.get("task_id") or "?")[:8], preview,
        )
    else:
        feedback = "All named-source attributions are linked or corpus-matched"

    review = ReviewerResult(
        reviewer="unlinked_attribution",
        approved=count == 0,
        score=_score(count, penalty_per=penalty_per, floor=floor),
        feedback=feedback,
        provider="unlinked_attribution",
    )

    pool = resolve_pool(state, atom="qa.unlinked_attribution")
    settings_service = state.get("settings_service")
    qa = MultiModelQA(
        pool=pool, settings_service=settings_service,
        site_config=site_config, platform=state.get("platform"),
    )
    gate_states = await resolve_gate_states(qa)
    # Gate status is DB-driven: required_to_pass=true since migration
    # 20260915_013131 (a hit vetoes); an operator can demote it to advisory
    # (poindexter#454) — the rail itself never hardcodes either posture.
    MultiModelQA._mark_advisory_if_configured(review, gate_states, "unlinked_attribution")
    return {"qa_rail_reviews": [reviewer_to_dict(review)]}


__all__ = ["ATOM_META", "run"]
