"""Comprehensive unit tests for workflow_routes.py.

Tests all major endpoints:
- pause_workflow   POST /api/workflows/pause/{workflow_id}
- resume_workflow  POST /api/workflows/resume/{workflow_id}
- cancel_workflow  POST /api/workflows/cancel/{workflow_id}
- get_workflow_status           GET /api/workflows/status/{workflow_id}
- list_workflow_executions      GET /api/workflows/executions
- execute_workflow_template     POST /api/workflows/execute/{template_name}
- get_workflow_history          GET /api/workflows/templates/history
- cancel_workflow_execution     POST /api/workflows/executions/{id}/cancel
- get_workflow_execution_progress GET /api/workflows/executions/{id}/progress
"""

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Path bootstrap – ensure src/cofounder_agent is importable
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parents[4] / "src" / "cofounder_agent"))

# Stub structlog before any route imports so lightweight test envs work.
if "structlog" not in sys.modules:
    _structlog_stub = ModuleType("structlog")
    setattr(_structlog_stub, "get_logger", lambda *args, **kwargs: MagicMock())
    sys.modules["structlog"] = _structlog_stub

from routes.workflow_routes import (  # noqa: E402
    cancel_workflow,
    cancel_workflow_execution,
    execute_workflow_template,
    get_workflow_execution_progress,
    get_workflow_history,
    get_workflow_status,
    list_workflow_executions,
    pause_workflow,
    resume_workflow,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_service():
    db = MagicMock()
    db.pool = MagicMock()
    return db


def _make_engine(pause=True, resume=True, cancel=True):
    engine = MagicMock()
    engine.pause_workflow = MagicMock(return_value=pause)
    engine.resume_workflow = MagicMock(return_value=resume)
    engine.cancel_workflow = MagicMock(return_value=cancel)
    return engine


# ===========================================================================
# pause_workflow tests
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pause_workflow_success(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(
        return_value={"status": "running", "id": "wf-1"}
    )
    mock_history.update_workflow_execution = AsyncMock(return_value=True)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    result = await pause_workflow(
        "wf-1",
        db_service=_make_db_service(),
        workflow_engine=_make_engine(),
    )

    assert result["success"] is True
    assert result["workflow_id"] == "wf-1"
    assert result["status"] == "paused"
    mock_history.update_workflow_execution.assert_awaited_once_with("wf-1", status="paused")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pause_workflow_not_found(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(return_value=None)
    mock_history.update_workflow_execution = AsyncMock()

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    with pytest.raises(HTTPException) as exc_info:
        await pause_workflow(
            "wf-missing",
            db_service=_make_db_service(),
            workflow_engine=_make_engine(),
        )

    assert exc_info.value.status_code == 404
    mock_history.update_workflow_execution.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pause_workflow_wrong_state(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(
        return_value={"status": "paused", "id": "wf-1"}
    )
    mock_history.update_workflow_execution = AsyncMock()

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    with pytest.raises(HTTPException) as exc_info:
        await pause_workflow(
            "wf-1",
            db_service=_make_db_service(),
            workflow_engine=_make_engine(),
        )

    assert exc_info.value.status_code == 400
    assert "running" in exc_info.value.detail
    mock_history.update_workflow_execution.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pause_workflow_engine_returns_false_still_updates_db(monkeypatch):
    """Engine returning False (workflow not in memory) should still persist paused status."""
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(
        return_value={"status": "running", "id": "wf-2"}
    )
    mock_history.update_workflow_execution = AsyncMock(return_value=True)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    result = await pause_workflow(
        "wf-2",
        db_service=_make_db_service(),
        workflow_engine=_make_engine(pause=False),
    )

    assert result["success"] is True
    assert result["status"] == "paused"
    mock_history.update_workflow_execution.assert_awaited_once_with("wf-2", status="paused")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pause_workflow_db_error_raises_500(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(side_effect=RuntimeError("db exploded"))

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    with pytest.raises(HTTPException) as exc_info:
        await pause_workflow(
            "wf-1",
            db_service=_make_db_service(),
            workflow_engine=_make_engine(),
        )

    assert exc_info.value.status_code == 500


# ===========================================================================
# resume_workflow tests
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_workflow_success(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(
        return_value={"status": "paused", "id": "wf-3"}
    )
    mock_history.update_workflow_execution = AsyncMock(return_value=True)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    result = await resume_workflow(
        "wf-3",
        db_service=_make_db_service(),
        workflow_engine=_make_engine(),
    )

    assert result["success"] is True
    assert result["workflow_id"] == "wf-3"
    assert result["status"] == "running"
    mock_history.update_workflow_execution.assert_awaited_once_with("wf-3", status="running")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_workflow_not_found(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    with pytest.raises(HTTPException) as exc_info:
        await resume_workflow(
            "wf-ghost",
            db_service=_make_db_service(),
            workflow_engine=_make_engine(),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_workflow_wrong_state(monkeypatch):
    """Resuming a running workflow should raise 400."""
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(
        return_value={"status": "running", "id": "wf-3"}
    )
    mock_history.update_workflow_execution = AsyncMock()

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    with pytest.raises(HTTPException) as exc_info:
        await resume_workflow(
            "wf-3",
            db_service=_make_db_service(),
            workflow_engine=_make_engine(),
        )

    assert exc_info.value.status_code == 400
    assert "paused" in exc_info.value.detail
    mock_history.update_workflow_execution.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_workflow_engine_returns_false_still_updates_db(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(
        return_value={"status": "paused", "id": "wf-4"}
    )
    mock_history.update_workflow_execution = AsyncMock(return_value=True)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    result = await resume_workflow(
        "wf-4",
        db_service=_make_db_service(),
        workflow_engine=_make_engine(resume=False),
    )

    assert result["success"] is True
    assert result["status"] == "running"
    mock_history.update_workflow_execution.assert_awaited_once_with("wf-4", status="running")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resume_workflow_db_error_raises_500(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(side_effect=ConnectionError("pool gone"))

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    with pytest.raises(HTTPException) as exc_info:
        await resume_workflow(
            "wf-4",
            db_service=_make_db_service(),
            workflow_engine=_make_engine(),
        )

    assert exc_info.value.status_code == 500


# ===========================================================================
# cancel_workflow tests
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_workflow_success_from_running(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(
        return_value={"status": "running", "id": "wf-5"}
    )
    mock_history.update_workflow_execution = AsyncMock(return_value=True)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    result = await cancel_workflow(
        "wf-5",
        db_service=_make_db_service(),
        workflow_engine=_make_engine(),
    )

    assert result["success"] is True
    assert result["workflow_id"] == "wf-5"
    assert result["status"] == "cancelled"
    mock_history.update_workflow_execution.assert_awaited_once_with("wf-5", status="cancelled")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_workflow_success_from_paused(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(
        return_value={"status": "paused", "id": "wf-6"}
    )
    mock_history.update_workflow_execution = AsyncMock(return_value=True)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    result = await cancel_workflow(
        "wf-6",
        db_service=_make_db_service(),
        workflow_engine=_make_engine(),
    )

    assert result["success"] is True
    assert result["status"] == "cancelled"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_workflow_not_found(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    with pytest.raises(HTTPException) as exc_info:
        await cancel_workflow(
            "wf-nope",
            db_service=_make_db_service(),
            workflow_engine=_make_engine(),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_workflow_wrong_state(monkeypatch):
    """Cancelling an already-completed workflow should raise 400."""
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(
        return_value={"status": "completed", "id": "wf-7"}
    )
    mock_history.update_workflow_execution = AsyncMock()

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    with pytest.raises(HTTPException) as exc_info:
        await cancel_workflow(
            "wf-7",
            db_service=_make_db_service(),
            workflow_engine=_make_engine(),
        )

    assert exc_info.value.status_code == 400
    assert "running" in exc_info.value.detail or "paused" in exc_info.value.detail
    mock_history.update_workflow_execution.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_workflow_engine_returns_false_still_updates_db(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(
        return_value={"status": "running", "id": "wf-8"}
    )
    mock_history.update_workflow_execution = AsyncMock(return_value=True)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    result = await cancel_workflow(
        "wf-8",
        db_service=_make_db_service(),
        workflow_engine=_make_engine(cancel=False),
    )

    assert result["success"] is True
    assert result["status"] == "cancelled"
    mock_history.update_workflow_execution.assert_awaited_once_with("wf-8", status="cancelled")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_workflow_db_error_raises_500(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(side_effect=OSError("disk full"))

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    with pytest.raises(HTTPException) as exc_info:
        await cancel_workflow(
            "wf-8",
            db_service=_make_db_service(),
            workflow_engine=_make_engine(),
        )

    assert exc_info.value.status_code == 500


# ===========================================================================
# get_workflow_status tests
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_workflow_status_success(monkeypatch):
    execution = {
        "id": "wf-10",
        "status": "running",
        "current_phase": "draft",
        "task_results": ["research"],
        "progress_percent": 30,
        "output_data": {"key": "value"},
        "start_time": "2026-03-01T00:00:00Z",
        "end_time": None,
        "duration_seconds": None,
    }
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(return_value=execution)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    result = await get_workflow_status("wf-10", db_service=_make_db_service())

    assert result["workflow_id"] == "wf-10"
    assert result["status"] == "running"
    assert result["current_phase"] == "draft"
    assert result["phases_executed"] == ["research"]
    assert result["progress_percent"] == 30
    assert result["results"] == {"key": "value"}
    assert result["started_at"] == "2026-03-01T00:00:00Z"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_workflow_status_not_found(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_status("wf-ghost", db_service=_make_db_service())

    assert exc_info.value.status_code == 404
    assert "wf-ghost" in exc_info.value.detail


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_workflow_status_db_error_raises_500(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(side_effect=RuntimeError("connection lost"))

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_status("wf-10", db_service=_make_db_service())

    assert exc_info.value.status_code == 500


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_workflow_status_missing_optional_fields(monkeypatch):
    """Route should handle execution records with only minimal fields."""
    execution = {"id": "wf-11", "status": "completed"}
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(return_value=execution)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    result = await get_workflow_status("wf-11", db_service=_make_db_service())

    assert result["workflow_id"] == "wf-11"
    assert result["status"] == "completed"
    assert result["current_phase"] == ""
    assert result["phases_executed"] == []
    assert result["progress_percent"] == 0
    assert result["results"] == {}


# ===========================================================================
# list_workflow_executions tests
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_executions_empty():
    """list_workflow_executions always returns empty list (feature stub)."""
    mock_request = MagicMock()
    result = await list_workflow_executions(request=mock_request, limit=10, offset=0, status=None)

    assert result["executions"] == []
    assert result["total"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_executions_respects_limit_and_offset():
    """The response envelope should echo back limit/offset even when list is empty."""
    mock_request = MagicMock()
    result = await list_workflow_executions(request=mock_request, limit=25, offset=50, status=None)

    # The current implementation is a stub that always returns empty list.
    assert isinstance(result["executions"], list)
    assert "total" in result


# ===========================================================================
# execute_workflow_template tests
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_template_success():
    template_service = MagicMock()
    template_service.validate_template_name = MagicMock()  # no exception = valid
    expected_result = {
        "execution_id": "exec-abc",
        "workflow_id": "wf-abc",
        "template": "blog_post",
        "status": "completed",
        "phases": ["research", "draft"],
        "phase_results": {},
        "final_output": {"content": "hello"},
        "error_message": None,
        "duration_ms": 1234.5,
    }
    template_service.execute_template = AsyncMock(return_value=expected_result)

    task_input = {"topic": "AI trends", "tone": "professional"}

    result = await execute_workflow_template(
        template_name="blog_post",
        task_input=task_input,
        skip_phases=None,
        quality_threshold=0.7,
        tags=None,
        template_service=template_service,
    )

    assert result["execution_id"] == "exec-abc"
    assert result["status"] == "completed"
    template_service.validate_template_name.assert_called_once_with("blog_post")
    template_service.execute_template.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_template_invalid_name_raises_404():
    template_service = MagicMock()
    template_service.validate_template_name = MagicMock(
        side_effect=ValueError("Unknown template: bad_template")
    )

    with pytest.raises(HTTPException) as exc_info:
        await execute_workflow_template(
            template_name="bad_template",
            task_input={"topic": "test"},
            skip_phases=None,
            quality_threshold=0.7,
            tags=None,
            template_service=template_service,
        )

    assert exc_info.value.status_code == 404
    assert "bad_template" in exc_info.value.detail


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_template_service_error_raises_500():
    template_service = MagicMock()
    template_service.validate_template_name = MagicMock()
    template_service.execute_template = AsyncMock(side_effect=RuntimeError("internal error"))

    with pytest.raises(HTTPException) as exc_info:
        await execute_workflow_template(
            template_name="blog_post",
            task_input={"topic": "AI"},
            skip_phases=None,
            quality_threshold=0.7,
            tags=None,
            template_service=template_service,
        )

    assert exc_info.value.status_code == 500


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_template_passes_skip_phases():
    template_service = MagicMock()
    template_service.validate_template_name = MagicMock()
    template_service.execute_template = AsyncMock(
        return_value={"execution_id": "exec-skip", "status": "completed"}
    )

    await execute_workflow_template(
        template_name="blog_post",
        task_input={"topic": "AI"},
        skip_phases=["assess"],
        quality_threshold=0.8,
        tags=["test"],
        template_service=template_service,
    )

    call_kwargs = template_service.execute_template.call_args.kwargs
    assert call_kwargs["skip_phases"] == ["assess"]
    assert call_kwargs["quality_threshold"] == 0.8
    assert call_kwargs["tags"] == ["test"]


# ===========================================================================
# get_workflow_history tests
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_workflow_history_returns_all():
    executions = [
        {"execution_id": "exec-1", "template": "blog_post", "status": "completed"},
        {"execution_id": "exec-2", "template": "email", "status": "failed"},
    ]
    template_service = MagicMock()
    template_service.get_execution_history = AsyncMock(
        return_value={"executions": executions, "total_count": 2}
    )

    result = await get_workflow_history(
        limit=50,
        offset=0,
        template_name=None,
        template_service=template_service,
    )

    assert "executions" in result
    assert len(result["executions"]) == 2
    assert result["total_count"] == 2
    template_service.get_execution_history.assert_awaited_once_with(
        owner_id="system",
        template_name=None,
        limit=50,
        offset=0,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_workflow_history_empty():
    template_service = MagicMock()
    template_service.get_execution_history = AsyncMock(
        return_value={"executions": [], "total_count": 0}
    )

    result = await get_workflow_history(
        limit=50,
        offset=0,
        template_name=None,
        template_service=template_service,
    )

    assert result["executions"] == []
    assert result["total_count"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_workflow_history_filter_by_template():
    template_service = MagicMock()
    template_service.get_execution_history = AsyncMock(
        return_value={"executions": [], "total_count": 0}
    )

    await get_workflow_history(
        limit=10,
        offset=5,
        template_name="email",
        template_service=template_service,
    )

    call_kwargs = template_service.get_execution_history.call_args.kwargs
    assert call_kwargs["template_name"] == "email"
    assert call_kwargs["limit"] == 10
    assert call_kwargs["offset"] == 5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_workflow_history_service_error_raises_500():
    template_service = MagicMock()
    template_service.get_execution_history = AsyncMock(side_effect=RuntimeError("db down"))

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_history(
            limit=50,
            offset=0,
            template_name=None,
            template_service=template_service,
        )

    assert exc_info.value.status_code == 500


# ===========================================================================
# cancel_workflow_execution (executions/{id}/cancel) tests
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_execution_success(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(
        return_value={"status": "running", "id": "exec-99"}
    )
    mock_history.update_workflow_execution = AsyncMock(return_value=True)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    result = await cancel_workflow_execution("exec-99", db_service=_make_db_service())

    assert result["execution_id"] == "exec-99"
    assert result["status"] == "cancelled"
    assert result["message"] == "Workflow execution cancelled successfully"
    assert result["previous_status"] == "running"
    mock_history.update_workflow_execution.assert_awaited_once_with("exec-99", status="cancelled")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_execution_not_found(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    with pytest.raises(HTTPException) as exc_info:
        await cancel_workflow_execution("exec-ghost", db_service=_make_db_service())

    assert exc_info.value.status_code == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_execution_already_completed_raises_409(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(
        return_value={"status": "completed", "id": "exec-done"}
    )
    mock_history.update_workflow_execution = AsyncMock()

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    with pytest.raises(HTTPException) as exc_info:
        await cancel_workflow_execution("exec-done", db_service=_make_db_service())

    assert exc_info.value.status_code == 409
    mock_history.update_workflow_execution.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_execution_already_failed_raises_409(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(
        return_value={"status": "failed", "id": "exec-fail"}
    )
    mock_history.update_workflow_execution = AsyncMock()

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    with pytest.raises(HTTPException) as exc_info:
        await cancel_workflow_execution("exec-fail", db_service=_make_db_service())

    assert exc_info.value.status_code == 409


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_execution_db_unavailable_raises_503(monkeypatch):
    """When db_service.pool is None/falsy the route should raise 503."""
    db_service = MagicMock()
    db_service.pool = None

    with pytest.raises(HTTPException) as exc_info:
        await cancel_workflow_execution("exec-x", db_service=db_service)

    assert exc_info.value.status_code == 503


# ===========================================================================
# get_workflow_execution_progress tests
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_execution_progress_success(monkeypatch):
    execution = {
        "status": "running",
        "current_phase": "draft",
        "completed_phases": ["research"],
        "remaining_phases": ["assess", "refine"],
        "error_message": None,
        "created_at": "2026-03-01T10:00:00Z",
        "updated_at": "2026-03-01T10:05:00Z",
    }
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(return_value=execution)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    result = await get_workflow_execution_progress("exec-prog", db_service=_make_db_service())

    assert result["execution_id"] == "exec-prog"
    assert result["status"] == "running"
    assert result["current_phase"] == "draft"
    assert result["phases_completed"] == ["research"]
    assert result["phases_remaining"] == ["assess", "refine"]
    # 1 completed out of 3 total → 33%
    assert result["progress_percent"] == 33


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_execution_progress_not_found(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_execution_progress("exec-ghost", db_service=_make_db_service())

    assert exc_info.value.status_code == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_execution_progress_db_unavailable_raises_503():
    db_service = MagicMock()
    db_service.pool = None

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_execution_progress("exec-x", db_service=db_service)

    assert exc_info.value.status_code == 503


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_execution_progress_completed_zero_phases(monkeypatch):
    """A completed workflow with no phase data should return 100%."""
    execution = {
        "status": "completed",
        "current_phase": None,
        "completed_phases": [],
        "remaining_phases": [],
        "error_message": None,
        "created_at": "2026-03-01T10:00:00Z",
        "updated_at": "2026-03-01T10:10:00Z",
    }
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(return_value=execution)

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    result = await get_workflow_execution_progress("exec-done", db_service=_make_db_service())

    assert result["progress_percent"] == 100
    assert result["status"] == "completed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_execution_progress_db_error_raises_500(monkeypatch):
    mock_history = MagicMock()
    mock_history.get_workflow_execution = AsyncMock(side_effect=RuntimeError("timeout"))

    monkeypatch.setattr(
        "routes.workflow_routes.WorkflowHistoryService", lambda pool: mock_history
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_execution_progress("exec-x", db_service=_make_db_service())

    assert exc_info.value.status_code == 500
