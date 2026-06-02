"""qa.critic — the legacy adversarial LLM critic as a composable atom.

Atom-cutover Plan 3 (#355). Wraps MultiModelQA._review_with_cloud_model
(returns (ReviewerResult, cost_log) | None) by delegation. Unlike the OSS
rails, the critic is the HARD gate — its review is NOT advisory, so a
failing critic vetoes in qa.aggregate. Title is sourced from seo_title
(falling back to title), mirroring the cross_model_qa stage.
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec
from services.atoms._qa_rail_common import reviewer_to_dict

ATOM_META = AtomMeta(
    name="qa.critic",
    type="atom",
    version="1.0.0",
    description="Adversarial LLM critic (the hard QA gate, non-advisory).",
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

    from services.multi_model_qa import MultiModelQA

    qa = MultiModelQA(pool=pool, settings_service=settings_service, site_config=site_config)
    result = await qa._review_with_cloud_model(title, content, topic, research_sources=research)
    if result is None:
        return {}
    reviewer_result, _cost_log = result
    # Hard gate — leave advisory at its default False so qa.aggregate can veto.
    return {"qa_rail_reviews": [reviewer_to_dict(reviewer_result)]}


__all__ = ["ATOM_META", "run"]
