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

ADVISORY by design (Matt's call on #765): it scores — nudging the weighted QA
mean and surfacing the offenders — but never vetoes. Status is DB-driven via
``qa_gates.unlinked_attribution.required_to_pass`` (seeded false); an operator
graduates it to a hard gate via the poindexter#454 lever, no code deploy. The
score penalty is deliberately gentle (a few points per offender, floored) so a
single missing link nudges rather than sinks an otherwise-good post.

Returns nothing (no review) when disabled, when there's no research corpus to
match against (can't tell real from fabricated without one — that's the deferred
grounded-LLM pass's job), or when the content is empty.
"""

from __future__ import annotations

import logging
from typing import Any

from modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from plugins.atom import AtomMeta, FieldSpec

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="qa.unlinked_attribution",
    type="atom",
    version="1.0.0",
    description=(
        "Advisory rail (#765): scores attribution-shaped phrases naming a source "
        "with no inline link and no research-corpus match (author names / unknown "
        "brands). Runs after content.reconcile_citations so it sees only the "
        "residual. Advisory via qa_gates.unlinked_attribution.required_to_pass "
        "(false → scores + lists offenders, never vetoes)."
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
        pass

    research_context = state.get("research_context") or ""
    if not research_context.strip():
        # No corpus → can't distinguish real-but-unlinked from fabricated.
        # Defer to the future grounded-LLM pass rather than flag blindly.
        return {}

    from modules.content.atoms._citation_match import (
        find_unmatched_attributions,
        parse_corpus,
    )
    from modules.content.multi_model_qa import MultiModelQA, ReviewerResult

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

    pool = getattr(state.get("database_service"), "pool", None)
    settings_service = state.get("settings_service")
    qa = MultiModelQA(
        pool=pool, settings_service=settings_service,
        site_config=site_config, platform=state.get("platform"),
    )
    gate_states = await resolve_gate_states(qa)
    # Advisory is DB-driven: seeded required_to_pass=false → advisory (scores,
    # never vetoes). An operator can graduate it to a hard gate (poindexter#454).
    MultiModelQA._mark_advisory_if_configured(review, gate_states, "unlinked_attribution")
    return {"qa_rail_reviews": [reviewer_to_dict(review)]}


__all__ = ["ATOM_META", "run"]
