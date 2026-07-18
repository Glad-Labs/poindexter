"""Unit tests — brain probe audit-mirror + rule-gate failure surfacing.

Silent-excepts OLD-debt burn-down (batch 23). Five brain probes share a
near-identical ``_emit_audit_event`` / ``_emit_audit`` helper that writes the
probe's event to ``audit_log``. Each swallowed the write at ``logger.debug``,
which the brain does not ship to Loki — so the failure was invisible in prod.

These five specifically **have no ``alert_events`` row**: unlike
``backup_watcher`` / ``smart_monitor`` / ``docker_port_forward_probe`` (whose
drift/failure events reach the operator through the independent
``alert_events`` -> ``alert_dispatcher`` path), these probes notify only via
direct ``notify_fn`` calls — and each makes strictly MORE audit calls than
notify calls, so several of their events are **audit-only**. For those, a
swallowed write destroys the sole record of the event.

The brain tree cannot ``emit_finding`` (no ``services`` import), so
``logger.warning`` is the bar — same as batch 2's ``_is_deduped`` fix. A
finding would be wrong here regardless: ``emit_finding`` writes to
``audit_log`` too, so it would vanish in the very outage it reports.

Also covers ``glitchtip_triage_probe._match_rule``, a different shape: a
malformed operator-authored ``max_count`` makes ``int()`` raise, the gate is
skipped, and **the rule still matches** — fail-OPEN on a path that
auto-resolves GlitchTip issues. The permissive behaviour is preserved (that is
a semantics decision, not a visibility one), but it must no longer be silent.

Mirrors the caplog assertion pattern in
``test_branch_drift_probe_silent_failures.py``.
"""

from __future__ import annotations

import importlib
import logging
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# brain/ is a standalone package outside the cofounder_agent distro.
_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

# (module, audit-helper attribute) — restore_test_probe named its helper
# without the _event suffix.
_AUDIT_HELPERS = [
    ("compose_drift_probe", "_emit_audit_event"),
    ("glitchtip_triage_probe", "_emit_audit_event"),
    ("migration_drift_probe", "_emit_audit_event"),
    ("prefect_stuck_flow_probe", "_emit_audit_event"),
    ("restore_test_probe", "_emit_audit"),
]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(("mod_name", "helper_name"), _AUDIT_HELPERS)
async def test_audit_mirror_write_failure_is_visible(mod_name, helper_name, caplog):
    """A failed audit_log write must WARN — for these five probes the audit
    row is the only record of an audit-only event."""
    mod = importlib.import_module(f"brain.{mod_name}")
    helper = getattr(mod, helper_name)

    pool = MagicMock()
    pool.execute = AsyncMock(side_effect=RuntimeError("audit_log write exploded"))

    with caplog.at_level(logging.WARNING, logger=f"brain.{mod_name}"):
        # Must not raise — best-effort semantics are preserved.
        await helper(pool, "probe.some_event", "a detail string")

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, (
        f"{mod_name}.{helper_name}: audit_log write failure must be visible at "
        "WARNING (debug is not shipped to Loki, so the event vanishes entirely)"
    )
    combined = " ".join(r.getMessage() for r in warnings)
    assert "audit_log write exploded" in combined, (
        "the underlying exception text must reach the operator"
    )


@pytest.mark.unit
def test_malformed_max_count_gate_is_visible(caplog):
    """A malformed ``max_count`` skips the gate and lets the rule match anyway
    (fail-open, on an auto-resolve path). Behaviour is preserved; it must warn."""
    gtp = importlib.import_module("brain.glitchtip_triage_probe")

    rule = {
        "max_count": "not-a-number",  # operator typo
        "_compiled": re.compile("boom"),
    }
    issue = {"title": "boom happened", "count": 999, "level": "error"}

    with caplog.at_level(logging.WARNING, logger="brain.glitchtip_triage_probe"):
        matched = gtp._match_rule(issue, [rule])

    # Fail-open is intentional and unchanged: the count gate is skipped, so a
    # 999-count issue still matches a rule meant to cap at some small number.
    assert matched is rule

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, (
        "a malformed max_count silently widens an auto-resolve rule — "
        "it must be visible at WARNING"
    )
    combined = " ".join(r.getMessage() for r in warnings).lower()
    assert "max_count" in combined
