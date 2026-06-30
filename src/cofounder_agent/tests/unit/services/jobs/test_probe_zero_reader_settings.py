"""Unit tests for ``services/jobs/probe_zero_reader_settings.py``.

The probe surfaces app_settings keys with a NULL ``last_read_at`` that have
existed past a grace window — orphan candidates that nothing in the running
system reads via ``SiteConfig.get``. It emits a single advisory
``settings_zero_reader_keys`` finding (severity ``warn``) routed to Discord
ops. Glad-Labs/poindexter#756 item 3.

Pool mocked. ``emit_finding`` patched so we assert routing intent without
touching audit_log.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from services.jobs.probe_zero_reader_settings import ProbeZeroReaderSettingsJob
from services.site_config import SiteConfig

_MODULE = "services.jobs.probe_zero_reader_settings"


def _make_pool(rows: list[dict] | None = None) -> Any:
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _orphan(key: str, category: str = "general") -> dict:
    return {
        "key": key,
        "category": category,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }


class TestProbeZeroReaderSettingsJob:
    async def test_emits_finding_when_orphans_exist(self):
        pool, _ = _make_pool([_orphan("dead_key_a"), _orphan("dead_key_b")])
        sc = SiteConfig()
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            result = await ProbeZeroReaderSettingsJob().run(
                pool, {"_site_config": sc}
            )
        assert result.ok is True
        mock_emit.assert_called_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["kind"] == "settings_zero_reader_keys"
        assert kwargs["severity"] == "warn"
        assert "dead_key_a" in kwargs["body"]
        assert kwargs["extra"]["count"] == 2
        assert "dead_key_a" in kwargs["extra"]["keys"]

    async def test_no_finding_when_no_orphans(self):
        pool, _ = _make_pool([])
        sc = SiteConfig()
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            result = await ProbeZeroReaderSettingsJob().run(
                pool, {"_site_config": sc}
            )
        assert result.ok is True
        mock_emit.assert_not_called()

    async def test_disabled_skips_query_and_finding(self):
        pool, conn = _make_pool([_orphan("dead_key")])
        sc = SiteConfig(initial_config={"settings_zero_reader_probe_enabled": "false"})
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            result = await ProbeZeroReaderSettingsJob().run(
                pool, {"_site_config": sc}
            )
        assert result.ok is True
        conn.fetch.assert_not_awaited()
        mock_emit.assert_not_called()

    async def test_default_grace_and_limit_params(self):
        pool, conn = _make_pool([])
        sc = SiteConfig()
        with patch(f"{_MODULE}.emit_finding"):
            await ProbeZeroReaderSettingsJob().run(pool, {"_site_config": sc})
        args = conn.fetch.await_args.args
        # SQL is args[0]; grace_days then limit follow.
        assert args[1] == 30
        assert args[2] == 50

    async def test_custom_grace_and_limit(self):
        pool, conn = _make_pool([])
        sc = SiteConfig(initial_config={
            "settings_zero_reader_grace_days": "7",
            "settings_zero_reader_max_report": "10",
        })
        with patch(f"{_MODULE}.emit_finding"):
            await ProbeZeroReaderSettingsJob().run(pool, {"_site_config": sc})
        args = conn.fetch.await_args.args
        assert args[1] == 7
        assert args[2] == 10

    async def test_query_excludes_secrets_and_deprecated(self):
        pool, conn = _make_pool([])
        sc = SiteConfig()
        with patch(f"{_MODULE}.emit_finding"):
            await ProbeZeroReaderSettingsJob().run(pool, {"_site_config": sc})
        sql = conn.fetch.await_args.args[0].lower()
        assert "last_read_at is null" in sql
        assert "is_secret = false" in sql
        assert "deprecated" in sql

    async def test_dedup_key_is_stable(self):
        pool, _ = _make_pool([_orphan("dead_key")])
        sc = SiteConfig()
        with patch(f"{_MODULE}.emit_finding") as mock_emit:
            await ProbeZeroReaderSettingsJob().run(pool, {"_site_config": sc})
        # Stable dedup_key so the dispatcher collapses repeated 6-hourly fires
        # into one page rather than re-alerting every cycle.
        assert mock_emit.call_args.kwargs["dedup_key"] == "settings_zero_reader_keys"

    async def test_none_pool_returns_not_ok(self):
        sc = SiteConfig()
        result = await ProbeZeroReaderSettingsJob().run(None, {"_site_config": sc})
        assert result.ok is False
