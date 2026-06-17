"""Unit tests for ``audit_log.query_summary``."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from services.audit_log import query_summary


def _pool(rows=None):
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows or [])
    return pool


class TestQuerySummary:
    async def test_returns_rows_as_dicts(self):
        pool = _pool([{"event_type": "qa_pass_completed", "severity": "info", "count": 5}])
        result = await query_summary(pool)
        assert result[0]["event_type"] == "qa_pass_completed"

    async def test_passes_hours_as_param(self):
        pool = _pool()
        await query_summary(pool, hours=48)
        args = pool.fetch.await_args.args
        assert 48 in args

    async def test_default_hours_is_24(self):
        pool = _pool()
        await query_summary(pool)
        args = pool.fetch.await_args.args
        assert 24 in args

    async def test_sql_groups_by_event_type_and_severity(self):
        pool = _pool()
        await query_summary(pool)
        sql = pool.fetch.await_args.args[0]
        assert "event_type" in sql
        assert "severity" in sql
        assert "GROUP BY" in sql

    async def test_empty_returns_empty_list(self):
        pool = _pool()
        assert await query_summary(pool) == []
