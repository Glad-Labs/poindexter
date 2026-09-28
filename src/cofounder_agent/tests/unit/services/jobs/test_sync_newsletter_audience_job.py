"""SyncNewsletterAudienceJob — the owned subscriber list's producer.

The service behaviour is pinned in tests/unit/services/test_newsletter_audience.py.
These pin the job's contract with the scheduler and the findings pipeline: a
fresh install is a quiet no-op, and every way the pull can fail reaches the
operator as a ``newsletter_audience_sync_failed`` finding.
"""

from __future__ import annotations

from typing import Any

import pytest

from poindexter.services import newsletter_audience as na
from poindexter.services.jobs import sync_newsletter_audience as mod
from poindexter.services.jobs.sync_newsletter_audience import SyncNewsletterAudienceJob
from tests.unit.services._newsletter_fakes import FakeSiteConfig


@pytest.fixture
def findings(monkeypatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(mod, "emit_finding", lambda **kw: captured.append(kw))
    return captured


def test_job_contract():
    job = SyncNewsletterAudienceJob()
    assert job.name == "sync_newsletter_audience"
    assert job.schedule == "every 15 minutes"
    # Read-then-insert per contact: overlapping passes must be serialized.
    assert job.idempotent is False


def test_job_registered_in_core_samples():
    from poindexter.plugins.registry import get_core_samples

    jobs = get_core_samples().get("jobs", [])
    assert any(getattr(j, "name", None) == "sync_newsletter_audience" for j in jobs)


async def test_no_site_config_is_a_failed_run(findings):
    result = await SyncNewsletterAudienceJob().run(object(), {})
    assert result.ok is False
    assert findings == []


async def test_unconfigured_install_is_a_quiet_no_op(findings, monkeypatch):
    async def must_not_run(*a, **k):  # pragma: no cover — must not be called
        raise AssertionError("no pull without a segment")

    monkeypatch.setattr(na, "sync_segment_to_subscribers", must_not_run)
    sc = FakeSiteConfig({"resend_audience_id": ""})
    result = await SyncNewsletterAudienceJob().run(object(), {"_site_config": sc})
    assert result.ok is True
    assert "no-op" in result.detail
    assert findings == []


async def test_a_clean_pull_reports_its_changes(findings, monkeypatch):
    outcome = na.AudienceSyncOutcome(
        segment_id="seg", contacts_seen=3, imported=2, unsubscribes_applied=1
    )

    async def fake_sync(pool, site_config, **kw):
        return outcome

    monkeypatch.setattr(na, "sync_segment_to_subscribers", fake_sync)
    result = await SyncNewsletterAudienceJob().run(object(), {"_site_config": FakeSiteConfig()})
    assert result.ok is True
    assert result.changes_made == 3
    assert result.metrics["imported"] == 2
    assert findings == []


async def test_per_contact_errors_raise_one_finding(findings, monkeypatch):
    outcome = na.AudienceSyncOutcome(segment_id="seg", errors=["contact c1: boom"])

    async def fake_sync(pool, site_config, **kw):
        return outcome

    monkeypatch.setattr(na, "sync_segment_to_subscribers", fake_sync)
    result = await SyncNewsletterAudienceJob().run(object(), {"_site_config": FakeSiteConfig()})
    assert result.ok is False
    [finding] = findings
    assert finding["kind"] == "newsletter_audience_sync_failed"
    assert "contact c1: boom" in finding["body"]


async def test_a_bad_segment_id_raises_a_finding(findings):
    sc = FakeSiteConfig({"resend_audience_id": "abc/def"})
    result = await SyncNewsletterAudienceJob().run(object(), {"_site_config": sc})
    assert result.ok is False
    [finding] = findings
    assert finding["kind"] == "newsletter_audience_sync_failed"
    assert "plain Resend id token" in finding["body"]


async def test_a_failed_read_raises_a_finding(findings, monkeypatch):
    async def boom(pool, site_config, **kw):
        raise RuntimeError("Resend 404: segment not found")

    monkeypatch.setattr(na, "sync_segment_to_subscribers", boom)
    result = await SyncNewsletterAudienceJob().run(object(), {"_site_config": FakeSiteConfig()})
    assert result.ok is False
    [finding] = findings
    assert finding["kind"] == "newsletter_audience_sync_failed"
    assert "segment not found" in finding["body"]
    # The finding says what is at stake, not just what broke.
    assert "not reaching newsletter_subscribers" in finding["body"]
