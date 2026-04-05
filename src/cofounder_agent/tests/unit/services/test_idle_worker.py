"""
Unit tests for services/idle_worker.py

Tests background maintenance tasks: quality audits, link checks,
topic gaps, threshold tuning, topic discovery, schedule persistence,
and post-publish verification.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest

from services.idle_worker import IdleWorker


def _make_pool(pending_count=0):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"c": pending_count})
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=None)
    return pool


class TestRunCycleSkipsWhenBusy:
    async def test_skips_when_tasks_pending(self):
        pool = _make_pool(pending_count=5)
        # Mark all lightweight/pre-check tasks as recently run so they don't fire
        worker = IdleWorker(pool)
        now = time.time()
        worker._last_run["sync_page_views"] = now
        worker._last_run["sync_newsletter_subscribers"] = now
        worker._last_run["expire_stale_approvals"] = now
        result = await worker.run_cycle()
        assert result.get("skipped") is True
        assert "5 active tasks" in result.get("reason", "")

    async def test_runs_when_no_tasks(self):
        pool = _make_pool(pending_count=0)
        worker = IdleWorker(pool)
        # Force all tasks to be due but mock every task method so no real I/O
        worker._last_run = {}
        # Mock all task methods that run_cycle calls to prevent real HTTP/DB calls
        for attr in dir(worker):
            if attr.startswith("_run_") or attr.startswith("_check_"):
                setattr(worker, attr, AsyncMock())
        result = await worker.run_cycle()
        assert result.get("skipped") is not True


class TestIsDue:
    def test_first_run_is_always_due(self):
        worker = IdleWorker(AsyncMock())
        assert worker._is_due("test_task", 60) is True

    def test_not_due_within_interval(self):
        worker = IdleWorker(AsyncMock())
        worker._last_run["test_task"] = time.time()
        assert worker._is_due("test_task", 60) is False

    def test_due_after_interval(self):
        worker = IdleWorker(AsyncMock())
        worker._last_run["test_task"] = time.time() - 3700  # Over 1 hour ago
        assert worker._is_due("test_task", 60) is True


class TestMarkRun:
    def test_updates_timestamp(self):
        worker = IdleWorker(AsyncMock())
        before = time.time()
        worker._mark_run("test_task")
        assert worker._last_run["test_task"] >= before


class TestPersistMarkRun:
    async def test_persists_to_db(self):
        pool = _make_pool()
        worker = IdleWorker(pool)
        before = time.time()
        await worker._persist_mark_run("my_task")
        assert worker._last_run["my_task"] >= before
        # Verify DB write was called with the right key
        pool.execute.assert_called()
        call_args = pool.execute.call_args
        assert "idle_last_run_my_task" in call_args[0]

    async def test_persists_even_if_db_fails(self):
        pool = _make_pool()
        pool.execute = AsyncMock(side_effect=Exception("DB down"))
        worker = IdleWorker(pool)
        await worker._persist_mark_run("my_task")
        # In-memory should still be updated even if DB fails
        assert "my_task" in worker._last_run


class TestLoadPersistedSchedules:
    async def test_loads_from_db(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[
            {"key": "idle_last_run_quality_audit", "value": "1700000000.0"},
            {"key": "idle_last_run_link_check", "value": "1700000100.0"},
        ])
        worker = IdleWorker(pool)
        await worker._load_persisted_schedules()
        assert worker._last_run["quality_audit"] == 1700000000.0
        assert worker._last_run["link_check"] == 1700000100.0
        assert worker._schedules_loaded is True

    async def test_skips_on_second_call(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[])
        worker = IdleWorker(pool)
        await worker._load_persisted_schedules()
        pool.fetch.reset_mock()
        await worker._load_persisted_schedules()
        pool.fetch.assert_not_called()

    async def test_handles_db_failure(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(side_effect=Exception("DB error"))
        worker = IdleWorker(pool)
        await worker._load_persisted_schedules()
        # Should mark as loaded so it doesn't retry every cycle
        assert worker._schedules_loaded is True


class TestVerifyPublishedPosts:
    @patch.dict("os.environ", {"DATABASE_URL": ""})
    async def test_skips_without_database_url(self):
        pool = _make_pool()
        worker = IdleWorker(pool)
        result = await worker._verify_published_posts()
        assert "skipping" in result.get("note", "").lower() or "no DATABASE_URL" in result.get("note", "")

    @patch.dict("os.environ", {"DATABASE_URL": "postgres://fake"})
    @patch("asyncpg.connect")
    async def test_no_recent_posts(self, mock_connect):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_connect.return_value = mock_conn
        pool = _make_pool()
        worker = IdleWorker(pool)
        result = await worker._verify_published_posts()
        assert result["checked"] == 0

    @patch.dict("os.environ", {"DATABASE_URL": "postgres://fake"})
    @patch("asyncpg.connect")
    @patch("httpx.AsyncClient")
    async def test_detects_failures(self, mock_httpx_cls, mock_connect):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"id": 1, "title": "Good Post", "slug": "good-post"},
            {"id": 2, "title": "Bad Post", "slug": "bad-post"},
        ])
        mock_connect.return_value = mock_conn

        mock_resp_ok = AsyncMock()
        mock_resp_ok.status_code = 200
        mock_resp_404 = AsyncMock()
        mock_resp_404.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_resp_ok, mock_resp_404])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx_cls.return_value = mock_client

        pool = _make_pool()
        worker = IdleWorker(pool)
        result = await worker._verify_published_posts()
        assert result["verified"] == 1
        assert len(result["failures"]) == 1
        assert result["failures"][0]["slug"] == "bad-post"


class TestQualityAudit:
    async def test_returns_audited_count(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[
            {"id": "1", "title": "Test Post", "slug": "test", "content_preview": "Word " * 600},
        ])
        worker = IdleWorker(pool)
        result = await worker._audit_published_quality()
        assert result["audited"] == 1

    async def test_flags_short_content(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[
            {"id": "1", "title": "Short Post", "slug": "short", "content_preview": "Too short"},
        ])
        worker = IdleWorker(pool)
        result = await worker._audit_published_quality()
        assert len(result.get("issues", [])) > 0

    async def test_all_recently_audited(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[])
        worker = IdleWorker(pool)
        result = await worker._audit_published_quality()
        assert result["audited"] == 0


class TestTopicGaps:
    async def test_finds_empty_categories(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(side_effect=[
            [{"name": "Technology", "posts": 40}, {"name": "Security", "posts": 0}],
            [],  # stale query
        ])
        worker = IdleWorker(pool)
        result = await worker._analyze_topic_gaps()
        assert "Security" in result.get("empty_categories", [])

    async def test_no_gaps(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(side_effect=[
            [{"name": "Technology", "posts": 40}, {"name": "Business", "posts": 10}],
            [],
        ])
        worker = IdleWorker(pool)
        result = await worker._analyze_topic_gaps()
        assert len(result.get("empty_categories", [])) == 0


class TestThresholdTuning:
    async def test_high_failure_rate_auto_lowers(self):
        pool = _make_pool()
        pool.fetchrow = AsyncMock(return_value={
            "total": 15, "published": 3, "failed": 10, "rejected": 2,
            "avg_score": 65.0, "stddev_score": 8.0,
        })
        pool.fetchval = AsyncMock(return_value="75")
        pool.execute = AsyncMock()
        worker = IdleWorker(pool)
        result = await worker._tune_thresholds()
        assert result["adjustment"] < 0
        assert "lower" in result.get("reason", "").lower() or "failure" in result.get("reason", "").lower()

    async def test_insufficient_data(self):
        pool = _make_pool()
        pool.fetchrow = AsyncMock(return_value={
            "total": 3, "published": 2, "failed": 1, "rejected": 0,
            "avg_score": 80.0, "stddev_score": 5.0,
        })
        worker = IdleWorker(pool)
        result = await worker._tune_thresholds()
        assert "insufficient" in result.get("note", "")
