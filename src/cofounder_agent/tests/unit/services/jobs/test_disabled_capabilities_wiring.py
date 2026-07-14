"""Wiring/contract tests for the #2133 disabled-capabilities probe.

Locks four things a future edit could silently break:
- the seed defaults exist and are non-empty (feedback_app_settings_value_not_null),
- the finding kind stays dot-free so findings_alert_router's 3-segment policy
  parser actually binds the Discord delivery policy,
- a warn-severity finding of this kind actually resolves to discord (not
  log_only),
- the emitted severity clears findings_alert_router's SQL-layer routable-set
  floor — an info-severity finding is filtered out BEFORE min_severity is
  even consulted, so it would never route regardless of policy config
  (findings-delivery-needs-warn-severity memory, glad-labs-stack#1471
  precedent: same mistake previously bit topic_gap),
- the job is registered in the core samples table.
"""

from __future__ import annotations

from plugins.registry import get_core_samples
from services.jobs.findings_alert_router import _ROUTABLE_SEVERITIES, _delivery_for
from services.jobs.probe_disabled_capabilities import _FINDING_KIND
from services.settings_defaults import DEFAULTS

_NEW_KEYS = {
    "findings.disabled_capabilities.delivery": "discord",
    "findings.disabled_capabilities.fallback": "log_only",
    "findings.disabled_capabilities.cooldown_minutes": "10080",
    "findings.disabled_capabilities.min_severity": "warn",
    "disabled_capabilities_probe_enabled": "true",
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


def test_warn_severity_resolves_to_discord():
    policies = {_FINDING_KIND: {"delivery": "discord", "min_severity": "warn"}}
    assert _delivery_for(_FINDING_KIND, "warn", policies) == "discord"


def test_min_severity_clears_the_sql_routable_floor():
    # findings_alert_router._fetch_unrouted_findings only ever fetches
    # severity IN ('warn','warning','critical') — an 'info' seed here would
    # be a lie: the finding could never be fetched to route in the first
    # place, no matter what min_severity says.
    assert DEFAULTS["findings.disabled_capabilities.min_severity"] in _ROUTABLE_SEVERITIES


def test_job_registered():
    job_names = {j.name for j in get_core_samples()["jobs"]}
    assert "probe_disabled_capabilities" in job_names
