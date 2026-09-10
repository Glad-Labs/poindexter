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

    ``reason`` ∈ {"eta_exceeds_budget", "no_fit", "game_mode"}; ``eta_seconds``
    carries the holder-remaining estimate when one was computed (None for pure
    fit rejects). Raised by ``gpu.lock(..., max_wait_s=...)`` BEFORE any wait,
    so fail-soft callers (rails, background jobs) can skip honestly this cycle
    instead of burning their timeout budget behind a long render.

    ``"game_mode"`` is raised by ``gpu_scheduler`` for EVERY new acquire while
    ``services.game_mode`` is active — including callers that passed no budget
    — because the operator has claimed the GPU for a known window and waiting
    it out would hang the caller for hours. Its ``eta_seconds`` is exact (time
    until expiry), not an estimate. See ``docs/operations/game-mode.md``.
    """

    def __init__(self, reason: str, eta_seconds: float | None):
        self.reason = reason
        self.eta_seconds = eta_seconds
        eta_txt = f" (holder ETA ~{eta_seconds:.0f}s)" if eta_seconds is not None else ""
        super().__init__(f"GPU admission rejected: {reason}{eta_txt}")


@dataclass(frozen=True)
class CardVram:
    """One card's VRAM telemetry as the fit gate sees it (poindexter#1016).

    ``free_gb`` / ``evictable_gb`` keep the #914 None-vs-0.0 contract:
    ``None`` is UNKNOWN (fails open), ``0.0`` is a known empty answer.
    ``headroom_gb`` is per-card on purpose — GPU 0 reserves for the desktop,
    while a compute-only second card may reserve eviction insurance instead
    (the operator box holds ~4.5 GB free on the 3090 by design).
    """

    index: int
    free_gb: float | None = None
    evictable_gb: float | None = None
    headroom_gb: float = 6.0


@dataclass(frozen=True)
class AdmissionInputs:
    """Everything :func:`decide` looks at, pre-read by the caller.

    ``None`` means "telemetry unavailable" and always fails open — see the
    module docstring for the per-field skip semantics.

    Card telemetry comes in two shapes: the legacy single-card fields
    (``free_gpu0_gb`` / ``evictable_gpu0_gb`` / ``headroom_gb``) and the
    multi-card ``cards`` tuple (poindexter#1016 — the deployment runs
    ollama-primary across two cards, so a gate that inspects one card is
    answering a question Ollama did not ask). When ``cards`` is non-empty
    it is authoritative and the legacy fields are ignored; when empty, the
    legacy fields are folded into a one-card list, so existing callers and
    tests keep their exact semantics.
    """

    max_wait_s: float | None
    holder_key: tuple[str, str] | None = None  # (owner, phase) of current holder
    holder_elapsed_s: float | None = None
    holder_stats: LeaseStats | None = None
    eta_fallback_s: float = 120.0
    free_gpu0_gb: float | None = None
    # None = UNKNOWN (telemetry stale/absent), 0.0 = known "nothing evictable".
    # These were conflated until poindexter#914: a scrape lag that hid a
    # resident 21GB model read as "nothing to evict" and produced a `no_fit`
    # that degraded the QA rail — which then passed OPEN, so the judgement
    # silently did not happen. Unknown must fail open, like free_gpu0_gb.
    evictable_gpu0_gb: float | None = None
    headroom_gb: float = 6.0
    model_estimate_gb: float | None = None
    cards: tuple[CardVram, ...] = ()

    def effective_cards(self) -> tuple[CardVram, ...]:
        """The card list the fit gate actually evaluates."""
        if self.cards:
            return self.cards
        return (
            CardVram(
                index=0,
                free_gb=self.free_gpu0_gb,
                evictable_gb=self.evictable_gpu0_gb,
                headroom_gb=self.headroom_gb,
            ),
        )


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

    # --- Fit gate (needs a model estimate + free-VRAM telemetry) ---
    # Multi-card semantics (poindexter#1016): Ollama places a model on the
    # card with the most free VRAM when it fits entirely, and SPLITS the
    # weights across cards when no single card can hold them — so the honest
    # fit question is "does it fit any one card, or the pool as a whole",
    # each card contributing its free VRAM minus its own reserve.
    cards = i.effective_cards()
    if i.model_estimate_gb is None or any(c.free_gb is None for c in cards):
        # Unknown estimate, or ANY card's telemetry missing: the pool total
        # is unknowable, so a no_fit cannot be asserted — fail open, same
        # posture the single-card gate always had.
        return AdmissionDecision(action="grant", eta_seconds=eta)

    # Per-card budget may be NEGATIVE (free below the card's reserve) — that
    # deficit is meaningful for the single-card check and for eviction credit
    # (a freed model must first refill the reserve before contributing), but
    # a deficit card contributes zero capacity to a split, never negative.
    budgets = [c.free_gb - c.headroom_gb for c in cards]
    if i.model_estimate_gb <= max(budgets) or i.model_estimate_gb <= sum(
        b for b in budgets if b > 0.0
    ):
        return AdmissionDecision(action="grant", eta_seconds=eta)
    # Eviction credit unknown -> fail open. We already know the model does not
    # fit the free budget, so the only question left is whether something
    # evictable is holding the rest of the card. Answering "no" on ignorance
    # rejects the caller, and a rejected QA rail passes OPEN — trading a slow
    # judgement for no judgement at all (poindexter#914).
    if any(c.evictable_gb is None for c in cards):
        return AdmissionDecision(action="grant_after_unload", eta_seconds=eta)
    with_credit = [
        b + (c.evictable_gb or 0.0) for b, c in zip(budgets, cards, strict=True)
    ]
    if i.model_estimate_gb <= max(with_credit) or i.model_estimate_gb <= sum(
        b for b in with_credit if b > 0.0
    ):
        return AdmissionDecision(action="grant_after_unload", eta_seconds=eta)
    return AdmissionDecision(action="reject", reason="no_fit", eta_seconds=eta)
