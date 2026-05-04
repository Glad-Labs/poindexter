"""``atoms.aggregate_reviews`` — fold N critic reviews into one verdict.

Phase 3 of the dynamic-pipeline-composition spec. Pairs with
:mod:`services.atoms.review_with_critic` — N critics push Review dicts
onto ``state['qa_reviews']``, this atom folds them into a single
``qa_final_score`` + verdict. Sets ``_halt=True`` when the aggregated
verdict is "reject".

Aggregation policy is configurable via state:

- ``aggregation_strategy`` (default: ``"strict"``):
    - ``"strict"`` — any critic's "reject" → fail; any "revise" with
      score < strict_threshold → fail; otherwise mean of overall
      scores. Mirrors the existing cross_model_qa "any-veto" policy.
    - ``"average"`` — mean of overall scores; verdict from threshold.
    - ``"majority"`` — verdict by majority vote across critics.
- ``aggregation_pass_threshold`` (default: 70.0) — score below this
  → halt with verdict=revise.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Issue: Glad-Labs/poindexter#362.
"""

from __future__ import annotations

import logging
import statistics
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)


ATOM_META = AtomMeta(
    name="atoms.aggregate_reviews",
    type="atom",
    version="1.0.0",
    description=(
        "Fold N qa_reviews from individual critics into one verdict + "
        "aggregate score. Sets _halt=True when the aggregate verdict "
        "is 'reject'. Policy configurable via state."
    ),
    inputs=(
        FieldSpec(
            name="qa_reviews", type="list[Review]",
            description="The Reviews accumulated by review_with_critic atoms.",
            required=True,
        ),
        FieldSpec(
            name="aggregation_strategy", type="str",
            description="One of 'strict', 'average', 'majority'. Default 'strict'.",
            required=False,
        ),
        FieldSpec(
            name="aggregation_pass_threshold", type="float",
            description="Score below which the aggregate verdict is 'revise'. Default 70.",
            required=False,
        ),
    ),
    outputs=(
        FieldSpec(
            name="qa_final_score", type="float",
            description="Aggregate 0-100 score across all critics.",
        ),
        FieldSpec(
            name="qa_final_verdict", type="str",
            description="One of 'approve', 'revise', 'reject'.",
        ),
        FieldSpec(
            name="qa_aggregate_issues", type="list[str]",
            description="Flat list of all critic-surfaced issues (deduped).",
        ),
        FieldSpec(
            name="_halt", type="bool",
            description="Set to True on 'reject' verdict.",
        ),
    ),
    requires=("qa_reviews",),
    produces=("qa_final_score", "qa_final_verdict", "qa_aggregate_issues"),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=(),
    retry=RetryPolicy(max_attempts=1),
    fallback=(),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Atom entry point."""
    reviews = list(state.get("qa_reviews") or [])
    if not reviews:
        logger.info("[atoms.aggregate_reviews] no reviews — passthrough")
        return {
            "qa_final_score": 0.0,
            "qa_final_verdict": "revise",
            "qa_aggregate_issues": ["no critics ran — cannot aggregate"],
        }

    strategy = (state.get("aggregation_strategy") or "strict").lower().strip()
    threshold = float(state.get("aggregation_pass_threshold") or 70.0)

    overall_scores: list[float] = []
    verdicts: list[str] = []
    all_issues: list[str] = []
    for r in reviews:
        if not isinstance(r, dict):
            continue
        try:
            overall_scores.append(float(r.get("overall", 0)))
        except (TypeError, ValueError):
            overall_scores.append(0.0)
        verdicts.append(str(r.get("verdict", "revise")).lower())
        for issue in r.get("issues", []) or []:
            if isinstance(issue, str) and issue.strip():
                all_issues.append(issue.strip())

    # Dedup issues, preserve order.
    seen: set[str] = set()
    deduped_issues = []
    for issue in all_issues:
        if issue not in seen:
            deduped_issues.append(issue)
            seen.add(issue)

    mean_score = statistics.fmean(overall_scores) if overall_scores else 0.0

    if strategy == "majority":
        # Tally and pick the mode; ties resolve to the worse verdict
        # because we'd rather under-publish than over-publish.
        from collections import Counter
        tally = Counter(verdicts)
        ranked_by_severity = ["reject", "revise", "approve"]
        for sev in ranked_by_severity:
            if tally.get(sev, 0) > 0 and tally[sev] >= max(tally.values()):
                verdict = sev
                break
        else:
            verdict = "revise"
    elif strategy == "average":
        verdict = (
            "approve" if mean_score >= threshold
            else "revise" if mean_score >= threshold - 20
            else "reject"
        )
    else:  # strict
        if "reject" in verdicts:
            verdict = "reject"
        elif mean_score < threshold:
            verdict = "revise"
        elif "revise" in verdicts and mean_score < threshold + 10:
            verdict = "revise"
        else:
            verdict = "approve"

    result: dict[str, Any] = {
        "qa_final_score": round(mean_score, 1),
        "qa_final_verdict": verdict,
        "qa_aggregate_issues": deduped_issues,
    }
    if verdict == "reject":
        result["_halt"] = True
        result["_halt_reason"] = (
            f"qa aggregate verdict=reject (score={mean_score:.1f}, "
            f"{len(deduped_issues)} issues)"
        )
    return result


__all__ = ["ATOM_META", "run"]
