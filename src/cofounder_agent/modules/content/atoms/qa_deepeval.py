"""qa.deepeval — the DeepEval rail family as one composable atom.

Atom-cutover Plan 3 (#355). Wraps MultiModelQA's three DeepEval rail
methods (brand-fabrication, g-eval, faithfulness) by delegating to a
MultiModelQA instance — zero rail logic is reimplemented. Each rail
self-gates (returns None when disabled or inapplicable). Advisory status
is DB-driven via ``qa_gates.<rail>.required_to_pass`` (False → advisory;
True → required hard gate). Baseline seeds deepeval rails as advisory.
Results are appended to the qa_rail_reviews channel. parallelizable=True.
"""

from __future__ import annotations

from typing import Any

from modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from plugins.atom import AtomMeta, FieldSpec

ATOM_META = AtomMeta(
    name="qa.deepeval",
    type="atom",
    version="1.0.0",
    description="DeepEval rails (brand-fabrication + g-eval + faithfulness); advisory is DB-driven via qa_gates.<rail>.required_to_pass.",
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

    # Lazy import — keeps module discovery cheap (multi_model_qa is heavy).
    from modules.content.multi_model_qa import MultiModelQA

    qa = MultiModelQA(pool=pool, settings_service=settings_service, site_config=site_config, platform=state.get("platform"))

    # Load gate states and run the three independent judges in parallel (#730).
    # _check_deepeval_brand is synchronous (regex-based); run it in a thread so
    # asyncio.gather can overlap it with the two async Ollama calls. Each judge
    # is independent — one failing should not abort the others, so we use
    # return_exceptions=True and treat any exception as a None (skipped rail).
    import asyncio

    gate_states, brand, g_eval, faith = await asyncio.gather(
        resolve_gate_states(qa),
        asyncio.to_thread(qa._check_deepeval_brand, content, topic),
        qa._check_deepeval_g_eval(content, topic),
        qa._check_deepeval_faithfulness(content, research),
        return_exceptions=True,
    )

    # Normalize exception results to None (graceful skip on per-judge failure).
    gate_states = gate_states if isinstance(gate_states, dict) else {}

    reviews: list[dict[str, Any]] = []
    for r in (brand, g_eval, faith):
        if isinstance(r, BaseException):
            import logging
            logging.getLogger(__name__).warning(
                "[qa.deepeval] judge raised an exception (skipping): %s", r,
            )
            continue
        if r is not None:
            MultiModelQA._mark_advisory_if_configured(r, gate_states, r.reviewer)
            reviews.append(reviewer_to_dict(r))
    return {"qa_rail_reviews": reviews} if reviews else {}


__all__ = ["ATOM_META", "run"]
