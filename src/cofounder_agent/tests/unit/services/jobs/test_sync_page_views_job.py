"""Unit tests for ``services/jobs/sync_page_views.py``.

Covers the watermark-based pull from cloud DB to local brain DB that
Grafana's traffic dashboards read from. Mirrors the pattern used in
test_sync_newsletter_subscribers_job.py.

Also fills the gap left when TestSyncPageViews moved out of
test_idle_worker.py (the idle_worker._sync_page_views method was
deleted in the Phase-C cleanup after PluginScheduler took over
scheduling).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.sync_page_views import SyncPageViewsJob


def _sc(value: Any = "") -> MagicMock:
    """Mock SiteConfig — replaces patch("services.site_config.site_config.get").

    Job migrated to DI seam in glad-labs-stack#330; tests now pass it
    via the config dict rather than patching the singleton.
    """
    sc = MagicMock()
    sc.get.return_value = value
    return sc


def _make_pool(watermark: Any = None) -> tuple[Any, Any]:
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="CREATE TABLE")
    conn.fetchval = AsyncMock(return_value=watermark)

    tx_ctx = AsyncMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _fake_asyncpg(rows: list[dict] | None = None, connect_raises=None):
    cloud_conn = AsyncMock()
    cloud_conn.fetch = AsyncMock(return_value=rows or [])
    cloud_conn.close = AsyncMock(return_value=None)

    async def _connect(url: str) -> Any:
        if connect_raises is not None:
            raise connect_raises
        return cloud_conn

    fake = MagicMock()
    fake.connect = _connect
    return fake, cloud_conn


def _row(id_: int = 1, path: str = "/", ts: datetime | None = None) -> dict:
    return {
        "id": id_,
        "path": path,
        "slug": None,
        "referrer": None,
        "user_agent": "test",
        "created_at": ts or datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc),
    }


@pytest.mark.unit
class TestSyncPageViewsJobMetadata:
    def test_name_matches_schedule_key(self):
        assert SyncPageViewsJob.name == "sync_page_views"

    def test_idempotent(self):
        assert SyncPageViewsJob.idempotent is True

    def test_schedule_is_human_readable(self):
        # apscheduler-compatible strings start with "every"
        assert "every" in SyncPageViewsJob.schedule.lower()


@pytest.mark.unit
@pytest.mark.asyncio
class TestSyncPageViewsJobRun:
    async def test_skips_when_no_database_url(self):
        pool, _ = _make_pool()
        job = SyncPageViewsJob()
        result = await job.run(pool, {"_site_config": _sc("")})
        assert result.ok is True
        assert result.changes_made == 0
        assert "no database_url" in result.detail

    async def test_skips_when_asyncpg_unavailable(self):
        pool, _ = _make_pool()
        job = SyncPageViewsJob()
        # Simulate asyncpg ImportError by blocking the import inside run().
        with patch.dict("sys.modules", {"asyncpg": None}):
            result = await job.run(pool, {"_site_config": _sc("postgres://cloud")})
        assert result.ok is False
        assert "asyncpg" in result.detail

    async def test_no_rows_returns_zero_changes(self):
        pool, _ = _make_pool(watermark=None)
        fake, _ = _fake_asyncpg(rows=[])
        job = SyncPageViewsJob()
        with patch.dict("sys.modules", {"asyncpg": fake}):
            result = await job.run(pool, {"_site_config": _sc("postgres://cloud")})
        assert result.ok is True
        assert result.changes_made == 0
        assert "no new rows" in result.detail

    async def test_watermark_initial_pull_uses_batch_size(self):
        """First run (no watermark) fetches with LIMIT $1 = batch_size."""
        pool, _ = _make_pool(watermark=None)
        fake, cloud_conn = _fake_asyncpg(rows=[_row(i) for i in range(3)])
        job = SyncPageViewsJob()
        with patch.dict("sys.modules", {"asyncpg": fake}):
            result = await job.run(
                pool,
                {"batch_size": 1234, "_site_config": _sc("postgres://cloud")},
            )
        assert result.ok is True
        assert result.changes_made == 3
        # The "no watermark" branch runs the LIMIT $1 query.
        sql, batch = cloud_conn.fetch.await_args.args
        assert "WHERE created_at > $1" not in sql
        assert batch == 1234

    async def test_watermark_incremental_pull_uses_max_ts(self):
        """Subsequent run (watermark present) passes MAX(created_at) as $1."""
        wm = datetime(2026, 4, 20, 6, 0, tzinfo=timezone.utc)
        pool, _ = _make_pool(watermark=wm)
        fake, cloud_conn = _fake_asyncpg(rows=[_row(5)])
        job = SyncPageViewsJob()
        with patch.dict("sys.modules", {"asyncpg": fake}):
            result = await job.run(pool, {"_site_config": _sc("postgres://cloud")})
        assert result.ok is True
        assert result.changes_made == 1
        sql, passed_wm, batch = cloud_conn.fetch.await_args.args
        assert "WHERE created_at > $1" in sql
        assert passed_wm == wm

    async def test_connect_failure_returns_not_ok(self):
        pool, _ = _make_pool()
        fake, _ = _fake_asyncpg(connect_raises=ConnectionRefusedError("no cloud"))
        job = SyncPageViewsJob()
        with patch.dict("sys.modules", {"asyncpg": fake}):
            result = await job.run(pool, {"_site_config": _sc("postgres://cloud")})
        assert result.ok is False
        assert "no cloud" in result.detail
