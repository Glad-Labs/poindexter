"""
Unit tests for services/idle_worker.py

Tests background maintenance tasks: quality audits, link checks,
topic gaps, threshold tuning, and topic discovery.
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
    return pool


class TestRunCycleSkipsWhenBusy:
    async def test_skips_when_tasks_pending(self):
        pool = _make_pool(pending_count=5)
        worker = IdleWorker(pool)
        result = await worker.run_cycle()
        assert result.get("skipped") is True
        assert "5 active tasks" in result.get("reason", "")

    async def test_runs_when_no_tasks(self):
        pool = _make_pool(pending_count=0)
        worker = IdleWorker(pool)
        # Force all tasks to be due
        worker._last_run = {}
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
    async def test_high_failure_rate_suggestion(self):
        pool = _make_pool()
        # _tune_thresholds calls pool.fetchrow directly (no pending check)
        pool.fetchrow = AsyncMock(return_value={
            "total": 10, "published": 3, "failed": 6, "rejected": 1, "avg_score": 65.0,
        })
        worker = IdleWorker(pool)
        result = await worker._tune_thresholds()
        assert any("failure" in s.lower() or "lower" in s.lower()
                    for s in result.get("suggestions", []))

    async def test_no_recent_tasks(self):
        pool = _make_pool()
        pool.fetchrow = AsyncMock(return_value={
            "total": 0, "published": 0, "failed": 0, "rejected": 0, "avg_score": None,
        })
        worker = IdleWorker(pool)
        result = await worker._tune_thresholds()
        assert "no recent tasks" in result.get("note", "")
