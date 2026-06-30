"""Wiring/contract tests for the #756 read-telemetry + orphan-probe feature.

Locks three things a future edit could silently break:
- the seed defaults exist and are non-empty (feedback_app_settings_value_not_null),
- the finding kind stays dot-free so findings_alert_router's 3-segment policy
  parser actually binds the Discord delivery policy,
- both jobs are registered in the core samples table.
"""

from __future__ import annotations

from plugins.registry import get_core_samples
from services.jobs.findings_alert_router import _delivery_for
from services.jobs.probe_zero_reader_settings import _FINDING_KIND
from services.settings_defaults import DEFAULTS

_NEW_KEYS = {
    "findings.settings_zero_reader_keys.delivery": "discord",
    "findings.settings_zero_reader_keys.min_severity": "warn",
    "settings_read_telemetry_enabled": "true",
    "settings_read_telemetry_min_restamp_seconds": "3600",
    "settings_zero_reader_probe_enabled": "true",
    "settings_zero_reader_grace_days": "30",
    "settings_zero_reader_max_report": "50",
}


def test_seeds_present_and_nonempty():
    for key, expected in _NEW_KEYS.items():
        assert DEFAULTS.get(key) == expected
        # '' is the unset sentinel that crashes NOT-NULL CI — never seed it.
        assert DEFAULTS[key] != ""


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


def test_both_jobs_registered():
    job_names = {j.name for j in get_core_samples()["jobs"]}
    assert "flush_settings_read_telemetry" in job_names
    assert "probe_zero_reader_settings" in job_names
