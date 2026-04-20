"""Unit tests for ``services/jobs/check_memory_staleness.py``.

Covers the cooldown-aware alerting: a stale writer fires Discord + audit
only once per cooldown window, even if the check runs every 30 min.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.check_memory_staleness import CheckMemoryStalenessJob


def _make_pool(last_alerts: dict | None = None, settings: dict | None = None):
    """Pool whose fetchrow returns settings values; execute is a no-op."""
    settings = settings or {}
    if last_alerts is not None:
        settings["memory_stale_last_alerts"] = json.dumps(last_alerts)

    async def _fetchrow(query: str, *args: Any) -> Any:
        key = args[0] if args else ""
        if key in settings:
            return {"value": settings[key]}
        return None

    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    return pool


def _stats_by_writer(writer_ages: dict[str, timedelta]) -> dict:
    """Build a fake MemoryClient.stats() response where each writer's
    'newest' is `now - age`."""
    now = datetime.now(timezone.utc)
    return {
        "by_writer": {
            writer: {"newest": now - age, "count": 100}
            for writer, age in writer_ages.items()
        }
    }


def _fake_memory_client(stats):
    mc = MagicMock()
    mc.stats = AsyncMock(return_value=stats)

    class _CtxMgr:
        async def __aenter__(self):
            return mc

        async def __aexit__(self, *args):
            return False

    return MagicMock(return_value=_CtxMgr())


@pytest.mark.unit
class TestCheckMemoryStalenessJobMetadata:
    def test_name(self):
        assert CheckMemoryStalenessJob.name == "check_memory_staleness"

    def test_schedule_30_min(self):
        assert "30" in CheckMemoryStalenessJob.schedule


@pytest.mark.unit
@pytest.mark.asyncio
class TestCheckMemoryStalenessJobRun:
    async def test_all_fresh_no_alerts(self):
        pool = _make_pool()
        # All writers 10 min old; default threshold 6h → none stale.
        stats = _stats_by_writer({"a": timedelta(minutes=10), "b": timedelta(minutes=20)})
        memory_cls = _fake_memory_client(stats)

        with patch("poindexter.memory.MemoryClient", memory_cls):
            result = await CheckMemoryStalenessJob().run(pool, {})

        assert result.ok is True
        assert result.changes_made == 0
        assert "0 stale writer" in result.detail

    async def test_stale_writer_fires_alert(self):
        pool = _make_pool()
        # Writer stale (10h old, threshold 6h).
        stats = _stats_by_writer({"openclaw": timedelta(hours=10)})
        memory_cls = _fake_memory_client(stats)

        notify_mock = AsyncMock()
        audit_mock = MagicMock()

        with patch("poindexter.memory.MemoryClient", memory_cls), \
             patch("services.task_executor._notify_openclaw", new=notify_mock), \
             patch("services.audit_log.audit_log_bg", new=audit_mock):
            result = await CheckMemoryStalenessJob().run(pool, {})

        assert result.ok is True
        assert result.changes_made == 1  # one alert fired
        notify_mock.assert_awaited_once()
        audit_mock.assert_called_once()
        # Cooldown state was persisted.
        pool.execute.assert_awaited_once()

    async def test_cooldown_suppresses_repeat_alert(self):
        now = datetime.now(timezone.utc)
        # We alerted 1 hour ago; default cooldown is 6h → should NOT re-alert.
        pool = _make_pool(last_alerts={"openclaw": (now - timedelta(hours=1)).isoformat()})
        stats = _stats_by_writer({"openclaw": timedelta(hours=10)})
        memory_cls = _fake_memory_client(stats)

        notify_mock = AsyncMock()
        with patch("poindexter.memory.MemoryClient", memory_cls), \
             patch("services.task_executor._notify_openclaw", new=notify_mock):
            result = await CheckMemoryStalenessJob().run(pool, {})

        assert result.ok is True
        assert result.changes_made == 0  # suppressed
        notify_mock.assert_not_awaited()
        # Nothing changed in the last_alerts blob → no UPDATE.
        pool.execute.assert_not_awaited()

    async def test_cooldown_expired_re_alerts(self):
        now = datetime.now(timezone.utc)
        # Last alert 7h ago, cooldown 6h → re-alert.
        pool = _make_pool(last_alerts={"openclaw": (now - timedelta(hours=7)).isoformat()})
        stats = _stats_by_writer({"openclaw": timedelta(hours=10)})
        memory_cls = _fake_memory_client(stats)

        notify_mock = AsyncMock()
        with patch("poindexter.memory.MemoryClient", memory_cls), \
             patch("services.task_executor._notify_openclaw", new=notify_mock):
            result = await CheckMemoryStalenessJob().run(pool, {})

        assert result.ok is True
        assert result.changes_made == 1
        notify_mock.assert_awaited_once()

    async def test_per_writer_threshold_override(self):
        """A writer-specific threshold row in app_settings wins over the global."""
        pool = _make_pool(
            settings={
                "memory_stale_threshold_seconds_openclaw": "3600",  # 1h — tight
            },
        )
        # Writer is 2h old. Global threshold is 6h but per-writer is 1h → stale.
        stats = _stats_by_writer({"openclaw": timedelta(hours=2)})
        memory_cls = _fake_memory_client(stats)

        notify_mock = AsyncMock()
        with patch("poindexter.memory.MemoryClient", memory_cls), \
             patch("services.task_executor._notify_openclaw", new=notify_mock):
            result = await CheckMemoryStalenessJob().run(pool, {})

        assert result.changes_made == 1

    async def test_missing_memory_client_returns_not_ok(self):
        pool = _make_pool()
        with patch.dict("sys.modules", {"poindexter.memory": None}):
            result = await CheckMemoryStalenessJob().run(pool, {})
        assert result.ok is False
        assert "poindexter.memory" in result.detail

    async def test_null_newest_skipped(self):
        pool = _make_pool()
        stats = {"by_writer": {"w": {"newest": None, "count": 5}}}
        memory_cls = _fake_memory_client(stats)
        with patch("poindexter.memory.MemoryClient", memory_cls):
            result = await CheckMemoryStalenessJob().run(pool, {})
        assert result.ok is True
        assert result.changes_made == 0
