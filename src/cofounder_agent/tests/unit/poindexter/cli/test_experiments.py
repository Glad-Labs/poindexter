"""Click CLI tests for ``poindexter experiments`` (Glad-Labs/poindexter#27).

The CLI commands are thin Click wrappers — DSN resolution + pool /
asyncpg factory + ExperimentService delegation. We patch the pool
factory and the asyncpg layer so the suite exercises the Click glue
(option parsing, JSON mode, error handling, exit codes) without a
live DB.

For end-to-end DB exercise of the underlying service, see
``test_pipeline_experiment_hook.py``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.experiments import experiments_group


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def fake_dsn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")


def _async_conn(*, fetch_results=None, fetchrow_result=None,
                fetchval_result=None, execute_result="UPDATE 1") -> Any:
    """Build an async connection mock that supports the queries each
    CLI command emits. Defaults match a "no rows" path so individual
    tests only have to override the bit they care about.
    """
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=fetch_results or [])
    conn.fetchrow = AsyncMock(return_value=fetchrow_result)
    conn.fetchval = AsyncMock(return_value=fetchval_result)
    conn.execute = AsyncMock(return_value=execute_result)
    conn.close = AsyncMock(return_value=None)
    return conn


@pytest.fixture
def fake_asyncpg(fake_dsn):
    """Patch ``asyncpg.connect`` and ``asyncpg.create_pool`` so the CLI
    never reaches a real DB.
    """
    conn = _async_conn()

    async def _connect(_dsn):
        return conn

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.close = AsyncMock(return_value=None)

    async def _create_pool(_dsn, **_kwargs):
        return pool

    asyncpg = MagicMock()
    asyncpg.connect = _connect
    asyncpg.create_pool = _create_pool

    with patch.dict("sys.modules", {"asyncpg": asyncpg}):
        yield {"conn": conn, "pool": pool, "asyncpg": asyncpg}


# ---------------------------------------------------------------------------
# experiments list
# ---------------------------------------------------------------------------


class TestExperimentsList:
    def test_help(self, runner):
        result = runner.invoke(experiments_group, ["list", "--help"])
        assert result.exit_code == 0
        assert "list experiments" in result.output.lower()

    def test_empty_prints_no_experiments(self, runner, fake_asyncpg):
        result = runner.invoke(experiments_group, ["list"])
        assert result.exit_code == 0
        assert "no experiments" in result.output.lower()

    def test_renders_rows_in_table(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].fetch = AsyncMock(return_value=[
            {
                "key": "writer_test",
                "description": "test exp",
                "status": "running",
                "assignment_field": "task_id",
                "created_at": None,
                "started_at": None,
                "completed_at": None,
                "winner_variant": None,
                "assignments": 7,
            },
        ])
        result = runner.invoke(experiments_group, ["list"])
        assert result.exit_code == 0
        assert "writer_test" in result.output
        assert "running" in result.output

    def test_json_output(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].fetch = AsyncMock(return_value=[
            {
                "key": "writer_test",
                "description": "test exp",
                "status": "running",
                "assignment_field": "task_id",
                "created_at": None,
                "started_at": None,
                "completed_at": None,
                "winner_variant": None,
                "assignments": 7,
            },
        ])
        result = runner.invoke(experiments_group, ["list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["key"] == "writer_test"


# ---------------------------------------------------------------------------
# experiments create
# ---------------------------------------------------------------------------


class TestExperimentsCreate:
    def test_help_lists_required_options(self, runner):
        result = runner.invoke(experiments_group, ["create", "--help"])
        assert result.exit_code == 0
        assert "--key" in result.output
        assert "--variants" in result.output

    def test_invalid_variants_json_exits_one(self, runner, fake_asyncpg):
        result = runner.invoke(
            experiments_group,
            ["create", "--key", "x", "--description", "y", "--variants", "{not json"],
        )
        assert result.exit_code == 1
        assert "invalid --variants" in result.output.lower()

    def test_validation_error_exits_one(self, runner, fake_asyncpg):
        # Single variant — ExperimentService rejects (≥2 required).
        variants = json.dumps([{"key": "only", "weight": 100, "config": {}}])
        result = runner.invoke(
            experiments_group,
            ["create", "--key", "x", "--description", "y", "--variants", variants],
        )
        assert result.exit_code == 1
        assert "at least 2 variants" in result.output.lower()

    def test_valid_variants_created_in_draft(self, runner, fake_asyncpg):
        variants = json.dumps([
            {"key": "a", "weight": 50, "config": {}},
            {"key": "b", "weight": 50, "config": {"writer_model": "tiny:1b"}},
        ])
        # ExperimentService.create RETURNING id::text — wire the conn.fetchrow
        # to give back a deterministic row.
        fake_asyncpg["conn"].fetchrow = AsyncMock(return_value={"id": "uuid-123"})
        result = runner.invoke(
            experiments_group,
            ["create", "--key", "exp_x", "--description", "test",
             "--variants", variants],
        )
        assert result.exit_code == 0
        assert "Created experiment" in result.output
        assert "draft" in result.output


# ---------------------------------------------------------------------------
# experiments start / pause
# ---------------------------------------------------------------------------


class TestExperimentsStart:
    def test_no_draft_row_exits_one(self, runner, fake_asyncpg):
        # asyncpg returns "UPDATE 0" when no row matched.
        fake_asyncpg["conn"].execute = AsyncMock(return_value="UPDATE 0")
        result = runner.invoke(experiments_group, ["start", "missing"])
        assert result.exit_code == 1
        assert "no draft experiment" in result.output.lower()

    def test_running_flip_exits_zero(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].execute = AsyncMock(return_value="UPDATE 1")
        result = runner.invoke(experiments_group, ["start", "exp_x"])
        assert result.exit_code == 0
        assert "started" in result.output.lower()


class TestExperimentsPause:
    def test_no_running_row_exits_one(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].execute = AsyncMock(return_value="UPDATE 0")
        result = runner.invoke(experiments_group, ["pause", "missing"])
        assert result.exit_code == 1

    def test_pause_exits_zero(self, runner, fake_asyncpg):
        fake_asyncpg["conn"].execute = AsyncMock(return_value="UPDATE 1")
        result = runner.invoke(experiments_group, ["pause", "exp_x"])
        assert result.exit_code == 0
        assert "paused" in result.output.lower()


# ---------------------------------------------------------------------------
# experiments report
# ---------------------------------------------------------------------------


class TestExperimentsReport:
    def test_no_data_exits_one(self, runner, fake_asyncpg):
        # ExperimentService.summary returns {} when no assignments.
        # The report path uses ExperimentService directly through a pool;
        # easiest is to patch ExperimentService.summary.
        with patch(
            "services.experiment_service.ExperimentService.summary",
            AsyncMock(return_value={}),
        ):
            result = runner.invoke(experiments_group, ["report", "missing"])
        assert result.exit_code == 1
        assert "no data" in result.output.lower()

    def test_renders_report_text(self, runner, fake_asyncpg):
        with patch(
            "services.experiment_service.ExperimentService.summary",
            AsyncMock(return_value={
                "control": {"n": 5, "metrics": {"score_avg": 75.0}},
                "fast": {"n": 4, "metrics": {"score_avg": 82.5}},
            }),
        ):
            result = runner.invoke(experiments_group, ["report", "exp_x"])
        assert result.exit_code == 0
        assert "control" in result.output
        assert "fast" in result.output
        assert "n=5" in result.output
        assert "score_avg" in result.output

    def test_json_output(self, runner, fake_asyncpg):
        with patch(
            "services.experiment_service.ExperimentService.summary",
            AsyncMock(return_value={
                "control": {"n": 1, "metrics": {"score_avg": 10.0}},
            }),
        ):
            result = runner.invoke(experiments_group, ["report", "exp_x", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["control"]["n"] == 1


# ---------------------------------------------------------------------------
# experiments conclude
# ---------------------------------------------------------------------------


class TestExperimentsConclude:
    def test_validation_error_exits_one(self, runner, fake_asyncpg):
        # ExperimentService.conclude raises ValueError on unknown experiment.
        with patch(
            "services.experiment_service.ExperimentService.conclude",
            AsyncMock(side_effect=ValueError("unknown experiment 'x'")),
        ):
            result = runner.invoke(
                experiments_group,
                ["conclude", "x", "--winner", "a"],
            )
        assert result.exit_code == 1
        assert "unknown experiment" in result.output.lower()

    def test_success_exits_zero(self, runner, fake_asyncpg):
        with patch(
            "services.experiment_service.ExperimentService.conclude",
            AsyncMock(return_value=None),
        ):
            result = runner.invoke(
                experiments_group,
                ["conclude", "exp_x", "--winner", "control"],
            )
        assert result.exit_code == 0
        assert "concluded" in result.output.lower()
        assert "control" in result.output


# ---------------------------------------------------------------------------
# experiments assign
# ---------------------------------------------------------------------------


class TestExperimentsAssign:
    def test_no_assignment_exits_one(self, runner, fake_asyncpg):
        with patch(
            "services.experiment_service.ExperimentService.assign",
            AsyncMock(return_value=None),
        ):
            result = runner.invoke(
                experiments_group,
                ["assign", "subject-1", "experiment_x"],
            )
        assert result.exit_code == 1
        assert "no assignment" in result.output.lower()

    def test_assignment_succeeds(self, runner, fake_asyncpg):
        with patch(
            "services.experiment_service.ExperimentService.assign",
            AsyncMock(return_value="control"),
        ):
            result = runner.invoke(
                experiments_group,
                ["assign", "subject-1", "experiment_x"],
            )
        assert result.exit_code == 0
        assert "subject-1" in result.output
        assert "control" in result.output
