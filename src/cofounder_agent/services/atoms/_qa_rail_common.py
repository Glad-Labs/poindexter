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


def aggregate_rail_reviews(
    reviews: list[dict[str, Any]],
    *,
    validator_weight: float = 0.4,
    critic_weight: float = 0.6,
    gate_weight: float = 0.3,
    threshold: float = 70.0,
) -> dict[str, Any]:
    """Combine per-rail review dicts into the gate decision.

    Returns ``{"qa_final_score", "qa_final_verdict", "approved", "vetoed_by"}``.
    """
    def _score(r: dict[str, Any]) -> float:
        try:
            return float(r.get("score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    scored = [r for r in reviews if _score(r) > 0]
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
    all_passed = not vetoed_by
    approved = all_passed and final_score >= threshold
    return {
        "qa_final_score": round(float(final_score), 2),
        "qa_final_verdict": "approve" if approved else "reject",
        "approved": approved,
        "vetoed_by": vetoed_by,
    }


__all__ = ["aggregate_rail_reviews", "reviewer_to_dict"]
