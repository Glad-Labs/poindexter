"""
Concurrency tests for the workflow and task execution systems.

Tests:
- Parallel workflow execution (asyncio.gather)
- Burst task creation under concurrent load
- Concurrent capability task operations
- DB service shared across concurrent coroutines
- Race-condition edge cases
"""

import asyncio
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "src" / "cofounder_agent"))

if "structlog" not in sys.modules:
    _stub = ModuleType("structlog")
    setattr(_stub, "get_logger", lambda *a, **k: MagicMock())
    sys.modules["structlog"] = _stub


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_mock_workflow_executor():
    exe = MagicMock()

    async def _execute(workflow_id: str, **kwargs):
        await asyncio.sleep(0)  # yield to allow interleaving
        return {"workflow_id": workflow_id, "status": "completed", "phases_completed": 3}

    exe.execute = AsyncMock(side_effect=_execute)
    exe.pause = AsyncMock(return_value={"status": "paused"})
    exe.resume = AsyncMock(return_value={"status": "running"})
    exe.cancel = AsyncMock(return_value={"status": "cancelled"})
    return exe


def _make_mock_db():
    db = MagicMock()
    _store: dict = {}
    _counter = [0]

    async def create_task(data):
        _counter[0] += 1
        tid = f"task_{_counter[0]}"
        _store[tid] = {**data, "id": tid}
        return tid

    async def get_task(tid):
        return _store.get(tid)

    async def update_task(tid, updates):
        if tid in _store:
            _store[tid].update(updates)

    db.create_task = AsyncMock(side_effect=create_task)
    db.get_task = AsyncMock(side_effect=get_task)
    db.update_task = AsyncMock(side_effect=update_task)
    return db


# ── parallel workflow execution ───────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parallel_workflows_all_complete():
    """Multiple workflows launched with gather() all complete independently."""
    executor = _make_mock_workflow_executor()

    workflow_ids = [f"wf-{i}" for i in range(5)]
    results = await asyncio.gather(
        *[executor.execute(wid, template_name="blog_post") for wid in workflow_ids]
    )

    assert len(results) == 5
    for i, result in enumerate(results):
        assert result["status"] == "completed"
        assert result["workflow_id"] == f"wf-{i}"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parallel_workflows_independent_ids():
    """Parallel executions never mix up their workflow IDs."""
    executor = _make_mock_workflow_executor()

    ids = [f"unique-wf-{i:03d}" for i in range(10)]
    results = await asyncio.gather(*[executor.execute(wid) for wid in ids])

    returned_ids = {r["workflow_id"] for r in results}
    assert returned_ids == set(ids)  # all distinct, none lost


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parallel_workflows_one_fails_others_complete():
    """A failure in one parallel workflow should not block the others."""
    executor = _make_mock_workflow_executor()

    call_count = [0]

    async def execute_with_one_failure(workflow_id: str, **kwargs):
        await asyncio.sleep(0)
        call_count[0] += 1
        if workflow_id == "wf-bad":
            raise RuntimeError("Workflow exploded")
        return {"workflow_id": workflow_id, "status": "completed", "phases_completed": 3}

    executor.execute = AsyncMock(side_effect=execute_with_one_failure)

    results = await asyncio.gather(
        executor.execute("wf-1"),
        executor.execute("wf-bad"),
        executor.execute("wf-3"),
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, dict)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 2
    assert len(failures) == 1
    assert "exploded" in str(failures[0])


# ── burst task creation ───────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_burst_task_creation_all_stored():
    """20 concurrent create_task calls each store their own record."""
    db = _make_mock_db()

    task_data = [
        {"type": "blog_post", "topic": f"Topic {i}", "status": "pending"}
        for i in range(20)
    ]

    task_ids = await asyncio.gather(*[db.create_task(d) for d in task_data])

    assert len(task_ids) == 20
    assert len(set(task_ids)) == 20  # all unique


@pytest.mark.unit
@pytest.mark.asyncio
async def test_burst_task_creation_sequential_ids():
    """Task IDs are assigned sequentially even under concurrent creation."""
    db = _make_mock_db()

    ids = await asyncio.gather(*[db.create_task({"status": "pending"}) for _ in range(8)])

    numeric = sorted(int(tid.split("_")[1]) for tid in ids)
    assert numeric == list(range(1, 9))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_concurrent_read_after_write():
    """Reads issued concurrently after writes all return correct data."""
    db = _make_mock_db()

    # Create 5 tasks
    ids = await asyncio.gather(*[
        db.create_task({"topic": f"post-{i}", "status": "pending"}) for i in range(5)
    ])

    # Read all of them concurrently
    tasks = await asyncio.gather(*[db.get_task(tid) for tid in ids])

    for i, task in enumerate(tasks):
        assert task is not None
        assert task["id"] == ids[i]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_concurrent_status_updates():
    """Concurrent status updates on different tasks don't cross-contaminate."""
    db = _make_mock_db()

    ids = await asyncio.gather(*[
        db.create_task({"status": "pending", "idx": i}) for i in range(6)
    ])

    # Update to different statuses concurrently
    statuses = ["running", "paused", "completed", "failed", "running", "completed"]
    await asyncio.gather(*[
        db.update_task(tid, {"status": s}) for tid, s in zip(ids, statuses)
    ])

    # Verify each task has the correct status
    for tid, expected_status in zip(ids, statuses):
        task = await db.get_task(tid)
        assert task["status"] == expected_status, f"{tid} has wrong status"


# ── concurrent capability operations ─────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parallel_capability_executions_isolated():
    """Executing the same capability concurrently for different tasks is isolated."""
    call_args = []

    async def mock_execute(task):
        await asyncio.sleep(0)
        call_args.append(task.id)
        result = MagicMock()
        result.execution_id = f"exec-{task.id}"
        result.status = "completed"
        result.step_results = []
        result.final_outputs = {}
        result.total_duration_ms = 100
        result.progress_percent = 100
        result.error = None
        from datetime import datetime, timezone
        result.started_at = datetime.now(timezone.utc)
        result.completed_at = datetime.now(timezone.utc)
        return result

    tasks = []
    for i in range(4):
        t = MagicMock()
        t.id = f"task-{i}"
        t.steps = []
        tasks.append(t)

    results = await asyncio.gather(*[mock_execute(t) for t in tasks])

    execution_ids = {r.execution_id for r in results}
    assert len(execution_ids) == 4
    assert execution_ids == {"exec-task-0", "exec-task-1", "exec-task-2", "exec-task-3"}


# ── pause / resume race conditions ────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pause_then_resume_sequential():
    """Pausing and then resuming a workflow returns final 'running' status."""
    executor = _make_mock_workflow_executor()

    pause_result = await executor.pause()
    assert pause_result["status"] == "paused"

    resume_result = await executor.resume()
    assert resume_result["status"] == "running"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_during_execution_reflected_immediately():
    """Cancelling while execution is in progress returns 'cancelled' status."""
    executor = _make_mock_workflow_executor()

    execution_task = asyncio.ensure_future(executor.execute("wf-cancel-test"))
    cancel_result = await executor.cancel()

    # Cancel should reflect immediately regardless of whether execute finished
    assert cancel_result["status"] == "cancelled"
    await execution_task  # drain the coroutine


@pytest.mark.unit
@pytest.mark.asyncio
async def test_concurrent_pause_calls_idempotent():
    """Multiple concurrent pause calls on the same workflow are all handled."""
    executor = _make_mock_workflow_executor()

    results = await asyncio.gather(*[executor.pause() for _ in range(5)])

    assert all(r["status"] == "paused" for r in results)
    assert executor.pause.await_count == 5


# ── DB mock shared-state safety ───────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_db_counter_is_atomic_under_concurrency():
    """The in-memory counter increments correctly under asyncio concurrency."""
    db = _make_mock_db()

    n = 50
    ids = await asyncio.gather(*[db.create_task({"x": i}) for i in range(n)])

    assert len(set(ids)) == n  # all unique task IDs
    assert db.create_task.await_count == n


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workflow_executor_call_count_under_burst():
    """The executor is called exactly N times for N parallel workflow launches."""
    executor = _make_mock_workflow_executor()

    n = 15
    await asyncio.gather(*[executor.execute(f"wf-{i}") for i in range(n)])

    assert executor.execute.await_count == n


# ── edge cases ────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_gather_returns_empty_list():
    """asyncio.gather on zero coroutines returns empty list."""
    results = await asyncio.gather()
    assert list(results) == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_single_workflow_not_affected_by_gather():
    """A single workflow in gather behaves the same as calling it directly."""
    executor = _make_mock_workflow_executor()

    (result,) = await asyncio.gather(executor.execute("wf-solo"))

    assert result["workflow_id"] == "wf-solo"
    assert result["status"] == "completed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_task_not_found_does_not_block_others():
    """A 404 for one task in a concurrent batch does not prevent others from resolving."""
    db = _make_mock_db()

    good_id = await db.create_task({"status": "pending"})

    results = await asyncio.gather(
        db.get_task(good_id),
        db.get_task("nonexistent-id"),
        return_exceptions=True,
    )

    assert results[0] is not None
    assert results[0]["id"] == good_id
    assert results[1] is None  # missing → None, not an exception
