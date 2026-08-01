"""Exhaustive pure tests for services/gpu_admission.py (poindexter#914 P1).

``decide`` is a pure function of :class:`AdmissionInputs`, so every branch of
the decision table is pinned here without a GPU, DB, or mock. The contract
under test (spec §3): legacy inertness, ETA math + fallback, fit math with
eviction credit, and fail-OPEN on every missing telemetry input.
"""

from __future__ import annotations

import pytest

from services.gpu_admission import (
    AdmissionDecision,
    AdmissionInputs,
    GpuBusyError,
    decide,
)
from services.gpu_lease_stats import LeaseStats


def _stats(p90_ms: float | None) -> LeaseStats:
    return LeaseStats(samples=10, ewma_ms=p90_ms, p50_ms=p90_ms, p90_ms=p90_ms)


# ---------------------------------------------------------------------------
# Legacy inertness
# ---------------------------------------------------------------------------


def test_no_budget_always_grants():
    """max_wait_s=None is the legacy path — granted no matter how bad the
    other inputs look (held lock, zero free VRAM, giant model)."""
    d = decide(
        AdmissionInputs(
            max_wait_s=None,
            holder_key=("ollama", "writer"),
            holder_elapsed_s=1.0,
            holder_stats=_stats(900_000.0),
            free_gpu0_gb=0.0,
            model_estimate_gb=100.0,
        )
    )
    assert d == AdmissionDecision(action="grant")


# ---------------------------------------------------------------------------
# ETA gate
# ---------------------------------------------------------------------------


def test_eta_is_p90_minus_elapsed():
    """p90=300s, elapsed=100s → ETA 200s; a 250s budget admits."""
    d = decide(
        AdmissionInputs(
            max_wait_s=250.0,
            holder_key=("ollama", "writer"),
            holder_elapsed_s=100.0,
            holder_stats=_stats(300_000.0),
        )
    )
    assert d.action == "grant"
    assert d.eta_seconds == pytest.approx(200.0)


def test_eta_floors_at_zero_when_holder_overran_p90():
    """Holder already past its p90 → ETA 0, always within budget."""
    d = decide(
        AdmissionInputs(
            max_wait_s=1.0,
            holder_key=("ollama", "writer"),
            holder_elapsed_s=500.0,
            holder_stats=_stats(300_000.0),
        )
    )
    assert d.action == "grant"
    assert d.eta_seconds == 0.0


def test_eta_over_budget_rejects_with_eta():
    d = decide(
        AdmissionInputs(
            max_wait_s=60.0,
            holder_key=("video", "video"),
            holder_elapsed_s=0.0,
            holder_stats=_stats(900_000.0),
        )
    )
    assert d.action == "reject"
    assert d.reason == "eta_exceeds_budget"
    assert d.eta_seconds == pytest.approx(900.0)


def test_missing_stats_uses_fallback_eta():
    """No stats for the holder key yet → the conservative fallback governs:
    over a 60s budget with the default 120s fallback ⇒ reject."""
    d = decide(
        AdmissionInputs(
            max_wait_s=60.0,
            holder_key=("ollama", "writer"),
            holder_elapsed_s=5.0,
            holder_stats=None,
            eta_fallback_s=120.0,
        )
    )
    assert d.action == "reject"
    assert d.reason == "eta_exceeds_budget"
    assert d.eta_seconds == 120.0


def test_stats_without_p90_uses_fallback_eta():
    """A warmup-empty LeaseStats (p90 None) degrades to the fallback too."""
    d = decide(
        AdmissionInputs(
            max_wait_s=200.0,
            holder_key=("ollama", "writer"),
            holder_stats=LeaseStats(),
            eta_fallback_s=120.0,
        )
    )
    assert d.action == "grant"
    assert d.eta_seconds == 120.0


def test_no_holder_skips_eta_gate():
    """Free lock → no ETA computed at all; grant carries eta None."""
    d = decide(AdmissionInputs(max_wait_s=1.0))
    assert d == AdmissionDecision(action="grant", eta_seconds=None)


# ---------------------------------------------------------------------------
# Fit gate
# ---------------------------------------------------------------------------


def test_fits_within_free_minus_headroom_grants():
    d = decide(
        AdmissionInputs(
            max_wait_s=60.0,
            free_gpu0_gb=20.0,
            headroom_gb=6.0,
            model_estimate_gb=14.0,
        )
    )
    assert d.action == "grant"


def test_fits_only_with_eviction_credit_grants_after_unload():
    """14 > 16−6 free budget, but ≤ (16−6)+8 with the resident evicted."""
    d = decide(
        AdmissionInputs(
            max_wait_s=60.0,
            free_gpu0_gb=16.0,
            evictable_gpu0_gb=8.0,
            headroom_gb=6.0,
            model_estimate_gb=14.0,
        )
    )
    assert d.action == "grant_after_unload"
    assert d.reason is None


def test_too_big_even_after_eviction_rejects_no_fit():
    d = decide(
        AdmissionInputs(
            max_wait_s=60.0,
            free_gpu0_gb=10.0,
            evictable_gpu0_gb=4.0,
            headroom_gb=6.0,
            model_estimate_gb=30.0,
        )
    )
    assert d.action == "reject"
    assert d.reason == "no_fit"
    assert d.eta_seconds is None


def test_eta_reject_wins_before_fit_check():
    """Both gates would reject — the ETA reason surfaces (it's checked first
    and carries the actionable number)."""
    d = decide(
        AdmissionInputs(
            max_wait_s=10.0,
            holder_key=("ollama", "writer"),
            holder_stats=_stats(600_000.0),
            free_gpu0_gb=1.0,
            model_estimate_gb=30.0,
        )
    )
    assert d.reason == "eta_exceeds_budget"


def test_eta_within_budget_then_fit_reject_still_rejects():
    """ETA passes (5s ≤ 60s) but the model can never fit → no_fit, and the
    computed ETA rides along for the error message."""
    d = decide(
        AdmissionInputs(
            max_wait_s=60.0,
            holder_key=("ollama", "writer"),
            holder_elapsed_s=295.0,
            holder_stats=_stats(300_000.0),
            free_gpu0_gb=2.0,
            evictable_gpu0_gb=0.0,
            headroom_gb=6.0,
            model_estimate_gb=30.0,
        )
    )
    assert d.action == "reject"
    assert d.reason == "no_fit"
    assert d.eta_seconds == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Fail-open on missing telemetry
# ---------------------------------------------------------------------------


def test_missing_free_vram_skips_fit_gate():
    d = decide(
        AdmissionInputs(max_wait_s=60.0, free_gpu0_gb=None, model_estimate_gb=100.0)
    )
    assert d.action == "grant"


def test_missing_model_estimate_skips_fit_gate():
    d = decide(
        AdmissionInputs(max_wait_s=60.0, free_gpu0_gb=0.5, model_estimate_gb=None)
    )
    assert d.action == "grant"


def test_unknown_evictable_fails_open():
    """Unknown eviction credit grants (after unload) rather than rejecting.

    REVERSED 2026-07-31 (poindexter#914). This previously asserted `reject`,
    reasoning that an absent per-process metric must not buy "phantom eviction
    credit". That concern is real — the replacement keeps it, in
    ``test_known_zero_evictable_still_rejects`` below: a card we can SEE is
    empty still rejects.

    What changed is the treatment of *ignorance*. The metric lags ~40s (10s
    exporter refresh + 30s Prometheus scrape) against a 45s rail budget, so
    "absent" routinely meant "a 21GB model loaded and hasn't been scraped yet",
    not "nothing is loaded". Rejecting on that degraded the QA rail — which
    **passes open** — so the judgement silently did not happen: 105 rejections
    and a 0% success rate on qa_deepeval_judge in one 66-minute prod window.

    The asymmetry decides it: a rejected rail means NO QA, an over-granted one
    merely thrashes. `GPURegistry.evictable_ollama_gb` now returns None for
    unknown and a float (including a real 0.0) when it knows.
    """
    d = decide(
        AdmissionInputs(
            max_wait_s=60.0,
            free_gpu0_gb=16.0,
            headroom_gb=6.0,
            model_estimate_gb=14.0,
            evictable_gpu0_gb=None,  # unknown
        )
    )
    assert d.action == "grant_after_unload"
    assert d.reason is None


def test_known_zero_evictable_still_rejects():
    """The teeth the reversal above must not remove: when we KNOW the card has
    nothing evictable and the model doesn't fit, reject."""
    d = decide(
        AdmissionInputs(
            max_wait_s=60.0,
            free_gpu0_gb=16.0,
            headroom_gb=6.0,
            model_estimate_gb=14.0,
            evictable_gpu0_gb=0.0,  # known-empty, not unknown
        )
    )
    assert d.action == "reject"
    assert d.reason == "no_fit"


# ---------------------------------------------------------------------------
# GpuBusyError shape
# ---------------------------------------------------------------------------


def test_gpu_busy_error_carries_reason_and_eta():
    err = GpuBusyError("eta_exceeds_budget", 412.0)
    assert err.reason == "eta_exceeds_budget"
    assert err.eta_seconds == 412.0
    assert "eta_exceeds_budget" in str(err)
    assert "412" in str(err)
    assert isinstance(err, RuntimeError)


def test_gpu_busy_error_without_eta():
    err = GpuBusyError("no_fit", None)
    assert err.eta_seconds is None
    assert "no_fit" in str(err)
