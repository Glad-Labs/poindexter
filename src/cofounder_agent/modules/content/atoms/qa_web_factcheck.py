"""qa.web_factcheck — the web-grounded fact-check rail as a composable atom.

Restores the ``web_factcheck`` reviewer AND the ``known_wrong_fact`` web-rescue
that BOTH stopped running on the live path when the #355 atom-cutover replaced
``MultiModelQA.review()`` with the ``qa.*`` atom chain (Glad-Labs/poindexter#661).

``review()`` ran a DuckDuckGo lookup (step "2e") that verifies product/spec
claims the LLM critic cannot — the training-cutoff override: a local model
rejects "RTX 5090 has 32GB VRAM" because it was trained before release, but a
web search confirms it in seconds (the cutoff-fabrication false-positive problem
in project_qa_critic_cutoff). The reviewer is ``provider='web_factcheck'`` →
``_qa_rail_common`` weights it at ``gate_weight``.

Critically, ``review()`` ALSO had a rescue: when the programmatic validator
flagged ONLY ``known_wrong_fact`` criticals (a real post-cutoff product the
stale regex thinks is fabricated), the rejection was deferred to this rail —
and if the web confirmed the claims, the validator's rejection was OVERRIDDEN.
On the live path that rescue path no longer existed, so ``qa.programmatic``
emitted a non-advisory ``known_wrong_fact`` veto that HARD-REJECTED legit
post-cutoff content with no web second opinion.

This atom restores half the rescue: it runs the web fact-check and appends its
review to ``qa_rail_reviews`` so ``qa.aggregate`` can read it. The other half —
SUPPRESSING the ``known_wrong_fact`` veto when web approved — lives in
``qa.aggregate`` (it owns the veto decision). That's why this rail is ordered
LAST in the qa block, immediately before ``qa.aggregate``: the rescue depends
on the web review existing when aggregate runs.

``qa.programmatic`` flags the ``known_wrong_fact``-only condition on the shared
state (``qa_known_wrong_fact_only``); ``qa.aggregate`` reads that flag + this
rail's verdict to decide the rescue. See ``qa.aggregate`` for the suppression
logic and ``services/atoms/_qa_rail_common.py`` for the rescue helper.

Advisory status is DB-driven via ``qa_gates.web_factcheck.required_to_pass``
(``_mark_advisory_if_configured``); the baseline seeds it advisory
(``enabled=true, required_to_pass=false``) and the restore keeps it there — the
rail scores but never vetoes on its own. Its RESCUE power (suppressing a
known_wrong_fact veto) is a correctness fix that PREVENTS a wrong hard-reject,
not a new veto. Returns nothing when no checkable product/spec claims are found
or the search fails (both legacy no-ops).
"""

from __future__ import annotations

from typing import Any

from modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict
from plugins.atom import AtomMeta, FieldSpec

ATOM_META = AtomMeta(
    name="qa.web_factcheck",
    type="atom",
    version="1.0.0",
    description=(
        "Web-grounded fact-check rail (DuckDuckGo) for product/spec claims the "
        "LLM critic cannot verify (training-cutoff override). Advisory is "
        "DB-driven via qa_gates.web_factcheck.required_to_pass. Also powers the "
        "known_wrong_fact rescue read by qa.aggregate."
    ),
    inputs=(FieldSpec(name="content", type="str", description="draft to fact-check"),),
    outputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="web-factcheck review"),),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier=None,  # web search — no LLM tier
    cost_class="free",  # DuckDuckGo search only — zero LLM/API spend in the budget model
    idempotent=False,
    side_effects=("DuckDuckGo web searches for product/spec claims",),
    parallelizable=True,
)


class _RailReviewView:
    """Duck-typed read-only view over a qa_rail_reviews dict.

    ``MultiModelQA._web_fact_check`` inspects ``existing_reviews`` for
    ``.provider == 'ollama'`` critics that are unapproved or low-scoring (to
    decide ``critic_concerned``). The live rail reviews are plain dicts; wrap
    them so the legacy attribute access keeps working without reconstructing a
    full ReviewerResult dataclass.
    """

    __slots__ = ("approved", "score", "provider")

    def __init__(self, d: dict[str, Any]) -> None:
        self.approved = bool(d.get("approved"))
        try:
            self.score = float(d.get("score") or 0.0)
        except (TypeError, ValueError):
            self.score = 0.0
        self.provider = d.get("provider")


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        return {}

    title = state.get("seo_title") or state.get("title") or state.get("topic") or ""
    topic = state.get("topic") or ""
    pool = getattr(state.get("database_service"), "pool", None)
    settings_service = state.get("settings_service")

    # Reviews accumulated by the upstream qa.* rails (operator.add channel) —
    # _web_fact_check reads them to gauge whether a critic was already concerned.
    existing = [
        _RailReviewView(r)
        for r in (state.get("qa_rail_reviews") or [])
        if isinstance(r, dict)
    ]

    from modules.content.multi_model_qa import MultiModelQA

    qa = MultiModelQA(pool=pool, settings_service=settings_service, site_config=site_config, platform=state.get("platform"))
    gate_states = await resolve_gate_states(qa)
    review = await qa._web_fact_check(title, topic, content, existing)  # type: ignore[arg-type]
    if review is None:
        # No checkable claims / search failed — legacy no-op. The
        # known_wrong_fact rescue in qa.aggregate then finds no web review and
        # upholds the validator rejection (mirrors review()'s else-branch).
        return {}
    # Advisory is DB-driven: the baseline seeds web_factcheck advisory, so this
    # rail scores but does not veto on its own. (Its rescue power lives in
    # qa.aggregate and is independent of this advisory flag.)
    MultiModelQA._mark_advisory_if_configured(review, gate_states, "web_factcheck")
    return {"qa_rail_reviews": [reviewer_to_dict(review)]}


__all__ = ["ATOM_META", "run"]
