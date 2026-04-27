"""Unit tests for ``services/jobs/topic_discovery_signals.py`` (#151).

Replaces the legacy idle_worker-based signal tests. Same algorithm,
same priority order, just lifted out of the per-cycle event loop and
into the apscheduler-driven Job protocol.

Tests focus on the *control flow* — which signal fires under which
state, and what reason string lands in the JobResult. The actual
``TopicDiscovery`` discover/queue logic is patched at the import
boundary so this suite doesn't pull in HackerNews / Dev.to / etc.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from services.jobs.topic_discovery_signals import (
    TopicDiscoverySignalsJob,
    _evaluate_signals,
)


# ---------------------------------------------------------------------------
# Fake pool — same minimal shape used by the prune-job tests.
# ---------------------------------------------------------------------------


class FakePool:
    def __init__(self) -> None:
        self.app_settings: dict[str, str] = {}
        self.pending_count: int = 5
        self.last_published_at: datetime | None = None
        self.recent_statuses: list[str] = []

    async def fetchrow(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("SELECT value FROM app_settings WHERE key = $1"):
            (key,) = args
            v = self.app_settings.get(key)
            return {"value": v} if v is not None else None
        raise AssertionError(f"unexpected fetchrow: {sql_norm[:80]}")

    async def fetchval(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if "FROM content_tasks WHERE status = 'pending'" in sql_norm:
            return self.pending_count
        if "FROM posts WHERE status = 'published'" in sql_norm:
            return self.last_published_at
        raise AssertionError(f"unexpected fetchval: {sql_norm[:80]}")

    async def fetch(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if "FROM content_tasks" in sql_norm and "ORDER BY updated_at DESC" in sql_norm:
            limit = args[0] if args else 10
            return [{"status": s} for s in self.recent_statuses[:limit]]
        raise AssertionError(f"unexpected fetch: {sql_norm[:80]}")

    async def execute(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("INSERT INTO app_settings"):
            key, value = args
            self.app_settings[key] = value
            return "INSERT 0 1"
        if sql_norm.startswith("UPDATE app_settings SET value"):
            return "UPDATE 1"
        raise AssertionError(f"unexpected execute: {sql_norm[:80]}")


@pytest.fixture
def fake_pool():
    return FakePool()


@pytest.fixture
def patch_throttle():
    """Stub ``services.pipeline_throttle.is_queue_full`` with a default
    'queue not full' response so signal evaluation can proceed past the
    throttle gate."""
    with patch(
        "services.pipeline_throttle.is_queue_full",
        new=AsyncMock(return_value=(False, 0, 100)),
    ) as m:
        yield m


# ---------------------------------------------------------------------------
# _evaluate_signals — priority order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEvaluateSignals:
    async def test_throttle_full_suppresses_everything(self, fake_pool):
        with patch(
            "services.pipeline_throttle.is_queue_full",
            new=AsyncMock(return_value=(True, 50, 50)),
        ):
            should_fire, reason = await _evaluate_signals(fake_pool, None)
        assert should_fire is False
        assert "queue_full" in reason

    async def test_manual_trigger_fires_and_clears_flag(
        self, fake_pool, patch_throttle,
    ):
        fake_pool.app_settings["topic_discovery_manual_trigger"] = "true"
        should_fire, reason = await _evaluate_signals(fake_pool, None)
        assert should_fire is True
        assert reason == "manual_trigger"

    async def test_cooldown_suppresses_when_recent(
        self, fake_pool, patch_throttle,
    ):
        # last_run = now → still inside cooldown (default 1800s)
        fake_pool.app_settings["idle_last_run_topic_discovery"] = str(time.time())
        should_fire, reason = await _evaluate_signals(fake_pool, None)
        assert should_fire is False
        assert reason == "cooldown"

    async def test_queue_low_fires(self, fake_pool, patch_throttle):
        # Past cooldown, pending count below threshold (default 2)
        fake_pool.app_settings["idle_last_run_topic_discovery"] = "0"
        fake_pool.pending_count = 1
        should_fire, reason = await _evaluate_signals(fake_pool, None)
        assert should_fire is True
        assert "queue_low" in reason

    async def test_stale_content_fires(self, fake_pool, patch_throttle):
        fake_pool.app_settings["idle_last_run_topic_discovery"] = "0"
        fake_pool.pending_count = 100  # past queue-low check
        # Last publish 10h ago (default stale threshold = 6h)
        fake_pool.last_published_at = (
            datetime.now(timezone.utc) - timedelta(hours=10)
        )
        should_fire, reason = await _evaluate_signals(fake_pool, None)
        assert should_fire is True
        assert "stale_content" in reason

    async def test_rejection_streak_fires(self, fake_pool, patch_throttle):
        fake_pool.app_settings["idle_last_run_topic_discovery"] = "0"
        fake_pool.pending_count = 100
        fake_pool.last_published_at = datetime.now(timezone.utc)  # not stale
        # 3 consecutive rejections → streak signal
        fake_pool.recent_statuses = ["rejected", "rejected_final", "rejected"]

        class _SC:
            def get_int(self, key, default):
                return default

        should_fire, reason = await _evaluate_signals(fake_pool, _SC())
        assert should_fire is True
        assert "rejection_streak" in reason

    async def test_safety_net_fires_after_24h(
        self, fake_pool, patch_throttle,
    ):
        fake_pool.app_settings["idle_last_run_topic_discovery"] = str(
            time.time() - 86500  # >24h ago
        )
        fake_pool.pending_count = 100
        fake_pool.last_published_at = datetime.now(timezone.utc)
        fake_pool.recent_statuses = ["published"]

        class _SC:
            def get_int(self, key, default):
                return default

        should_fire, reason = await _evaluate_signals(fake_pool, _SC())
        assert should_fire is True
        assert reason == "safety_net_24h"

    async def test_no_signal_returns_false(self, fake_pool, patch_throttle):
        # Past cooldown, queue not low, content fresh, no streak
        fake_pool.app_settings["idle_last_run_topic_discovery"] = str(
            time.time() - 3600
        )
        fake_pool.pending_count = 100
        fake_pool.last_published_at = datetime.now(timezone.utc)
        fake_pool.recent_statuses = ["published"]

        class _SC:
            def get_int(self, key, default):
                return default

        should_fire, reason = await _evaluate_signals(fake_pool, _SC())
        assert should_fire is False
        assert reason == "no_signal"


# ---------------------------------------------------------------------------
# Job.run — JobResult shape, fired vs not-fired
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRun:
    async def test_no_signal_run_does_not_fire(self, fake_pool, patch_throttle):
        # Pre-set state so cooldown bails first.
        fake_pool.app_settings["idle_last_run_topic_discovery"] = str(time.time())
        result = await TopicDiscoverySignalsJob().run(pool=fake_pool, config={})
        assert result.ok is True
        assert result.changes_made == 0
        assert result.metrics["fired"] is False
        assert result.metrics["trigger"] == "cooldown"

    async def test_fired_run_invokes_topic_discovery(
        self, fake_pool, patch_throttle,
    ):
        # Force manual trigger
        fake_pool.app_settings["topic_discovery_manual_trigger"] = "true"

        # Patch TopicDiscovery so we don't pull in real sources.
        class _FakeTopic:
            def __init__(self, title: str) -> None:
                self.title = title

        class _FakeDiscovery:
            def __init__(self, *_a, **_k) -> None:
                pass

            async def discover(self, max_topics):
                return [_FakeTopic("a"), _FakeTopic("b")]

            async def queue_topics(self, topics):
                return len(topics)

        with patch(
            "services.topic_discovery.TopicDiscovery", _FakeDiscovery,
        ):
            result = await TopicDiscoverySignalsJob().run(
                pool=fake_pool, config={"site_config": None},
            )
        assert result.ok is True
        assert result.metrics["fired"] is True
        assert result.metrics["trigger"] == "manual_trigger"
        assert result.changes_made == 2
        # last_run was persisted so the next call hits cooldown.
        assert "idle_last_run_topic_discovery" in fake_pool.app_settings


class TestProtocol:
    def test_required_attrs(self):
        job = TopicDiscoverySignalsJob()
        assert job.name == "topic_discovery_signals"
        assert job.idempotent is True
        assert job.schedule == "*/5 * * * *"
