"""Regression gate: the orphaned ``drive_media_gates`` job seeds stay gone.

``drive_media_gates`` was a *planned* media-gate driver job
(``docs/superpowers/plans/2026-05-31-media-gated-publish.md``) whose
implementation never shipped — ``services/jobs/drive_media_gates.py`` does
not exist and ``plugins/registry.py`` does not register it. But three
orphaned seed rows for it survived in ``0000_baseline.seeds.sql``, so the
PluginScheduler carried a phantom entry that last ran 2026-06-06 and could
never resolve a job class again. They were removed (and a tombstone
migration deletes them from already-seeded DBs).

This test pins the cleanup so a future baseline re-squash — or a re-run of
the settings-defaults extractor that regenerates the seed file — can't
silently reintroduce the dead job.

Media is now produced by the Stage-2 ``media_pipeline`` lane
(``services/jobs/dispatch_media_pipeline.py`` -> render atoms ->
``services/jobs/media_distribute.py``), with the ``media_reconciliation``
watchdog as the DB<->R2 safety net — none of which is ``drive_media_gates``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# The three orphaned app_settings keys that seeded the dead job: its
# scheduler config plus the two PluginScheduler telemetry rows.
_DEAD_JOB_KEYS = [
    "plugin.job.drive_media_gates",
    "plugin_job_last_run_drive_media_gates",
    "plugin_job_last_status_drive_media_gates",
]

# Substring shared by every dead-job key (and nothing else live). A broad
# net that catches any reintroduction regardless of the exact key name.
_DEAD_JOB_TOKEN = "drive_media_gates"


def _backend_root() -> Path:
    # parents[0]=migrations [1]=services [2]=unit [3]=tests [4]=cofounder_agent
    return Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    return (
        _backend_root() / "services" / "migrations" / "0000_baseline.seeds.sql"
    ).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def settings_defaults_text() -> str:
    return (
        _backend_root() / "services" / "settings_defaults.py"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("key", _DEAD_JOB_KEYS)
def test_dead_job_key_not_in_baseline_seeds(key: str, baseline_seeds_text: str) -> None:
    """No orphaned ``drive_media_gates`` row may be seeded by the baseline.

    Seeding a job with no resolvable class makes the PluginScheduler carry a
    phantom entry — exactly the "dead media job" that confused media-pipeline
    triage. If this fails, delete the matching INSERT from the seed file.
    """
    assert f"'{key}'" not in baseline_seeds_text, (
        f"Orphaned drive_media_gates seed '{key}' is back in "
        "0000_baseline.seeds.sql. The job class was never implemented "
        "(no services/jobs/drive_media_gates.py); seeding it makes the "
        "PluginScheduler carry a phantom, never-runnable entry."
    )


def test_no_drive_media_gates_token_in_baseline_seeds(baseline_seeds_text: str) -> None:
    """Belt-and-suspenders: the dead-job token must not appear at all.

    Catches a reintroduction under a renamed key (e.g. a future
    ``plugin_job_last_error_drive_media_gates``) that the per-key checks
    above wouldn't enumerate.
    """
    assert _DEAD_JOB_TOKEN not in baseline_seeds_text, (
        f"'{_DEAD_JOB_TOKEN}' reappeared in 0000_baseline.seeds.sql — the "
        "dead media-gate driver job must not be seeded in any form."
    )


@pytest.mark.parametrize("key", _DEAD_JOB_KEYS)
def test_dead_job_key_not_in_settings_defaults(key: str, settings_defaults_text: str) -> None:
    """The dead job must not creep back in via the defaults seeder either.

    ``settings_defaults.py`` is applied every boot (``seed_all_defaults``);
    a default here would re-create the orphan even after the tombstone
    migration deletes it.
    """
    assert key not in settings_defaults_text, (
        f"Orphaned drive_media_gates key '{key}' is in settings_defaults.py. "
        "The job was never implemented — don't seed it as a default."
    )
