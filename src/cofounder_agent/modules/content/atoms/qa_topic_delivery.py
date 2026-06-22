"""qa.topic_delivery — the bait-and-switch topic-delivery gate as a rail atom.

Restores the binary topic-delivery veto that stopped running on the live path
when the #355 atom-cutover replaced ``MultiModelQA.review()`` with the ``qa.*``
atom chain (Glad-Labs/poindexter#658). ``review()`` ran it UNCONDITIONALLY as
step "2b" — no ``qa_gates`` row, no advisory marking — so an
``approved=False`` was a hard veto that rejected the post: it catches titles
that promise something the body never delivers (e.g. "11 indie hackers to
follow" with zero hackers named). The cutover ported the text rails but NOT
this gate, so the bait-and-switch veto ran on zero live posts.

Design mirrors ``qa.programmatic`` / ``qa.ragas``: this atom delegates to the
retained ``MultiModelQA._check_topic_delivery(topic, content)`` rail method
(``provider='consistency_gate'`` → ``_qa_rail_common`` weights it at
``gate_weight``) and appends the ``ReviewerResult`` to the ``qa_rail_reviews``
channel that ``qa.aggregate`` folds into the gate decision. Returns nothing
(no rail review) when the topic is empty or Ollama is unreachable.

Advisory status is DB-driven via ``qa_gates.topic_delivery.required_to_pass``
(``_mark_advisory_if_configured``). The #658 fix seeds a NEW ``topic_delivery``
gate row (the gate had none — it was an unconditional veto). Per the
advisory-first restoration posture, it's seeded ``required_to_pass=false`` so
the rail SCORES on every pass but does not yet veto; an operator graduates it
to a hard veto (restoring the legacy binary semantics) with
``poindexter qa-gates require qa.topic_delivery`` (the poindexter#454 lever) —
no code deploy. ``poindexter qa-gates advisory qa.topic_delivery`` reverts it.
"""

from __future__ import annotations

from typing import Any

from modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from plugins.atom import AtomMeta, FieldSpec

ATOM_META = AtomMeta(
    name="qa.topic_delivery",
    type="atom",
    version="1.0.0",
    description=(
        "Bait-and-switch topic-delivery gate — does the body deliver what the "
        "title/topic promised? Advisory is DB-driven via "
        "qa_gates.topic_delivery.required_to_pass (false at restore → scores "
        "but does not veto; flip true to restore the legacy binary veto)."
    ),
    inputs=(FieldSpec(name="content", type="str", description="draft to review"),),
    outputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="topic-delivery review"),),
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
    if not str(topic).strip():
        # No topic to check delivery against — the legacy gate also no-oped here.
        return {}

    pool = getattr(state.get("database_service"), "pool", None)
    settings_service = state.get("settings_service")

    from modules.content.multi_model_qa import MultiModelQA

    qa = MultiModelQA(pool=pool, settings_service=settings_service, site_config=site_config, platform=state.get("platform"))
    gate_states = await resolve_gate_states(qa)
    review = await qa._check_topic_delivery(topic, content)
    if review is None:
        return {}
    # Advisory is DB-driven: required_to_pass=true → real binary veto in
    # qa.aggregate; false (the restore default) → advisory (scores, never vetoes).
    MultiModelQA._mark_advisory_if_configured(review, gate_states, "topic_delivery")
    return {"qa_rail_reviews": [reviewer_to_dict(review)]}


__all__ = ["ATOM_META", "run"]
