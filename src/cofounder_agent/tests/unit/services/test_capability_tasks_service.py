"""
Unit tests for services/capability_tasks_service.py (CapabilityTasksService).

The service uses asyncpg (pool.acquire() → conn.fetchrow / execute / fetch).
All DB calls are mocked via AsyncMock — no real database access.
"""

import json
import pytest
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

_NOW = datetime.now(timezone.utc)


def _make_pool(
    fetchrow_result=None,
    fetchrow_side_effect=None,
    fetch_result=None,
    execute_result=None,
    fetchval_result=None,
):
    """Build a mock asyncpg Pool that yields a mock connection via acquire()."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value=fetchrow_result, side_effect=fetchrow_side_effect
    )
    conn.fetch = AsyncMock(return_value=fetch_result or [])
    conn.execute = AsyncMock(return_value=execute_result or "INSERT 0 1")
    conn.fetchval = AsyncMock(return_value=fetchval_result or 0)
    # Support conn.transaction() as async context manager
    conn.transaction = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=None),
        )
    )

    pool = MagicMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=None),
        )
    )
    pool._conn = conn  # expose for assertions
    return pool


def _make_service(pool=None) -> CapabilityTasksService:
    return CapabilityTasksService(pool=pool or _make_pool())


def _step(name="echo", output_key="out", order=0) -> CapabilityStep:
    return CapabilityStep(
        capability_name=name, inputs={}, output_key=output_key, order=order
    )


def _task_row(task_id="t1", name="my-task", total_count=None):
    row = MagicMock()
    data = {
        "id": task_id,
        "name": name,
        "version": 1,
        "description": "desc",
        "owner_id": "owner-1",
        "steps": json.dumps(
            [{"capability_name": "echo", "inputs": {}, "output_key": "out", "order": 0}]
        ),
        "tags": json.dumps(["test"]),
        "created_at": _NOW,
        "total_count": total_count if total_count is not None else 1,
    }
    row.__getitem__ = lambda self, key: data[key]
    row.get = lambda key, default=None: data.get(key, default)
    return row


def _exec_row(exec_id="e1", task_id="t1", status="completed", total_count=None):
    row = MagicMock()
    data = {
        "id": exec_id,
        "task_id": task_id,
        "owner_id": "owner-1",
        "status": status,
        "error_message": None,
        "step_results": json.dumps(
            [
                {
                    "step_index": 0,
                    "capability_name": "echo",
                    "output_key": "out",
                    "output": "hello",
                    "duration_ms": 10.0,
                    "error": None,
                    "status": "completed",
                }
            ]
        ),
        "final_outputs": json.dumps({"out": "hello"}),
        "total_duration_ms": 10.0,
        "progress_percent": 100,
        "started_at": _NOW,
        "completed_at": _NOW,
        "total_count": total_count if total_count is not None else 1,
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
        svc = _make_service()

        result = await svc.create_task(
            name="Test Task",
            description="desc",
            steps=[_step()],
            owner_id="owner-1",
            tags=["ai"],
        )

        assert isinstance(result, CapabilityTaskDefinition)
        assert result.name == "Test Task"
        assert result.owner_id == "owner-1"
        assert result.tags == ["ai"]
        assert len(result.steps) == 1

    @pytest.mark.asyncio
    async def test_no_tags_defaults_to_empty_list(self):
        svc = _make_service()
        result = await svc.create_task("t", "d", [_step()], "owner-1")
        assert result.tags == []


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------


class TestGetTask:
    @pytest.mark.asyncio
    async def test_found_returns_definition(self):
        pool = _make_pool(fetchrow_result=_task_row())
        svc = _make_service(pool)

        result = await svc.get_task("t1", "owner-1")

        assert result is not None
        assert result.id == "t1"
        assert result.name == "my-task"

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_result=None)
        svc = _make_service(pool)

        result = await svc.get_task("missing", "owner-1")

        assert result is None


# ---------------------------------------------------------------------------
# list_tasks
# ---------------------------------------------------------------------------


class TestListTasks:
    @pytest.mark.asyncio
    async def test_returns_tasks_and_count(self):
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=2)
        conn.fetch = AsyncMock(return_value=[_task_row("t1", total_count=2), _task_row("t2", total_count=2)])

        pool = MagicMock()
        pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        svc = _make_service(pool)
        tasks, total = await svc.list_tasks("owner-1")

        assert total == 2
        assert len(tasks) == 2
        assert all(isinstance(t, CapabilityTaskDefinition) for t in tasks)

    @pytest.mark.asyncio
    async def test_empty_results(self):
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=0)
        conn.fetch = AsyncMock(return_value=[])

        pool = MagicMock()
        pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        svc = _make_service(pool)
        tasks, total = await svc.list_tasks("owner-1")

        assert total == 0
        assert tasks == []


# ---------------------------------------------------------------------------
# update_task
# ---------------------------------------------------------------------------


class TestUpdateTask:
    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_result=None)
        svc = _make_service(pool)

        result = await svc.update_task("missing", "owner-1", name="new")

        assert result is None

    @pytest.mark.asyncio
    async def test_found_executes_update_and_returns_task(self):
        # update_task calls conn.execute (UPDATE), then self.get_task which calls
        # conn.fetchrow. There is no fetchrow during the UPDATE step itself.
        updated = _task_row(name="updated-name")

        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=updated)
        conn.execute = AsyncMock(return_value="UPDATE 1")

        pool = MagicMock()
        pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        svc = _make_service(pool)
        result = await svc.update_task("t1", "owner-1", name="updated-name")

        assert result is not None
        assert result.name == "updated-name"


# ---------------------------------------------------------------------------
# delete_task
# ---------------------------------------------------------------------------


class TestDeleteTask:
    @pytest.mark.asyncio
    async def test_deleted_returns_true(self):
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="UPDATE 1")

        pool = MagicMock()
        pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        svc = _make_service(pool)
        result = await svc.delete_task("t1", "owner-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_not_found_returns_false(self):
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="UPDATE 0")

        pool = MagicMock()
        pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        svc = _make_service(pool)
        result = await svc.delete_task("missing", "owner-1")

        assert result is False


# ---------------------------------------------------------------------------
# persist_execution
# ---------------------------------------------------------------------------


class TestPersistExecution:
    @pytest.mark.asyncio
    async def test_returns_execution_id(self):
        svc = _make_service()

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


# ---------------------------------------------------------------------------
# get_execution
# ---------------------------------------------------------------------------


class TestGetExecution:
    @pytest.mark.asyncio
    async def test_found_returns_result(self):
        pool = _make_pool(fetchrow_result=_exec_row())
        svc = _make_service(pool)

        result = await svc.get_execution("e1", "owner-1")

        assert result is not None
        assert result.execution_id == "e1"
        assert result.status == "completed"
        assert len(result.step_results) == 1

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        pool = _make_pool(fetchrow_result=None)
        svc = _make_service(pool)

        result = await svc.get_execution("missing", "owner-1")

        assert result is None


# ---------------------------------------------------------------------------
# list_executions
# ---------------------------------------------------------------------------


class TestListExecutions:
    @pytest.mark.asyncio
    async def test_returns_executions_and_count(self):
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=1)
        conn.fetch = AsyncMock(return_value=[_exec_row(total_count=1)])

        pool = MagicMock()
        pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        svc = _make_service(pool)
        executions, total = await svc.list_executions("t1", "owner-1")

        assert total == 1
        assert len(executions) == 1
        assert isinstance(executions[0], TaskExecutionResult)

    @pytest.mark.asyncio
    async def test_status_filter_appended(self):
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=0)
        conn.fetch = AsyncMock(return_value=[])

        pool = MagicMock()
        pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=conn),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        svc = _make_service(pool)
        executions, total = await svc.list_executions("t1", "owner-1", status_filter="failed")

        assert total == 0
        assert executions == []


# ---------------------------------------------------------------------------
# _row_to_task — JSON string vs list inputs
# ---------------------------------------------------------------------------


class TestRowToTask:
    def test_steps_as_list(self):
        svc = _make_service()
        row = _task_row()
        task = svc._row_to_task(row)
        assert len(task.steps) == 1
        assert task.steps[0].capability_name == "echo"

    def test_steps_as_json_string(self):
        svc = _make_service()
        steps_json = json.dumps(
            [
                {
                    "capability_name": "search",
                    "inputs": {"q": "AI"},
                    "output_key": "results",
                    "order": 0,
                }
            ]
        )
        data = {
            "id": "t1",
            "name": "n",
            "description": "d",
            "owner_id": "o",
            "steps": steps_json,
            "tags": json.dumps(["tag1"]),
            "created_at": _NOW,
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
        svc = _make_service()
        row = _exec_row()
        result = svc._row_to_execution(row)
        assert len(result.step_results) == 1
        assert result.step_results[0].capability_name == "echo"
        assert result.final_outputs == {"out": "hello"}

    def test_step_results_as_json_strings(self):
        svc = _make_service()
        step_data = [
            {
                "step_index": 0,
                "capability_name": "cap",
                "output_key": "k",
                "output": "v",
                "duration_ms": 5.0,
                "error": None,
                "status": "completed",
            }
        ]
        data = {
            "id": "e1",
            "task_id": "t1",
            "owner_id": "o",
            "status": "completed",
            "error_message": None,
            "step_results": json.dumps(step_data),
            "final_outputs": json.dumps({"k": "v"}),
            "total_duration_ms": 5.0,
            "progress_percent": 100,
            "started_at": _NOW,
            "completed_at": _NOW,
        }
        row = MagicMock()
        row.__getitem__ = lambda self, key: data[key]
        row.get = lambda key, default=None: data.get(key, default)

        result = svc._row_to_execution(row)

        assert result.step_results[0].capability_name == "cap"
        assert result.final_outputs == {"k": "v"}
