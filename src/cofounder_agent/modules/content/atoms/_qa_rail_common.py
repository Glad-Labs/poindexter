"""Shared helpers for the qa.* rail atoms (atom-cutover Plan 3, #355).

Underscore-prefixed so ``atom_registry._walk_package`` skips it — this is
a helper module, NOT a discoverable atom. Holds the ReviewerResult->dict
serializer and the pure rail-aggregation function (weighted score +
non-advisory veto + threshold), both unit-testable without a DB.

The aggregation mirrors the CORE of multi_model_qa.review()'s decision
(services/multi_model_qa.py:762-861): provider-weighted mean of the
positive scores, a veto from any non-advisory failing review, and
``approved = all_passed and final_score >= threshold``. Per the spec's
"no parity check" clause (the granularity refactor changes behavior, so
validation is quality-canary based), it intentionally omits the
validator-warning penalty and the web-factcheck override, which depend on
rails outside the OSS qa.* set.
"""

from __future__ import annotations

from typing import Any

# Provider -> weight bucket. programmatic = validator weight; the LLM
# critics = critic weight; the gate providers = gate weight. Unknown
# providers default to 0.5 (matches multi_model_qa.review()).
_VALIDATOR_PROVIDERS = ("programmatic",)
_CRITIC_PROVIDERS = ("anthropic", "google", "ollama")
_GATE_PROVIDERS = ("consistency_gate", "vision_gate", "web_factcheck", "url_verifier")


def reviewer_to_dict(r: Any) -> dict[str, Any]:
    """Serialize a ReviewerResult (or duck-typed equivalent) to a plain
    dict for the ``qa_rail_reviews`` state channel."""
    return {
        "reviewer": r.reviewer,
        "approved": bool(r.approved),
        "score": float(r.score),
        "feedback": getattr(r, "feedback", "") or "",
        "provider": r.provider,
        "advisory": bool(getattr(r, "advisory", False)),
    }


def _weight_for(provider: str | None, *, validator_weight: float, critic_weight: float, gate_weight: float) -> float:
    if provider in _VALIDATOR_PROVIDERS:
        return validator_weight
    if provider in _CRITIC_PROVIDERS:
        return critic_weight
    if provider in _GATE_PROVIDERS:
        return gate_weight
    return 0.5


def known_wrong_fact_rescued(
    reviews: list[dict[str, Any]],
    vetoed_by: list[Any],
    *,
    known_wrong_fact_only: bool,
) -> bool:
    """Decide whether the programmatic_validator's known_wrong_fact veto is
    rescued by the web fact-check rail (Glad-Labs/poindexter#661).

    Mirrors review()'s ``_fact_only_rejection`` carve-out: when the ONLY
    non-advisory veto is the ``programmatic_validator`` and its rejection was
    a known_wrong_fact-only critical (the stale-regex false-positive on a real
    post-cutoff product), the web fact-check gets to override it. The rescue
    fires only when a ``web_factcheck`` review is present AND approved — a
    missing/failed web check upholds the rejection (matches the legacy else
    branch). All other critical categories never reach here.

    Returns True when the ``programmatic_validator`` veto should be SUPPRESSED.
    This is a correctness fix that prevents a WRONG hard-reject, not a new veto.
    """
    if not known_wrong_fact_only:
        return False
    # The validator's veto must be the only thing rejecting the pass.
    if list(vetoed_by) != ["programmatic_validator"]:
        return False
    web = next(
        (r for r in reviews if r.get("reviewer") == "web_factcheck"), None,
    )
    return bool(web is not None and web.get("approved"))


def aggregate_rail_reviews(
    reviews: list[dict[str, Any]],
    *,
    validator_weight: float = 0.4,
    critic_weight: float = 0.6,
    gate_weight: float = 0.3,
    threshold: float = 70.0,
    known_wrong_fact_only: bool = False,
) -> dict[str, Any]:
    """Combine per-rail review dicts into the gate decision.

    ``known_wrong_fact_only`` (#661): when the programmatic validator's only
    critical was a known_wrong_fact (set by qa.programmatic on the shared
    state), a present + approved ``web_factcheck`` review SUPPRESSES the
    validator veto — restoring the legacy review() rescue for stale-regex
    false-positives on real post-cutoff products.

    Returns ``{"qa_final_score", "qa_final_verdict", "approved", "vetoed_by",
    "known_wrong_fact_rescued"}``.
    """
    def _score(r: dict[str, Any]) -> float:
        try:
            return float(r.get("score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    # Advisory rails (required_to_pass=False) inform but MUST NOT gate. They
    # already cannot veto (see vetoed_by below); excluding them from the
    # weighted score too keeps the "advisory" contract honest. Without this,
    # low-scoring advisory rails (g_eval, ragas, internal_consistency) silently
    # dragged the gated mean below threshold and rejected otherwise-clean posts
    # (2026-06 incident). Fall back to all positive-scored reviews only in the
    # degenerate case where no non-advisory rail scored, so the score stays
    # non-zero instead of collapsing to a spurious reject.
    non_advisory_scored = [
        r for r in reviews if _score(r) > 0 and not r.get("advisory")
    ]
    scored = non_advisory_scored or [r for r in reviews if _score(r) > 0]
    if scored:
        total_w = sum(
            _weight_for(r.get("provider"), validator_weight=validator_weight,
                        critic_weight=critic_weight, gate_weight=gate_weight)
            for r in scored
        )
        if total_w > 0:
            final_score = sum(
                _score(r) * _weight_for(r.get("provider"), validator_weight=validator_weight,
                                        critic_weight=critic_weight, gate_weight=gate_weight)
                for r in scored
            ) / total_w
        else:
            final_score = 0.0
    else:
        final_score = 0.0

    # A non-advisory failing review vetoes the whole pass.
    vetoed_by = [
        r.get("reviewer") for r in reviews
        if not r.get("approved") and not r.get("advisory")
    ]

    # #661 rescue: a known_wrong_fact-only validator veto that the web
    # fact-check rail confirmed is suppressed (the stale regex was wrong).
    rescued = known_wrong_fact_rescued(
        reviews, vetoed_by, known_wrong_fact_only=known_wrong_fact_only,
    )
    if rescued:
        vetoed_by = [v for v in vetoed_by if v != "programmatic_validator"]

    all_passed = not vetoed_by
    approved = all_passed and final_score >= threshold
    return {
        "qa_final_score": round(float(final_score), 2),
        "qa_final_verdict": "approve" if approved else "reject",
        "approved": approved,
        "vetoed_by": vetoed_by,
        "known_wrong_fact_rescued": rescued,
    }


def missing_required_gates(
    reviews: list[dict[str, Any]],
    gate_states: dict[str, tuple[bool, bool]],
) -> list[str]:
    """Required + enabled gates that produced NO matching review.

    Powers the qa.aggregate vacuous-pass guard (poindexter#680): a required
    rail that emits no review would otherwise pass silently, because
    ``aggregate_rail_reviews`` only vetoes on reviews it can *see*. We fail
    closed when a required gate is genuinely absent.

    Reviewer→gate aliasing is load-bearing here. The critic writes
    ``reviewer="ollama_critic"`` while its gate row is named ``llm_critic``
    (and ``internal_consistency`` → ``consistency``). Each review's reviewer
    is resolved through ``qa_gates_db_writer.reviewer_to_gate`` — the single
    source of truth for that aliasing — with identity fallback for reviewers
    whose name already equals their gate name. Without the alias, the raw
    name membership test saw the required ``llm_critic`` gate as absent and
    hard-rejected EVERY otherwise-passing post (regression introduced with
    the #680 guard; the alias table predates it but the guard didn't use it).

    ``gate_states`` is ``{name: (enabled, required_to_pass)}`` as returned by
    ``resolve_gate_states``. An empty mapping (no-DB / fresh checkout) yields
    an empty list — the guard then leaves the aggregate decision untouched.
    """
    from services.qa_gates_db_writer import reviewer_to_gate

    present: set[str] = set()
    for r in reviews:
        reviewer = r.get("reviewer")
        if not reviewer:
            continue
        present.add(reviewer_to_gate(reviewer) or reviewer)
    return [
        name
        for name, (enabled, required) in gate_states.items()
        if required and enabled and name not in present
    ]


async def resolve_gate_states(qa: Any) -> dict[str, Any]:
    """Best-effort ``{gate_name: (enabled, required_to_pass)}`` from ``qa_gates`` via
    the MultiModelQA instance. Empty dict on any failure — advisory marking
    then leaves reviews as seeded (qa_gates is baseline-seeded in prod, so ``{}``
    only happens in no-DB environments)."""
    try:
        return await qa._load_gate_states()
    except Exception:  # noqa: BLE001
        return {}


__all__ = [
    "aggregate_rail_reviews",
    "known_wrong_fact_rescued",
    "missing_required_gates",
    "resolve_gate_states",
    "reviewer_to_dict",
]
