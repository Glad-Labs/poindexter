"""Unit tests for ``services/jobs/flush_settings_read_telemetry.py``.

The flush job drains ``SiteConfig.drain_read_keys()`` once a minute and
stamps ``app_settings.last_read_at`` for the keys that were actually read,
throttled so a hot key is re-stamped at most once per
``settings_read_telemetry_min_restamp_seconds`` window. Powers
Glad-Labs/poindexter#756 item 2 (read-telemetry).

Pool mocked (no asyncpg). The SiteConfig is real so the drain semantics
under test are the production ones.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import settings_read_sink
from services.jobs.flush_settings_read_telemetry import (
    FlushSettingsReadTelemetryJob,
)
from services.site_config import SiteConfig


@pytest.fixture(autouse=True)
def _clean_read_sink():
    """The SettingsService read sink is module-global; other test files that
    exercise ``SettingsService.get`` leave keys in it. Drain before and after
    each flush-job test so those cross-file reads can't leak into (or out of)
    these assertions — otherwise a stray sink key turns a no-op case into a
    spurious UPDATE."""
    settings_read_sink.drain_read_keys()
    yield
    settings_read_sink.drain_read_keys()


def _make_pool(execute_status: str = "UPDATE 0") -> Any:
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=execute_status)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _site_config_with_reads(*keys: str, **settings: str) -> SiteConfig:
    """A SiteConfig that has 'read' the given keys (so they're in the drain set),
    seeded with optional control settings."""
    sc = SiteConfig(initial_config=dict(settings))
    for k in keys:
        sc.get(k)
    return sc


class TestFlushSettingsReadTelemetryJob:
    async def test_flushes_drained_keys(self):
        sc = _site_config_with_reads("site_url", "site_name")
        pool, conn = _make_pool(execute_status="UPDATE 2")

        result = await FlushSettingsReadTelemetryJob().run(
            pool, {"_site_config": sc}
        )

        assert result.ok is True
        assert result.changes_made == 2
        conn.execute.assert_awaited_once()
        # First UPDATE arg is the key array.
        args = conn.execute.await_args.args
        flushed_keys = set(args[1])
        assert {"site_url", "site_name"} <= flushed_keys

    async def test_drain_empties_the_set(self):
        sc = _site_config_with_reads("a")
        pool, _ = _make_pool(execute_status="UPDATE 1")
        await FlushSettingsReadTelemetryJob().run(pool, {"_site_config": sc})
        # The job drained the read set — a subsequent drain has none of the
        # original keys (the job's own control-setting reads may re-populate
        # it, but 'a' must be gone).
        assert "a" not in sc.drain_read_keys()

    async def test_no_keys_read_noops_without_write(self):
        sc = SiteConfig()  # nothing read
        pool, conn = _make_pool()
        result = await FlushSettingsReadTelemetryJob().run(
            pool, {"_site_config": sc}
        )
        assert result.ok is True
        assert result.changes_made == 0
        conn.execute.assert_not_awaited()

    async def test_disabled_discards_without_writing(self):
        sc = _site_config_with_reads(
            "site_url", settings_read_telemetry_enabled="false"
        )
        pool, conn = _make_pool()
        result = await FlushSettingsReadTelemetryJob().run(
            pool, {"_site_config": sc}
        )
        assert result.ok is True
        conn.execute.assert_not_awaited()
        # Still drained for memory hygiene: the original read is gone.
        assert "site_url" not in sc.drain_read_keys()

    async def test_default_restamp_window_passed_to_update(self):
        sc = _site_config_with_reads("site_url")
        pool, conn = _make_pool(execute_status="UPDATE 1")
        await FlushSettingsReadTelemetryJob().run(pool, {"_site_config": sc})
        # Second positional UPDATE arg is the re-stamp window in seconds.
        args = conn.execute.await_args.args
        assert args[2] == 3600

    async def test_custom_restamp_window_honored(self):
        sc = _site_config_with_reads(
            "site_url", settings_read_telemetry_min_restamp_seconds="60"
        )
        pool, conn = _make_pool(execute_status="UPDATE 1")
        await FlushSettingsReadTelemetryJob().run(pool, {"_site_config": sc})
        args = conn.execute.await_args.args
        assert args[2] == 60

    async def test_missing_site_config_returns_not_ok(self):
        pool, _ = _make_pool()
        result = await FlushSettingsReadTelemetryJob().run(pool, {})
        assert result.ok is False
        assert "site_config" in result.detail

    async def test_none_pool_preserves_keys(self):
        sc = _site_config_with_reads("site_url")
        result = await FlushSettingsReadTelemetryJob().run(
            None, {"_site_config": sc}
        )
        assert result.ok is False
        # Keys were NOT drained — they survive to the next cycle when a pool
        # is available again.
        assert "site_url" in sc.drain_read_keys()

    # --- SettingsService read sink union (poindexter#756) -------------------

    async def test_flushes_settings_service_sink_keys(self):
        """SettingsService.get reads land in the shared sink, not the SiteConfig
        instance set; the flush job must drain and stamp those too — else every
        qa_* weight and pipeline_*_model read via SettingsService looks 'never
        read' to the zero-reader probe."""
        settings_read_sink.record_read("qa_critic_weight")
        sc = SiteConfig()  # no SiteConfig-path reads
        pool, conn = _make_pool(execute_status="UPDATE 1")

        result = await FlushSettingsReadTelemetryJob().run(
            pool, {"_site_config": sc}
        )

        assert result.ok is True
        conn.execute.assert_awaited_once()
        flushed_keys = set(conn.execute.await_args.args[1])
        assert "qa_critic_weight" in flushed_keys

    async def test_unions_siteconfig_and_sink_keys(self):
        settings_read_sink.record_read("qa_temperature")  # SettingsService path
        sc = _site_config_with_reads("site_url")  # SiteConfig path
        pool, conn = _make_pool(execute_status="UPDATE 2")

        await FlushSettingsReadTelemetryJob().run(pool, {"_site_config": sc})

        flushed_keys = set(conn.execute.await_args.args[1])
        assert {"site_url", "qa_temperature"} <= flushed_keys

    async def test_sink_drained_after_flush(self):
        settings_read_sink.record_read("qa_temperature")
        sc = SiteConfig()
        pool, _ = _make_pool(execute_status="UPDATE 1")
        await FlushSettingsReadTelemetryJob().run(pool, {"_site_config": sc})
        # Drained — the next cycle won't re-stamp a key not re-read since.
        assert settings_read_sink.drain_read_keys() == []

    async def test_none_pool_preserves_sink_keys(self):
        """Symmetry with the SiteConfig path: no pool means we can't persist,
        so the sink is left intact for the next cycle rather than lost."""
        settings_read_sink.record_read("qa_temperature")
        sc = _site_config_with_reads("site_url")
        result = await FlushSettingsReadTelemetryJob().run(
            None, {"_site_config": sc}
        )
        assert result.ok is False
        assert "qa_temperature" in settings_read_sink.drain_read_keys()
