"""Unit tests for ``brain/business_probes.probe_webhook_freshness``.

Glad-Labs/poindexter#27 — alert when revenue_events / subscriber_events
table go quiet beyond their configured thresholds. The probe is
designed to be best-effort + never raise, so the tests cover both
the happy paths and the "DB hiccups" / "feature disabled" branches.

The probe reads four app_settings keys + queries two tables, so we
mock the ``pool`` to return values from in-memory dicts. This is
exactly the pattern the other brain probe tests
(test_brain_compose_drift_probe.py, test_brain_health_probes.py) use.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain import business_probes as bp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(
    *,
    settings: dict[str, str] | None = None,
    revenue_last: datetime | None = None,
    subscriber_last: datetime | None = None,
) -> Any:
    """Build an async pool stub that serves the four reads the probe makes:

    * ``SELECT value FROM app_settings WHERE key = $1 AND is_active = TRUE``
    * ``SELECT MAX(created_at) FROM revenue_events``
    * ``SELECT MAX(created_at) FROM subscriber_events``

    The probe calls each via ``pool.fetchval``. We side_effect on the
    first arg (the SQL string) — keeping things string-matchy keeps the
    mocks honest about which query the probe is actually emitting.
    """
    settings = settings or {}

    async def _fetchval(query: str, *args: Any) -> Any:
        if query.startswith("SELECT value FROM app_settings"):
            key = args[0]
            return settings.get(key)
        if "revenue_events" in query:
            return revenue_last
        if "subscriber_events" in query:
            return subscriber_last
        return None

    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=_fetchval)
    return pool


@pytest.fixture(autouse=True)
def _reset_probe_state():
    """Probes track their own due-time in the module-level _last_run dict.
    Wipe between tests so each test has a clean slate.
    """
    bp._last_run.clear()
    yield
    bp._last_run.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbeWebhookFreshness:
    async def test_disabled_via_app_settings_returns_early(self):
        pool = _make_pool(settings={"probe_webhook_freshness_enabled": "false"})
        notify = MagicMock()
        result = await bp.probe_webhook_freshness(pool, notify)
        assert result["ok"] is True
        assert "disabled" in result["detail"]
        notify.assert_not_called()

    async def test_not_due_returns_early(self):
        # First run marks itself as run; second immediate run should
        # return "not due yet" without re-querying anything.
        pool = _make_pool(
            settings={"probe_webhook_freshness_interval_minutes": "1440"},
            revenue_last=datetime.now(timezone.utc),
            subscriber_last=datetime.now(timezone.utc),
        )
        notify = MagicMock()
        first = await bp.probe_webhook_freshness(pool, notify)
        assert first["ok"] is True

        second = await bp.probe_webhook_freshness(pool, notify)
        assert second["ok"] is True
        assert "not due" in second["detail"]

    async def test_fresh_rows_no_alert(self):
        # Both tables have a row from yesterday — well within thresholds.
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        pool = _make_pool(
            revenue_last=recent,
            subscriber_last=recent,
        )
        notify = MagicMock()
        result = await bp.probe_webhook_freshness(pool, notify)
        assert result["ok"] is True
        assert "all webhook tables fresh" in result["detail"]
        notify.assert_not_called()

    async def test_stale_revenue_fires_alert(self):
        # Revenue 60d old (threshold 30d) → alert.
        # Subscribers fresh — no alert for that table.
        old = datetime.now(timezone.utc) - timedelta(days=60)
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        pool = _make_pool(
            revenue_last=old,
            subscriber_last=recent,
        )
        notify = MagicMock()
        result = await bp.probe_webhook_freshness(pool, notify)
        assert result["ok"] is True
        assert "fired 1" in result["detail"]
        notify.assert_called_once()
        body = notify.call_args.args[0]
        assert "revenue_events" in body
        assert "lemonsqueezy" in body.lower()
        assert "subscriber_events" not in body

    async def test_stale_subscribers_fires_alert(self):
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        old = datetime.now(timezone.utc) - timedelta(days=14)
        pool = _make_pool(
            revenue_last=recent,
            subscriber_last=old,  # 14d > 7d default threshold.
        )
        notify = MagicMock()
        result = await bp.probe_webhook_freshness(pool, notify)
        assert result["ok"] is True
        assert "fired 1" in result["detail"]
        notify.assert_called_once()
        body = notify.call_args.args[0]
        assert "subscriber_events" in body
        assert "resend" in body.lower()

    async def test_both_stale_fires_two_alerts_in_one_message(self):
        """When both tables are stale we batch into one notification —
        cheaper (one Telegram push) and easier to triage.
        """
        old = datetime.now(timezone.utc) - timedelta(days=60)
        pool = _make_pool(
            revenue_last=old,
            subscriber_last=old,
        )
        notify = MagicMock()
        result = await bp.probe_webhook_freshness(pool, notify)
        assert result["ok"] is True
        assert "fired 2" in result["detail"]
        notify.assert_called_once()
        body = notify.call_args.args[0]
        assert "revenue_events" in body
        assert "subscriber_events" in body

    async def test_empty_table_treated_as_infinitely_stale(self):
        """MAX() over an empty table returns NULL → probe treats as
        "no row ever" and fires an alert (matches operator intent — a
        wired webhook with zero rows is a config-failure signal).
        """
        pool = _make_pool(
            revenue_last=None,
            subscriber_last=None,
        )
        notify = MagicMock()
        result = await bp.probe_webhook_freshness(pool, notify)
        assert result["ok"] is True
        assert "fired 2" in result["detail"]
        body = notify.call_args.args[0]
        assert "table empty" in body

    async def test_custom_thresholds_honored(self):
        """Operator-set tighter thresholds make the probe alert sooner."""
        old = datetime.now(timezone.utc) - timedelta(days=2)
        # 2 days old, threshold 1 day → alert.
        pool = _make_pool(
            settings={"webhook_freshness_revenue_threshold_days": "1"},
            revenue_last=old,
            subscriber_last=datetime.now(timezone.utc),
        )
        notify = MagicMock()
        result = await bp.probe_webhook_freshness(pool, notify)
        assert "fired 1" in result["detail"]

    async def test_async_notify_fn_is_awaited(self):
        """Regression: production `notify` is async (since #344) — the
        probe MUST await it. The brain logged
        ``RuntimeWarning: coroutine 'notify' was never awaited`` for
        weeks because business_probes called notify_fn synchronously
        and silently dropped every alert. This test pins the await.
        """
        old = datetime.now(timezone.utc) - timedelta(days=60)
        pool = _make_pool(
            revenue_last=old,
            subscriber_last=old,
        )
        notify = AsyncMock()
        result = await bp.probe_webhook_freshness(pool, notify)

        assert result["ok"] is True
        assert "fired 2" in result["detail"]
        # The contract: notify_fn was both CALLED and AWAITED. AsyncMock
        # tracks await_count separately from call_count — a sync
        # ``notify_fn(body)`` call would bump call_count without ever
        # awaiting (the silent-failure shape).
        assert notify.await_count == 1, (
            "notify_fn was not awaited — silent-failure regression. "
            "If you intentionally changed the call shape, update this "
            "test and double-check no RuntimeWarning fires."
        )
