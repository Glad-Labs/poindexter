"""
Unit tests for services/idle_worker.py

Tests background maintenance tasks: quality audits, link checks,
topic gaps, threshold tuning, topic discovery, schedule persistence,
post-publish verification, Gitea issue creation, and self-healing tasks.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

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
    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_runs_when_no_tasks(self):
        pool = _make_pool(pending_count=0)
        worker = IdleWorker(pool)
        # Force all tasks to be due
        worker._last_run = {}
        # Mock every async method except run_cycle itself to prevent real HTTP/DB calls
        for attr_name in dir(worker):
            if attr_name.startswith("_") and attr_name != "run_cycle" and not attr_name.startswith("__"):
                attr = getattr(worker, attr_name, None)
                if callable(attr) and asyncio.iscoroutinefunction(attr):
                    setattr(worker, attr_name, AsyncMock(return_value={}))
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
    @pytest.mark.asyncio
    async def test_updates_timestamp(self):
        pool = _make_pool()
        worker = IdleWorker(pool)
        before = time.time()
        await worker._persist_mark_run("test_task")
        assert worker._last_run["test_task"] >= before


class TestPersistMarkRun:
    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_persists_even_if_db_fails(self):
        pool = _make_pool()
        pool.execute = AsyncMock(side_effect=Exception("DB down"))
        worker = IdleWorker(pool)
        await worker._persist_mark_run("my_task")
        # In-memory should still be updated even if DB fails
        assert "my_task" in worker._last_run


class TestLoadPersistedSchedules:
    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_skips_on_second_call(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[])
        worker = IdleWorker(pool)
        await worker._load_persisted_schedules()
        pool.fetch.reset_mock()
        await worker._load_persisted_schedules()
        pool.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_db_failure(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(side_effect=Exception("DB error"))
        worker = IdleWorker(pool)
        await worker._load_persisted_schedules()
        # Should mark as loaded so it doesn't retry every cycle
        assert worker._schedules_loaded is True


class TestVerifyPublishedPosts:
    @pytest.mark.asyncio
    @patch.dict("os.environ", {"DATABASE_URL": ""})
    async def test_skips_without_database_url(self):
        pool = _make_pool()
        worker = IdleWorker(pool)
        result = await worker._verify_published_posts()
        assert "skipping" in result.get("note", "").lower() or "no DATABASE_URL" in result.get("note", "")

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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
    @pytest.mark.asyncio
    async def test_returns_audited_count(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[
            {"id": "1", "title": "Test Post", "slug": "test", "content_preview": "Word " * 600},
        ])
        worker = IdleWorker(pool)
        result = await worker._audit_published_quality()
        assert result["audited"] == 1

    @pytest.mark.asyncio
    async def test_flags_short_content(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[
            {"id": "1", "title": "Short Post", "slug": "short", "content_preview": "Too short"},
        ])
        worker = IdleWorker(pool)
        result = await worker._audit_published_quality()
        assert len(result.get("issues", [])) > 0

    @pytest.mark.asyncio
    async def test_all_recently_audited(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[])
        worker = IdleWorker(pool)
        result = await worker._audit_published_quality()
        assert result["audited"] == 0


class TestTopicGaps:
    @pytest.mark.asyncio
    async def test_finds_empty_categories(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(side_effect=[
            [{"name": "Technology", "posts": 40}, {"name": "Security", "posts": 0}],
            [],  # stale query
        ])
        worker = IdleWorker(pool)
        result = await worker._analyze_topic_gaps()
        assert "Security" in result.get("empty_categories", [])

    @pytest.mark.asyncio
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
    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_insufficient_data(self):
        pool = _make_pool()
        pool.fetchrow = AsyncMock(return_value={
            "total": 3, "published": 2, "failed": 1, "rejected": 0,
            "avg_score": 80.0, "stddev_score": 5.0,
        })
        worker = IdleWorker(pool)
        result = await worker._tune_thresholds()
        assert "insufficient" in result.get("note", "")


# ===========================================================================
# _mark_completed and cooldown in _is_due
# ===========================================================================


class TestMarkCompleted:
    def test_sets_completion_key(self):
        worker = IdleWorker(AsyncMock())
        worker._mark_completed("audit_quality")
        assert f"audit_quality_completed" in worker._last_run

    def test_completion_cooldown_extends_interval(self):
        """After _mark_completed, task should use 4x interval."""
        worker = IdleWorker(AsyncMock())
        # Task last ran 90 mins ago (due at 60 min interval normally)
        worker._last_run["audit_quality"] = time.time() - 5400
        # But it completed all work — should NOT be due (need 4x = 240 min)
        worker._last_run["audit_quality_completed"] = time.time()
        assert worker._is_due("audit_quality", 60) is False

    def test_normal_interval_without_completion(self):
        worker = IdleWorker(AsyncMock())
        worker._last_run["audit_quality"] = time.time() - 5400
        # No completion marker — normal 60-min interval, 90 min elapsed = due
        assert worker._is_due("audit_quality", 60) is True


# ===========================================================================
# _create_gitea_issue
# ===========================================================================


class TestCreateGiteaIssue:
    @pytest.mark.asyncio
    async def test_skips_when_no_password(self):
        worker = IdleWorker(AsyncMock())
        with patch("services.idle_worker.site_config", {"gitea_password": ""}):
            result = await worker._create_gitea_issue("Test Issue", "Body")
        assert result is False

    @pytest.mark.asyncio
    async def test_creates_issue_successfully(self):
        worker = IdleWorker(AsyncMock())

        mock_client = AsyncMock()
        # Search returns no existing issues
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = []
        # Create succeeds
        create_resp = MagicMock()
        create_resp.status_code = 201
        create_resp.json.return_value = {"number": 42}

        mock_client.get = AsyncMock(return_value=search_resp)
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        config = {"gitea_url": "http://gitea:3001", "gitea_user": "test", "gitea_password": "pass", "gitea_repo": "test/repo"}
        with patch("services.idle_worker.site_config", config), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await worker._create_gitea_issue("test: new issue", "Body here")
        assert result is True

    @pytest.mark.asyncio
    async def test_deduplicates_by_title_prefix(self):
        worker = IdleWorker(AsyncMock())

        mock_client = AsyncMock()
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = [{"title": "seo: fix missing metadata", "number": 10}]

        mock_client.get = AsyncMock(return_value=search_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        config = {"gitea_url": "http://gitea:3001", "gitea_user": "test", "gitea_password": "pass", "gitea_repo": "test/repo"}
        with patch("services.idle_worker.site_config", config), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await worker._create_gitea_issue("seo: another seo issue", "Body")
        assert result is False  # Deduped — same "seo" prefix

    @pytest.mark.asyncio
    async def test_handles_network_error(self):
        worker = IdleWorker(AsyncMock())

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        config = {"gitea_url": "http://gitea:3001", "gitea_user": "test", "gitea_password": "pass", "gitea_repo": "test/repo"}
        with patch("services.idle_worker.site_config", config), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await worker._create_gitea_issue("test: issue", "Body")
        assert result is False


# ===========================================================================
# _expire_stale_approvals
# ===========================================================================


class TestExpireStaleApprovals:
    @pytest.mark.asyncio
    async def test_expires_old_tasks(self):
        pool = _make_pool()
        pool.fetchrow = AsyncMock(return_value={"value": "7"})
        pool.fetch = AsyncMock(return_value=[
            {"task_id": "task-abc-123", "topic": "Old Topic"},
        ])
        worker = IdleWorker(pool)
        result = await worker._expire_stale_approvals()
        assert result["expired_count"] == 1

    @pytest.mark.asyncio
    async def test_no_expired_tasks(self):
        pool = _make_pool()
        pool.fetchrow = AsyncMock(return_value=None)
        pool.fetch = AsyncMock(return_value=[])
        worker = IdleWorker(pool)
        result = await worker._expire_stale_approvals()
        assert result["expired_count"] == 0

    @pytest.mark.asyncio
    async def test_uses_custom_ttl(self):
        pool = _make_pool()
        pool.fetchrow = AsyncMock(return_value={"value": "14"})
        pool.fetch = AsyncMock(return_value=[])
        worker = IdleWorker(pool)
        result = await worker._expire_stale_approvals()
        assert result["ttl_days"] == 14

    @pytest.mark.asyncio
    async def test_handles_db_error(self):
        pool = _make_pool()
        # TTL lookup succeeds but the UPDATE query fails
        pool.fetchrow = AsyncMock(return_value={"value": "7"})
        pool.fetch = AsyncMock(side_effect=Exception("DB down"))
        worker = IdleWorker(pool)
        result = await worker._expire_stale_approvals()
        assert "error" in result


# ===========================================================================
# _fix_uncategorized_posts
# ===========================================================================


class TestFixUncategorizedPosts:
    @pytest.mark.asyncio
    @patch("asyncpg.connect")
    async def test_no_uncategorized_posts(self, mock_connect):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.close = AsyncMock()
        mock_connect.return_value = mock_conn

        worker = IdleWorker(_make_pool())
        result = await worker._fix_uncategorized_posts()
        assert result["fixed"] == 0

    @pytest.mark.asyncio
    async def test_handles_no_database_url(self):
        worker = IdleWorker(_make_pool())
        with patch.dict("os.environ", {"DATABASE_URL": ""}):
            result = await worker._fix_uncategorized_posts()
        # Should return error since it can't connect
        assert "error" in result


# ===========================================================================
# _fix_missing_seo
# ===========================================================================


class TestFixMissingSeo:
    @pytest.mark.asyncio
    @patch("asyncpg.connect")
    async def test_no_missing_seo(self, mock_connect):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.close = AsyncMock()
        mock_connect.return_value = mock_conn

        worker = IdleWorker(_make_pool())
        result = await worker._fix_missing_seo()
        assert result["missing"] == 0

    @pytest.mark.asyncio
    @patch("asyncpg.connect")
    async def test_flags_posts_missing_seo(self, mock_connect):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"id": 1, "title": "Post Without SEO"},
            {"id": 2, "title": "Another Missing SEO"},
        ])
        mock_conn.close = AsyncMock()
        mock_connect.return_value = mock_conn

        worker = IdleWorker(_make_pool())
        worker._create_gitea_issue = AsyncMock(return_value=True)
        result = await worker._fix_missing_seo()
        assert result["missing"] == 2
        worker._create_gitea_issue.assert_awaited_once()


# ===========================================================================
# _detect_duplicate_posts
# ===========================================================================


class TestDetectDuplicatePosts:
    @pytest.mark.asyncio
    @patch("asyncpg.connect")
    async def test_no_duplicates(self, mock_connect):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"id": 1, "title": "Completely Different Topic"},
            {"id": 2, "title": "Another Unrelated Subject"},
        ])
        mock_conn.close = AsyncMock()
        mock_connect.return_value = mock_conn

        worker = IdleWorker(_make_pool())
        result = await worker._detect_duplicate_posts()
        assert result["duplicates"] == 0

    @pytest.mark.asyncio
    @patch("asyncpg.connect")
    async def test_detects_similar_titles(self, mock_connect):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"id": 1, "title": "How to Build AI Agents with Python and LLMs"},
            {"id": 2, "title": "How to Build AI Agents with Python and Tools"},
        ])
        mock_conn.close = AsyncMock()
        mock_connect.return_value = mock_conn

        worker = IdleWorker(_make_pool())
        worker._create_gitea_issue = AsyncMock(return_value=True)
        result = await worker._detect_duplicate_posts()
        assert result["duplicates"] >= 1


# ===========================================================================
# Init and pool
# ===========================================================================


class TestIdleWorkerInit:
    def test_initializes_with_pool(self):
        pool = AsyncMock()
        worker = IdleWorker(pool)
        assert worker.pool is pool
        assert worker._last_run == {}
        assert worker._schedules_loaded is False
