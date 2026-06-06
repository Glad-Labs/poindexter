"""qa.critic — the legacy adversarial LLM critic as a composable atom.

Atom-cutover Plan 3 (#355). Wraps MultiModelQA._review_with_cloud_model
(returns (ReviewerResult, cost_log) | None) by delegation. Advisory status
is DB-driven via ``qa_gates.llm_critic.required_to_pass`` (True in prod
→ hard gate that vetoes in qa.aggregate; False → advisory, operator-
configurable via poindexter#454 lever). Title is sourced from seo_title
(falling back to title), mirroring the cross_model_qa stage.
"""

from __future__ import annotations

from typing import Any

from modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from plugins.atom import AtomMeta, FieldSpec

ATOM_META = AtomMeta(
    name="qa.critic",
    type="atom",
    version="1.0.0",
    description="Adversarial LLM critic; advisory is DB-driven via qa_gates.llm_critic.required_to_pass (True in prod → hard gate).",
    inputs=(FieldSpec(name="content", type="str", description="draft to review"),),
    outputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="critic review"),),
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

    title = state.get("seo_title") or state.get("title") or ""
    topic = state.get("topic") or ""
    research = state.get("research_context")
    pool = getattr(state.get("database_service"), "pool", None)
    settings_service = state.get("settings_service")

    from modules.content.multi_model_qa import MultiModelQA

    qa = MultiModelQA(pool=pool, settings_service=settings_service, site_config=site_config, platform=state.get("platform"))
    gate_states = await resolve_gate_states(qa)
    result = await qa._review_with_cloud_model(title, content, topic, research_sources=research)
    if result is None:
        return {}
    reviewer_result, _cost_log = result
    # Advisory status is DB-driven: qa_gates.llm_critic.required_to_pass=True
    # in prod → hard gate (veto in qa.aggregate). An operator can flip it to
    # advisory (required_to_pass=False) via the poindexter#454 lever without
    # a code deploy.
    MultiModelQA._mark_advisory_if_configured(reviewer_result, gate_states, "llm_critic")
    return {"qa_rail_reviews": [reviewer_to_dict(reviewer_result)]}


__all__ = ["ATOM_META", "run"]
