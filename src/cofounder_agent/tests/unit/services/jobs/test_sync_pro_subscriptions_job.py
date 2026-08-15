"""Unit tests for SyncProSubscriptionsJob (glad-labs-stack#3216)."""

from __future__ import annotations

from typing import Any

import pytest

import services.pro_delivery as pro_delivery
from services.jobs.sync_pro_subscriptions import SyncProSubscriptionsJob
from services.pro_delivery import ProDeliveryConfigError, SyncOutcome
from services.site_config import SiteConfig


@pytest.fixture()
def findings(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    import services.jobs.sync_pro_subscriptions as job_module

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
