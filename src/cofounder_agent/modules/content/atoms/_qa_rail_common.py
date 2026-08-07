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

from services.logger_config import get_logger

logger = get_logger(__name__)

# Provider -> weight bucket. programmatic = validator weight; the LLM
# critics = critic weight; the gate providers = gate weight. Unknown
# providers default to 0.5 (matches multi_model_qa.review()).
_VALIDATOR_PROVIDERS = ("programmatic",)
_CRITIC_PROVIDERS = ("anthropic", "google", "ollama")
_GATE_PROVIDERS = (
    "consistency_gate",
    "vision_gate",
    "web_factcheck",
    "url_verifier",
    "title_coherence_gate",
)

# Providers whose veto a text revision can NEVER fix (bad image / dead link).
# Under broaden=True these stay surface-direct (no regen) — see is_rescuable_reject.
# ``http_head`` is the citation_verifier dead-link-ratio rail: the writer re-runs
# from the same research bundle and re-cites the same dead URL, so a rewrite burns
# a pass without fixing it — flag directly, same as the single-link url_verifier.
_NON_TEXT_FIXABLE_PROVIDERS = ("vision_gate", "url_verifier", "http_head")


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


def is_rescuable_reject(
    reviews: list[dict[str, Any]],
    vetoed_by: list[Any],
    *,
    final_score: float,
    threshold: float,
    broaden: bool = False,
    content: str | None = None,
) -> bool:
    """Decide whether a qa.aggregate REJECT is eligible for one rewrite pass.

    ``broaden=False`` (default — the switch-off path) keeps the original rule:
    rescuable only when the reject came from a SOFT judgment a targeted
    revision could plausibly fix.

    (a) Critic-only veto: ``vetoed_by`` is non-empty AND every vetoing
        reviewer resolves to a critic provider (anthropic/google/ollama). A
        ``programmatic_validator`` veto (fabrication/structure), a
        gate-provider veto (consistency/vision/web_factcheck/url), or a
        synthetic ``missing_required:*`` veto makes the reject NON-rescuable.
    (b) Score-threshold reject: ``vetoed_by == []`` (no hard veto — the
        critic approved) AND ``final_score < threshold`` (the weighted score
        fell below the floor). A rewrite can lift the score.

    ``broaden=True`` (self-heal-before-paging — the qa_flag_instead_of_reject
    switch is on) widens (a): the garbage-collector reruns ANY draft a text
    revision could plausibly fix before flag-and-continue, so a
    ``programmatic_validator`` / brand / factcheck / consistency veto is now
    rescuable too. It STILL returns False for a ``missing_required:*`` veto
    (infra — a rerun won't make an absent rail emit) and for a
    non-text-fixable gate (``vision_gate`` / ``url_verifier`` /
    ``citation_verifier`` (``http_head``) — a text revise can't fix a bad
    image or a dead external link).

    Returns False for an empty veto whose score already clears the threshold
    (i.e. an approve) under either mode.

    ``content`` (Glad-Labs/poindexter#986): when the caller passes the draft
    under review and it is TRUNCATED by the deterministic #984 detector, the
    reject is non-rescuable under BOTH modes regardless of who vetoed. The
    severed tail no longer exists — a text revision can only invent an
    ending (a fabrication vector) or ship a shorter mangle, and every 2026-07
    rescue of a truncated draft re-failed after burning a full rail re-run.
    The producer must regenerate; a rewrite cannot restore lost content.
    """
    # Truncated input is unrecoverable by revision — both modes (#986).
    if content is not None:
        from modules.content.content_validator import detect_truncated_content

        if detect_truncated_content(content):
            return False

    # (b) Score-threshold reject: nothing vetoed, just below the bar.
    if not vetoed_by:
        return final_score < threshold

    # (a) Map each vetoing reviewer back to its provider.
    by_name = {r.get("reviewer"): r for r in reviews}
    for name in vetoed_by:
        if isinstance(name, str) and name.startswith("missing_required:"):
            return False  # vacuous-pass guard veto — infra, not content
        review = by_name.get(name)
        if review is None:
            return False  # unknown veto source — fail safe, don't rescue
        provider = review.get("provider")
        if broaden:
            if provider in _NON_TEXT_FIXABLE_PROVIDERS:
                return False  # a text revise can't fix a bad image / dead link
            continue  # every other veto is regen-eligible under self-heal
        if provider not in _CRITIC_PROVIDERS:
            return False  # switch-off: programmatic / gate veto — hard correctness
    return True


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

    # Informational-only visibility (#2125): the operator wants to SEE a score
    # that reflects EVERY rail (advisory included) plus a per-rail breakdown,
    # even though only the non-advisory rails feed the gating ``qa_final_score``
    # and only the two hard rejectors can veto. ``qa_all_rail_score`` is the
    # weighted mean over ALL positive-scored reviews; ``rail_breakdown`` is the
    # per-rail detail (score, pass, advisory flag, feedback) so a false positive
    # is diagnosable instead of invisible. Neither gates — do NOT compare
    # ``qa_all_rail_score`` to the threshold.
    all_scored = [r for r in reviews if _score(r) > 0]
    if all_scored:
        all_w = sum(
            _weight_for(r.get("provider"), validator_weight=validator_weight,
                        critic_weight=critic_weight, gate_weight=gate_weight)
            for r in all_scored
        )
        all_rail_score = (
            sum(
                _score(r) * _weight_for(
                    r.get("provider"), validator_weight=validator_weight,
                    critic_weight=critic_weight, gate_weight=gate_weight)
                for r in all_scored
            ) / all_w
            if all_w > 0 else 0.0
        )
    else:
        all_rail_score = 0.0

    rail_breakdown = [
        {
            "reviewer": r.get("reviewer"),
            "provider": r.get("provider"),
            "score": round(_score(r), 2),
            "approved": bool(r.get("approved")),
            "advisory": bool(r.get("advisory")),
            "gated": _score(r) > 0 and not r.get("advisory"),
            "feedback": (r.get("feedback") or "")[:200],
        }
        for r in reviews
    ]

    return {
        "qa_final_score": round(float(final_score), 2),
        "qa_final_verdict": "approve" if approved else "reject",
        "approved": approved,
        "vetoed_by": vetoed_by,
        "known_wrong_fact_rescued": rescued,
        # Informational only — see comment above. Not used for gating.
        "qa_all_rail_score": round(float(all_rail_score), 2),
        "rail_breakdown": rail_breakdown,
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
    ``resolve_gate_states``. An empty mapping (no-DB environments only —
    with a live pool ``resolve_gate_states`` raises instead of returning
    ``{}``) yields an empty list — the guard then leaves the aggregate
    decision untouched.
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


class GateStatesUnavailable(RuntimeError):
    """``qa_gates`` config could not be read even though a DB pool is live.

    Raised by :func:`resolve_gate_states` so a ``qa.*`` rail atom fails
    LOUDLY — the node halts the run as a retryable infra failure — instead
    of proceeding with ``gate_states={}``. Proceeding is the dangerous
    path: ``MultiModelQA._mark_advisory_if_configured`` treats an absent
    gate name as "required", so an empty dict silently converts EVERY
    advisory rail into a hard veto and a finished draft gets rejected
    over a transient DB blip (latent path confirmed 2026-07-02; violates
    feedback_no_silent_defaults).
    """


def _audit_gate_states_unavailable(qa: Any, *, reason: str) -> None:
    """Loud surfacing for a gate-state read failure: WARNING log always,
    plus a best-effort ``qa_gate_states_unavailable`` audit event when the
    MultiModelQA instance carries a platform handle."""
    logger.warning(
        "[qa rails] qa_gates state unavailable with a live pool (%s) — "
        "failing the rail atom loudly instead of defaulting every gate "
        "to required",
        reason,
    )
    try:
        platform = getattr(qa, "_platform", None)
        if platform is not None:
            platform.audit.write_bg(
                "qa_gate_states_unavailable",
                source="qa_rails",
                details={"reason": reason[:500]},
                severity="warning",
            )
    except Exception:  # noqa: BLE001 - silent-ok: CIRCULARITY. This handler
        # guards the audit write itself, and emit_finding routes through
        # audit_log_bg -> audit_log, so a finding here would take the exact
        # path that just failed. logger.warning is the only escape, and the
        # callers of this helper already warn on the outcome that matters. — audit is best-effort
        pass


async def resolve_gate_states(qa: Any) -> dict[str, Any]:
    """``{gate_name: (enabled, required_to_pass)}`` from ``qa_gates`` via the
    MultiModelQA instance.

    No-DB environments (``qa.pool is None`` — unit tests, callers without a
    DB) get ``{}`` — advisory marking then leaves reviews as seeded, the
    long-standing legacy fallback. With a LIVE pool, a failed or empty
    lookup raises :class:`GateStatesUnavailable` instead: ``qa_gates`` is
    baseline-seeded in prod, so "live pool but no gate rows" only means the
    read failed or the install is broken — and proceeding would silently
    promote every advisory rail to a hard gate (see the exception class
    docstring).
    """
    pool = getattr(qa, "pool", None)
    try:
        states = await qa._load_gate_states()
    except Exception as exc:  # noqa: BLE001
        if pool is None:
            return {}
        reason = f"{type(exc).__name__}: {exc}"
        _audit_gate_states_unavailable(qa, reason=reason)
        raise GateStatesUnavailable(
            f"qa_gates lookup failed with a live pool ({reason})"
        ) from exc
    if not states and pool is not None:
        _audit_gate_states_unavailable(
            qa, reason="empty gate_states with a live pool",
        )
        raise GateStatesUnavailable(
            "qa_gates returned no rows with a live pool — advisory/required "
            "config unavailable; refusing to default every rail to required"
        )
    return states


__all__ = [
    "GateStatesUnavailable",
    "aggregate_rail_reviews",
    "is_rescuable_reject",
    "known_wrong_fact_rescued",
    "missing_required_gates",
    "resolve_gate_states",
    "reviewer_to_dict",
]
