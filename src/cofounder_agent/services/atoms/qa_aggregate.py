"""qa.aggregate — combine the qa.* rail reviews into the gate decision.

Atom-cutover Plan 3 (#355). Reads the ``qa_rail_reviews`` channel (the
ReviewerResult dicts emitted by qa.deepeval / qa.guardrails / qa.ragas /
qa.critic), applies the DB-configurable weighted-score + non-advisory-veto
+ threshold aggregation (services/atoms/_qa_rail_common.py), and emits
``qa_final_score`` / ``qa_final_verdict``. On reject it sets ``_halt`` so
build_graph_from_spec's halt-aware router short-circuits the graph.
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec
from services.atoms._qa_rail_common import aggregate_rail_reviews

ATOM_META = AtomMeta(
    name="qa.aggregate",
    type="atom",
    version="1.0.0",
    description="Combine qa.* rail reviews into the final QA gate decision.",
    inputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="per-rail reviews"),),
    outputs=(
        FieldSpec(name="qa_final_score", type="float", description="weighted QA score"),
        FieldSpec(name="qa_final_verdict", type="str", description="approve|reject"),
    ),
    requires=("qa_rail_reviews",),
    produces=("qa_final_score", "qa_final_verdict"),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
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
    out: dict[str, Any] = {
        "qa_final_score": result["qa_final_score"],
        "qa_final_verdict": result["qa_final_verdict"],
    }
    if not result["approved"]:
        out["_halt"] = True
        out["_halt_reason"] = (
            f"qa.aggregate: verdict=reject score={result['qa_final_score']} "
            f"vetoed_by={result['vetoed_by']}"
        )
    return out


__all__ = ["ATOM_META", "run"]
