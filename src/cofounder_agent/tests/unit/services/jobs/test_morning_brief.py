"""Unit tests for ``services/jobs/morning_brief.py``.

DB pool, Discord webhook, and the operator_notify integration are all
mocked at the module boundary — no real DB / HTTP / Telegram calls.
Focus: section formatting, Telegram-only-on-critical routing, the
disabled short-circuit, and the explicit failure when the Discord
webhook isn't configured (per ``feedback_no_silent_defaults``).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.morning_brief import MorningBriefJob


# ---------------------------------------------------------------------------
# Pool fakes
# ---------------------------------------------------------------------------


def _make_pool(
    *,
    settings: dict[str, str],
    published: list[dict] | None = None,
    awaiting: list[dict] | None = None,
    failed: list[dict] | None = None,
    alert_groups: list[dict] | None = None,
    cost_row: dict | None = None,
    brain_failures: int = 0,
    brain_cycles: int = 0,
) -> Any:
    """Construct an asyncpg-pool-shaped MagicMock that returns canned rows.

    The job calls ``pool.fetchval(... key)`` for each app_settings read,
    then ``pool.acquire()`` once for the data-gather block. We dispatch
    each query inside the acquired connection by SQL substring.
    """
    settings = dict(settings)
    published = published or []
    awaiting = awaiting or []
    failed = failed or []
    alert_groups = alert_groups or []

    async def _settings_fetchval(query: str, *args: Any) -> Any:
        # The job's _read_setting calls SELECT value FROM app_settings WHERE key = $1
        if "FROM app_settings" in query and args:
            return settings.get(args[0])
        return None

    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=_settings_fetchval)

    async def _conn_fetch(query: str, *args: Any) -> list[dict]:
        if "FROM posts" in query:
            return published
        if "status = 'awaiting_approval'" in query:
            return awaiting
        if "status = 'failed'" in query:
            return failed
        if "FROM alert_events" in query:
            return alert_groups
        return []

    async def _conn_fetchrow(query: str, *args: Any) -> dict | None:
        if "FROM cost_logs" in query:
            return cost_row
        return None

    async def _conn_fetchval(query: str, *args: Any) -> Any:
        if "FROM audit_log" in query and "severity" in query:
            return brain_failures
        if "FROM audit_log" in query:
            return brain_cycles
        return None

    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=_conn_fetch)
    conn.fetchrow = AsyncMock(side_effect=_conn_fetchrow)
    conn.fetchval = AsyncMock(side_effect=_conn_fetchval)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=ctx)
    return pool


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


class TestContract:
    def test_has_required_attrs(self):
        job = MorningBriefJob()
        assert job.name == "morning_brief"
        assert job.schedule == "0 7 * * *"
        assert job.idempotent is True
        assert "morning brief" in job.description.lower()


# ---------------------------------------------------------------------------
# Happy path — every section populated, NO criticals → Telegram silent
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_full_brief_no_criticals_does_not_ping_telegram(self):
        pool = _make_pool(
            settings={
                "morning_brief_enabled": "true",
                "discord_ops_webhook_url": "https://discord.example/webhook/abc",
                "morning_brief_telegram_critical_only": "true",
                "morning_brief_lookback_hours": "24",
                "site_url": "https://www.test-site.example.com",
            },
            published=[
                {"id": "p1", "title": "Indie devs guide", "slug": "indie-devs-guide"},
                {"id": "p2", "title": "Second post", "slug": "second-post"},
            ],
            awaiting=[
                {"task_id": "t1", "title": "AI Agent stack for indie devs"},
            ],
            # No criticals, no failed tasks — Telegram must stay silent.
            failed=[],
            alert_groups=[
                {"severity": "warning", "count": 5, "top_alertname": "noise_alert"},
            ],
            cost_row={"cloud_usd": 0.0, "cloud_calls": 0, "local_calls": 1440},
            brain_failures=0,
            brain_cycles=1440,
        )

        sent_messages: list[tuple[str, dict]] = []

        async def _fake_post(self, url, json=None, **kw):  # noqa: A002 — mirroring httpx.AsyncClient.post
            sent_messages.append((url, json))
            resp = MagicMock()
            resp.status_code = 204
            resp.text = ""
            return resp

        notify_mock = AsyncMock()

        with patch("httpx.AsyncClient.post", new=_fake_post), patch(
            "services.integrations.operator_notify.notify_operator", new=notify_mock,
        ), patch(
            "services.jobs.morning_brief._gather_open_prs",
            new=AsyncMock(return_value=[]),
        ):
            job = MorningBriefJob()
            result = await job.run(pool, {})

        assert result.ok is True
        assert result.changes_made == 1
        assert result.metrics["telegram_pinged"] is False
        # Telegram must NOT have been pinged on a quiet morning.
        notify_mock.assert_not_called()

        assert len(sent_messages) == 1
        body = sent_messages[0][1]["content"]
        # All seven sections present.
        assert "Morning brief" in body
        assert "Published (24h):** 2" in body
        assert "Awaiting approval (24h):** 1 new" in body
        assert "AI Agent stack for indie devs" in body
        assert "Alerts (24h):** 0 critical, 5 warnings" in body
        assert "Cost (24h):** $0.00 cloud (local-only)" in body
        assert "Failed tasks (24h):** 0" in body
        assert "Open PRs >24h with green CI:** 0" in body
        assert "Brain probes:** 0 failed across 1,440 cycles" in body
        # Markdown link for the published post used the site_url.
        assert "https://www.test-site.example.com/posts/indie-devs-guide" in body


# ---------------------------------------------------------------------------
# Critical path — failed tasks OR critical alerts → Telegram fires
# ---------------------------------------------------------------------------


class TestCriticalRouting:
    @pytest.mark.asyncio
    async def test_critical_alert_pings_telegram(self):
        pool = _make_pool(
            settings={
                "morning_brief_enabled": "true",
                "discord_ops_webhook_url": "https://discord.example/webhook/abc",
            },
            alert_groups=[
                {"severity": "critical", "count": 2, "top_alertname": "docker_port_forward_recovery_failed"},
                {"severity": "warning", "count": 7, "top_alertname": "stuck_task"},
            ],
            cost_row={"cloud_usd": 0.0, "cloud_calls": 0, "local_calls": 100},
        )

        async def _fake_post(self, url, json=None, **kw):  # noqa: A002
            resp = MagicMock()
            resp.status_code = 204
            return resp

        notify_mock = AsyncMock()

        with patch("httpx.AsyncClient.post", new=_fake_post), patch(
            "services.integrations.operator_notify.notify_operator", new=notify_mock,
        ), patch(
            "services.jobs.morning_brief._gather_open_prs",
            new=AsyncMock(return_value=[]),
        ):
            job = MorningBriefJob()
            result = await job.run(pool, {})

        assert result.ok is True
        assert result.metrics["telegram_pinged"] is True
        notify_mock.assert_called_once()
        # Critical kwarg must be True — that's what routes via telegram_ops.
        _, kwargs = notify_mock.call_args
        assert kwargs.get("critical") is True
        msg = notify_mock.call_args.args[0]
        assert "critical" in msg.lower()
        assert "docker_port_forward_recovery_failed" in msg

    @pytest.mark.asyncio
    async def test_failed_task_pings_telegram_even_without_critical_alerts(self):
        pool = _make_pool(
            settings={
                "morning_brief_enabled": "true",
                "discord_ops_webhook_url": "https://discord.example/webhook/abc",
            },
            failed=[
                {"task_id": "t99", "title": "research run", "error_message": "research timeout on niche X"},
            ],
            cost_row={"cloud_usd": 0.0, "cloud_calls": 0, "local_calls": 0},
        )

        async def _fake_post(self, url, json=None, **kw):  # noqa: A002
            resp = MagicMock()
            resp.status_code = 204
            return resp

        notify_mock = AsyncMock()

        with patch("httpx.AsyncClient.post", new=_fake_post), patch(
            "services.integrations.operator_notify.notify_operator", new=notify_mock,
        ), patch(
            "services.jobs.morning_brief._gather_open_prs",
            new=AsyncMock(return_value=[]),
        ):
            job = MorningBriefJob()
            result = await job.run(pool, {})

        assert result.ok is True
        assert result.metrics["telegram_pinged"] is True
        notify_mock.assert_called_once()
        _, kwargs = notify_mock.call_args
        assert kwargs.get("critical") is True


# ---------------------------------------------------------------------------
# Disabled short-circuit — no DB queries, no webhooks, ok=True
# ---------------------------------------------------------------------------


class TestDisabled:
    @pytest.mark.asyncio
    async def test_disabled_returns_early_without_querying_postgres(self):
        # Track whether anything beyond the master-switch read touches
        # the pool. The job must short-circuit on master switch alone.
        fetchval_calls: list[Any] = []

        async def _fetchval(query: str, *args: Any) -> Any:
            fetchval_calls.append(args)
            if args and args[0] == "morning_brief_enabled":
                return "false"
            # Anything else means we've leaked past the short-circuit.
            return None

        pool = MagicMock()
        pool.fetchval = AsyncMock(side_effect=_fetchval)
        pool.acquire = MagicMock(
            side_effect=AssertionError("acquire() called when job is disabled"),
        )

        async def _refuse_post(self, *a, **kw):
            raise AssertionError("Discord webhook called when job is disabled")

        notify_mock = AsyncMock(
            side_effect=AssertionError("Telegram called when job is disabled"),
        )

        with patch("httpx.AsyncClient.post", new=_refuse_post), patch(
            "services.integrations.operator_notify.notify_operator", new=notify_mock,
        ):
            job = MorningBriefJob()
            result = await job.run(pool, {})

        assert result.ok is True
        assert result.detail == "disabled"
        assert result.changes_made == 0
        notify_mock.assert_not_called()
        # Only the master-switch read should have happened.
        assert all(
            args and args[0] == "morning_brief_enabled" for args in fetchval_calls
        )


# ---------------------------------------------------------------------------
# Discord webhook missing — explicit failure, no silent swallow
# ---------------------------------------------------------------------------


class TestWebhookMissing:
    @pytest.mark.asyncio
    async def test_missing_webhook_returns_not_ok_with_explicit_detail(self):
        pool = _make_pool(
            settings={
                "morning_brief_enabled": "true",
                # No discord_ops_webhook_url at all.
            },
        )

        async def _refuse_post(self, *a, **kw):
            raise AssertionError("Discord webhook called when URL is missing")

        notify_mock = AsyncMock(
            side_effect=AssertionError("Telegram called on missing-webhook path"),
        )

        with patch("httpx.AsyncClient.post", new=_refuse_post), patch(
            "services.integrations.operator_notify.notify_operator", new=notify_mock,
        ):
            job = MorningBriefJob()
            result = await job.run(pool, {})

        assert result.ok is False
        assert "discord_ops_webhook_url" in result.detail
        assert "not configured" in result.detail
        assert result.changes_made == 0
        notify_mock.assert_not_called()
