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
        # Mark all lightweight/pre-gate tasks as recently run so they don't fire
        worker = IdleWorker(pool)
        now = time.time()
        worker._last_run["sync_page_views"] = now
        worker._last_run["sync_newsletter_subscribers"] = now
        worker._last_run["expire_stale_approvals"] = now
        # Non-GPU tasks that now run before the gate
        worker._last_run["topic_discovery"] = now
        worker._last_run["topic_gaps"] = now
        worker._last_run["context_sync"] = now
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
        assert "audit_quality_completed" in worker._last_run

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


# ===========================================================================
# _audit_published_quality
# ===========================================================================


class TestAuditPublishedQuality:
    @pytest.mark.asyncio
    async def test_no_posts_returns_audited_zero(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        worker = IdleWorker(pool)
        result = await worker._audit_published_quality()
        assert result["audited"] == 0

    @pytest.mark.asyncio
    async def test_short_post_creates_issue(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {
                "id": "p1", "title": "Short post",
                "slug": "short-post",
                "content_preview": "## Heading\nOnly a few words.",
            }
        ])
        worker = IdleWorker(pool)
        worker._create_gitea_issue = AsyncMock(return_value=True)

        result = await worker._audit_published_quality()
        assert result["audited"] == 1
        # The issue creation was called because word count is well under 500
        worker._create_gitea_issue.assert_awaited_once()
        assert any("only" in i and "words" in i for i in result["issues"])

    @pytest.mark.asyncio
    async def test_post_without_headings_creates_issue(self):
        pool = AsyncMock()
        # Pad to 600 words so word count check passes
        long_text = "word " * 600
        pool.fetch = AsyncMock(return_value=[
            {
                "id": "p2", "title": "No headings",
                "slug": "no-headings",
                "content_preview": long_text,
            }
        ])
        worker = IdleWorker(pool)
        worker._create_gitea_issue = AsyncMock(return_value=True)

        result = await worker._audit_published_quality()
        assert any("no headings" in i for i in result["issues"])

    @pytest.mark.asyncio
    async def test_good_post_no_issues(self):
        long_with_heading = "## Section\n\n" + ("word " * 600)
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": "p3", "title": "Good post", "slug": "good", "content_preview": long_with_heading}
        ])
        worker = IdleWorker(pool)
        worker._create_gitea_issue = AsyncMock()

        result = await worker._audit_published_quality()
        assert result["audited"] == 1
        assert result["issues"] == []
        worker._create_gitea_issue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_db_exception_returns_error(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=RuntimeError("db down"))
        worker = IdleWorker(pool)
        result = await worker._audit_published_quality()
        assert "error" in result


# ===========================================================================
# _check_published_links
# ===========================================================================


class TestCheckPublishedLinks:
    @pytest.mark.asyncio
    async def test_no_external_urls_no_issues(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": "p1", "title": "Plain", "content": "No links here."},
        ])
        worker = IdleWorker(pool)
        worker._create_gitea_issue = AsyncMock()

        result = await worker._check_published_links()
        assert result["checked"] == 0
        assert result["broken"] == 0
        worker._create_gitea_issue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_internal_links_skipped(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": "p1", "title": "Internal", "content": "See https://gladlabs.io/posts/x"},
        ])
        worker = IdleWorker(pool)
        worker._create_gitea_issue = AsyncMock()

        with patch("services.idle_worker.site_config") as mock_sc:
            mock_sc.get.return_value = "gladlabs.io"
            result = await worker._check_published_links()
        # Internal URL was skipped, nothing was checked
        assert result["checked"] == 0

    @pytest.mark.asyncio
    async def test_broken_link_creates_issue(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": "p1", "title": "Has bad link",
             "content": "Read more at https://example.com/dead"},
        ])
        worker = IdleWorker(pool)
        worker._create_gitea_issue = AsyncMock(return_value=True)

        bad_resp = MagicMock()
        bad_resp.status_code = 404

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(return_value=bad_resp)

        with patch("services.idle_worker.site_config") as mock_sc, \
             patch("httpx.AsyncClient", return_value=mock_client):
            mock_sc.get.return_value = "gladlabs.io"
            result = await worker._check_published_links()

        assert result["broken"] == 1
        worker._create_gitea_issue.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unreachable_link_marked_broken(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": "p1", "title": "Bad", "content": "Visit https://example.com/x"},
        ])
        worker = IdleWorker(pool)
        worker._create_gitea_issue = AsyncMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(side_effect=RuntimeError("connection failed"))

        with patch("services.idle_worker.site_config") as mock_sc, \
             patch("httpx.AsyncClient", return_value=mock_client):
            mock_sc.get.return_value = "gladlabs.io"
            result = await worker._check_published_links()

        assert result["broken"] == 1
        # Status was 'unreachable' for the broken url
        assert any(d["status"] == "unreachable" for d in result["details"])

    @pytest.mark.asyncio
    async def test_good_link_no_issue(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": "p1", "title": "Good", "content": "Read https://example.com/page"},
        ])
        worker = IdleWorker(pool)
        worker._create_gitea_issue = AsyncMock()

        good_resp = MagicMock()
        good_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(return_value=good_resp)

        with patch("services.idle_worker.site_config") as mock_sc, \
             patch("httpx.AsyncClient", return_value=mock_client):
            mock_sc.get.return_value = "gladlabs.io"
            result = await worker._check_published_links()

        assert result["checked"] == 1
        assert result["broken"] == 0
        worker._create_gitea_issue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_db_exception_returns_error(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=RuntimeError("db down"))
        worker = IdleWorker(pool)
        result = await worker._check_published_links()
        assert "error" in result


# ===========================================================================
# _refresh_stale_embeddings
# ===========================================================================


class TestRefreshStaleEmbeddings:
    @pytest.mark.asyncio
    async def test_no_local_db_url_returns_note(self):
        worker = IdleWorker(AsyncMock())
        with patch("services.idle_worker.site_config") as mock_sc:
            mock_sc.get.return_value = None
            # The function does its own import-from inside, so need to ensure
            # site_config.get returns None
            result = await worker._refresh_stale_embeddings()
        assert "note" in result

    @pytest.mark.asyncio
    async def test_with_local_db_url_returns_deferred(self):
        worker = IdleWorker(AsyncMock())
        # Patch the source module — function does a local import
        with patch("services.site_config.site_config") as mock_sc:
            mock_sc.get.return_value = "postgresql://local"
            result = await worker._refresh_stale_embeddings()
        assert "note" in result
        assert "deferred" in result["note"]


# ===========================================================================
# _discover_and_queue_topics
# ===========================================================================


class TestDiscoverAndQueueTopics:
    @pytest.mark.asyncio
    async def test_no_topics_found(self):
        worker = IdleWorker(AsyncMock())
        fake_discovery = MagicMock()
        fake_discovery.discover = AsyncMock(return_value=[])
        fake_discovery.queue_topics = AsyncMock()

        with patch("services.topic_discovery.TopicDiscovery", return_value=fake_discovery):
            result = await worker._discover_and_queue_topics()

        assert result["discovered"] == 0
        assert result["queued"] == 0
        fake_discovery.queue_topics.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_topics_discovered_and_queued(self):
        worker = IdleWorker(AsyncMock())
        fake_topic = MagicMock()
        fake_topic.title = "Some Trending Topic in AI"

        fake_discovery = MagicMock()
        fake_discovery.discover = AsyncMock(return_value=[fake_topic, fake_topic])
        fake_discovery.queue_topics = AsyncMock(return_value=2)

        with patch("services.topic_discovery.TopicDiscovery", return_value=fake_discovery):
            result = await worker._discover_and_queue_topics()

        assert result["discovered"] == 2
        assert result["queued"] == 2
        assert len(result["topics"]) == 2

    @pytest.mark.asyncio
    async def test_discovery_exception_returns_error(self):
        worker = IdleWorker(AsyncMock())
        fake_discovery = MagicMock()
        fake_discovery.discover = AsyncMock(side_effect=RuntimeError("hn down"))

        with patch("services.topic_discovery.TopicDiscovery", return_value=fake_discovery):
            result = await worker._discover_and_queue_topics()
        assert "error" in result


# ===========================================================================
# _sync_shared_context + _auto_embed_posts (subprocess wrappers)
# ===========================================================================


class TestSubprocessWrappers:
    @pytest.mark.asyncio
    async def test_sync_shared_context_success(self):
        worker = IdleWorker(AsyncMock())

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"sync ok", b""))

        with patch("services.idle_worker.site_config") as mock_sc, \
             patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)):
            mock_sc.get.return_value = "/app"
            result = await worker._sync_shared_context()

        assert result["ok"] is True
        assert "sync ok" in result["output"]

    @pytest.mark.asyncio
    async def test_sync_shared_context_subprocess_failure(self):
        worker = IdleWorker(AsyncMock())

        fake_proc = MagicMock()
        fake_proc.returncode = 1
        fake_proc.communicate = AsyncMock(return_value=(b"oops", b""))

        with patch("services.idle_worker.site_config") as mock_sc, \
             patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)):
            mock_sc.get.return_value = "/app"
            result = await worker._sync_shared_context()

        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_sync_shared_context_exception(self):
        worker = IdleWorker(AsyncMock())
        with patch("services.idle_worker.site_config") as mock_sc, \
             patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError("python"))):
            mock_sc.get.return_value = "/app"
            result = await worker._sync_shared_context()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_auto_embed_posts_success(self):
        worker = IdleWorker(AsyncMock())

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"embedded 5 posts", b""))

        with patch("services.idle_worker.site_config") as mock_sc, \
             patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)):
            mock_sc.get.return_value = "/app"
            result = await worker._auto_embed_posts()

        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_auto_embed_posts_exception(self):
        worker = IdleWorker(AsyncMock())
        with patch("services.idle_worker.site_config") as mock_sc, \
             patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=RuntimeError("boom"))):
            mock_sc.get.return_value = "/app"
            result = await worker._auto_embed_posts()
        assert "error" in result


# ===========================================================================
# _sync_page_views
# ===========================================================================


class TestSyncPageViews:
    @pytest.mark.asyncio
    async def test_no_database_url_returns_note(self, monkeypatch):
        pool = AsyncMock()
        worker = IdleWorker(pool)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        result = await worker._sync_page_views()
        assert "note" in result
        assert "no DATABASE_URL" in result["note"]


# ===========================================================================
# _fix_broken_internal_links
# ===========================================================================


class TestFixBrokenInternalLinks:
    @pytest.mark.asyncio
    async def test_removes_link_to_unpublished_post(self, monkeypatch):
        """Markdown + HTML links to posts not in the published set get stripped."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        cloud = AsyncMock()
        # Published slugs (does NOT include "deleted-post")
        cloud.fetch = AsyncMock(side_effect=[
            [{"slug": "real-post"}, {"slug": "another-real"}],
            [{
                "id": 1,
                "title": "Post with broken link",
                "content": "See [this guide](/posts/deleted-post) for details.",
            }],
        ])
        cloud.execute = AsyncMock()
        cloud.close = AsyncMock()

        worker = IdleWorker(AsyncMock())
        worker._create_gitea_issue = AsyncMock(return_value=True)

        with patch("asyncpg.connect", AsyncMock(return_value=cloud)):
            result = await worker._fix_broken_internal_links()

        assert result["fixed"] == 1
        # UPDATE called once
        cloud.execute.assert_awaited_once()
        updated_content = cloud.execute.await_args.args[1]
        assert "deleted-post" not in updated_content
        assert "this guide" in updated_content  # link text preserved as plain text

    @pytest.mark.asyncio
    async def test_no_broken_links_fixes_nothing(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        cloud = AsyncMock()
        cloud.fetch = AsyncMock(side_effect=[
            [{"slug": "real-post"}],
            [{
                "id": 1,
                "title": "Post",
                "content": "See [the guide](/posts/real-post).",
            }],
        ])
        cloud.execute = AsyncMock()
        cloud.close = AsyncMock()

        worker = IdleWorker(AsyncMock())

        with patch("asyncpg.connect", AsyncMock(return_value=cloud)):
            result = await worker._fix_broken_internal_links()

        assert result["fixed"] == 0
        cloud.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_posts_with_internal_links(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        cloud = AsyncMock()
        cloud.fetch = AsyncMock(side_effect=[
            [{"slug": "post-a"}],
            [],  # no posts match LIKE '%/posts/%'
        ])
        cloud.execute = AsyncMock()
        cloud.close = AsyncMock()

        worker = IdleWorker(AsyncMock())

        with patch("asyncpg.connect", AsyncMock(return_value=cloud)):
            result = await worker._fix_broken_internal_links()

        assert result["fixed"] == 0

    @pytest.mark.asyncio
    async def test_db_exception_returns_error(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        worker = IdleWorker(AsyncMock())

        with patch("asyncpg.connect", AsyncMock(side_effect=RuntimeError("conn refused"))):
            result = await worker._fix_broken_internal_links()

        assert "error" in result

    @pytest.mark.asyncio
    async def test_creates_gitea_issue_when_fixes_applied(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        cloud = AsyncMock()
        cloud.fetch = AsyncMock(side_effect=[
            [{"slug": "alive"}],
            [{
                "id": 1,
                "title": "p1",
                "content": "[x](/posts/dead-slug)",
            }],
        ])
        cloud.execute = AsyncMock()
        cloud.close = AsyncMock()

        worker = IdleWorker(AsyncMock())
        worker._create_gitea_issue = AsyncMock(return_value=True)

        with patch("asyncpg.connect", AsyncMock(return_value=cloud)):
            await worker._fix_broken_internal_links()

        worker._create_gitea_issue.assert_awaited_once()


# ===========================================================================
# _fix_broken_external_links
# ===========================================================================


class TestFixBrokenExternalLinks:
    @pytest.mark.asyncio
    async def test_removes_404_external_links(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        cloud = AsyncMock()
        cloud.fetch = AsyncMock(return_value=[{
            "id": 1,
            "title": "Post with bad link",
            "content": "See [this doc](https://example.com/dead-page) for more.",
        }])
        cloud.execute = AsyncMock()
        cloud.close = AsyncMock()

        # httpx client that returns 404 for the one external URL
        fake_response = MagicMock()
        fake_response.status_code = 404
        fake_client = AsyncMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)

        worker = IdleWorker(AsyncMock())
        worker._create_gitea_issue = AsyncMock(return_value=True)

        with patch("asyncpg.connect", AsyncMock(return_value=cloud)), \
             patch("httpx.AsyncClient", MagicMock(return_value=fake_client)), \
             patch("httpx.Timeout", MagicMock(return_value=None)), \
             patch("services.idle_worker.site_config") as mock_sc:
            mock_sc.get.return_value = "mysite.com"
            result = await worker._fix_broken_external_links()

        assert result["links_removed"] == 1
        assert result["posts_fixed"] == 1
        updated = cloud.execute.await_args.args[1]
        assert "dead-page" not in updated
        assert "this doc" in updated

    @pytest.mark.asyncio
    async def test_working_links_not_touched(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        cloud = AsyncMock()
        cloud.fetch = AsyncMock(return_value=[{
            "id": 1,
            "title": "Post",
            "content": "See [the docs](https://example.com/ok).",
        }])
        cloud.execute = AsyncMock()
        cloud.close = AsyncMock()

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_client = AsyncMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)

        worker = IdleWorker(AsyncMock())

        with patch("asyncpg.connect", AsyncMock(return_value=cloud)), \
             patch("httpx.AsyncClient", MagicMock(return_value=fake_client)), \
             patch("httpx.Timeout", MagicMock(return_value=None)), \
             patch("services.idle_worker.site_config") as mock_sc:
            mock_sc.get.return_value = "mysite.com"
            result = await worker._fix_broken_external_links()

        assert result["links_removed"] == 0
        assert result["posts_fixed"] == 0
        cloud.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_own_domain(self, monkeypatch):
        """Links to the site's own domain are not checked (they're internal)."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        cloud = AsyncMock()
        cloud.fetch = AsyncMock(return_value=[{
            "id": 1,
            "title": "p",
            "content": "See [home](https://mysite.com/about).",
        }])
        cloud.execute = AsyncMock()
        cloud.close = AsyncMock()

        fake_client = AsyncMock()
        fake_client.get = AsyncMock()  # should never be called
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)

        worker = IdleWorker(AsyncMock())

        with patch("asyncpg.connect", AsyncMock(return_value=cloud)), \
             patch("httpx.AsyncClient", MagicMock(return_value=fake_client)), \
             patch("httpx.Timeout", MagicMock(return_value=None)), \
             patch("services.idle_worker.site_config") as mock_sc:
            mock_sc.get.return_value = "mysite.com"
            await worker._fix_broken_external_links()

        # No URL fetch should have happened — all URLs were on own domain
        fake_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_network_error_treated_as_broken(self, monkeypatch):
        """httpx raising is treated as a broken link (removed)."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

        cloud = AsyncMock()
        cloud.fetch = AsyncMock(return_value=[{
            "id": 1,
            "title": "p",
            "content": "[x](https://example.com/unreachable)",
        }])
        cloud.execute = AsyncMock()
        cloud.close = AsyncMock()

        fake_client = AsyncMock()
        fake_client.get = AsyncMock(side_effect=ConnectionError("DNS failure"))
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)

        worker = IdleWorker(AsyncMock())
        worker._create_gitea_issue = AsyncMock(return_value=True)

        with patch("asyncpg.connect", AsyncMock(return_value=cloud)), \
             patch("httpx.AsyncClient", MagicMock(return_value=fake_client)), \
             patch("httpx.Timeout", MagicMock(return_value=None)), \
             patch("services.idle_worker.site_config") as mock_sc:
            mock_sc.get.return_value = "mysite.com"
            result = await worker._fix_broken_external_links()

        assert result["links_removed"] == 1

    @pytest.mark.asyncio
    async def test_db_exception_returns_error(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
        worker = IdleWorker(AsyncMock())
        with patch("asyncpg.connect", AsyncMock(side_effect=RuntimeError("down"))):
            result = await worker._fix_broken_external_links()
        assert "error" in result


# ===========================================================================
# _crosspost_to_devto
# ===========================================================================


class TestCrosspostToDevto:
    @pytest.mark.asyncio
    async def test_skipped_when_no_api_key(self):
        pool = AsyncMock()
        worker = IdleWorker(pool)

        fake_svc = MagicMock()
        fake_svc._get_api_key = AsyncMock(return_value=None)

        with patch("services.devto_service.DevToCrossPostService", MagicMock(return_value=fake_svc)):
            result = await worker._crosspost_to_devto()

        assert result.get("skipped") is True
        assert result["reason"] == "devto_api_key not configured"

    @pytest.mark.asyncio
    async def test_no_pending_posts_returns_zero(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        worker = IdleWorker(pool)

        fake_svc = MagicMock()
        fake_svc._get_api_key = AsyncMock(return_value="dev-key-123")

        with patch("services.devto_service.DevToCrossPostService", MagicMock(return_value=fake_svc)):
            result = await worker._crosspost_to_devto()

        assert result["crossposted"] == 0
        assert "note" in result

    @pytest.mark.asyncio
    async def test_crossposts_published_posts(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": "post-1", "title": "Post 1", "slug": "post-1"},
            {"id": "post-2", "title": "Post 2", "slug": "post-2"},
        ])
        worker = IdleWorker(pool)

        fake_svc = MagicMock()
        fake_svc._get_api_key = AsyncMock(return_value="key")
        fake_svc.cross_post_by_post_id = AsyncMock(side_effect=[
            "https://dev.to/u/post-1",
            "https://dev.to/u/post-2",
        ])

        with patch("services.devto_service.DevToCrossPostService", MagicMock(return_value=fake_svc)):
            result = await worker._crosspost_to_devto()

        assert result["crossposted"] == 2
        assert result["checked"] == 2

    @pytest.mark.asyncio
    async def test_collects_errors_for_failed_crossposts(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": "post-1", "title": "Post 1", "slug": "post-1"},
            {"id": "post-2", "title": "Post 2", "slug": "post-2"},
        ])
        worker = IdleWorker(pool)

        fake_svc = MagicMock()
        fake_svc._get_api_key = AsyncMock(return_value="key")
        fake_svc.cross_post_by_post_id = AsyncMock(side_effect=[
            "https://dev.to/u/post-1",
            RuntimeError("rate limited"),
        ])

        with patch("services.devto_service.DevToCrossPostService", MagicMock(return_value=fake_svc)):
            result = await worker._crosspost_to_devto()

        assert result["crossposted"] == 1
        assert "errors" in result
        assert "rate limited" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_none_return_treated_as_error(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": "post-1", "title": "P", "slug": "p"},
        ])
        worker = IdleWorker(pool)

        fake_svc = MagicMock()
        fake_svc._get_api_key = AsyncMock(return_value="key")
        fake_svc.cross_post_by_post_id = AsyncMock(return_value=None)

        with patch("services.devto_service.DevToCrossPostService", MagicMock(return_value=fake_svc)):
            result = await worker._crosspost_to_devto()

        assert result["crossposted"] == 0
        assert "errors" in result

    @pytest.mark.asyncio
    async def test_service_exception_returns_error(self):
        pool = AsyncMock()
        worker = IdleWorker(pool)

        with patch("services.devto_service.DevToCrossPostService", MagicMock(side_effect=RuntimeError("svc down"))):
            result = await worker._crosspost_to_devto()

        assert "error" in result


# ===========================================================================
# _backup_database
# ===========================================================================


class TestBackupDatabase:
    @pytest.mark.asyncio
    async def test_writes_json_files_per_table(self, tmp_path, monkeypatch):
        # Redirect the backup dir to tmp_path
        monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path) if p == "~" else p)

        pool = AsyncMock()
        # Return some rows for the first table, empty for the rest
        row = MagicMock()
        row_data = {"id": 1, "title": "post"}
        row.__getitem__ = lambda self, k: row_data.get(k)
        row.items = lambda: row_data.items()
        row.keys = lambda: row_data.keys()

        # dict(row) requires keys() + __getitem__
        call_count = [0]

        async def _fetch(query):
            call_count[0] += 1
            if call_count[0] == 1:
                return [{"id": 1, "title": "post"}]  # posts table
            return []

        pool.fetch = AsyncMock(side_effect=_fetch)
        worker = IdleWorker(pool)

        result = await worker._backup_database()

        # Should have backed up at least one table (posts)
        assert "backed_up" in result or result.get("error") is None
        # Backup dir should exist
        backup_dir = tmp_path / ".poindexter" / "backups"
        assert backup_dir.exists()

    @pytest.mark.asyncio
    async def test_handles_table_fetch_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path) if p == "~" else p)

        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=RuntimeError("table missing"))
        worker = IdleWorker(pool)

        result = await worker._backup_database()
        # Errors captured per-table, method doesn't raise
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_datetime_values_serialized(self, tmp_path, monkeypatch):
        """Datetime columns should be converted via isoformat."""
        from datetime import datetime
        monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path) if p == "~" else p)

        pool = AsyncMock()
        now = datetime.now()
        call_count = [0]

        async def _fetch(query):
            call_count[0] += 1
            if call_count[0] == 1:
                return [{"id": 1, "created_at": now}]
            return []

        pool.fetch = AsyncMock(side_effect=_fetch)
        worker = IdleWorker(pool)

        # Should not raise on datetime serialization
        await worker._backup_database()
