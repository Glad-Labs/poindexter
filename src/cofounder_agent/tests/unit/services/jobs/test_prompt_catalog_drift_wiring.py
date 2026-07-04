"""Wiring/contract tests for the Langfuse prompt-mirror sync job.

Locks the same three things the #756 zero-reader wiring test locks (a future
edit could silently break any of them):
- the seed defaults exist and are non-empty (feedback_app_settings_value_not_null),
- the finding kind stays dot-free so findings_alert_router's 3-segment policy
  parser actually binds the Discord delivery policy,
- the job is registered in the core samples table.

Plus the mirror-architecture invariants: the legacy Langfuse-first override
lookup ships OFF, and the superseded probe flag is not re-seeded.
"""

from __future__ import annotations

from plugins.registry import get_core_samples
from services.jobs.findings_alert_router import _delivery_for
from services.jobs.sync_prompt_catalog_to_langfuse import _FINDING_KIND
from services.settings_defaults import DEFAULTS

_NEW_KEYS = {
    "findings.prompt_catalog_drift.delivery": "discord",
    "findings.prompt_catalog_drift.min_severity": "warn",
    "langfuse_prompt_mirror_enabled": "true",
    # SKILL.md is authoritative — the override layer must ship disabled or
    # the masking trap (bulk-imported Langfuse copies shadowing SKILL.md
    # edits) comes back on day one.
    "langfuse_prompt_overrides_enabled": "false",
}


def test_seeds_present_and_nonempty():
    for key, expected in _NEW_KEYS.items():
        assert DEFAULTS.get(key) == expected
        # '' is the unset sentinel that crashes NOT-NULL CI — never seed it.
        assert DEFAULTS[key] != ""


def test_superseded_probe_flag_not_reseeded():
    # Migration 20260704_024658 deletes this key from live installs; a seed
    # would resurrect it on next boot (reference_baseline_seeds_reseed_drift).
    assert "prompt_catalog_drift_probe_enabled" not in DEFAULTS


def test_finding_kind_is_dot_free():
    # findings_alert_router._load_policies parses findings.<kind>.<field> as
    # EXACTLY 3 dot-segments; a dotted kind would make the policy a 4-segment
    # key it silently skips, so the Discord routing would never bind.
    assert "." not in _FINDING_KIND
    assert f"findings.{_FINDING_KIND}.delivery" in DEFAULTS


def test_delivery_resolves_to_discord():
    # Mirror the dict shape _load_policies produces, then prove a warn-severity
    # finding of this kind resolves to the discord channel.
    policies = {_FINDING_KIND: {"delivery": "discord", "min_severity": "warn"}}
    assert _delivery_for(_FINDING_KIND, "warn", policies) == "discord"


def test_job_registered():
    job_names = {j.name for j in get_core_samples()["jobs"]}
    assert "sync_prompt_catalog_to_langfuse" in job_names
    # the superseded probe must be gone — two jobs hitting the same finding
    # kind would double-page
    assert "probe_prompt_catalog_drift" not in job_names
