"""
Unit tests for capability_tasks_routes.py.

Tests the actual route handler logic using direct function calls with mocked
dependencies — no live database or HTTP server needed.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── path setup ──────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parents[4] / "src" / "cofounder_agent"))

# Stub structlog before importing routes
if "structlog" not in sys.modules:
    _stub = ModuleType("structlog")
    setattr(_stub, "get_logger", lambda *a, **k: MagicMock())
    sys.modules["structlog"] = _stub

from fastapi import HTTPException

from routes.capability_tasks_routes import (
    create_capability_task,
    delete_capability_task,
    execute_capability_task_endpoint,
    get_capability,
    get_capability_task,
    get_execution_result,
    list_capabilities,
    list_capability_tasks,
    list_executions,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_mock_task(task_id: str = "task-1", owner_id: str = "user-1"):
    t = MagicMock()
    t.id = task_id
    t.name = "My Task"
    t.description = "desc"
    t.steps = []
    t.tags = ["ai"]
    t.owner_id = owner_id
    t.created_at = datetime.now(timezone.utc)
    return t


def _make_execution(exec_id: str = "exec-1", task_id: str = "task-1"):
    e = MagicMock()
    e.execution_id = exec_id
    e.task_id = task_id
    e.status = "completed"
    e.step_results = []
    e.final_outputs = {"result": "ok"}
    e.total_duration_ms = 500
    e.progress_percent = 100
    e.error = None
    e.started_at = datetime.now(timezone.utc)
    e.completed_at = datetime.now(timezone.utc)
    return e


_SENTINEL = object()  # distinguish "no pool given" from explicit None


def _make_db_service(pool=_SENTINEL):
    db = MagicMock()
    db.pool = MagicMock() if pool is _SENTINEL else pool
    return db


def _make_user(uid: str = "user-1"):
    return {"id": uid, "email": "test@example.com"}


# ── list_capabilities ────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_capabilities_empty(monkeypatch):
    mock_registry = MagicMock()
    mock_registry.list_capabilities.return_value = {}
    monkeypatch.setattr("routes.capability_tasks_routes.get_registry", lambda: mock_registry)

    result = await list_capabilities(tag=None, cost_tier=None)

    assert result.capabilities == []
    assert result.total == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_capabilities_returns_all(monkeypatch):
    meta = MagicMock()
    meta.description = "Do research"
    meta.version = "1.0.0"
    meta.tags = ["research"]
    meta.cost_tier = "cheap"
    meta.timeout_ms = 30000

    cap = MagicMock()
    cap.input_schema.parameters = []
    cap.output_schema.to_dict.return_value = {"return_type": "str", "description": "", "output_format": "json"}

    mock_registry = MagicMock()
    mock_registry.list_capabilities.return_value = {"research": meta}
    mock_registry.get.return_value = cap
    monkeypatch.setattr("routes.capability_tasks_routes.get_registry", lambda: mock_registry)

    result = await list_capabilities(tag=None, cost_tier=None)

    assert result.total == 1
    assert result.capabilities[0].name == "research"
    assert result.capabilities[0].cost_tier == "cheap"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_capabilities_filters_by_tag(monkeypatch):
    meta_a = MagicMock(tags=["research"], cost_tier="cheap", description="", version="1.0", timeout_ms=5000)
    meta_b = MagicMock(tags=["writing"], cost_tier="balanced", description="", version="1.0", timeout_ms=5000)

    mock_registry = MagicMock()
    mock_registry.list_capabilities.return_value = {"research": meta_a, "writing": meta_b}
    mock_registry.get.return_value = None
    monkeypatch.setattr("routes.capability_tasks_routes.get_registry", lambda: mock_registry)

    result = await list_capabilities(tag="research", cost_tier=None)

    assert result.total == 1
    assert result.capabilities[0].name == "research"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_capabilities_filters_by_cost_tier(monkeypatch):
    meta_cheap = MagicMock(tags=[], cost_tier="cheap", description="", version="1.0", timeout_ms=5000)
    meta_prem = MagicMock(tags=[], cost_tier="premium", description="", version="1.0", timeout_ms=5000)

    mock_registry = MagicMock()
    mock_registry.list_capabilities.return_value = {"a": meta_cheap, "b": meta_prem}
    mock_registry.get.return_value = None
    monkeypatch.setattr("routes.capability_tasks_routes.get_registry", lambda: mock_registry)

    result = await list_capabilities(tag=None, cost_tier="premium")

    assert result.total == 1
    assert result.capabilities[0].name == "b"


# ── get_capability ────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_capability_not_found(monkeypatch):
    mock_registry = MagicMock()
    mock_registry.get_metadata.return_value = None
    monkeypatch.setattr("routes.capability_tasks_routes.get_registry", lambda: mock_registry)

    with pytest.raises(HTTPException) as exc:
        await get_capability("nonexistent")

    assert exc.value.status_code == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_capability_success(monkeypatch):
    meta = MagicMock()
    meta.description = "Do research"
    meta.version = "2.0.0"
    meta.tags = ["ai"]
    meta.cost_tier = "balanced"
    meta.timeout_ms = 60000

    cap = MagicMock()
    cap.input_schema.parameters = []
    cap.output_schema.to_dict.return_value = {"return_type": "str", "description": "", "output_format": "json"}

    mock_registry = MagicMock()
    mock_registry.get_metadata.return_value = meta
    mock_registry.get.return_value = cap
    monkeypatch.setattr("routes.capability_tasks_routes.get_registry", lambda: mock_registry)

    result = await get_capability("research")

    assert result.name == "research"
    assert result.version == "2.0.0"
    assert result.cost_tier == "balanced"


# ── create_capability_task ────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_capability_task_unknown_capability(monkeypatch):
    mock_registry = MagicMock()
    mock_registry.get_metadata.return_value = None  # capability doesn't exist
    monkeypatch.setattr("routes.capability_tasks_routes.get_registry", lambda: mock_registry)

    request = MagicMock()
    step = MagicMock(capability_name="ghost_capability", inputs={}, output_key="out")
    request.steps = [step]

    with pytest.raises(HTTPException) as exc:
        await create_capability_task(
            request=request,
            current_user=_make_user(),
            db_service=_make_db_service(),
        )

    assert exc.value.status_code == 400
    assert "ghost_capability" in exc.value.detail


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_capability_task_success(monkeypatch):
    mock_registry = MagicMock()
    mock_registry.get_metadata.return_value = MagicMock()  # capability exists
    monkeypatch.setattr("routes.capability_tasks_routes.get_registry", lambda: mock_registry)

    mock_task = _make_mock_task()
    mock_task_service = MagicMock()
    mock_task_service.create_task = AsyncMock(return_value=mock_task)
    monkeypatch.setattr(
        "routes.capability_tasks_routes.CapabilityTasksService",
        lambda pool: mock_task_service,
    )

    step = MagicMock(capability_name="research", inputs={"query": "AI"}, output_key="research_out")
    request = MagicMock(
        steps=[step], name="My Task", description="desc", tags=["ai"]
    )

    result = await create_capability_task(
        request=request,
        current_user=_make_user(),
        db_service=_make_db_service(),
    )

    assert result.id == "task-1"
    assert result.name == "My Task"
    mock_task_service.create_task.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_capability_task_db_unavailable(monkeypatch):
    mock_registry = MagicMock()
    mock_registry.get_metadata.return_value = MagicMock()
    monkeypatch.setattr("routes.capability_tasks_routes.get_registry", lambda: mock_registry)

    # Pool is None — _require_pool should raise 503 before any task service is created.
    # Do NOT monkeypatch CapabilityTasksService here so _require_pool runs normally.
    db_service = _make_db_service(pool=None)

    step = MagicMock()
    step.capability_name = "research"
    step.inputs = {}
    step.output_key = "out"

    request = MagicMock()
    request.steps = [step]
    request.name = "My Task"
    request.description = ""
    request.tags = []

    with pytest.raises(HTTPException) as exc:
        await create_capability_task(
            request=request,
            current_user=_make_user(),
            db_service=db_service,
        )

    assert exc.value.status_code == 503


# ── list_capability_tasks ─────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_capability_tasks_empty(monkeypatch):
    mock_task_service = MagicMock()
    mock_task_service.list_tasks = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(
        "routes.capability_tasks_routes.CapabilityTasksService",
        lambda pool: mock_task_service,
    )

    result = await list_capability_tasks(
        skip=0, limit=50,
        current_user=_make_user(),
        db_service=_make_db_service(),
    )

    assert result.tasks == []
    assert result.total == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_capability_tasks_returns_owned(monkeypatch):
    task = _make_mock_task(owner_id="user-1")
    mock_task_service = MagicMock()
    mock_task_service.list_tasks = AsyncMock(return_value=([task], 1))
    monkeypatch.setattr(
        "routes.capability_tasks_routes.CapabilityTasksService",
        lambda pool: mock_task_service,
    )

    result = await list_capability_tasks(
        skip=0, limit=50,
        current_user=_make_user("user-1"),
        db_service=_make_db_service(),
    )

    assert result.total == 1
    assert result.tasks[0].owner_id == "user-1"
    mock_task_service.list_tasks.assert_awaited_once_with(owner_id="user-1", skip=0, limit=50)


# ── get_capability_task ───────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_capability_task_not_found(monkeypatch):
    mock_task_service = MagicMock()
    mock_task_service.get_task = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "routes.capability_tasks_routes.CapabilityTasksService",
        lambda pool: mock_task_service,
    )

    with pytest.raises(HTTPException) as exc:
        await get_capability_task(
            task_id="missing",
            current_user=_make_user(),
            db_service=_make_db_service(),
        )

    assert exc.value.status_code == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_capability_task_success(monkeypatch):
    task = _make_mock_task()
    mock_task_service = MagicMock()
    mock_task_service.get_task = AsyncMock(return_value=task)
    monkeypatch.setattr(
        "routes.capability_tasks_routes.CapabilityTasksService",
        lambda pool: mock_task_service,
    )

    result = await get_capability_task(
        task_id="task-1",
        current_user=_make_user(),
        db_service=_make_db_service(),
    )

    assert result.id == "task-1"


# ── delete_capability_task ────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_capability_task_not_found(monkeypatch):
    mock_task_service = MagicMock()
    mock_task_service.get_task = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "routes.capability_tasks_routes.CapabilityTasksService",
        lambda pool: mock_task_service,
    )

    with pytest.raises(HTTPException) as exc:
        await delete_capability_task(
            task_id="ghost",
            current_user=_make_user(),
            db_service=_make_db_service(),
        )

    assert exc.value.status_code == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_capability_task_success(monkeypatch):
    task = _make_mock_task()
    mock_task_service = MagicMock()
    mock_task_service.get_task = AsyncMock(return_value=task)
    mock_task_service.delete_task = AsyncMock()
    monkeypatch.setattr(
        "routes.capability_tasks_routes.CapabilityTasksService",
        lambda pool: mock_task_service,
    )

    result = await delete_capability_task(
        task_id="task-1",
        current_user=_make_user(),
        db_service=_make_db_service(),
    )

    assert result["message"] == "Task deleted"
    mock_task_service.delete_task.assert_awaited_once_with("task-1", "user-1")


# ── execute_capability_task_endpoint ─────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_task_not_found(monkeypatch):
    mock_task_service = MagicMock()
    mock_task_service.get_task = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "routes.capability_tasks_routes.CapabilityTasksService",
        lambda pool: mock_task_service,
    )

    with pytest.raises(HTTPException) as exc:
        await execute_capability_task_endpoint(
            task_id="missing",
            current_user=_make_user(),
            db_service=_make_db_service(),
        )

    assert exc.value.status_code == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_task_success(monkeypatch):
    task = _make_mock_task()
    execution = _make_execution()

    mock_task_service = MagicMock()
    mock_task_service.get_task = AsyncMock(return_value=task)
    mock_task_service.persist_execution = AsyncMock()
    monkeypatch.setattr(
        "routes.capability_tasks_routes.CapabilityTasksService",
        lambda pool: mock_task_service,
    )
    monkeypatch.setattr(
        "routes.capability_tasks_routes.execute_capability_task",
        AsyncMock(return_value=execution),
    )

    result = await execute_capability_task_endpoint(
        task_id="task-1",
        current_user=_make_user(),
        db_service=_make_db_service(),
    )

    assert result.execution_id == "exec-1"
    assert result.status == "completed"
    mock_task_service.persist_execution.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_task_exception_returns_failed_response(monkeypatch):
    task = _make_mock_task()
    mock_task_service = MagicMock()
    mock_task_service.get_task = AsyncMock(return_value=task)
    monkeypatch.setattr(
        "routes.capability_tasks_routes.CapabilityTasksService",
        lambda pool: mock_task_service,
    )
    monkeypatch.setattr(
        "routes.capability_tasks_routes.execute_capability_task",
        AsyncMock(side_effect=RuntimeError("Step 2 failed: timeout")),
    )

    result = await execute_capability_task_endpoint(
        task_id="task-1",
        current_user=_make_user(),
        db_service=_make_db_service(),
    )

    # Errors are caught and returned as a failed ExecutionResponse (not a 500)
    assert result.status == "failed"
    assert "Step 2 failed" in (result.error or "")


# ── get_execution_result ──────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_execution_result_not_found(monkeypatch):
    mock_task_service = MagicMock()
    mock_task_service.get_execution = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "routes.capability_tasks_routes.CapabilityTasksService",
        lambda pool: mock_task_service,
    )

    with pytest.raises(HTTPException) as exc:
        await get_execution_result(
            task_id="task-1",
            exec_id="missing-exec",
            current_user=_make_user(),
            db_service=_make_db_service(),
        )

    assert exc.value.status_code == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_execution_result_success(monkeypatch):
    execution = _make_execution(exec_id="exec-99", task_id="task-1")
    mock_task_service = MagicMock()
    mock_task_service.get_execution = AsyncMock(return_value=execution)
    monkeypatch.setattr(
        "routes.capability_tasks_routes.CapabilityTasksService",
        lambda pool: mock_task_service,
    )

    result = await get_execution_result(
        task_id="task-1",
        exec_id="exec-99",
        current_user=_make_user(),
        db_service=_make_db_service(),
    )

    assert result.execution_id == "exec-99"
    assert result.task_id == "task-1"
    assert result.status == "completed"


# ── list_executions ───────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_executions_empty(monkeypatch):
    task = _make_mock_task()
    mock_task_service = MagicMock()
    mock_task_service.get_task = AsyncMock(return_value=task)
    mock_task_service.list_executions = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(
        "routes.capability_tasks_routes.CapabilityTasksService",
        lambda pool: mock_task_service,
    )

    result = await list_executions(
        task_id="task-1",
        skip=0, limit=50, status=None,
        current_user=_make_user(),
        db_service=_make_db_service(),
    )

    assert result is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_executions_with_items(monkeypatch):
    task = _make_mock_task()
    execs = [_make_execution(f"exec-{i}") for i in range(3)]
    mock_task_service = MagicMock()
    mock_task_service.get_task = AsyncMock(return_value=task)
    mock_task_service.list_executions = AsyncMock(return_value=(execs, 3))
    monkeypatch.setattr(
        "routes.capability_tasks_routes.CapabilityTasksService",
        lambda pool: mock_task_service,
    )

    result = await list_executions(
        task_id="task-1",
        skip=0, limit=50, status=None,
        current_user=_make_user(),
        db_service=_make_db_service(),
    )

    assert result is not None
    mock_task_service.list_executions.assert_awaited_once()


# ── concurrency / edge cases ──────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_task_no_steps_raises_400(monkeypatch):
    """A task with no steps should still be accepted (validation happens per-step)."""
    mock_registry = MagicMock()
    monkeypatch.setattr("routes.capability_tasks_routes.get_registry", lambda: mock_registry)

    mock_task = _make_mock_task()
    mock_task_service = MagicMock()
    mock_task_service.create_task = AsyncMock(return_value=mock_task)
    monkeypatch.setattr(
        "routes.capability_tasks_routes.CapabilityTasksService",
        lambda pool: mock_task_service,
    )

    request = MagicMock(steps=[], name="Empty", description="", tags=[])

    # No steps → no registry lookups → create_task is called with empty steps
    result = await create_capability_task(
        request=request,
        current_user=_make_user(),
        db_service=_make_db_service(),
    )
    assert result.id == "task-1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_tasks_respects_pagination(monkeypatch):
    mock_task_service = MagicMock()
    mock_task_service.list_tasks = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(
        "routes.capability_tasks_routes.CapabilityTasksService",
        lambda pool: mock_task_service,
    )

    await list_capability_tasks(
        skip=10, limit=25,
        current_user=_make_user(),
        db_service=_make_db_service(),
    )

    mock_task_service.list_tasks.assert_awaited_once_with(owner_id="user-1", skip=10, limit=25)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_multiple_invalid_capabilities_first_one_reported(monkeypatch):
    """When multiple steps reference unknown capabilities, the first bad one is reported."""
    mock_registry = MagicMock()
    mock_registry.get_metadata.return_value = None
    monkeypatch.setattr("routes.capability_tasks_routes.get_registry", lambda: mock_registry)

    step_a = MagicMock(capability_name="bad_a", inputs={}, output_key="a")
    step_b = MagicMock(capability_name="bad_b", inputs={}, output_key="b")
    request = MagicMock(steps=[step_a, step_b], name="T", description="", tags=[])

    with pytest.raises(HTTPException) as exc:
        await create_capability_task(
            request=request,
            current_user=_make_user(),
            db_service=_make_db_service(),
        )

    assert exc.value.status_code == 400
    assert "bad_a" in exc.value.detail
