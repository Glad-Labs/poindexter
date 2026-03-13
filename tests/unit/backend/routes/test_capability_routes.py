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

# Stub sqlalchemy — capability_tasks_service uses it but it's not installed in the dev env
if "sqlalchemy" not in sys.modules:
    _sa = ModuleType("sqlalchemy")
    for _name in ("and_", "desc", "select", "update", "Column", "String", "Integer", "DateTime",
                  "Boolean", "Text", "JSON", "create_engine", "MetaData", "Table"):
        setattr(_sa, _name, MagicMock())
    sys.modules["sqlalchemy"] = _sa
    sys.modules["sqlalchemy.dialects"] = ModuleType("sqlalchemy.dialects")
    sys.modules["sqlalchemy.dialects.postgresql"] = ModuleType("sqlalchemy.dialects.postgresql")
    setattr(sys.modules["sqlalchemy.dialects.postgresql"], "insert", MagicMock())
    sys.modules["sqlalchemy.orm"] = ModuleType("sqlalchemy.orm")
    setattr(sys.modules["sqlalchemy.orm"], "Session", MagicMock())

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
            owner_id="user-1",
        )

    assert exc.value.status_code == 400
    assert "ghost_capability" in exc.value.detail


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_capability_task_success(monkeypatch):
    mock_registry = MagicMock()
    mock_registry.get_metadata.return_value = MagicMock()  # capability exists
    monkeypatch.setattr("routes.capability_tasks_routes.get_registry", lambda: mock_registry)

    step = MagicMock(capability_name="research", inputs={"query": "AI"}, output_key="research_out", order=0)
    request = MagicMock(spec_set=["steps", "name", "description", "tags"])
    request.steps = [step]
    request.name = "My Task"
    request.description = "desc"
    request.tags = ["ai"]

    result = await create_capability_task(
        request=request,
        owner_id="user-1",
    )

    assert result.name == "My Task"
    assert result.owner_id == "user-1"
    assert isinstance(result.id, str)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_capability_task_multiple_invalid_capabilities(monkeypatch):
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
            owner_id="user-1",
        )

    assert exc.value.status_code == 400
    assert "bad_a" in exc.value.detail


# ── list_capability_tasks ─────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_capability_tasks_empty():
    """list_capability_tasks is a stub that always returns empty list."""
    result = await list_capability_tasks(
        skip=0, limit=50,
        owner_id="user-1",
    )

    assert result.tasks == []
    assert result.total == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_tasks_respects_pagination():
    """Pagination params are echoed back in the response."""
    result = await list_capability_tasks(
        skip=10, limit=25,
        owner_id="user-1",
    )

    assert result.skip == 10
    assert result.limit == 25
    assert result.total == 0


# ── get_capability_task ───────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_capability_task_not_found():
    """get_capability_task stub always raises 404."""
    with pytest.raises(HTTPException) as exc:
        await get_capability_task(
            task_id="missing",
            owner_id="user-1",
        )

    assert exc.value.status_code == 404


# ── delete_capability_task ────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_capability_task_success():
    """delete_capability_task stub returns success message."""
    result = await delete_capability_task(
        task_id="task-1",
        owner_id="user-1",
    )

    assert result["message"] == "Task deleted"


# ── execute_capability_task_endpoint ─────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_task_not_found():
    """execute_capability_task_endpoint stub always raises 404."""
    with pytest.raises(HTTPException) as exc:
        await execute_capability_task_endpoint(
            task_id="missing",
            owner_id="user-1",
        )

    assert exc.value.status_code == 404


# ── get_execution_result ──────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_execution_result_not_found():
    """get_execution_result stub always raises 404."""
    with pytest.raises(HTTPException) as exc:
        await get_execution_result(
            task_id="task-1",
            exec_id="missing-exec",
            owner_id="user-1",
        )

    assert exc.value.status_code == 404


# ── list_executions ───────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_executions_empty():
    """list_executions stub returns empty list."""
    result = await list_executions(
        task_id="task-1",
        skip=0, limit=50, status=None,
        owner_id="user-1",
    )

    assert result is not None
    assert result["executions"] == []
    assert result["total"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_executions_echoes_pagination():
    """list_executions stub echoes back skip/limit."""
    result = await list_executions(
        task_id="task-1",
        skip=5, limit=20, status=None,
        owner_id="user-1",
    )

    assert result["skip"] == 5
    assert result["limit"] == 20
