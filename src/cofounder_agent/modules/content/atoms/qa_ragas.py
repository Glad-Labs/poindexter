"""qa.ragas — the Ragas rail as one composable atom.

Atom-cutover Plan 3 (#355). Wraps MultiModelQA._check_ragas_eval by
delegation. Advisory status is DB-driven via
``qa_gates.ragas_eval.required_to_pass`` (False → advisory; True →
required hard gate). Baseline seeds ragas_eval as advisory. Yields
nothing when research context is absent (the rail needs retrieved
contexts). Appends to qa_rail_reviews.
"""

from __future__ import annotations

from typing import Any

from modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from plugins.atom import AtomMeta, FieldSpec

ATOM_META = AtomMeta(
    name="qa.ragas",
    type="atom",
    version="1.0.0",
    description="Ragas faithfulness/relevancy/precision rail; advisory is DB-driven via qa_gates.ragas_eval.required_to_pass.",
    inputs=(FieldSpec(name="content", type="str", description="draft to review"),),
    outputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="advisory reviews"),),
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

    topic = state.get("topic") or ""
    research = state.get("research_context")
    pool = getattr(state.get("database_service"), "pool", None)
    settings_service = state.get("settings_service")

    from modules.content.multi_model_qa import MultiModelQA

    qa = MultiModelQA(pool=pool, settings_service=settings_service, site_config=site_config, platform=state.get("platform"))
    gate_states = await resolve_gate_states(qa)
    ragas = await qa._check_ragas_eval(content, topic, research)
    if ragas is None:
        return {}
    MultiModelQA._mark_advisory_if_configured(ragas, gate_states, ragas.reviewer)
    return {"qa_rail_reviews": [reviewer_to_dict(ragas)]}


__all__ = ["ATOM_META", "run"]
