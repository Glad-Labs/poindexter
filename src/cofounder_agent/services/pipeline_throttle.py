"""
Pipeline Throttle Service — shared state for the approval-queue throttle.

The executor throttles forward motion when the approval queue is full
(``awaiting_approval >= max_approval_queue``). Historically that state
was silent — nothing observable, no metric, no API feedback. This
module is the single source of truth for the throttle, so multiple
callers (task executor, idle worker's discovery gate, API create-task
handler, Prometheus endpoint) can all reason about the same state.

Design:
    - One module-level ``_STATE`` holds monotonic-time-based accounting.
    - ``poll_queue_full(pool)`` hits the DB to see whether
      ``COUNT(awaiting_approval) >= max_approval_queue`` and updates
      ``_STATE`` as a side effect. Callers use the return value to
      decide whether to gate themselves; the state is observable via
      ``get_state()`` for Prometheus.
    - Counters are cumulative seconds spent in the throttled state,
      suitable for a Prometheus counter that Grafana can rate() over.

All tunables live in ``app_settings`` (DB-first config) and are read
through a ``site_config`` parameter passed to ``is_queue_full`` — Phase H
(GH#95) migrated this module off the module-level singleton import.
``max_approval_queue`` is the only knob this module needs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class _ThrottleState:
    """Module-level accounting state for the pipeline throttle."""

    # True when the most recent check saw the approval queue full.
    active: bool = False
    # monotonic() timestamp when the current throttled interval began,
    # or None when we are not currently throttled.
    active_since_ts: float | None = None
    # Cumulative seconds spent throttled across all intervals since boot.
    total_seconds: float = 0.0
    # Last observed queue size and limit — surfaced to operators for
    # the "why are we throttled" answer in API responses and logs.
    last_queue_size: int = 0
    last_queue_limit: int = 0
    # Wall-clock timestamp of last check — used by /api/prometheus to
    # freshness-stamp the gauge even if no check has been done yet.
    last_check_monotonic: float | None = None


# Module-level singleton. Tests reset via ``reset_for_tests``.
_STATE: _ThrottleState = _ThrottleState()


def _now() -> float:
    """monotonic() indirection so tests can freeze time."""
    return time.monotonic()


def _mark_active(now: float, queue_size: int, queue_limit: int) -> None:
    """Transition to throttled. Idempotent if already active."""
    _STATE.last_queue_size = queue_size
    _STATE.last_queue_limit = queue_limit
    _STATE.last_check_monotonic = now
    if _STATE.active:
        return
    _STATE.active = True
    _STATE.active_since_ts = now


def _mark_inactive(now: float, queue_size: int = 0, queue_limit: int = 0) -> None:
    """Transition out of throttled. Accumulates elapsed seconds."""
    _STATE.last_queue_size = queue_size
    _STATE.last_queue_limit = queue_limit
    _STATE.last_check_monotonic = now
    if not _STATE.active:
        return
    if _STATE.active_since_ts is not None:
        _STATE.total_seconds += max(0.0, now - _STATE.active_since_ts)
    _STATE.active = False
    _STATE.active_since_ts = None


def _current_total_seconds(now: float) -> float:
    """Return total throttled seconds, including any ongoing interval."""
    total = _STATE.total_seconds
    if _STATE.active and _STATE.active_since_ts is not None:
        total += max(0.0, now - _STATE.active_since_ts)
    return total


async def is_queue_full(pool: Any, site_config: Any) -> tuple[bool, int, int]:
    """Check whether the approval queue is at/above ``max_approval_queue``.

    Returns ``(is_full, queue_size, queue_limit)``.

    Reads ``max_approval_queue`` from ``app_settings`` via
    ``site_config.get_int`` (DB-first). On any DB error returns
    ``(False, 0, 0)`` — we never want a dead DB to mask the queue
    check and pile up nothing-is-happening.

    Updates module state as a side effect so ``get_state()`` stays
    current even for callers that just want to observe.

    Phase H (GH#95): ``site_config`` is now an explicit parameter, not a
    module-level singleton import. Callers pass either the lifespan-bound
    ``app.state.site_config`` (via ``Depends(get_site_config_dependency)``
    in routes) or the transitional ``services.site_config.site_config``
    singleton (in not-yet-migrated services).
    """
    now = _now()
    max_queue = site_config.get_int("max_approval_queue", 3)
    if not pool:
        _mark_inactive(now, 0, max_queue)
        return False, 0, max_queue

    try:
        row = await pool.fetchrow(
            "SELECT COUNT(*) AS c FROM content_tasks "
            "WHERE status = 'awaiting_approval'"
        )
        queue_size = int(row["c"]) if row else 0
    except Exception:
        # DB hiccup — don't poison the executor; callers treat
        # "unknown" as "not throttled" so the pipeline can try again
        # next tick rather than silently stalling.
        _STATE.last_check_monotonic = now
        return False, 0, max_queue

    full = queue_size >= max_queue
    if full:
        _mark_active(now, queue_size, max_queue)
    else:
        _mark_inactive(now, queue_size, max_queue)
    return full, queue_size, max_queue


def get_state() -> dict[str, Any]:
    """Return a snapshot of the current throttle state.

    Shape is designed for both the /api/prometheus endpoint and the
    /api/health payload, so it uses plain types (ints, floats, bools).
    """
    now = _now()
    active_seconds = 0.0
    if _STATE.active and _STATE.active_since_ts is not None:
        active_seconds = max(0.0, now - _STATE.active_since_ts)
    return {
        "active": _STATE.active,
        "active_seconds": active_seconds,
        "total_seconds": _current_total_seconds(now),
        "queue_size": _STATE.last_queue_size,
        "queue_limit": _STATE.last_queue_limit,
    }


def reset_for_tests() -> None:
    """Reset module state — tests should call this in fixtures."""
    global _STATE
    _STATE = _ThrottleState()
