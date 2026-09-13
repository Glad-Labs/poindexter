"""Which stage of the brain cycle is running right now.

The cycle watchdog cancels a stuck ``await`` and logs "Cycle aborted by
timeout" -- nine times in two months without ever saying WHAT was stuck
(GlitchTip #925, 2026-09-12 audit). ``run_cycle`` and ``run_health_probes``
stamp the current stage here before each await, so the timeout message (and
the page that follows a persistent hang) names the stage, e.g.
``health_probe:ollama`` or ``run_compose_drift_probe``.

A module-level string on purpose: the brain is single-threaded, one cycle at
a time, and this must be readable from the watchdog's except-branch without
plumbing a context object through forty call sites. Stdlib only.
"""
from __future__ import annotations

_IDLE = "idle"
_current: str = _IDLE


def set_stage(name: str) -> None:
    """Record ``name`` as the stage now awaiting."""
    global _current
    _current = str(name) if name else _IDLE


def get_stage() -> str:
    """The stage most recently stamped (``idle`` between cycles)."""
    return _current


def reset() -> None:
    set_stage(_IDLE)
