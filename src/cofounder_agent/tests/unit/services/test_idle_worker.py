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
        # _get_setting must return a string (callers do .strip()/.lower()
        # on the result); the blanket AsyncMock above returns {}.
        worker._get_setting = AsyncMock(return_value="true")
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
# _should_trigger_discovery — throttle gate (GH-89 AC#3)
# ===========================================================================


class TestShouldTriggerDiscoveryThrottleGate:
    """When the approval queue is full, topic discovery must
    early-return False. Otherwise auto-generated topics pile up behind
    the throttle wall and clutter pending indefinitely."""

    @pytest.mark.asyncio
    async def test_early_returns_when_queue_full(self):
        pool = _make_pool(pending_count=0)
        worker = IdleWorker(pool)
        # Patch the shared throttle check to report a full queue
        with patch(
            "services.pipeline_throttle.is_queue_full",
            new=AsyncMock(return_value=(True, 10, 3)),
        ):
            should_fire, reason = await worker._should_trigger_discovery()
        assert should_fire is False
        assert "queue_full" in reason

    @pytest.mark.asyncio
    async def test_continues_to_cooldown_when_queue_not_full(self):
        """Not-full queue does NOT short-circuit — we proceed to the
        normal cooldown/manual/signal ladder."""
        pool = _make_pool(pending_count=0)
        worker = IdleWorker(pool)

        # Suppress everything past the gate via a huge cooldown so
        # _should_trigger_discovery returns "cooldown" — proving we
        # got past the throttle gate (not "queue_full").
        async def _settings(key, default=""):
            if key == "topic_discovery_min_cooldown_seconds":
                return "99999999999"  # ~3000 years
            if key == "idle_last_run_topic_discovery":
                return str(time.time())  # just ran = definitely in cooldown
            return default

        worker._get_setting = _settings
        with patch(
            "services.pipeline_throttle.is_queue_full",
            new=AsyncMock(return_value=(False, 1, 3)),
        ):
            should_fire, reason = await worker._should_trigger_discovery()
        assert should_fire is False
        # Suppressed by cooldown, NOT by queue_full — the gate let us through
        assert "queue_full" not in reason
        assert reason == "cooldown"

    @pytest.mark.asyncio
    async def test_throttle_check_exception_does_not_kill_discovery(self):
        """A failing throttle check must NOT poison the decision — the
        rest of the signal ladder still runs."""
        pool = _make_pool(pending_count=0)
        worker = IdleWorker(pool)
        worker._get_setting = AsyncMock(return_value="0")  # cooldown 0 = due
        # Block the rest of the ladder so we can assert we got past the gate
        pool.fetchval = AsyncMock(return_value=0)  # pending=0 triggers queue_low
        with patch(
            "services.pipeline_throttle.is_queue_full",
            new=AsyncMock(side_effect=RuntimeError("throttle module exploded")),
        ):
            should_fire, reason = await worker._should_trigger_discovery()
        # We got past the gate — reason is not "queue_full"
        assert "queue_full" not in reason


# ===========================================================================
# topic_discovery_auto_enabled kill-switch (migration 0118)
# ===========================================================================


class TestTopicDiscoveryAutoEnabledKillSwitch:
    """Migration 0118 introduced ``topic_discovery_auto_enabled`` as a
    master kill-switch over the legacy auto-firing discovery loop.

    - Default ``"true"`` keeps backward-compatible behaviour for OSS users
      with no niches configured.
    - ``"false"`` makes operators with niches drive discovery via the
      niche-aware operator flow (``poindexter topics rank-batch /
      resolve-batch``) and suppresses signal-driven auto-firing.
    - Manual trigger (``topic_discovery_manual_trigger=true``) MUST still
      work even when auto is disabled — operators may want one-shot fires.
    """

    @pytest.mark.asyncio
    async def test_default_true_fires_on_queue_low_signal(self):
        """When ``topic_discovery_auto_enabled`` is unset (default 'true'),
        the queue-low signal still fires discovery as before."""
        pool = _make_pool(pending_count=0)
        worker = IdleWorker(pool)

        async def _settings(key, default=""):
            # Manual off, auto-enabled defaults to "true" via the default arg,
            # cooldown elapsed, queue-low threshold = 2.
            if key == "topic_discovery_manual_trigger":
                return "false"
            if key == "topic_discovery_min_cooldown_seconds":
                return "0"
            if key == "idle_last_run_topic_discovery":
                return "0"
            if key == "topic_discovery_queue_low_threshold":
                return "2"
            # Falls through for topic_discovery_auto_enabled — returns
            # the caller's default of "true".
            return default

        worker._get_setting = _settings
        # 0 pending tasks → queue_low fires
        pool.fetchval = AsyncMock(return_value=0)
        with patch(
            "services.pipeline_throttle.is_queue_full",
            new=AsyncMock(return_value=(False, 0, 999)),
        ):
            should_fire, reason = await worker._should_trigger_discovery()
        assert should_fire is True
        assert "queue_low" in reason

    @pytest.mark.asyncio
    async def test_auto_disabled_skips_signals_and_logs_skip(self, caplog):
        """When ``topic_discovery_auto_enabled=false`` the signal ladder
        is suppressed and a recognizable INFO skip log is emitted."""
        import logging

        pool = _make_pool(pending_count=0)
        worker = IdleWorker(pool)

        async def _settings(key, default=""):
            if key == "topic_discovery_manual_trigger":
                return "false"
            if key == "topic_discovery_auto_enabled":
                return "false"
            return default

        worker._get_setting = _settings
        # Even with a queue-low condition that would normally fire, auto
        # disabled must short-circuit before the signal checks.
        pool.fetchval = AsyncMock(return_value=0)
        with caplog.at_level(logging.INFO, logger="services.idle_worker"):
            with patch(
                "services.pipeline_throttle.is_queue_full",
                new=AsyncMock(return_value=(False, 0, 999)),
            ):
                should_fire, reason = await worker._should_trigger_discovery()

        assert should_fire is False
        assert reason == "auto_disabled"
        # Skip log fires once per evaluation.
        assert any(
            "topic_discovery_auto_enabled=false" in record.getMessage()
            and "skipped" in record.getMessage()
            for record in caplog.records
        ), f"expected skip log not found in: {[r.getMessage() for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_manual_trigger_still_works_when_auto_disabled(self):
        """Manual operator override fires BEFORE the auto-enabled gate so
        a one-shot ``topic_discovery_manual_trigger=true`` keeps working
        even when auto-firing is otherwise off."""
        pool = _make_pool(pending_count=0)
        worker = IdleWorker(pool)

        async def _settings(key, default=""):
            if key == "topic_discovery_manual_trigger":
                return "true"
            if key == "topic_discovery_auto_enabled":
                return "false"
            return default

        worker._get_setting = _settings
        with patch(
            "services.pipeline_throttle.is_queue_full",
            new=AsyncMock(return_value=(False, 0, 999)),
        ):
            should_fire, reason = await worker._should_trigger_discovery()
        assert should_fire is True
        assert reason == "manual_trigger"
        # Verify the manual flag was cleared so it doesn't re-fire next cycle.
        pool.execute.assert_awaited()


# ===========================================================================
# _sync_shared_context + _auto_embed_posts (subprocess wrappers)
# ===========================================================================

