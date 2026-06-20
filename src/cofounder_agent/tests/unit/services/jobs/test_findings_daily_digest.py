"""Unit tests for ``services/jobs/findings_daily_digest.py`` (poindexter#549).

The DB pool and the operator_notify integration are mocked at the module
boundary — no real DB / HTTP. Focus: the disabled short-circuit, the explicit
failure when the Discord webhook isn't configured (``feedback_no_silent_defaults``),
the by-kind rollup + delivery-policy join, the pending-above-watermark count, and
the message formatting (top-N + empty case).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.findings_daily_digest import FindingsDailyDigestJob

# ---------------------------------------------------------------------------
# Pool fake — settings reads go through pool.fetchrow (get_secret); the
# data-gather block runs inside pool.acquire() and is dispatched by SQL
# substring (mirrors test_morning_brief._make_pool).
# ---------------------------------------------------------------------------


def _make_pool(
    *,
    settings: dict[str, str],
    by_kind: list[tuple[str, int]] | None = None,
    deliveries: dict[str, str] | None = None,
    watermark: str = "0",
    pending: int = 0,
) -> Any:
    settings = dict(settings)
    by_kind = by_kind or []  # pre-sorted (kind, count) — the SQL ORDER BY cnt DESC
    deliveries = deliveries or {}  # {kind: delivery}

    async def _pool_fetchrow(query: str, *args: Any) -> Any:
        # get_secret: SELECT value, is_secret FROM app_settings WHERE key = $1
        if "FROM app_settings" in query and args:
            val = settings.get(args[0])
            if val is None:
                return None
            return {"value": val, "is_secret": False}
        return None

    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=_pool_fetchrow)

    async def _conn_fetch(query: str, *args: Any) -> list[dict]:
        if "event_type = 'finding'" in query and "GROUP BY" in query:
            return [{"kind": k, "cnt": c} for k, c in by_kind]
        if "findings.%.delivery" in query:
            return [
                {"key": f"findings.{k}.delivery", "value": d}
                for k, d in deliveries.items()
            ]
        return []

    async def _conn_fetchrow(query: str, *args: Any) -> dict | None:
        # watermark: SELECT value FROM app_settings WHERE key = $1
        if "FROM app_settings" in query and args:
            return {"value": watermark}
        return None

    async def _conn_fetchval(query: str, *args: Any) -> Any:
        if "event_type = 'finding'" in query and "severity" in query:
            return pending
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


_WEBHOOK = {"discord_ops_webhook_url": "https://discord.example/webhook/abc"}


class TestContract:
    def test_has_required_attrs(self):
        job = FindingsDailyDigestJob()
        assert job.name == "findings_daily_digest"
        assert job.schedule == "0 9 * * *"
        assert job.idempotent is True
        assert "findings" in job.description.lower()


class TestGuards:
    @pytest.mark.asyncio
    async def test_disabled_short_circuits_without_sending(self):
        pool = _make_pool(settings={"findings_daily_digest_enabled": "false", **_WEBHOOK})
        notify = AsyncMock()
        with patch("services.jobs.findings_daily_digest.notify_operator", new=notify):
            result = await FindingsDailyDigestJob().run(pool, {})
        assert result.ok is True
        assert result.detail == "disabled"
        assert result.changes_made == 0
        notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_webhook_fails_loud(self):
        pool = _make_pool(settings={"findings_daily_digest_enabled": "true"})  # no webhook
        notify = AsyncMock()
        with patch("services.jobs.findings_daily_digest.notify_operator", new=notify):
            result = await FindingsDailyDigestJob().run(pool, {})
        assert result.ok is False
        assert "discord_ops_webhook_url" in result.detail
        notify.assert_not_awaited()


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_sends_rollup_with_kinds_deliveries_and_pending(self):
        pool = _make_pool(
            settings={"findings_daily_digest_enabled": "true", **_WEBHOOK},
            by_kind=[("media_drift", 30), ("missing_seo", 9), ("quality_regression", 8)],
            deliveries={
                "media_drift": "log_only",
                "missing_seo": "auto_fix",
                "quality_regression": "github_issue",
            },
            watermark="100",
            pending=0,
        )
        notify = AsyncMock()
        with patch("services.jobs.findings_daily_digest.notify_operator", new=notify):
            result = await FindingsDailyDigestJob().run(pool, {})

        assert result.ok is True
        assert result.changes_made == 1
        assert result.metrics["total_findings"] == 47
        assert result.metrics["kind_count"] == 3
        assert result.metrics["pending_delivery"] == 0

        notify.assert_awaited_once()
        body = notify.await_args.args[0]
        assert "Findings — last 24h:" in body
        assert "47 across 3 kinds" in body
        assert "media_drift ×30 (log_only)" in body
        assert "missing_seo ×9 (auto_fix)" in body
        assert "quality_regression ×8 (github_issue)" in body
        assert "0 pending delivery" in body
        # Routine digest → Discord only, never a Telegram critical ping.
        assert notify.await_args.kwargs.get("critical") is not True

    @pytest.mark.asyncio
    async def test_unmapped_kind_defaults_to_route(self):
        # A kind with no findings.<kind>.delivery policy stays loud ('route').
        pool = _make_pool(
            settings={"findings_daily_digest_enabled": "true", **_WEBHOOK},
            by_kind=[("brand_new_kind", 3)],
            deliveries={},
            pending=2,
        )
        notify = AsyncMock()
        with patch("services.jobs.findings_daily_digest.notify_operator", new=notify):
            await FindingsDailyDigestJob().run(pool, {})
        body = notify.await_args.args[0]
        assert "brand_new_kind ×3 (route)" in body
        assert "2 pending delivery" in body

    @pytest.mark.asyncio
    async def test_top_n_truncates_and_counts_remaining_kinds(self):
        pool = _make_pool(
            settings={
                "findings_daily_digest_enabled": "true",
                "findings_daily_digest_top_n": "2",
                **_WEBHOOK,
            },
            by_kind=[("a", 10), ("b", 5), ("c", 3), ("d", 1)],
            deliveries={},
        )
        notify = AsyncMock()
        with patch("services.jobs.findings_daily_digest.notify_operator", new=notify):
            await FindingsDailyDigestJob().run(pool, {})
        body = notify.await_args.args[0]
        assert "a ×10" in body and "b ×5" in body
        # Only the top 2 named; the other 2 kinds summarized.
        assert "c ×3" not in body
        assert "+2 more kinds" in body


class TestEmpty:
    @pytest.mark.asyncio
    async def test_no_findings_reports_none_but_still_sends(self):
        pool = _make_pool(
            settings={"findings_daily_digest_enabled": "true", **_WEBHOOK},
            by_kind=[],
            pending=0,
        )
        notify = AsyncMock()
        with patch("services.jobs.findings_daily_digest.notify_operator", new=notify):
            result = await FindingsDailyDigestJob().run(pool, {})
        assert result.ok is True
        body = notify.await_args.args[0]
        assert "Findings — last 24h:" in body
        assert "none" in body.lower()
