"""qa.aggregate — combine the qa.* rail reviews into the QA gate decision.

Atom-cutover #355. Reads the ``qa_rail_reviews`` channel, applies the
DB-configurable weighted-score + non-advisory-veto + threshold aggregation
(_qa_rail_common.aggregate_rail_reviews), and acts as the QA-decision point
the cross_model_qa stage used to be:

- APPROVE: emit qa_final_score / qa_final_verdict, promote
  quality_score = max(early, qa) and populate qa_reviews (read by
  finalize_task for the approval-UI feedback).
- REJECT: do the same DB writes the legacy stage did (via _qa_persist) —
  status=rejected + rejected-draft + model_performance + gate_history —
  then set _halt so build_graph_from_spec's halt-aware router short-circuits
  the graph (skipping the rest of the pipeline), mirroring the legacy
  continue_workflow=False.
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec
from services.atoms._qa_rail_common import aggregate_rail_reviews

ATOM_META = AtomMeta(
    name="qa.aggregate",
    type="atom",
    version="2.0.0",
    description="Combine qa.* rail reviews into the QA gate decision (+ reject persistence).",
    inputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="per-rail reviews"),),
    outputs=(
        FieldSpec(name="qa_final_score", type="float", description="weighted QA score"),
        FieldSpec(name="qa_final_verdict", type="str", description="approve|reject"),
        FieldSpec(name="quality_score", type="float", description="promoted max(early, qa)"),
        FieldSpec(name="qa_reviews", type="list[dict]", description="reviews for the approval UI"),
    ),
    requires=("qa_rail_reviews",),
    produces=("qa_final_score", "qa_final_verdict", "quality_score", "qa_reviews"),
    capability_tier=None,
    cost_class="free",
    idempotent=False,
    side_effects=("writes pipeline_tasks/pipeline_versions/pipeline_gate_history on reject",),
    parallelizable=False,
)


def _weight(site_config: Any, key: str, default: float) -> float:
    if site_config is None:
        return default
    try:
        return float(site_config.get(key, default))
    except (TypeError, ValueError):
        return default


async def run(state: dict[str, Any]) -> dict[str, Any]:
    site_config = state.get("site_config")
    reviews = state.get("qa_rail_reviews") or []
    result = aggregate_rail_reviews(
        reviews,
        validator_weight=_weight(site_config, "qa_validator_weight", 0.4),
        critic_weight=_weight(site_config, "qa_critic_weight", 0.6),
        gate_weight=_weight(site_config, "qa_gate_weight", 0.3),
        threshold=_weight(site_config, "qa_final_score_threshold", 70.0),
    )
    final_score = result["qa_final_score"]

    # Promote the canonical quality_score (max of early-eval + QA), mirroring
    # the legacy stage so downstream finalize_task / auto-publish use the QA score.
    early = 0.0
    try:
        early = float(state.get("quality_score") or 0.0)
    except (TypeError, ValueError):
        early = 0.0
    promoted = max(early, float(final_score))

    out: dict[str, Any] = {
        "qa_final_score": final_score,
        "qa_final_verdict": result["qa_final_verdict"],
        "quality_score": promoted,
        # qa_reviews uses an operator.add reducer; it's empty before this node
        # in canonical_blog (rails write qa_rail_reviews), so this populates it
        # for finalize_task's qa_feedback.
        "qa_reviews": list(reviews),
        "qa_rewrite_attempts": 0,
    }

    if not result["approved"]:
        from services.atoms._qa_persist import (
            build_qa_feedback,
            build_reject_reason,
            persist_qa_reject,
        )
        reason = build_reject_reason(reviews, result["vetoed_by"], float(final_score))
        await persist_qa_reject(
            state.get("database_service"),
            task_id=str(state.get("task_id") or ""),
            reason=reason,
            final_score=float(final_score),
            content=str(state.get("content") or ""),
            title=str(state.get("title") or state.get("topic") or ""),
            qa_feedback=build_qa_feedback(reviews, float(final_score), approved=False),
            models_used_by_phase=state.get("models_used_by_phase") or {},
        )
        out["_halt"] = True
        out["_halt_reason"] = f"qa.aggregate: reject (score={final_score}, {reason[:120]})"
        # Belt-and-suspenders: the DB write above is load-bearing (status is
        # not a PipelineState channel), but set it in state too in case a
        # caller reads final_state.
        out["status"] = "rejected"

    return out


__all__ = ["ATOM_META", "run"]
