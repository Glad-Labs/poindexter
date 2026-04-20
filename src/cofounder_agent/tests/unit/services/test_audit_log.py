"""
Unit tests for services/audit_log.py

Covers:
- AuditLogger.log: inserts audit row via pool.execute
- AuditLogger.log: swallows exceptions (never crashes caller)
- AuditLogger.log: serializes details dict to JSON
- AuditLogger.query: builds SQL with no filters
- AuditLogger.query: builds SQL with all filters
- AuditLogger.query: returns list of dicts from rows
- init_global_audit_logger: sets global singleton
- get_audit_logger: returns None before init, returns logger after init
- audit_log_bg: silently drops event when no global logger
- audit_log_bg: schedules background task when logger exists
- _handle_audit_task_exception: logs warning on failed task
"""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.audit_log import (
    AuditLogger,
    _handle_audit_task_exception,
    audit_log_bg,
    get_audit_logger,
    init_global_audit_logger,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_pool():
    """Create a mock asyncpg Pool."""
    pool = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    return pool


# ---------------------------------------------------------------------------
# AuditLogger.log
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuditLoggerLog:
    @pytest.mark.asyncio
    async def test_inserts_audit_row(self):
        pool = _make_pool()
        audit = AuditLogger(pool)

        await audit.log("task_created", "content_router", {"topic": "AI"}, task_id="t1")

        pool.execute.assert_awaited_once()
        call_args = pool.execute.call_args[0]
        assert call_args[0] == AuditLogger.INSERT_SQL
        assert call_args[1] == "task_created"
        assert call_args[2] == "content_router"
        assert call_args[3] == "t1"
        # details should be a JSON string
        parsed = json.loads(call_args[4])
        assert parsed == {"topic": "AI"}
        assert call_args[5] == "info"

    @pytest.mark.asyncio
    async def test_default_severity_is_info(self):
        pool = _make_pool()
        audit = AuditLogger(pool)

        await audit.log("evt", "src")
        call_args = pool.execute.call_args[0]
        assert call_args[5] == "info"

    @pytest.mark.asyncio
    async def test_custom_severity(self):
        pool = _make_pool()
        audit = AuditLogger(pool)

        await audit.log("evt", "src", severity="error")
        call_args = pool.execute.call_args[0]
        assert call_args[5] == "error"

    @pytest.mark.asyncio
    async def test_swallows_exceptions(self):
        pool = _make_pool()
        pool.execute = AsyncMock(side_effect=RuntimeError("DB down"))
        audit = AuditLogger(pool)

        # Should not raise
        await audit.log("evt", "src", {"detail": "val"}, task_id="t1")

    @pytest.mark.asyncio
    async def test_none_details_becomes_empty_json(self):
        pool = _make_pool()
        audit = AuditLogger(pool)

        await audit.log("evt", "src")
        call_args = pool.execute.call_args[0]
        parsed = json.loads(call_args[4])
        assert parsed == {}

    @pytest.mark.asyncio
    async def test_none_task_id_passed_through(self):
        pool = _make_pool()
        audit = AuditLogger(pool)

        await audit.log("evt", "src")
        call_args = pool.execute.call_args[0]
        assert call_args[3] is None


# ---------------------------------------------------------------------------
# AuditLogger.query
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuditLoggerQuery:
    @pytest.mark.asyncio
    async def test_query_no_filters(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[])
        audit = AuditLogger(pool)

        result = await audit.query()

        assert result == []
        pool.fetch.assert_awaited_once()
        sql = pool.fetch.call_args[0][0]
        assert "WHERE" not in sql
        assert "ORDER BY timestamp DESC" in sql

    @pytest.mark.asyncio
    async def test_query_with_all_filters(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[])
        audit = AuditLogger(pool)
        since = datetime(2025, 1, 1, tzinfo=timezone.utc)

        await audit.query(
            event_type="task_created",
            source="router",
            task_id="t1",
            severity="info",
            since=since,
            limit=50,
        )

        sql = pool.fetch.call_args[0][0]
        assert "event_type = $1" in sql
        assert "source = $2" in sql
        assert "task_id = $3" in sql
        assert "severity = $4" in sql
        assert "timestamp >= $5" in sql

    @pytest.mark.asyncio
    async def test_query_returns_dicts(self):
        pool = _make_pool()
        row = MagicMock()
        row.__iter__ = MagicMock(return_value=iter([("event_type", "test")]))
        row.items = MagicMock(return_value=[("event_type", "test")])
        row.keys = MagicMock(return_value=["event_type"])

        # dict(record) in asyncpg returns a plain dict
        mock_record = {"event_type": "test", "source": "src"}
        pool.fetch = AsyncMock(return_value=[mock_record])
        audit = AuditLogger(pool)

        result = await audit.query()
        assert len(result) == 1
        assert result[0]["event_type"] == "test"


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGlobalAuditLogger:
    def test_get_audit_logger_returns_none_before_init(self):
        import services.audit_log as mod
        original = mod._global_audit_logger
        try:
            mod._global_audit_logger = None
            assert get_audit_logger() is None
        finally:
            mod._global_audit_logger = original

    def test_init_sets_global(self):
        import services.audit_log as mod
        original = mod._global_audit_logger
        try:
            pool = _make_pool()
            logger = init_global_audit_logger(pool)
            assert isinstance(logger, AuditLogger)
            assert get_audit_logger() is logger
        finally:
            mod._global_audit_logger = original

    def test_audit_log_bg_drops_when_no_logger(self):
        import services.audit_log as mod
        original = mod._global_audit_logger
        try:
            mod._global_audit_logger = None
            # Should not raise
            audit_log_bg("evt", "src")
        finally:
            mod._global_audit_logger = original

    @pytest.mark.asyncio
    async def test_audit_log_bg_schedules_task(self):
        import services.audit_log as mod
        original = mod._global_audit_logger

        pool = _make_pool()
        init_global_audit_logger(pool)

        try:
            audit_log_bg("evt", "src", {"key": "val"}, task_id="t1")
            # Allow the scheduled task to run
            await asyncio.sleep(0.05)
            pool.execute.assert_awaited_once()
        finally:
            mod._global_audit_logger = original


# ---------------------------------------------------------------------------
# _handle_audit_task_exception
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleAuditTaskException:
    def test_cancelled_task_does_nothing(self):
        task = MagicMock()
        task.cancelled.return_value = True
        # Should not raise
        _handle_audit_task_exception(task)

    def test_failed_task_logs_warning(self):
        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = RuntimeError("boom")
        # Should not raise
        _handle_audit_task_exception(task)

    def test_successful_task_does_nothing(self):
        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = None
        # Should not raise
        _handle_audit_task_exception(task)
