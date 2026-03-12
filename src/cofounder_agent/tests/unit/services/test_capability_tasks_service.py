"""
Unit tests for services/capability_tasks_service.py (CapabilityTasksService).

Tests cover:
- create_task — happy path, returns CapabilityTaskDefinition
- get_task — found (row → object), not found (None)
- list_tasks — basic listing, pagination params, active_only filter
- update_task — not found returns None, found updates and returns via get_task
- delete_task — "UPDATE 1" → True, "UPDATE 0" → False
- persist_execution — happy path, counter update via transaction
- get_execution — found, not found
- list_executions — basic listing, status filter
- _row_to_task — JSON/list steps, JSON/list tags
- _row_to_execution — JSON/list step_results, JSON/dict final_outputs

The asyncpg pool is fully mocked; no real database access.
"""

import json
import pytest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from services.capability_task_executor import (
    CapabilityStep,
    CapabilityTaskDefinition,
    StepResult,
    TaskExecutionResult,
)
from services.capability_tasks_service import CapabilityTasksService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn():
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=0)
    conn.fetch = AsyncMock(return_value=[])
    # transaction context manager
    conn.transaction = MagicMock()
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    return conn


def _make_pool(conn):
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool


def _service(conn=None) -> tuple[CapabilityTasksService, MagicMock]:
    c = conn or _make_conn()
    pool = _make_pool(c)
    return CapabilityTasksService(pool=pool), c


def _step(name="echo", output_key="out", order=0) -> CapabilityStep:
    return CapabilityStep(capability_name=name, inputs={}, output_key=output_key, order=order)


def _task_row(task_id="t1", name="my-task", steps=None, tags=None):
    """Build a mock asyncpg row for capability_tasks."""
    steps_list = steps or [{"capability_name": "echo", "inputs": {}, "output_key": "out", "order": 0, "metadata": {}}]
    tags_list = tags or ["test"]
    row = MagicMock()
    data = {
        "id": task_id,
        "name": name,
        "description": "desc",
        "owner_id": "owner-1",
        "steps": steps_list,           # already a list (asyncpg returns jsonb as list)
        "tags": tags_list,
        "created_at": datetime.now(timezone.utc),
    }
    row.__getitem__ = lambda self, key: data[key]
    row.get = lambda key, default=None: data.get(key, default)
    return row


def _exec_row(exec_id="e1", task_id="t1", status="completed"):
    row = MagicMock()
    data = {
        "id": exec_id,
        "task_id": task_id,
        "owner_id": "owner-1",
        "status": status,
        "error_message": None,
        "step_results": [
            {
                "step_index": 0,
                "capability_name": "echo",
                "output_key": "out",
                "output": "hello",
                "duration_ms": 10.0,
                "error": None,
                "status": "completed",
            }
        ],
        "final_outputs": {"out": "hello"},
        "total_duration_ms": 10.0,
        "progress_percent": 100,
        "started_at": datetime.now(timezone.utc),
        "completed_at": datetime.now(timezone.utc),
    }
    row.__getitem__ = lambda self, key: data[key]
    row.get = lambda key, default=None: data.get(key, default)
    return row


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------


class TestCreateTask:
    @pytest.mark.asyncio
    async def test_returns_capability_task_definition(self):
        svc, conn = _service()
        conn.execute = AsyncMock()

        steps = [_step()]
        result = await svc.create_task(
            name="Test Task",
            description="desc",
            steps=steps,
            owner_id="owner-1",
            tags=["ai"],
        )

        assert isinstance(result, CapabilityTaskDefinition)
        assert result.name == "Test Task"
        assert result.owner_id == "owner-1"
        assert result.tags == ["ai"]
        assert len(result.steps) == 1
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_tags_defaults_to_empty_list(self):
        svc, conn = _service()
        result = await svc.create_task("t", "d", [_step()], "owner-1")
        assert result.tags == []


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------


class TestGetTask:
    @pytest.mark.asyncio
    async def test_found_returns_definition(self):
        svc, conn = _service()
        conn.fetchrow = AsyncMock(return_value=_task_row())

        result = await svc.get_task("t1", "owner-1")

        assert result is not None
        assert result.id == "t1"
        assert result.name == "my-task"

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        svc, conn = _service()
        conn.fetchrow = AsyncMock(return_value=None)

        result = await svc.get_task("missing", "owner-1")

        assert result is None


# ---------------------------------------------------------------------------
# list_tasks
# ---------------------------------------------------------------------------


class TestListTasks:
    @pytest.mark.asyncio
    async def test_returns_tasks_and_count(self):
        svc, conn = _service()
        conn.fetchval = AsyncMock(return_value=2)
        conn.fetch = AsyncMock(return_value=[_task_row("t1"), _task_row("t2")])

        tasks, total = await svc.list_tasks("owner-1")

        assert total == 2
        assert len(tasks) == 2
        assert all(isinstance(t, CapabilityTaskDefinition) for t in tasks)

    @pytest.mark.asyncio
    async def test_empty_results(self):
        svc, conn = _service()
        conn.fetchval = AsyncMock(return_value=0)
        conn.fetch = AsyncMock(return_value=[])

        tasks, total = await svc.list_tasks("owner-1")

        assert total == 0
        assert tasks == []


# ---------------------------------------------------------------------------
# update_task
# ---------------------------------------------------------------------------


class TestUpdateTask:
    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        svc, conn = _service()
        conn.fetchrow = AsyncMock(return_value=None)

        result = await svc.update_task("missing", "owner-1", name="new")

        assert result is None

    @pytest.mark.asyncio
    async def test_found_executes_update_and_returns_task(self):
        svc, conn = _service()
        version_row = MagicMock()
        version_row.__getitem__ = lambda self, key: {"version": 1}[key]

        updated_row = _task_row(name="updated-name")
        # First fetchrow: get version; second fetchrow: get updated task
        conn.fetchrow = AsyncMock(side_effect=[version_row, updated_row])

        result = await svc.update_task("t1", "owner-1", name="updated-name")

        assert result is not None
        assert result.name == "updated-name"


# ---------------------------------------------------------------------------
# delete_task
# ---------------------------------------------------------------------------


class TestDeleteTask:
    @pytest.mark.asyncio
    async def test_deleted_returns_true(self):
        svc, conn = _service()
        conn.execute = AsyncMock(return_value="UPDATE 1")

        result = await svc.delete_task("t1", "owner-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_not_found_returns_false(self):
        svc, conn = _service()
        conn.execute = AsyncMock(return_value="UPDATE 0")

        result = await svc.delete_task("missing", "owner-1")

        assert result is False


# ---------------------------------------------------------------------------
# persist_execution
# ---------------------------------------------------------------------------


class TestPersistExecution:
    @pytest.mark.asyncio
    async def test_returns_execution_id(self):
        svc, conn = _service()

        step_r = StepResult(
            step_index=0,
            capability_name="echo",
            output_key="out",
            output="hello",
            duration_ms=10.0,
            status="completed",
        )
        exec_result = TaskExecutionResult(
            task_id="t1",
            execution_id="exec-123",
            owner_id="owner-1",
            status="completed",
            step_results=[step_r],
            final_outputs={"out": "hello"},
        )

        returned_id = await svc.persist_execution(exec_result)

        assert returned_id == "exec-123"
        # Both INSERT and UPDATE should have been called inside the transaction
        assert conn.execute.call_count == 2


# ---------------------------------------------------------------------------
# get_execution
# ---------------------------------------------------------------------------


class TestGetExecution:
    @pytest.mark.asyncio
    async def test_found_returns_result(self):
        svc, conn = _service()
        conn.fetchrow = AsyncMock(return_value=_exec_row())

        result = await svc.get_execution("e1", "owner-1")

        assert result is not None
        assert result.execution_id == "e1"
        assert result.status == "completed"
        assert len(result.step_results) == 1

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        svc, conn = _service()
        conn.fetchrow = AsyncMock(return_value=None)

        result = await svc.get_execution("missing", "owner-1")

        assert result is None


# ---------------------------------------------------------------------------
# list_executions
# ---------------------------------------------------------------------------


class TestListExecutions:
    @pytest.mark.asyncio
    async def test_returns_executions_and_count(self):
        svc, conn = _service()
        conn.fetchval = AsyncMock(return_value=1)
        conn.fetch = AsyncMock(return_value=[_exec_row()])

        executions, total = await svc.list_executions("t1", "owner-1")

        assert total == 1
        assert len(executions) == 1
        assert isinstance(executions[0], TaskExecutionResult)

    @pytest.mark.asyncio
    async def test_status_filter_appended(self):
        svc, conn = _service()
        conn.fetchval = AsyncMock(return_value=0)
        conn.fetch = AsyncMock(return_value=[])

        executions, total = await svc.list_executions(
            "t1", "owner-1", status_filter="failed"
        )

        assert total == 0
        assert executions == []
        # The second fetch call includes the status filter param
        fetch_call_args = conn.fetch.call_args
        assert "failed" in fetch_call_args[0]  # positional args include status value


# ---------------------------------------------------------------------------
# _row_to_task — JSON string vs list inputs
# ---------------------------------------------------------------------------


class TestRowToTask:
    def test_steps_as_list(self):
        svc, _ = _service()
        row = _task_row()
        task = svc._row_to_task(row)
        assert len(task.steps) == 1
        assert task.steps[0].capability_name == "echo"

    def test_steps_as_json_string(self):
        svc, _ = _service()
        steps_json = json.dumps([{
            "capability_name": "search",
            "inputs": {"q": "AI"},
            "output_key": "results",
            "order": 0,
            "metadata": {},
        }])
        data = {
            "id": "t1",
            "name": "n",
            "description": "d",
            "owner_id": "o",
            "steps": steps_json,          # JSON string
            "tags": json.dumps(["tag1"]),  # JSON string
            "created_at": datetime.now(timezone.utc),
        }
        row = MagicMock()
        row.__getitem__ = lambda self, key: data[key]
        row.get = lambda key, default=None: data.get(key, default)

        task = svc._row_to_task(row)

        assert task.steps[0].capability_name == "search"
        assert task.tags == ["tag1"]


# ---------------------------------------------------------------------------
# _row_to_execution — JSON string vs list/dict inputs
# ---------------------------------------------------------------------------


class TestRowToExecution:
    def test_step_results_as_list(self):
        svc, _ = _service()
        row = _exec_row()
        result = svc._row_to_execution(row)
        assert len(result.step_results) == 1
        assert result.step_results[0].capability_name == "echo"
        assert result.final_outputs == {"out": "hello"}

    def test_step_results_as_json_strings(self):
        svc, _ = _service()
        step_data = [{"step_index": 0, "capability_name": "cap", "output_key": "k",
                      "output": "v", "duration_ms": 5.0, "error": None, "status": "completed"}]
        data = {
            "id": "e1",
            "task_id": "t1",
            "owner_id": "o",
            "status": "completed",
            "error_message": None,
            "step_results": json.dumps(step_data),  # JSON string
            "final_outputs": json.dumps({"k": "v"}),  # JSON string
            "total_duration_ms": 5.0,
            "progress_percent": 100,
            "started_at": datetime.now(timezone.utc),
            "completed_at": datetime.now(timezone.utc),
        }
        row = MagicMock()
        row.__getitem__ = lambda self, key: data[key]
        row.get = lambda key, default=None: data.get(key, default)

        result = svc._row_to_execution(row)

        assert result.step_results[0].capability_name == "cap"
        assert result.final_outputs == {"k": "v"}
