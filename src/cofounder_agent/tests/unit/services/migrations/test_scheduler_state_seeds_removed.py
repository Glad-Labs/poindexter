"""Regression gate: scheduler run/status state is no longer seeded in app_settings.

The plugin_job_last_run_<job> / plugin_job_last_status_<job> rows were relocated
out of app_settings into the dedicated job_run_state table (2026-06-21,
docs/superpowers/specs/2026-06-21-job-run-state-table-design.md). The baseline
seeds were scrubbed in the same change; this test pins that so a future baseline
re-squash or a settings-defaults regeneration can't reintroduce them — which
would resurrect the config-table pollution AND lose to the relocation migration's
one-time DELETE on every boot (the reseed-drift trap, see
scripts/ci/settings_seed_drift_lint.py).
"""
from __future__ import annotations

from pathlib import Path

import pytest

_PREFIXES = ("plugin_job_last_run_", "plugin_job_last_status_")


def _backend_root() -> Path:
    # parents: [0]=migrations [1]=services [2]=unit [3]=tests [4]=cofounder_agent
    return Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    return (
        _backend_root() / "services" / "migrations" / "0000_baseline.seeds.sql"
    ).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def settings_defaults_text() -> str:
    return (_backend_root() / "services" / "settings_defaults.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("prefix", _PREFIXES)
def test_no_scheduler_state_in_baseline_seeds(prefix: str, baseline_seeds_text: str) -> None:
    assert prefix not in baseline_seeds_text, (
        f"{prefix!r} reappeared in 0000_baseline.seeds.sql. Scheduler run/status "
        "state lives in job_run_state now; seeding it in app_settings resurrects "
        "the relocated keys on every boot (reseed-drift)."
    )


@pytest.mark.parametrize("prefix", _PREFIXES)
def test_no_scheduler_state_in_settings_defaults(prefix: str, settings_defaults_text: str) -> None:
    assert prefix not in settings_defaults_text, (
        f"{prefix!r} is in settings_defaults.py — scheduler state must not be "
        "seeded as an app_settings default; it lives in job_run_state."
    )
