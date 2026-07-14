"""Unit tests for ``services/jobs/probe_disabled_capabilities.py``.

The probe checks a curated list of opt-in capability flags and emits a
single advisory ``disabled_capabilities`` finding (severity ``warn``) when
any are off. glad-labs-stack#2133.

Severity is ``warn``, not ``info``: findings_alert_router's SQL layer only
ever fetches severity IN ('warn','warning','critical') before the per-kind
min_severity policy is even consulted, so an info-severity finding can never
route regardless of policy config (findings-delivery-needs-warn-severity
memory, glad-labs-stack#1471 precedent — same mistake previously bit
topic_gap). ``test_severity_is_routable`` guards against a regression back
to ``info``.

``emit_finding`` patched so we assert routing intent without touching
audit_log.
"""

from __future__ import annotations

from unittest.mock import patch

from services.jobs.probe_disabled_capabilities import (
    _WATCHED_CAPABILITIES,
    ProbeDisabledCapabilitiesJob,
)
from services.site_config import SiteConfig

_MODULE = "services.jobs.probe_disabled_capabilities"


class TestProbeDisabledCapabilitiesJob:
    async def test_emits_finding_when_all_default_off(self):
        # No initial_config -> every watched key falls back to its
        # get_bool(..., False) default, matching the real seeded defaults.
        sc = SiteConfig()
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            result = await ProbeDisabledCapabilitiesJob().run(None, {"_site_config": sc})
        assert result.ok is True
        mock_emit.assert_called_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["kind"] == "disabled_capabilities"
        assert kwargs["severity"] == "warn"
        assert kwargs["extra"]["count"] == len(_WATCHED_CAPABILITIES)
        assert "enable_writer_self_review" in kwargs["extra"]["keys"]

    async def test_severity_is_routable(self):
        # Regression guard: findings_alert_router._fetch_unrouted_findings
        # only fetches severity IN ('warn','warning','critical') at the SQL
        # layer, BEFORE the per-kind min_severity policy is even consulted.
        # An 'info' severity here would make this finding permanently
        # unroutable no matter how findings.disabled_capabilities.* is
        # configured — see findings-delivery-needs-warn-severity memory.
        from services.jobs.findings_alert_router import _ROUTABLE_SEVERITIES

        sc = SiteConfig()
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            await ProbeDisabledCapabilitiesJob().run(None, {"_site_config": sc})
        assert mock_emit.call_args.kwargs["severity"] in _ROUTABLE_SEVERITIES

    async def test_no_finding_when_all_enabled(self):
        sc = SiteConfig(initial_config={key: "true" for key, _label in _WATCHED_CAPABILITIES})
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            result = await ProbeDisabledCapabilitiesJob().run(None, {"_site_config": sc})
        assert result.ok is True
        mock_emit.assert_not_called()

    async def test_reports_only_the_disabled_subset(self):
        all_on = {key: "true" for key, _label in _WATCHED_CAPABILITIES}
        all_on["social_drafts_enabled"] = "false"
        all_on["newsletter_enabled"] = "false"
        sc = SiteConfig(initial_config=all_on)
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            result = await ProbeDisabledCapabilitiesJob().run(None, {"_site_config": sc})
        assert result.ok is True
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["extra"]["count"] == 2
        assert set(kwargs["extra"]["keys"]) == {"social_drafts_enabled", "newsletter_enabled"}
        assert "enable_writer_self_review" not in kwargs["body"]
        assert "Social draft generation" in kwargs["body"]

    async def test_disabled_probe_skips_check_and_finding(self):
        sc = SiteConfig(initial_config={"disabled_capabilities_probe_enabled": "false"})
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            result = await ProbeDisabledCapabilitiesJob().run(None, {"_site_config": sc})
        assert result.ok is True
        assert result.detail == "probe disabled"
        mock_emit.assert_not_called()

    async def test_dedup_key_is_stable(self):
        sc = SiteConfig()
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            await ProbeDisabledCapabilitiesJob().run(None, {"_site_config": sc})
        # Stable dedup_key so the dispatcher collapses repeated daily fires
        # per the findings.disabled_capabilities.cooldown_minutes policy
        # instead of re-alerting every cycle.
        assert mock_emit.call_args.kwargs["dedup_key"] == "disabled_capabilities"

    async def test_no_site_config_falls_back_to_defaults(self):
        # config.get("_site_config") returning None must not crash — every
        # watched key's _cfg_bool default is False, so this behaves like
        # "everything disabled" rather than raising.
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            result = await ProbeDisabledCapabilitiesJob().run(None, {})
        assert result.ok is True
        mock_emit.assert_called_once()
        assert mock_emit.call_args.kwargs["extra"]["count"] == len(_WATCHED_CAPABILITIES)
