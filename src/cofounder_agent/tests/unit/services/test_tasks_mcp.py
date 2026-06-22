"""Unit tests for ``services.tasks_mcp``.

Two thin helpers that the MCP server's ``list_tasks`` and
``_resolve_task_id`` tools delegate to.  Uses the same fake-pool pattern
as ``test_experiment_admin.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from services import tasks_mcp


def _build_pool(conn: MagicMock) -> MagicMock:
    pool = MagicMock()
    pool.fetch = conn.fetch
    pool.fetchrow = conn.fetchrow
    return pool


@pytest.fixture
def fake_pool():
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    return _build_pool(conn), conn


# ---------------------------------------------------------------------------
# list_tasks
# ---------------------------------------------------------------------------


class TestListTasks:
    async def test_no_status_filter_fetches_all(self, fake_pool):
        pool, conn = fake_pool
        await tasks_mcp.list_tasks(pool, status="all", limit=10)
        sql = conn.fetch.await_args.args[0]
        assert "pipeline_tasks_view" in sql
        assert "$1" in sql  # limit param

    async def test_status_filter_adds_where_clause(self, fake_pool):
        pool, conn = fake_pool
        await tasks_mcp.list_tasks(pool, status="pending", limit=5)
        args = conn.fetch.await_args.args
        sql = args[0]
        assert "status" in sql
        assert "pending" in args

    async def test_returns_rows_as_dicts(self, fake_pool):
        pool, conn = fake_pool
        tid = str(uuid4())
        pool.fetch = AsyncMock(return_value=[{"task_id": tid, "topic": "t", "status": "pending", "quality_score": None, "created_at": None}])
        rows = await tasks_mcp.list_tasks(pool, status="all", limit=10)
        assert rows[0]["task_id"] == tid

    async def test_limit_capped_at_100(self, fake_pool):
        pool, conn = fake_pool
        await tasks_mcp.list_tasks(pool, status="all", limit=9999)
        args = conn.fetch.await_args.args
        # The limit value passed as a param must be ≤ 100
        assert 100 in args

    async def test_empty_returns_empty_list(self, fake_pool):
        pool, conn = fake_pool
        result = await tasks_mcp.list_tasks(pool, status="all", limit=10)
        assert result == []

    async def test_selects_qa_flagged_and_feedback(self, fake_pool):
        # Self-heal-before-paging: the operator queue surfaces the flag + findings.
        pool, conn = fake_pool
        await tasks_mcp.list_tasks(pool, status="awaiting_approval", limit=10)
        sql = conn.fetch.await_args.args[0]
        assert "qa_flagged" in sql
        assert "qa_feedback" in sql

    async def test_row_carries_qa_flagged(self, fake_pool):
        pool, _conn = fake_pool
        pool.fetch = AsyncMock(return_value=[{
            "task_id": "t1", "topic": "x", "status": "awaiting_approval",
            "quality_score": 79, "created_at": None,
            "qa_feedback": "Final score: 79/100 (REJECTED)", "qa_flagged": True,
        }])
        rows = await tasks_mcp.list_tasks(pool, status="awaiting_approval", limit=10)
        assert rows[0]["qa_flagged"] is True
        assert "qa_feedback" in rows[0]


# ---------------------------------------------------------------------------
# get_task_qa_feedback
# ---------------------------------------------------------------------------


class TestGetTaskQaFeedback:
    async def test_returns_feedback(self, fake_pool):
        pool, _conn = fake_pool
        pool.fetchrow = AsyncMock(
            return_value={"qa_feedback": "Final score: 79/100 (REJECTED)\n- ..."},
        )
        fb = await tasks_mcp.get_task_qa_feedback(pool, "t1")
        assert "79/100" in fb

    async def test_none_returns_empty(self, fake_pool):
        pool, _conn = fake_pool
        pool.fetchrow = AsyncMock(return_value=None)
        fb = await tasks_mcp.get_task_qa_feedback(pool, "t1")
        assert fb == ""


# ---------------------------------------------------------------------------
# resolve_task_prefix
# ---------------------------------------------------------------------------


class TestResolveTaskPrefix:
    async def test_full_uuid_skips_db(self, fake_pool):
        pool, conn = fake_pool
        full = "a" * 32
        result = await tasks_mcp.resolve_task_prefix(pool, full)
        conn.fetchrow.assert_not_called()
        assert result == full

    async def test_prefix_queries_db(self, fake_pool):
        pool, conn = fake_pool
        await tasks_mcp.resolve_task_prefix(pool, "abc123")
        conn.fetchrow.assert_called_once()

    async def test_prefix_resolves_to_full_uuid(self, fake_pool):
        pool, _conn = fake_pool
        full = str(uuid4())
        pool.fetchrow = AsyncMock(return_value={"task_id": full})
        result = await tasks_mcp.resolve_task_prefix(pool, "abc1")
        assert result == full

    async def test_no_match_returns_input(self, fake_pool):
        pool, _conn = fake_pool
        pool.fetchrow = AsyncMock(return_value=None)
        result = await tasks_mcp.resolve_task_prefix(pool, "nomatch")
        assert result == "nomatch"
