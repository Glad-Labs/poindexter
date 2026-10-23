"""Unit tests for SyncProSubscriptionsJob (glad-labs-stack#3216)."""

from __future__ import annotations

from typing import Any

import pytest

import poindexter.services.pro_delivery as pro_delivery
from poindexter.services.jobs.sync_pro_subscriptions import SyncProSubscriptionsJob
from poindexter.services.pro_delivery import ProDeliveryConfigError, SyncOutcome
from poindexter.services.site_config import SiteConfig


@pytest.fixture()
def findings(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    import poindexter.services.jobs.sync_pro_subscriptions as job_module

    monkeypatch.setattr(job_module, "emit_finding", lambda **kw: captured.append(kw))
    return captured


async def test_noop_without_site_config():
    result = await SyncProSubscriptionsJob().run(object(), {})
    assert result.ok is False
    assert "_site_config" in result.detail


async def test_noop_when_disabled():
    config = {"_site_config": SiteConfig(initial_config={"pro_delivery_enabled": "false"})}
    result = await SyncProSubscriptionsJob().run(object(), config)
    assert result.ok is True
    assert "no-op" in result.detail


async def test_config_error_fails_loud_with_finding(monkeypatch, findings):
    async def _boom(pool, site_config, **kw):
        raise ProDeliveryConfigError("set: pro_delivery_github_repo")

    monkeypatch.setattr(pro_delivery, "run_sync", _boom)
    config = {"_site_config": SiteConfig(initial_config={"pro_delivery_enabled": "true"})}
    result = await SyncProSubscriptionsJob().run(object(), config)

    assert result.ok is False
    assert "pro_delivery_github_repo" in result.detail
    assert len(findings) == 1
    assert findings[0]["kind"] == "pro_delivery_error"
    assert findings[0]["severity"] == "error"


async def test_success_reports_metrics_and_changes(monkeypatch, findings):
    outcome = SyncOutcome(
        subscriptions_seen=3,
        invited=["octocat"],
        revoked=["lapsed-user"],
        missing_username=["55"],
        revenue_rows=1,
    )

    async def _ok(pool, site_config, **kw):
        return outcome

    monkeypatch.setattr(pro_delivery, "run_sync", _ok)
    config = {"_site_config": SiteConfig(initial_config={"pro_delivery_enabled": "true"})}
    result = await SyncProSubscriptionsJob().run(object(), config)

    assert result.ok is True
    assert result.changes_made == 3  # invite + revoke + revenue row
    assert result.metrics["subscriptions_seen"] == 3
    assert "invited 1" in result.detail
    assert findings == []


async def test_partial_errors_flip_ok_false(monkeypatch, findings):
    outcome = SyncOutcome(subscriptions_seen=2, errors=["101: github 500"])

    async def _partial(pool, site_config, **kw):
        return outcome

    monkeypatch.setattr(pro_delivery, "run_sync", _partial)
    config = {"_site_config": SiteConfig(initial_config={"pro_delivery_enabled": "true"})}
    result = await SyncProSubscriptionsJob().run(object(), config)

    assert result.ok is False
    assert result.metrics["errors"] == 1


# ---- A failed poll is deferred, not paged (2026-10-23) ----------------------

import httpx  # noqa: E402


class _Pool:
    """Answers the last-good-sync query with ``minutes`` (None = no record)."""

    def __init__(self, minutes):
        self.minutes = minutes

    async def fetchval(self, sql, *args):
        assert "job_run" in sql
        return self.minutes


def _status_error(code):
    req = httpx.Request("GET", "https://api.lemonsqueezy.com/v1/subscriptions")
    return httpx.HTTPStatusError(f"{code}", request=req, response=httpx.Response(code, request=req))


def _enabled():
    return {"_site_config": SiteConfig(initial_config={"pro_delivery_enabled": "true"})}


@pytest.mark.parametrize("exc", [
    _status_error(503),
    httpx.ConnectTimeout("timed out"),
    httpx.ConnectError("All connection attempts failed"),
    httpx.ReadTimeout(""),
])
async def test_a_lemon_squeezy_blip_is_deferred_without_a_page(monkeypatch, findings, exc):
    """Oct 4, 18 and 22: one failed poll paged twice; the next poll succeeded."""
    async def _blip(pool, site_config, **kw):
        raise exc

    monkeypatch.setattr(pro_delivery, "run_sync", _blip)
    result = await SyncProSubscriptionsJob().run(_Pool(5.0), _enabled())
    assert result.ok is True  # no scheduler job_failure
    assert result.detail.startswith("deferred")
    assert findings == []


async def test_an_outage_past_the_window_pages_once(monkeypatch, findings):
    async def _down(pool, site_config, **kw):
        raise _status_error(503)

    monkeypatch.setattr(pro_delivery, "run_sync", _down)
    result = await SyncProSubscriptionsJob().run(_Pool(75.0), _enabled())
    assert result.ok is True
    assert [f["dedup_key"] for f in findings] == ["pro_delivery_sync_stalled"]
    assert "75 minutes" in findings[0]["body"]


async def test_no_good_sync_on_record_pages(monkeypatch, findings):
    async def _down(pool, site_config, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(pro_delivery, "run_sync", _down)
    await SyncProSubscriptionsJob().run(_Pool(None), _enabled())
    assert len(findings) == 1


async def test_a_rejected_key_still_pages_at_once(monkeypatch, findings):
    """A 401 is not something the next poll fixes."""
    async def _denied(pool, site_config, **kw):
        raise _status_error(401)

    monkeypatch.setattr(pro_delivery, "run_sync", _denied)
    result = await SyncProSubscriptionsJob().run(_Pool(5.0), _enabled())
    assert result.ok is False
    assert [f["dedup_key"] for f in findings] == ["pro_delivery_sync_failed"]
