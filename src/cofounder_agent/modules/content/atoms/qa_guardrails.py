"""qa.guardrails — the guardrails-ai rail family as one composable atom.

Atom-cutover Plan 3 (#355). Wraps MultiModelQA's guardrails rails
(brand + competitor) by delegation. Advisory status is DB-driven via
``qa_gates.<rail>.required_to_pass`` (False → advisory; True → required
hard gate). Baseline seeds guardrails rails as advisory. Appends to
qa_rail_reviews.
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec
from modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict

ATOM_META = AtomMeta(
    name="qa.guardrails",
    type="atom",
    version="1.0.0",
    description="guardrails-ai rails (brand + competitor); advisory is DB-driven via qa_gates.<rail>.required_to_pass.",
    inputs=(FieldSpec(name="content", type="str", description="draft to review"),),
    outputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="advisory reviews"),),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    parallelizable=True,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        return {}

    pool = getattr(state.get("database_service"), "pool", None)
    settings_service = state.get("settings_service")

    from modules.content.multi_model_qa import MultiModelQA

    qa = MultiModelQA(pool=pool, settings_service=settings_service, site_config=site_config)
    gate_states = await resolve_gate_states(qa)
    brand = await qa._check_guardrails_brand(content)
    competitor = await qa._check_guardrails_competitor(content)

    reviews: list[dict[str, Any]] = []
    for r in (brand, competitor):
        if r is not None:
            MultiModelQA._mark_advisory_if_configured(r, gate_states, r.reviewer)
            reviews.append(reviewer_to_dict(r))
    return {"qa_rail_reviews": reviews} if reviews else {}


__all__ = ["ATOM_META", "run"]
