"""
Unit tests for services/alert_handler.py

Tests alert pattern matching, cooldown, escalation, and auto-resolution.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.alert_handler import AlertHandler


def _make_pool(action_row=None):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=action_row)
    pool.execute = AsyncMock()
    return pool


def _make_action(pattern="test_alert", action_type="notify_only", cooldown=30,
                 last_triggered=None, failures=0, escalate_after=3):
    return {
        "id": 1,
        "pattern": pattern,
        "action_type": action_type,
        "action_config": {"message": "test alert"},
        "cooldown_minutes": cooldown,
        "last_triggered_at": last_triggered,
        "consecutive_failures": failures,
        "escalate_after_failures": escalate_after,
    }


class TestHandleUnknownPattern:
    async def test_unknown_pattern_escalates(self):
        pool = _make_pool(None)
        send_fn = MagicMock()
        handler = AlertHandler(pool, send_telegram_fn=send_fn)
        resolved = await handler.handle("unknown_pattern", "something broke")
        assert resolved is False
        send_fn.assert_called_once()


class TestCooldown:
    async def test_in_cooldown_suppresses(self):
        recent = datetime.now(timezone.utc) - timedelta(minutes=5)
        action = _make_action(cooldown=30, last_triggered=recent)
        pool = _make_pool(action)
        handler = AlertHandler(pool)
        resolved = await handler.handle("test_alert", "detail")
        assert resolved is True  # Suppressed = treated as resolved
        pool.execute.assert_not_awaited()  # No DB update during cooldown

    async def test_outside_cooldown_processes(self):
        old = datetime.now(timezone.utc) - timedelta(minutes=60)
        action = _make_action(cooldown=30, last_triggered=old)
        pool = _make_pool(action)
        handler = AlertHandler(pool)
        await handler.handle("test_alert", "detail")
        assert pool.execute.await_count >= 1  # Should update trigger time


class TestNotifyOnly:
    async def test_notify_returns_false(self):
        action = _make_action(action_type="notify_only")
        pool = _make_pool(action)
        handler = AlertHandler(pool)
        resolved = await handler.handle("test_alert", "something")
        assert resolved is False  # notify_only never "resolves"


class TestEscalation:
    async def test_escalates_after_threshold(self):
        action = _make_action(failures=2, escalate_after=3)
        pool = _make_pool(action)
        send_fn = MagicMock()
        handler = AlertHandler(pool, send_telegram_fn=send_fn)
        await handler.handle("test_alert", "keeps failing")
        send_fn.assert_called_once()

    async def test_no_escalation_below_threshold(self):
        action = _make_action(failures=0, escalate_after=3)
        pool = _make_pool(action)
        send_fn = MagicMock()
        handler = AlertHandler(pool, send_telegram_fn=send_fn)
        await handler.handle("test_alert", "first failure")
        send_fn.assert_not_called()


class TestLogForReview:
    async def test_log_resolves_true(self):
        action = _make_action(action_type="log_for_review")
        action["action_config"] = {"severity": "warning"}
        pool = _make_pool(action)
        handler = AlertHandler(pool)
        resolved = await handler.handle("broken_links", "3 broken URLs")
        assert resolved is True


class TestDBUpdate:
    async def test_db_update_executes_query(self):
        action = _make_action(action_type="db_update")
        action["action_config"] = {
            "query": "UPDATE content_tasks SET status = 'pending' WHERE status = 'in_progress'"
        }
        pool = _make_pool(action)
        handler = AlertHandler(pool)
        resolved = await handler.handle("stale_tasks", "3 stuck")
        assert resolved is True

    async def test_db_update_no_query(self):
        action = _make_action(action_type="db_update")
        action["action_config"] = {}
        pool = _make_pool(action)
        handler = AlertHandler(pool)
        resolved = await handler.handle("stale_tasks", "detail")
        assert resolved is False
