"""Spend Throttle — defer *new work* when total spend crosses a soft budget.

P3 of the cost-attribution design
(``docs/superpowers/specs/2026-06-21-cost-control-attribution-design.md`` §6).
A read-only, fail-OPEN sibling of ``services.pipeline_throttle``: instead of
gating on approval-queue depth it gates on money — the ``cost_ledger``
``total_usd`` **minus idle electricity** against two soft ceilings.

The subtraction is the point: the throttle gates *controllable* spend. Paid API
and ACTIVE electricity are both caused by running work, so deferring work
reduces them. IDLE electricity is drawn whether or not the pipeline runs, so
counting it makes the cap measure "was the machine on" — and since deferring
work cannot bring it back down, once idle alone crosses the ceiling the
pipeline stays throttled until the window rolls over. See
``cost_throttle_count_idle_electricity`` below to restore the old sum.

The two ceilings:

* **daily** — a rate limit that clears at midnight (``get_spend("day")`` resets
  each UTC day), with hysteresis so it doesn't chatter around the threshold:
  engages at ``cost_throttle_daily_budget_usd``, releases only below
  ``budget * (1 - cost_throttle_resume_buffer_pct/100)``.
* **monthly** — a cumulative backstop: a month-to-date SUM only ever rises, so
  once crossed it stays engaged until month rollover (no hysteresis). Its
  default sits well above real burn; the escape hatch is to raise it or set 0.

Consulted at the **new-work seam** — the Prefect ``content_generation_flow``
before it claims a ``pending`` row — NOT in the per-LLM-call hot path. Over
budget => defer *claiming* new work; in-flight tasks finish. That is what makes
"slow down" safe: it never kills a post mid-generation, it just stops starting
new ones.

Fails **OPEN**: a DB / ledger read error (or no pool) returns "not throttled" —
a dead DB must never become a content outage. This mirrors
``pipeline_throttle`` and aligns with ``feedback_self_heal_not_suppress``
(throttle before page). The per-call *hard* money stop is ``cost_guard`` (which
fails closed); this is the gentler "slow down" that precedes it.

All tunables live in ``app_settings`` (DB-first), read via ``site_config``:

* ``cost_throttle_enabled`` (default ``true``) — master switch.
* ``cost_throttle_daily_budget_usd`` (default ``3.00``; ``<= 0`` disables the
  daily axis).
* ``cost_throttle_monthly_budget_usd`` (default ``60.00``; ``<= 0`` disables the
  monthly axis).
* ``cost_throttle_resume_buffer_pct`` (default ``10``) — daily hysteresis band.
* ``cost_throttle_count_idle_electricity`` (default ``false``) — set ``true`` to
  count idle electricity toward the ceilings again (the pre-2026-07-31
  behaviour). Only affects the MEASURED electricity path; on the estimated path
  there is no idle component in the total to begin with.

``get_state()`` exposes the current state for Prometheus (``metrics_exporter``)
and health payloads, mirroring ``pipeline_throttle.get_state()``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from services import cost_ledger
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


@dataclass
class ThrottleDecision:
    """Result of a spend-throttle check — returned to the new-work seam.

    ``throttled`` is the gate; ``ceiling`` names which budget tripped
    (``"daily"`` | ``"monthly"`` | ``None``); the ``*_usd`` fields + ``reason``
    are for the operator log / notification.
    """

    throttled: bool
    ceiling: str | None
    reason: str
    daily_total_usd: float = 0.0
    daily_budget_usd: float = 0.0
    monthly_total_usd: float = 0.0
    monthly_budget_usd: float = 0.0


@dataclass
class _ThrottleState:
    """Module-level accounting state (mirrors ``pipeline_throttle._ThrottleState``)."""

    # True when the most recent check saw EITHER ceiling crossed.
    active: bool = False
    # monotonic() timestamp when the current throttled interval began.
    active_since_ts: float | None = None
    # Cumulative seconds spent throttled across all intervals since boot.
    total_seconds: float = 0.0
    # Daily-ceiling hysteresis latch — True while inside the engage→release band.
    daily_latched: bool = False
    # Last observed values — surfaced to operators for the "why" answer.
    last_ceiling: str | None = None
    last_daily_total: float = 0.0
    last_daily_budget: float = 0.0
    last_monthly_total: float = 0.0
    last_monthly_budget: float = 0.0
    last_check_monotonic: float | None = None


# Module-level singleton. Tests reset via ``reset_for_tests``.
_STATE: _ThrottleState = _ThrottleState()


def _now() -> float:
    """monotonic() indirection so tests can reason about accounting."""
    return time.monotonic()


def _mark_active(now: float, ceiling: str | None) -> None:
    """Transition to throttled. Idempotent if already active."""
    _STATE.last_check_monotonic = now
    _STATE.last_ceiling = ceiling
    if _STATE.active:
        return
    _STATE.active = True
    _STATE.active_since_ts = now


def _mark_inactive(now: float) -> None:
    """Transition out of throttled. Accumulates elapsed seconds."""
    _STATE.last_check_monotonic = now
    _STATE.last_ceiling = None
    if not _STATE.active:
        return
    if _STATE.active_since_ts is not None:
        _STATE.total_seconds += max(0.0, now - _STATE.active_since_ts)
    _STATE.active = False
    _STATE.active_since_ts = None


def _current_total_seconds(now: float) -> float:
    """Total throttled seconds including any ongoing interval."""
    total = _STATE.total_seconds
    if _STATE.active and _STATE.active_since_ts is not None:
        total += max(0.0, now - _STATE.active_since_ts)
    return total


def _reason(ceiling: str | None, dt: float, db: float, mt: float, mb: float) -> str:
    if ceiling == "monthly":
        return f"monthly total ${mt:.2f} >= ${mb:.2f} (cumulative backstop)"
    if ceiling == "daily":
        return f"daily total ${dt:.2f} vs ${db:.2f} cap (rate limit)"
    return "within budget"


def _emit_engaged_finding(
    ceiling: str | None, dt: float, db: float, mt: float, mb: float
) -> None:
    """Emit a `spend_throttle_engaged` finding on the engage transition.

    Fires once per engage (the caller only invokes this on inactive→active), so
    the findings dispatcher never sees a flood. ``severity='warn'`` routes it to
    Discord (routine "slowing down"), never a Telegram page — the throttle is
    the self-heal that *precedes* an alert.
    """
    which = ceiling or "budget"
    emit_finding(
        source="spend_throttle",
        kind="spend_throttle_engaged",
        severity="warn",
        title=f"spend throttle engaged ({which} ceiling)",
        body=(
            "## Spend throttle engaged\n\n"
            f"New content generation is **paused** because the **{which}** spend "
            "ceiling was crossed. In-flight tasks finish; the flow just stops "
            "claiming new `pending` work until spend falls back under budget.\n\n"
            f"- Daily total (API + electricity): `${dt:.2f}` / cap `${db:.2f}`\n"
            f"- Month-to-date total: `${mt:.2f}` / backstop `${mb:.2f}`\n\n"
            "This is advisory self-throttling, not the hard `cost_guard` cap. "
            "Raise `cost_throttle_daily_budget_usd` / "
            "`cost_throttle_monthly_budget_usd` (or set `0` to disable an axis) "
            "to resume immediately."
        ),
        dedup_key=f"spend_throttle:{which}",
        extra={
            "ceiling": ceiling,
            "daily_total_usd": round(dt, 4),
            "daily_budget_usd": round(db, 4),
            "monthly_total_usd": round(mt, 4),
            "monthly_budget_usd": round(mb, 4),
        },
    )


async def should_throttle(pool: Any, *, site_config: Any = None) -> ThrottleDecision:
    """Return a :class:`ThrottleDecision` for the new-work seam.

    Throttled if EITHER ceiling is crossed on ``cost_ledger`` ``total_usd``.
    Reads all knobs from ``site_config`` (documented defaults when ``None``).
    Fails **open** on any error / missing pool. Updates module state as a side
    effect so ``get_state()`` stays current, and emits a one-shot
    ``spend_throttle_engaged`` finding on the inactive→active transition.
    """
    now = _now()
    try:
        if site_config is not None:
            # Coerce at the boundary so a misbehaving getter (or a malformed
            # setting that survived as a non-numeric string) can't blow up the
            # ceiling arithmetic below. If coercion itself raises, the outer
            # fail-open handler catches it — the throttle never crashes the flow.
            enabled = bool(site_config.get_bool("cost_throttle_enabled", True))
            daily_budget = float(
                site_config.get_float("cost_throttle_daily_budget_usd", 3.0)
            )
            monthly_budget = float(
                site_config.get_float("cost_throttle_monthly_budget_usd", 60.0)
            )
            buffer_pct = float(
                site_config.get_float("cost_throttle_resume_buffer_pct", 10.0)
            )
            count_idle = bool(
                site_config.get_bool("cost_throttle_count_idle_electricity", False)
            )
        else:
            enabled, daily_budget, monthly_budget, buffer_pct = True, 3.0, 60.0, 10.0
            count_idle = False

        if not enabled:
            _STATE.daily_latched = False
            _mark_inactive(now)
            return ThrottleDecision(
                False, None, "throttle disabled (cost_throttle_enabled=false)"
            )

        if not pool:
            # Fail open — no DB to check spend against.
            _mark_inactive(now)
            return ThrottleDecision(False, None, "no DB pool (fail-open)")

        day = await cost_ledger.get_spend(pool, window="day", site_config=site_config)
        month = await cost_ledger.get_spend(
            pool, window="month", site_config=site_config
        )
        # Throttle on CONTROLLABLE spend. Idle electricity is what the machine
        # draws whether or not the pipeline runs, so counting it makes the cap
        # measure "was the PC on" rather than "did we spend money" — and worse,
        # deferring work cannot bring the number back down, so once idle alone
        # crosses the ceiling the pipeline stays throttled until the window
        # rolls over. Observed 2026-07-31: $61.06 monthly total against a $60
        # cap, of which $41.73 was idle electricity and only $11.37 was real
        # cloud API spend — content generation stopped for the rest of the month
        # over power the machine would have drawn while sitting idle anyway.
        #
        # Active electricity STAYS counted: that is caused by running work, so
        # deferring work genuinely reduces it. Set
        # cost_throttle_count_idle_electricity=true to restore the old sum.
        daily_idle = 0.0 if count_idle else float(day.idle_electricity_usd)
        monthly_idle = 0.0 if count_idle else float(month.idle_electricity_usd)
        daily_total = float(day.total_usd) - daily_idle
        monthly_total = float(month.total_usd) - monthly_idle

        # Monthly backstop — cumulative, no hysteresis; disabled at budget <= 0.
        monthly_tripped = monthly_budget > 0 and monthly_total >= monthly_budget

        # Daily rate limit — hysteresis via a latch; disabled at budget <= 0.
        if daily_budget <= 0:
            _STATE.daily_latched = False
            daily_tripped = False
        elif _STATE.daily_latched:
            # Already engaged: stay engaged until spend drops below the release band.
            buffer = min(100.0, max(0.0, buffer_pct))
            release_at = daily_budget * (1.0 - buffer / 100.0)
            daily_tripped = daily_total >= release_at
            _STATE.daily_latched = daily_tripped
        else:
            # Not engaged: engage only once spend reaches the full cap.
            daily_tripped = daily_total >= daily_budget
            _STATE.daily_latched = daily_tripped

        throttled = monthly_tripped or daily_tripped
        # Report the more-binding ceiling: monthly (sticky until rollover) wins.
        ceiling = "monthly" if monthly_tripped else ("daily" if daily_tripped else None)

        _STATE.last_daily_total = daily_total
        _STATE.last_daily_budget = daily_budget
        _STATE.last_monthly_total = monthly_total
        _STATE.last_monthly_budget = monthly_budget

        if throttled:
            was_active = _STATE.active
            _mark_active(now, ceiling)
            if not was_active:
                # One-shot notification on the engage transition (dedup by design).
                _emit_engaged_finding(
                    ceiling, daily_total, daily_budget, monthly_total, monthly_budget
                )
                logger.warning(
                    "[SPEND_THROTTLE] engaged (%s): %s",
                    ceiling,
                    _reason(
                        ceiling, daily_total, daily_budget, monthly_total, monthly_budget
                    ),
                )
        else:
            _mark_inactive(now)

        return ThrottleDecision(
            throttled=throttled,
            ceiling=ceiling,
            reason=_reason(
                ceiling, daily_total, daily_budget, monthly_total, monthly_budget
            ),
            daily_total_usd=daily_total,
            daily_budget_usd=daily_budget,
            monthly_total_usd=monthly_total,
            monthly_budget_usd=monthly_budget,
        )
    except Exception as e:  # noqa: BLE001 — fail OPEN on ANY error
        # A dead DB, a malformed setting, or any unexpected error must never
        # become a content outage. Log loud, return not-throttled, and leave the
        # latch untouched so a transient blip doesn't reset an in-progress throttle.
        logger.warning(
            "[SPEND_THROTTLE] check failed; failing open (not throttling): %s", e
        )
        _STATE.last_check_monotonic = now
        return ThrottleDecision(False, None, f"throttle check failed (fail-open): {e}")


def get_state() -> dict[str, Any]:
    """Snapshot of throttle state for Prometheus / health payloads.

    Plain types only (bools, floats, ints, str|None) so ``metrics_exporter`` and
    ``/api/health`` can serialize it directly. Mirrors
    ``pipeline_throttle.get_state()``.
    """
    now = _now()
    active_seconds = 0.0
    if _STATE.active and _STATE.active_since_ts is not None:
        active_seconds = max(0.0, now - _STATE.active_since_ts)
    return {
        "active": _STATE.active,
        "active_seconds": active_seconds,
        "total_seconds": _current_total_seconds(now),
        "ceiling": _STATE.last_ceiling,
        "daily_total_usd": _STATE.last_daily_total,
        "daily_budget_usd": _STATE.last_daily_budget,
        "monthly_total_usd": _STATE.last_monthly_total,
        "monthly_budget_usd": _STATE.last_monthly_budget,
    }


def reset_for_tests() -> None:
    """Reset module state — tests call this in fixtures."""
    global _STATE
    _STATE = _ThrottleState()
