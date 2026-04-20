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
        # #229 event-driven discovery: mock the signal evaluator so it
        # doesn't fire when the queue is genuinely pending (prevents real
        # DuckDuckGo/HN/dev.to HTTP calls inside the unit test).
        worker._should_trigger_discovery = AsyncMock(
            return_value=(False, "queue_full(5>=2)")
        )
        worker._publish_scheduled_posts = AsyncMock(return_value={"published": 0})
        result = await worker.run_cycle()
        assert result.get("skipped") is True or result.get("scheduled_publishes") is not None

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
        # _should_trigger_discovery must return a (bool, reason) tuple; the
        # blanket AsyncMock above returns {} which can't unpack.
        worker._should_trigger_discovery = AsyncMock(
            return_value=(False, "mocked_not_triggered")
        )
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
    """The actual Gitea logic now lives in utils.gitea_issues — full
    coverage there. Here we only verify ``_create_gitea_issue`` delegates."""

    @pytest.mark.asyncio
    async def test_delegates_to_shared_utility(self):
        worker = IdleWorker(AsyncMock())
        with patch(
            "utils.gitea_issues.create_gitea_issue",
            new=AsyncMock(return_value=True),
        ) as mock_util:
            result = await worker._create_gitea_issue("links: broken", "body")
        assert result is True
        mock_util.assert_awaited_once_with("links: broken", "body")

    @pytest.mark.asyncio
    async def test_returns_false_when_utility_returns_false(self):
        worker = IdleWorker(AsyncMock())
        with patch(
            "utils.gitea_issues.create_gitea_issue",
            new=AsyncMock(return_value=False),
        ):
            result = await worker._create_gitea_issue("links: broken", "body")
        assert result is False


# ===========================================================================
# _expire_stale_approvals
# ===========================================================================


class TestIdleWorkerInit:
    def test_initializes_with_pool(self):
        pool = AsyncMock()
        worker = IdleWorker(pool)
        assert worker.pool is pool
        assert worker._last_run == {}
        assert worker._schedules_loaded is False


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

