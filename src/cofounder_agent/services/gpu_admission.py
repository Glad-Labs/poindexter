"""Pure GPU admission calculator — P1 of the scheduler rebuild (poindexter#914).

Answers "should this caller wait for the GPU, wait-after-eviction, or skip
now?" from numbers alone: :func:`decide` does NO I/O and imports nothing but
dataclasses and the :class:`~services.gpu_lease_stats.LeaseStats` type, so the
whole decision table unit-tests exhaustively without a GPU, a DB, or a mock.
``gpu_scheduler.lock()`` assembles :class:`AdmissionInputs` from live telemetry
(each read individually fail-open) and acts on the returned
:class:`AdmissionDecision`.

Design contract (spec §3, ``docs/superpowers/specs/
2026-07-26-gpu-scheduler-queue-admission-design.md``):

- ``max_wait_s=None`` — the legacy path — is ALWAYS granted. Callers that
  haven't opted into the wait contract behave exactly as before P1.
- ETA gate: the current holder's remaining time is estimated from its
  ``gpu_lease_stats`` p90 minus elapsed (floored at 0), falling back to
  ``eta_fallback_s`` when the key has no stats yet. An ETA over the caller's
  budget is an honest immediate ``reject`` instead of a doomed 900s wait.
- Fit gate: ``estimate ≤ free − headroom`` grants outright;
  ``estimate ≤ free + evictable − headroom`` grants after evicting the
  resident Ollama models; anything larger rejects (``no_fit``).
- **Every missing telemetry input fails OPEN** (grant): admission can only
  ever be as strict as its data is real. ``free_gpu0_gb=None`` or
  ``model_estimate_gb=None`` skips the fit gate entirely;
  ``holder_stats=None`` degrades the ETA to the conservative fallback;
  no holder at all skips the ETA gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.gpu_lease_stats import LeaseStats


class GpuBusyError(RuntimeError):
    """Admission refused the wait — the GPU won't be usable within budget.

    ``reason`` ∈ {"eta_exceeds_budget", "no_fit"}; ``eta_seconds`` carries the
    holder-remaining estimate when one was computed (None for pure fit
    rejects). Raised by ``gpu.lock(..., max_wait_s=...)`` BEFORE any wait, so
    fail-soft callers (rails, background jobs) can skip honestly this cycle
    instead of burning their timeout budget behind a long render.
    """

    def __init__(self, reason: str, eta_seconds: float | None):
        self.reason = reason
        self.eta_seconds = eta_seconds
        eta_txt = f" (holder ETA ~{eta_seconds:.0f}s)" if eta_seconds is not None else ""
        super().__init__(f"GPU admission rejected: {reason}{eta_txt}")


@dataclass(frozen=True)
class AdmissionInputs:
    """Everything :func:`decide` looks at, pre-read by the caller.

    ``None`` means "telemetry unavailable" and always fails open — see the
    module docstring for the per-field skip semantics.
    """

    max_wait_s: float | None
    holder_key: tuple[str, str] | None = None  # (owner, phase) of current holder
    holder_elapsed_s: float | None = None
    holder_stats: LeaseStats | None = None
    eta_fallback_s: float = 120.0
    free_gpu0_gb: float | None = None
    evictable_gpu0_gb: float = 0.0  # 0.0 when unknown (conservative)
    headroom_gb: float = 6.0
    model_estimate_gb: float | None = None


@dataclass(frozen=True)
class AdmissionDecision:
    action: str  # "grant" | "grant_after_unload" | "reject"
    reason: str | None = None  # set on reject: "eta_exceeds_budget" | "no_fit"
    eta_seconds: float | None = None


def decide(i: AdmissionInputs) -> AdmissionDecision:
    """PURE fold of the admission inputs into a decision. No I/O ever."""
    # Legacy path — no wait budget declared, admission does not apply.
    if i.max_wait_s is None:
        return AdmissionDecision(action="grant")

    # --- ETA gate (only when someone currently holds the lock) ---
    eta: float | None = None
    if i.holder_key is not None:
        if i.holder_stats is not None and i.holder_stats.p90_ms is not None:
            elapsed = i.holder_elapsed_s or 0.0
            eta = max(i.holder_stats.p90_ms / 1000.0 - elapsed, 0.0)
        else:
            # Unknown holder profile — assume the conservative fallback.
            eta = i.eta_fallback_s
        if eta > i.max_wait_s:
            return AdmissionDecision(
                action="reject", reason="eta_exceeds_budget", eta_seconds=eta
            )

    # --- Fit gate (needs BOTH free-VRAM telemetry and a model estimate) ---
    if i.free_gpu0_gb is None or i.model_estimate_gb is None:
        return AdmissionDecision(action="grant", eta_seconds=eta)

    budget = i.free_gpu0_gb - i.headroom_gb
    if i.model_estimate_gb <= budget:
        return AdmissionDecision(action="grant", eta_seconds=eta)
    if i.model_estimate_gb <= budget + i.evictable_gpu0_gb:
        return AdmissionDecision(action="grant_after_unload", eta_seconds=eta)
    return AdmissionDecision(action="reject", reason="no_fit", eta_seconds=eta)
