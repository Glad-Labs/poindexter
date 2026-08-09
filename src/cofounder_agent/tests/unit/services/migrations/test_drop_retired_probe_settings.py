"""Regression guard for the settings + code retired with two dead probes.

Sibling to ``test_drop_dead_settings_batch3.py``, but sourced differently: the
batch-N sweeps come from the two zero-reader detectors, and **neither detector
found these**. ``ProbeZeroReaderSettingsJob`` was structurally unable to —
prod carries ~810 never-read keys past the 30-day grace against a
``settings_zero_reader_max_report`` cap of 50, so a key that has been dead
since May sits hundreds of rows below the fold and never reaches the finding.
These four were found by walking probe-name references by hand instead.

**``gate_auto_expire_*`` (3 keys)** — read by
``brain/gate_auto_expire_probe.py``, deleted with
``brain/gate_pending_summary_probe.py`` in ``a38f60591`` (poindexter#559 /
#570). They outlived their reader because ``0000_baseline.seeds.sql`` also
seeded them, and the Phase G fold-forward squash (2026-07-11) re-froze them
into the regenerated baseline — so every fresh install since has created three
settings nothing reads. The descriptions still advertised the probe, which is
the actual harm: ``gate_auto_expire_enabled`` reads as a live master switch,
so flipping it to ``false`` looks like it disables something.

**``scheduled_tasks_probe_watch_tasks``** — read by
``brain/health_probes.py::probe_scheduled_tasks``, retired in the same change.
It asked the host Recovery Agent to enumerate **Windows** Task Scheduler
entries; the host has run Pop!_OS since the July migration and systemd timers
replaced Task Scheduler. The probe never fired falsely (its fail-open branch
kept it advisory once ``mcp_http_probe_recovery_url`` went empty) — but a
probe that cannot fail is not coverage, and its presence in ``PROBES`` implied
the host's scheduled work was watched when it was not.

The keep-list below is the load-bearing half. ``mcp_http_probe_recovery_url``
and ``_token`` look like they belong to the retired probe — the retired code
read them, and ``health_probes.py`` still defines both constants — but they
are SHARED with ``mcp_http_probe`` and ``compose_drift_probe`` (same physical
agent) and are read by ``health_probes._call_agent_recovery``. Taking them out
alongside the watch-list key would silently disable host-side service restarts.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[6]
_MIGRATIONS_DIR = _REPO / "src" / "cofounder_agent" / "services" / "migrations"
_DEFAULTS_PY = _REPO / "src" / "cofounder_agent" / "services" / "settings_defaults.py"
_BRAIN_SEED = _REPO / "brain" / "seed_app_settings.json"
_HEALTH_PROBES_PY = _REPO / "brain" / "health_probes.py"
_RECOVERY_AGENT_PY = _REPO / "scripts" / "recovery-agent.py"
_MIGRATION = (
    _MIGRATIONS_DIR
    / "20260809_020020_drop_settings_orphaned_by_the_retired_gate_and_scheduled_tasks_probes.py"
)

_DEAD_KEYS = (
    # -> brain/gate_auto_expire_probe.py, deleted in a38f60591 (poindexter#559/#570)
    "gate_auto_expire_enabled",
    "gate_auto_expire_batch_size",
    "gate_auto_expire_notify_threshold",
    # -> probe_scheduled_tasks, retired 2026-08-08 (Windows Task Scheduler is gone)
    "scheduled_tasks_probe_watch_tasks",
)

# Shared with mcp_http_probe + compose_drift_probe, and read by
# health_probes._call_agent_recovery. The retired probe read them too, which is
# exactly why they are easy to delete by association.
_KEEP_RECOVERY_AGENT_KEYS = (
    "mcp_http_probe_recovery_url",
    "mcp_http_probe_recovery_token",
)

# Names that must not come back into brain/health_probes.py.
_RETIRED_SYMBOLS = (
    "probe_scheduled_tasks",
    "SCHED_TASKS_WATCH_KEY",
    "_evaluate_scheduled_task_health",
    "_derive_tasks_url",
)


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    return (_MIGRATIONS_DIR / "0000_baseline.seeds.sql").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def brain_seed_keys() -> set[str]:
    data = json.loads(_BRAIN_SEED.read_text(encoding="utf-8"))
    return {s["key"] for s in data.get("settings", []) if isinstance(s, dict) and "key" in s}


@pytest.fixture(scope="module")
def defaults_keys() -> set[str]:
    tree = ast.parse(_DEFAULTS_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        target_is_defaults = False
        value = None
        if isinstance(node, ast.Assign):
            target_is_defaults = any(
                isinstance(t, ast.Name) and t.id == "DEFAULTS" for t in node.targets
            )
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_is_defaults = node.target.id == "DEFAULTS"
            value = node.value
        if target_is_defaults and isinstance(value, ast.Dict):
            return {
                k.value
                for k in value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    raise AssertionError("DEFAULTS dict not found in settings_defaults.py")


@pytest.fixture(scope="module")
def health_probes_text() -> str:
    return _HEALTH_PROBES_PY.read_text(encoding="utf-8")


def _seeds_key(seeds_text: str, key: str) -> bool:
    """True when the baseline seeds an EXACT key (the quote+comma anchors the match)."""
    return re.search(rf"VALUES \('{re.escape(key)}',", seeds_text) is not None


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_baseline_seeds(baseline_seeds_text: str, key: str) -> None:
    assert not _seeds_key(baseline_seeds_text, key), (
        f"{key!r} is back in 0000_baseline.seeds.sql — its reader was deleted "
        "(gate probes in a38f60591 / probe_scheduled_tasks 2026-08-08). A DB "
        "delete alone loses to the next fresh install; leave it out of the seeds."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_defaults(defaults_keys: set[str], key: str) -> None:
    assert key not in defaults_keys, (
        f"{key!r} is back in settings_defaults.DEFAULTS — seed_all_defaults would "
        "re-insert it on the next boot, undoing the migration."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_brain_seed(brain_seed_keys: set[str], key: str) -> None:
    assert key not in brain_seed_keys, (
        f"{key!r} is back in brain/seed_app_settings.json — the brain seeds "
        "FIRST on a `docker compose up` install, so it would win the race and "
        "resurrect the key."
    )


@pytest.mark.parametrize("key", _KEEP_RECOVERY_AGENT_KEYS)
def test_shared_recovery_agent_keys_survive(
    defaults_keys: set[str], baseline_seeds_text: str, key: str
) -> None:
    assert key not in _DEAD_KEYS, (
        f"{key!r} was read by the retired probe but is SHARED with mcp_http_probe "
        "and compose_drift_probe (same physical agent). Retiring it disables "
        "host-side service restarts."
    )
    # The url is an ordinary DEFAULTS key; the token is one of the two
    # is_secret=true placeholders the baseline seeds (secrets are excluded from
    # DEFAULTS by convention), so accept either home.
    assert key in defaults_keys or _seeds_key(baseline_seeds_text, key), (
        f"{key!r} vanished from both settings_defaults.DEFAULTS and the baseline "
        "seeds — health_probes._call_agent_recovery reads it to POST host restarts."
    )


@pytest.mark.parametrize("symbol", _RETIRED_SYMBOLS)
def test_retired_probe_symbols_gone(health_probes_text: str, symbol: str) -> None:
    assert symbol not in health_probes_text, (
        f"{symbol!r} is back in brain/health_probes.py. probe_scheduled_tasks "
        "watched Windows Task Scheduler via the host agent's GET /tasks; the "
        "host runs Pop!_OS with systemd timers, so there is nothing to watch. "
        "A Linux replacement needs its own probe, not this one restored."
    )


def test_scheduled_tasks_not_in_probe_registry(health_probes_text: str) -> None:
    # The PROBES dict is what the brain cycle actually iterates — a probe left
    # defined but unregistered would be dead code, and one registered but
    # undefined is an import-time crash.
    assert '"scheduled_tasks":' not in health_probes_text


def test_recovery_agent_tasks_endpoint_removed() -> None:
    # The agent's GET /tasks existed solely to serve probe_scheduled_tasks and
    # is pure PowerShell (Get-ScheduledTask / Get-ScheduledTaskInfo).
    text = _RECOVERY_AGENT_PY.read_text(encoding="utf-8")
    assert "query_task_statuses" not in text
    assert "Get-ScheduledTask" not in text
    assert '"/tasks"' not in text
    # …but the POST /recover half of the agent stays.
    assert "dispatch_recovery" in text


def test_migration_lists_exactly_the_dead_keys() -> None:
    # Keeps the migration's explicit tuple and this test's list from drifting
    # apart — the migration deletes by exact name (never a LIKE pattern) so it
    # can't widen onto a live key.
    tree = ast.parse(_MIGRATION.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "ORPHANED_KEYS" for t in node.targets
        ):
            keys = {
                e.value
                for e in node.value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }
            assert keys == set(_DEAD_KEYS)
            return
    raise AssertionError("ORPHANED_KEYS tuple not found in the migration")
