"""Contract test for the plugin_job_last_run_* baseline seeds.

Pins the 2026-05-26 audit fix: 25 plugin_job_last_run_<name> rows in
0000_baseline.seeds.sql previously carried Matt's actual operator
epoch timestamps (~1778260286 = early May 2026), which on a fresh OSS
install would tell PluginScheduler "these jobs last ran 2+ weeks ago"
on day 1 — suppressing first-fire of every scheduled job until each
catches up to its own interval.

The seed values are TELEMETRY-only (PluginScheduler writes them every
fire but doesn't read them for scheduling decisions — APScheduler
tracks actual fire-times internally). Zeroing them means a fresh
install starts with "never run" semantics, and the first real fire
overwrites the seed with a true epoch via the ON CONFLICT DO NOTHING
seed path. Matt's prod is unaffected — ON CONFLICT DO NOTHING means
existing rows stay where they are.

This test pins the contract so a future migration squash (the
baseline was last squashed 2026-05-08) doesn't accidentally re-record
the operator's epoch state into the canonical seed file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Match every INSERT row whose key starts with "plugin_job_last_run_".
# Capture (key, value) so we can report which row regressed if the
# assertion fails.
_PLUGIN_JOB_LAST_RUN_INSERT = re.compile(
    r"INSERT INTO app_settings.+?VALUES \('(plugin_job_last_run_[^']+)', '([^']+)'",
    re.DOTALL,
)


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    """Read 0000_baseline.seeds.sql once per test module."""
    # parents[0]=migrations parents[1]=services parents[2]=unit
    # parents[3]=tests parents[4]=cofounder_agent — drop down into
    # services/migrations to find the canonical seed file.
    seeds_path = (
        Path(__file__).resolve().parents[4]
        / "services"
        / "migrations"
        / "0000_baseline.seeds.sql"
    )
    return seeds_path.read_text(encoding="utf-8")


def _plugin_job_last_run_rows(seeds_text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in _PLUGIN_JOB_LAST_RUN_INSERT.finditer(seeds_text)]


def test_plugin_job_last_run_seeds_exist(baseline_seeds_text: str) -> None:
    """Sanity check: the baseline still seeds these keys at all.
    If this fails, either the seeding intent has changed or the
    regex no longer matches — investigate before relaxing the test.
    """
    rows = _plugin_job_last_run_rows(baseline_seeds_text)
    assert len(rows) >= 20, (
        f"Expected ~25 plugin_job_last_run_* seed rows, found {len(rows)}. "
        "Either the baseline lost the seeds (which would degrade fresh-install "
        "behaviour — PluginScheduler dashboards lose the existence of the keys) "
        "or the assertion regex is stale."
    )


def test_plugin_job_last_run_seeds_are_zero(baseline_seeds_text: str) -> None:
    """Every plugin_job_last_run_<name> baseline seed must be '0'.

    Non-zero values are operator-leak (the actual epoch from Matt's prod
    leaks into the canonical baseline) AND a fresh-install footgun
    (PluginScheduler dashboards on a clean OSS install would show
    "last run 2+ weeks ago" until each job catches up).
    """
    rows = _plugin_job_last_run_rows(baseline_seeds_text)
    non_zero = [(k, v) for k, v in rows if v != "0"]
    assert not non_zero, (
        "plugin_job_last_run_* seeds must be '0' on a fresh install. "
        f"Non-zero rows found: {non_zero[:5]}{'…' if len(non_zero) > 5 else ''}. "
        "If you just regenerated the baseline, re-run with operator timestamps zeroed."
    )


def test_seed_uses_on_conflict_do_nothing(baseline_seeds_text: str) -> None:
    """The seeds must use ON CONFLICT DO NOTHING so production's
    real epochs survive a baseline re-replay. If a future change
    drops that clause, the next migration run would overwrite
    PluginScheduler's accumulated state with '0' across the board.
    """
    # Pull each whole INSERT statement that targets a
    # plugin_job_last_run_* key so we can check its tail clause.
    inserts = re.findall(
        r"INSERT INTO app_settings[^;]*?'plugin_job_last_run_[^']+'[^;]*;",
        baseline_seeds_text,
    )
    missing = [s for s in inserts if "ON CONFLICT (key) DO NOTHING" not in s]
    assert not missing, (
        f"{len(missing)} plugin_job_last_run_* INSERT(s) missing ON CONFLICT DO NOTHING. "
        "Without it, replaying the baseline would clobber Matt's prod telemetry to 0."
    )
