"""ProbeNewsletterSignupJob — the daily end-to-end signup canary.

The canary logic is pinned in tests/unit/services/test_newsletter_signup_canary.py.
These pin the job's contract: off until a URL is set, and every failure mode
(broken capture, misconfiguration, a crash) reaches the operator as a
``newsletter_signup_capture_broken`` finding. That is the finding this path
never had through two silent outages.
"""

from __future__ import annotations

from typing import Any

import pytest

from poindexter.services import newsletter_signup_canary as canary
from poindexter.services.jobs import probe_newsletter_signup as mod
from poindexter.services.jobs.probe_newsletter_signup import ProbeNewsletterSignupJob
from tests.unit.services._newsletter_fakes import FakeSiteConfig


@pytest.fixture
def findings(monkeypatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(mod, "emit_finding", lambda **kw: captured.append(kw))
    return captured


def _stub(monkeypatch, result=None, exc: Exception | None = None):
    async def fake(site_config, **kw):
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(canary, "run_signup_canary", fake)


def test_job_contract():
    job = ProbeNewsletterSignupJob()
    assert job.name == "probe_newsletter_signup"
    assert job.schedule == "every 24 hours"
    assert job.idempotent is False


def test_job_registered_in_core_samples():
    from poindexter.plugins.registry import get_core_samples

    jobs = get_core_samples().get("jobs", [])
    assert any(getattr(j, "name", None) == "probe_newsletter_signup" for j in jobs)


async def test_off_until_a_url_is_set(findings, monkeypatch):
    _stub(monkeypatch, result=None)
    result = await ProbeNewsletterSignupJob().run(object(), {"_site_config": FakeSiteConfig()})
    assert result.ok is True
    assert "canary off" in result.detail
    assert findings == []


async def test_a_healthy_run_is_quiet(findings, monkeypatch):
    _stub(monkeypatch, result=canary.SignupCanaryOutcome(
        url="u", route_ok=True, in_segment=True, cleaned_up=True,
        http_status=200, attempts=1, latency_ms=120,
    ))
    result = await ProbeNewsletterSignupJob().run(object(), {"_site_config": FakeSiteConfig()})
    assert result.ok is True
    assert result.metrics["healthy"] is True
    assert findings == []


async def test_a_broken_capture_raises_the_finding(findings, monkeypatch):
    _stub(monkeypatch, result=canary.SignupCanaryOutcome(
        url="https://site.example/api/newsletter/subscribe",
        http_status=503, attempts=2,
        problem="the signup endpoint did not capture the canary (HTTP 503)",
    ))
    result = await ProbeNewsletterSignupJob().run(object(), {"_site_config": FakeSiteConfig()})
    assert result.ok is False
    [finding] = findings
    assert finding["kind"] == "newsletter_signup_capture_broken"
    assert finding["severity"] == "warn"
    assert "HTTP 503" in finding["body"]
    assert "site.example" in finding["body"]
    # Actionable: says where to look and how to re-check.
    assert "RESEND_AUDIENCE_ID" in finding["body"]
    assert "poindexter newsletter canary" in finding["body"]


async def test_a_canary_that_cannot_run_raises_the_finding(findings, monkeypatch):
    _stub(monkeypatch, exc=canary.CanaryConfigError("resend_api_key is not set"))
    result = await ProbeNewsletterSignupJob().run(object(), {"_site_config": FakeSiteConfig()})
    assert result.ok is False
    [finding] = findings
    assert finding["kind"] == "newsletter_signup_capture_broken"
    assert "resend_api_key is not set" in finding["body"]


async def test_a_crash_raises_the_finding(findings, monkeypatch):
    _stub(monkeypatch, exc=RuntimeError("unexpected"))
    result = await ProbeNewsletterSignupJob().run(object(), {"_site_config": FakeSiteConfig()})
    assert result.ok is False
    [finding] = findings
    assert finding["kind"] == "newsletter_signup_capture_broken"
    assert "RuntimeError: unexpected" in finding["body"]
