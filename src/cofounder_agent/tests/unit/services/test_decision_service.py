"""Unit tests for services/decision_service.py.

The decision service is a thin wrapper around four async pool methods:
log_decision, record_outcome, get_past_decisions, get_decision_stats.
Tests use a mocked asyncpg pool — no DB.
"""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.decision_service import (
    get_decision_stats,
    get_past_decisions,
    log_decision,
    record_outcome,
)

# ---------------------------------------------------------------------------
# log_decision
# ---------------------------------------------------------------------------


class TestLogDecision:
    @pytest.mark.asyncio
    async def test_returns_decision_id_on_success(self):
        pool = AsyncMock()
        new_id = uuid4()
        pool.fetchrow = AsyncMock(return_value={"id": new_id})

        result = await log_decision(
            pool=pool,
            decision_type="image_source",
            decision_point="image_decision_agent",
            context={"section": "Schema"},
            decision={"source": "sdxl", "style": "blueprint"},
            task_id="task-1",
            post_id="post-1",
            model_used="qwen3:8b",
            duration_ms=1200,
            cost_usd=0.0001,
        )

        assert result == str(new_id)
        pool.fetchrow.assert_awaited_once()
        args = pool.fetchrow.await_args.args
        # SQL is args[0]; positional values follow
        assert "INSERT INTO decision_log" in args[0]
        assert args[1] == "image_source"
        assert args[2] == "image_decision_agent"
        # context is JSON-serialized
        assert json.loads(args[3]) == {"section": "Schema"}
        assert json.loads(args[4]) == {"source": "sdxl", "style": "blueprint"}
        assert args[5] == "task-1"
        assert args[6] == "post-1"
        assert args[7] == "qwen3:8b"
        assert args[8] == 1200
        assert args[9] == 0.0001

    @pytest.mark.asyncio
    async def test_returns_none_on_db_error(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=RuntimeError("db down"))

        result = await log_decision(
            pool=pool,
            decision_type="x",
            decision_point="y",
            context={},
            decision={},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_optional_params_default_to_none(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"id": uuid4()})

        await log_decision(
            pool=pool,
            decision_type="x",
            decision_point="y",
            context={},
            decision={},
        )
        args = pool.fetchrow.await_args.args
        assert args[5] is None  # task_id
        assert args[6] is None  # post_id
        assert args[7] is None  # model_used
        assert args[8] is None  # duration_ms
        assert args[9] == 0.0  # cost_usd default


# ---------------------------------------------------------------------------
# record_outcome
# ---------------------------------------------------------------------------


class TestRecordOutcome:
    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        pool = AsyncMock()
        pool.execute = AsyncMock()
        result = await record_outcome(
            pool=pool,
            decision_id="decision-uuid",
            outcome={"success": True, "user_approved": True},
        )
        assert result is True
        pool.execute.assert_awaited_once()
        args = pool.execute.await_args.args
        assert "UPDATE decision_log" in args[0]
        # outcome is JSON-serialized
        assert json.loads(args[1]) == {"success": True, "user_approved": True}
        assert args[2] == "decision-uuid"

    @pytest.mark.asyncio
    async def test_returns_false_on_db_error(self):
        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=RuntimeError("constraint violation"))
        result = await record_outcome(pool, "id", {"success": False})
        assert result is False


# ---------------------------------------------------------------------------
# get_past_decisions
# ---------------------------------------------------------------------------


class TestGetPastDecisions:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"id": "d1", "decision_type": "image_source", "decision_point": "agent",
             "context": "{}", "decision": "{}", "outcome": None, "task_id": None,
             "model_used": "x", "duration_ms": 100, "cost_usd": 0.0, "created_at": None},
            {"id": "d2", "decision_type": "image_source", "decision_point": "agent",
             "context": "{}", "decision": "{}", "outcome": "{}", "task_id": "t1",
             "model_used": "x", "duration_ms": 200, "cost_usd": 0.0, "created_at": None},
        ])
        result = await get_past_decisions(pool, "image_source", limit=50)
        assert len(result) == 2
        assert result[0]["id"] == "d1"
        assert result[1]["id"] == "d2"

    @pytest.mark.asyncio
    async def test_default_limit_used_in_query(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        await get_past_decisions(pool, "x")  # default limit=50
        args = pool.fetch.await_args.args
        # Last positional arg is limit
        assert args[-1] == 50

    @pytest.mark.asyncio
    async def test_with_outcomes_only_adds_filter(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        await get_past_decisions(pool, "x", with_outcomes_only=True)
        args = pool.fetch.await_args.args
        assert "outcome IS NOT NULL" in args[0]

    @pytest.mark.asyncio
    async def test_task_id_filter_adds_param(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        await get_past_decisions(pool, "x", task_id="task-uuid", limit=10)
        args = pool.fetch.await_args.args
        assert "task_id" in args[0]
        # decision_type, task_id, limit
        assert args[1] == "x"
        assert args[2] == "task-uuid"
        assert args[3] == 10

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_db_error(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=RuntimeError("query failed"))
        result = await get_past_decisions(pool, "x")
        assert result == []

    @pytest.mark.asyncio
    async def test_combined_filters(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        await get_past_decisions(
            pool, "image_source",
            limit=25, with_outcomes_only=True, task_id="t1",
        )
        args = pool.fetch.await_args.args
        sql = args[0]
        assert "decision_type = $1" in sql
        assert "outcome IS NOT NULL" in sql
        assert "task_id = $2" in sql


# ---------------------------------------------------------------------------
# get_decision_stats
# ---------------------------------------------------------------------------


class TestGetDecisionStats:
    @pytest.mark.asyncio
    async def test_returns_dict_on_success(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={
            "total_decisions": 100,
            "decisions_with_outcomes": 80,
            "avg_duration_ms": 1500.0,
            "total_cost_usd": 0.05,
            "earliest": None,
            "latest": None,
        })
        result = await get_decision_stats(pool, "image_source", days=30)
        assert result["total_decisions"] == 100
        assert result["decisions_with_outcomes"] == 80
        assert result["avg_duration_ms"] == 1500.0

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_no_row(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        result = await get_decision_stats(pool, "x")
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_dict_on_db_error(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=RuntimeError("query failed"))
        result = await get_decision_stats(pool, "x")
        assert result == {}

    @pytest.mark.asyncio
    async def test_days_default_is_30(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={})
        await get_decision_stats(pool, "x")
        args = pool.fetchrow.await_args.args
        # decision_type, then days as a string for the interval
        assert args[1] == "x"
        assert args[2] == "30"

    @pytest.mark.asyncio
    async def test_custom_days(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={})
        await get_decision_stats(pool, "x", days=7)
        args = pool.fetchrow.await_args.args
        assert args[2] == "7"
