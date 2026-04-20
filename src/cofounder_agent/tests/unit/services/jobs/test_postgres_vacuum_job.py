"""Unit tests for ``services/jobs/postgres_vacuum.py``.

No real DB — asyncpg pool is mocked. Focus: identifier safety, config
validation, per-table success/failure aggregation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from typing import Any

import pytest

from services.jobs.postgres_vacuum import DEFAULT_TABLES, PostgresVacuumJob


def _make_mock_pool(execute_side_effect=None) -> Any:
    """Return a pool whose acquire() yields a conn with execute stubbed."""
    conn = AsyncMock()
    if execute_side_effect is not None:
        conn.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        conn.execute = AsyncMock(return_value="VACUUM")

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


class TestContract:
    def test_has_required_attrs(self):
        job = PostgresVacuumJob()
        assert job.name == "postgres_vacuum"
        assert job.schedule == "every 6 hours"
        assert job.idempotent is True


class TestDefaultTables:
    def test_covers_high_churn_set(self):
        # At minimum we want the append-only and status-churning tables
        # to be on the default list.
        required = {"embeddings", "audit_log", "cost_logs", "pipeline_tasks"}
        assert required.issubset(set(DEFAULT_TABLES))


class TestRun:
    @pytest.mark.asyncio
    async def test_default_run_vacuums_all_default_tables(self):
        pool, conn = _make_mock_pool()
        job = PostgresVacuumJob()
        result = await job.run(pool, {})

        assert result.ok is True
        assert result.changes_made == len(DEFAULT_TABLES)
        # statement_timeout set + one VACUUM per table
        assert conn.execute.call_count == len(DEFAULT_TABLES) + 1
        # Each table appears in the detail string.
        for t in DEFAULT_TABLES:
            assert t in result.detail

    @pytest.mark.asyncio
    async def test_custom_tables(self):
        pool, conn = _make_mock_pool()
        job = PostgresVacuumJob()
        result = await job.run(pool, {"tables": ["posts", "tasks"]})
        assert result.ok is True
        assert result.changes_made == 2
        # Only the 2 VACUUMs + 1 SET statement_timeout
        assert conn.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_rejects_unsafe_table_name(self):
        pool, _ = _make_mock_pool()
        job = PostgresVacuumJob()
        # Semicolon, quote, uppercase — all rejected.
        for bad in ["posts; DROP TABLE users", 'posts"abc', "Posts"]:
            result = await job.run(pool, {"tables": [bad]})
            assert result.ok is False
            assert "unsafe table name" in result.detail

    @pytest.mark.asyncio
    async def test_rejects_non_list_tables_config(self):
        pool, _ = _make_mock_pool()
        job = PostgresVacuumJob()
        result = await job.run(pool, {"tables": "posts"})
        assert result.ok is False
        assert "must be a list" in result.detail

    @pytest.mark.asyncio
    async def test_per_table_failure_does_not_abort(self):
        # Simulate first VACUUM failing, rest succeeding.
        call_count = {"n": 0}

        async def side_effect(sql: str):
            call_count["n"] += 1
            if call_count["n"] == 2:  # Second call = first actual VACUUM
                raise RuntimeError("lock timeout")
            return "VACUUM"

        pool, conn = _make_mock_pool(execute_side_effect=side_effect)
        job = PostgresVacuumJob()
        result = await job.run(pool, {"tables": ["a", "b", "c"]})

        # 1 success out of 3 → job reports not-ok but does run the others.
        assert result.ok is False
        assert result.changes_made == 2
        assert "a=FAIL" in result.detail
        assert "b=ok" in result.detail
        assert "c=ok" in result.detail

    @pytest.mark.asyncio
    async def test_statement_timeout_applied(self):
        pool, conn = _make_mock_pool()
        job = PostgresVacuumJob()
        await job.run(pool, {
            "tables": ["posts"],
            "statement_timeout_seconds": 60,
        })
        # First call should be SET statement_timeout = 60000 (ms)
        first_sql = conn.execute.call_args_list[0][0][0]
        assert "SET statement_timeout" in first_sql
        assert "60000" in first_sql

    @pytest.mark.asyncio
    async def test_all_failures_yield_not_ok(self):
        async def side_effect(sql: str):
            if sql.startswith("VACUUM"):
                raise RuntimeError("permission denied")
            return "SET"

        pool, conn = _make_mock_pool(execute_side_effect=side_effect)
        job = PostgresVacuumJob()
        result = await job.run(pool, {"tables": ["posts"]})
        assert result.ok is False
        assert result.changes_made == 0
        assert "posts=FAIL" in result.detail
