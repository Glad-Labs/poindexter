"""qa.programmatic — the programmatic ContentValidator as a composable QA rail.

Restores the hard anti-hallucination gate that stopped running on the live
path when the #355 atom-cutover replaced ``MultiModelQA.review()`` with the
``qa.*`` atom chain. The cutover ported ``qa.critic`` (the LLM critic) but NOT
the ``programmatic_validator`` / ``url_verifier`` legs ``review()`` ran FIRST —
so the regex/heuristic fabrication net (fake people, fake stats, made-up Glad
Labs claims, hallucinated library refs) silently stopped gating, even though
``qa_gates.programmatic_validator.required_to_pass`` is still ``true`` in prod.

Design mirrors ``qa.critic``: this atom runs the deterministic validator
(``modules.content.content_validator.validate_content`` — NO LLM) and contributes a
``ReviewerResult`` into the ``qa_rail_reviews`` channel that ``qa.aggregate``
folds into the gate decision. ``provider='programmatic'`` so
``_qa_rail_common`` weights it at ``validator_weight`` and a non-advisory
failing review vetoes the pass.

Advisory status is DB-driven via ``qa_gates.programmatic_validator.required_to_pass``
(``_mark_advisory_if_configured``): True in prod → hard gate (critical
fabrication is a real veto in qa.aggregate); False → advisory (operator lever,
poindexter#454). Absent gate row (no DB) → stays required, i.e. fail-closed.

Placed FIRST in the qa block (before qa.critic) — the validator is the cheap
deterministic layer, the critic the expensive nuanced one — matching the
"validators first" layering the legacy cross_model_qa stage used internally.
"""

from __future__ import annotations

from typing import Any

from plugins.atom import AtomMeta, FieldSpec
from modules.content.atoms._qa_rail_common import resolve_gate_states, reviewer_to_dict

ATOM_META = AtomMeta(
    name="qa.programmatic",
    type="atom",
    version="1.0.0",
    description=(
        "Programmatic ContentValidator (regex/heuristics, NO LLM) as a QA "
        "rail; advisory is DB-driven via qa_gates.programmatic_validator."
        "required_to_pass (True in prod → hard veto on critical fabrication)."
    ),
    inputs=(FieldSpec(name="content", type="str", description="draft to validate"),),
    outputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="programmatic validator review"),),
    requires=("content",),
    produces=("qa_rail_reviews",),
    capability_tier=None,  # programmatic — no LLM tier
    cost_class="free",
    idempotent=True,
    side_effects=(),
    parallelizable=True,
)


def _score(critical_count: int, warning_count: int) -> float:
    """Map validator counts to a 0-100 rail score.

    100 when clean; a critical issue zeroes the score (it also vetoes via
    ``approved=False``); warnings shave 10 points each so the weighted trend
    reflects soft quality without flipping the gate. A 0-score failing review
    is excluded from the weighted mean by ``aggregate_rail_reviews`` — the veto,
    not the score, is what rejects.
    """
    if critical_count > 0:
        return 0.0
    return max(0.0, 100.0 - 10.0 * warning_count)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = (state.get("content") or "").strip()
    site_config = state.get("site_config")
    if not content or site_config is None:
        return {}

    from modules.content.content_validator import validate_content
    from modules.content.multi_model_qa import MultiModelQA, ReviewerResult

    title = state.get("seo_title") or state.get("title") or ""
    topic = state.get("topic") or ""
    tags = state.get("tags") or []
    niche = state.get("niche")

    # known_wrong_fact-only rejection: when EVERY critical issue is a
    # known_wrong_fact (the stale-regex false-positive on a real post-cutoff
    # product), the legacy review() deferred the rejection to the web
    # fact-check rail, which could OVERRIDE it. We surface that condition as a
    # state flag so qa.aggregate (which owns the veto) can apply the rescue
    # when qa.web_factcheck approved (Glad-Labs/poindexter#661).
    known_wrong_fact_only = False
    try:
        result = validate_content(
            title=title,
            content=content,
            topic=topic,
            tags=list(tags) if isinstance(tags, list) else [],
            niche=niche,
            site_config=site_config,
        )
        crit = result.critical_count
        warn = result.warning_count
        if crit > 0:
            first = result.issues[0].description if result.issues else "?"
            feedback = f"{crit} critical issue(s) — first: {first}"
            # Mirror review()'s _fact_only_rejection: not-passed AND every
            # critical issue is category 'known_wrong_fact'.
            known_wrong_fact_only = not result.passed and all(
                i.severity != "critical" or i.category == "known_wrong_fact"
                for i in result.issues
            )
        elif warn > 0:
            feedback = f"clean (no fabrication); {warn} warning(s)"
        else:
            feedback = "clean — no fabrication or quality issues"
        review = ReviewerResult(
            reviewer="programmatic_validator",
            approved=bool(result.passed) and crit == 0,
            score=_score(crit, warn),
            feedback=feedback,
            provider="programmatic",
        )
    except Exception as exc:  # noqa: BLE001
        # Fail closed: a crashed anti-hallucination net is NOT a pass. Emit a
        # non-advisory failing review (subject to the gate's advisory config
        # below) so qa.aggregate rejects rather than waving content through.
        review = ReviewerResult(
            reviewer="programmatic_validator",
            approved=False,
            score=0.0,
            feedback=f"validator crashed: {type(exc).__name__}: {exc}",
            provider="programmatic",
        )

    pool = getattr(state.get("database_service"), "pool", None)
    settings_service = state.get("settings_service")
    qa = MultiModelQA(pool=pool, settings_service=settings_service, site_config=site_config)
    gate_states = await resolve_gate_states(qa)
    # Advisory is DB-driven: required_to_pass=True (prod) → stays a real veto;
    # False → advisory; absent → stays required (fail-closed).
    MultiModelQA._mark_advisory_if_configured(review, gate_states, "programmatic_validator")
    out: dict[str, Any] = {"qa_rail_reviews": [reviewer_to_dict(review)]}
    # Surface the known_wrong_fact-only flag so qa.aggregate can apply the web
    # fact-check rescue (#661). Last-value channel — only set it when true so a
    # later rail can't accidentally clobber a True with a default False.
    if known_wrong_fact_only:
        out["qa_known_wrong_fact_only"] = True
    return out


__all__ = ["ATOM_META", "run"]
