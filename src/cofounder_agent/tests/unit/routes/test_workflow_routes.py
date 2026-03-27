"""
Unit tests for routes/workflow_routes.py.

Tests cover:
- POST /api/workflows/templates          — list_workflow_templates
- GET  /api/workflows/status/{id}        — get_workflow_status
- POST /api/workflows/{id}/pause         — pause_workflow
- POST /api/workflows/{id}/resume        — resume_workflow
- POST /api/workflows/{id}/cancel        — cancel_workflow
- GET  /api/workflows/executions         — list_workflow_executions
- POST /api/workflows/execute/{template} — execute_workflow_template
- GET  /api/workflows/templates/history  — get_workflow_history
- POST /api/workflows/executions/{id}/cancel — cancel_workflow_execution
- GET  /api/workflows/executions/{id}/progress — get_workflow_execution_progress

Auth and DB are overridden so no real I/O occurs.
WorkflowHistoryService is patched to avoid real DB calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.auth_unified import get_current_user
from routes.workflow_routes import router
from tests.unit.routes.conftest import TEST_USER, make_mock_db
from utils.route_utils import get_database_dependency
from utils.route_utils import (
    get_template_execution_service_dependency as get_template_service_dependency,
)
from utils.route_utils import get_workflow_engine_dependency

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

RUNNING_EXECUTION = {
    "id": "wf-001",
    "status": "running",
    "current_phase": "draft",
    "task_results": [],
    "progress_percent": 40,
    "output_data": {},
    "start_time": "2026-03-01T10:00:00Z",
    "end_time": None,
    "duration_seconds": None,
    "completed_phases": ["research"],
    "remaining_phases": ["assess", "finalize"],
    "error_message": None,
    "created_at": "2026-03-01T10:00:00Z",
    "updated_at": "2026-03-01T10:05:00Z",
}

PAUSED_EXECUTION = {**RUNNING_EXECUTION, "status": "paused"}
COMPLETED_EXECUTION = {**RUNNING_EXECUTION, "status": "completed", "progress_percent": 100}
CANCELLED_EXECUTION = {**RUNNING_EXECUTION, "status": "cancelled"}


def _make_history_svc(execution=None):
    """Return a MagicMock WorkflowHistoryService with pre-configured return values."""
    svc = MagicMock()
    svc.get_workflow_execution = AsyncMock(return_value=execution)
    svc.update_workflow_execution = AsyncMock(return_value=True)
    return svc


def _make_template_svc():
    """Return a MagicMock TemplateExecutionService."""
    svc = MagicMock()
    svc.validate_template_name = MagicMock(return_value=None)
    svc.execute_template = AsyncMock(
        return_value={
            "execution_id": "exec-001",
            "template_name": "blog_post",
            "status": "running",
        }
    )
    svc.get_execution_history = AsyncMock(
        return_value={"executions": [], "total": 0, "offset": 0, "limit": 50}
    )
    return svc


def _build_app(mock_db=None, history_svc=None, workflow_engine=None, template_svc=None):
    """Build a minimal FastAPI app with all workflow router dependencies overridden."""
    if mock_db is None:
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()

    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    app.dependency_overrides[get_database_dependency] = lambda: mock_db
    app.dependency_overrides[get_workflow_engine_dependency] = lambda: workflow_engine
    if template_svc is not None:
        app.dependency_overrides[get_template_service_dependency] = lambda: template_svc

    return app


# ---------------------------------------------------------------------------
# POST /api/workflows/templates
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListWorkflowTemplates:
    def test_returns_200_with_templates(self):
        client = TestClient(_build_app())
        resp = client.post("/api/workflows/templates")
        assert resp.status_code == 200

    def test_response_includes_required_keys(self):
        client = TestClient(_build_app())
        data = client.post("/api/workflows/templates").json()
        assert "templates" in data
        assert "total" in data

    def test_response_has_expected_template_names(self):
        client = TestClient(_build_app())
        data = client.post("/api/workflows/templates").json()
        names = {t["name"] for t in data["templates"]}
        assert "blog_post" in names
        assert "social_media" in names
        assert "email" in names

    def test_total_matches_templates_count(self):
        client = TestClient(_build_app())
        data = client.post("/api/workflows/templates").json()
        assert data["total"] == len(data["templates"])

    def test_each_template_has_phases(self):
        client = TestClient(_build_app())
        data = client.post("/api/workflows/templates").json()
        for template in data["templates"]:
            assert "phases" in template
            assert isinstance(template["phases"], list)
            assert len(template["phases"]) > 0


# ---------------------------------------------------------------------------
# GET /api/workflows/status/{workflow_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetWorkflowStatus:
    def test_found_workflow_returns_200(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(RUNNING_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.get("/api/workflows/status/wf-001")
        assert resp.status_code == 200

    def test_found_workflow_returns_correct_fields(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(RUNNING_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            data = client.get("/api/workflows/status/wf-001").json()
        assert data["workflow_id"] == "wf-001"
        assert data["status"] == "running"
        assert "current_phase" in data
        assert "progress_percent" in data

    def test_missing_workflow_returns_404(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(None),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.get("/api/workflows/status/nonexistent")
        assert resp.status_code == 404

    def test_db_unavailable_returns_404_or_200(self):
        """When db_service.pool is None the route falls through to None execution → 404."""
        mock_db = make_mock_db()
        mock_db.pool = None
        client = TestClient(_build_app(mock_db))
        resp = client.get("/api/workflows/status/wf-001")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/workflows/{workflow_id}/pause
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPauseWorkflow:
    def test_pause_running_workflow_returns_200(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(RUNNING_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.post("/api/workflows/wf-001/pause")
        assert resp.status_code == 200

    def test_pause_returns_paused_status(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(RUNNING_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            data = client.post("/api/workflows/wf-001/pause").json()
        assert data["status"] == "paused"
        assert data["success"] is True
        assert data["workflow_id"] == "wf-001"

    def test_pause_nonexistent_workflow_returns_404(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(None),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.post("/api/workflows/nonexistent/pause")
        assert resp.status_code == 404

    def test_pause_already_paused_workflow_returns_400(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(PAUSED_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.post("/api/workflows/wf-001/pause")
        assert resp.status_code == 400

    def test_pause_completed_workflow_returns_400(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(COMPLETED_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.post("/api/workflows/wf-001/pause")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/workflows/{workflow_id}/resume
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResumeWorkflow:
    def test_resume_paused_workflow_returns_200(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(PAUSED_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.post("/api/workflows/wf-001/resume")
        assert resp.status_code == 200

    def test_resume_returns_running_status(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(PAUSED_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            data = client.post("/api/workflows/wf-001/resume").json()
        assert data["status"] == "running"
        assert data["success"] is True

    def test_resume_nonexistent_workflow_returns_404(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(None),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.post("/api/workflows/nonexistent/resume")
        assert resp.status_code == 404

    def test_resume_running_workflow_returns_400(self):
        """Cannot resume a workflow that is not paused."""
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(RUNNING_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.post("/api/workflows/wf-001/resume")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/workflows/{workflow_id}/cancel
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCancelWorkflow:
    def test_cancel_running_workflow_returns_200(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(RUNNING_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.post("/api/workflows/wf-001/cancel")
        assert resp.status_code == 200

    def test_cancel_paused_workflow_returns_200(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(PAUSED_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.post("/api/workflows/wf-001/cancel")
        assert resp.status_code == 200

    def test_cancel_returns_cancelled_status(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(RUNNING_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            data = client.post("/api/workflows/wf-001/cancel").json()
        assert data["status"] == "cancelled"
        assert data["success"] is True

    def test_cancel_nonexistent_workflow_returns_404(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(None),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.post("/api/workflows/nonexistent/cancel")
        assert resp.status_code == 404

    def test_cancel_completed_workflow_returns_400(self):
        """Already-completed workflows are not cancellable."""
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(COMPLETED_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.post("/api/workflows/wf-001/cancel")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/workflows/executions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListWorkflowExecutions:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/workflows/executions")
        assert resp.status_code == 200

    def test_response_has_pagination_fields(self):
        client = TestClient(_build_app())
        data = client.get("/api/workflows/executions").json()
        assert "executions" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data

    def test_default_limit_is_20(self):
        client = TestClient(_build_app())
        data = client.get("/api/workflows/executions").json()
        assert data["limit"] == 20

    def test_custom_limit_and_offset(self):
        client = TestClient(_build_app())
        data = client.get("/api/workflows/executions?limit=5&offset=10").json()
        assert data["limit"] == 5
        assert data["offset"] == 10


# ---------------------------------------------------------------------------
# POST /api/workflows/execute/{template_name}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteWorkflowTemplate:
    def test_valid_template_returns_200(self):
        template_svc = _make_template_svc()
        client = TestClient(_build_app(template_svc=template_svc))
        resp = client.post(
            "/api/workflows/execute/blog_post",
            json={"topic": "AI trends", "task_name": "Test"},
        )
        assert resp.status_code == 200

    def test_valid_template_returns_execution_id(self):
        template_svc = _make_template_svc()
        client = TestClient(_build_app(template_svc=template_svc))
        data = client.post(
            "/api/workflows/execute/blog_post",
            json={"topic": "AI trends"},
        ).json()
        assert "execution_id" in data

    def test_invalid_template_returns_404(self):
        template_svc = _make_template_svc()
        template_svc.validate_template_name = MagicMock(
            side_effect=ValueError("Unknown template 'nonexistent'")
        )
        client = TestClient(_build_app(template_svc=template_svc))
        resp = client.post(
            "/api/workflows/execute/nonexistent",
            json={"topic": "AI"},
        )
        assert resp.status_code == 404

    def test_execution_service_error_returns_500(self):
        template_svc = _make_template_svc()
        template_svc.execute_template = AsyncMock(side_effect=RuntimeError("DB down"))
        client = TestClient(_build_app(template_svc=template_svc))
        resp = client.post(
            "/api/workflows/execute/blog_post",
            json={"topic": "AI"},
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/workflows/templates/history
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetWorkflowHistory:
    def test_returns_200(self):
        template_svc = _make_template_svc()
        client = TestClient(_build_app(template_svc=template_svc))
        resp = client.get("/api/workflows/templates/history")
        assert resp.status_code == 200

    def test_response_has_pagination_fields(self):
        template_svc = _make_template_svc()
        client = TestClient(_build_app(template_svc=template_svc))
        data = client.get("/api/workflows/templates/history").json()
        assert "executions" in data
        assert "total" in data

    def test_service_error_returns_500(self):
        template_svc = _make_template_svc()
        template_svc.get_execution_history = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(template_svc=template_svc))
        resp = client.get("/api/workflows/templates/history")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/workflows/executions/{execution_id}/cancel
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCancelWorkflowExecution:
    def test_cancel_running_execution_returns_200(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(RUNNING_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.post("/api/workflows/executions/wf-001/cancel")
        assert resp.status_code == 200

    def test_cancel_returns_cancelled_status_and_previous_status(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(RUNNING_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            data = client.post("/api/workflows/executions/wf-001/cancel").json()
        assert data["status"] == "cancelled"
        assert data["previous_status"] == "running"
        assert data["execution_id"] == "wf-001"

    def test_cancel_nonexistent_execution_returns_404(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(None),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.post("/api/workflows/executions/nonexistent/cancel")
        assert resp.status_code == 404

    def test_cancel_completed_execution_returns_409(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(COMPLETED_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.post("/api/workflows/executions/wf-001/cancel")
        assert resp.status_code == 409

    def test_cancel_cancelled_execution_returns_409(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(CANCELLED_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.post("/api/workflows/executions/wf-001/cancel")
        assert resp.status_code == 409

    def test_db_unavailable_returns_503(self):
        mock_db = make_mock_db()
        mock_db.pool = None
        client = TestClient(_build_app(mock_db))
        resp = client.post("/api/workflows/executions/wf-001/cancel")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/workflows/executions/{execution_id}/progress
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetWorkflowExecutionProgress:
    def test_found_execution_returns_200(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(RUNNING_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.get("/api/workflows/executions/wf-001/progress")
        assert resp.status_code == 200

    def test_progress_fields_are_correct(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(RUNNING_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            data = client.get("/api/workflows/executions/wf-001/progress").json()
        assert data["execution_id"] == "wf-001"
        assert data["status"] == "running"
        assert "progress_percent" in data
        assert "phases_completed" in data
        assert "phases_remaining" in data

    def test_completed_execution_shows_100_percent(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(COMPLETED_EXECUTION),
        ):
            client = TestClient(_build_app(mock_db))
            data = client.get("/api/workflows/executions/wf-001/progress").json()
        assert data["progress_percent"] == 100

    def test_nonexistent_execution_returns_404(self):
        mock_db = make_mock_db()
        mock_db.pool = MagicMock()
        with patch(
            "routes.workflow_routes.WorkflowHistoryService",
            return_value=_make_history_svc(None),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.get("/api/workflows/executions/nonexistent/progress")
        assert resp.status_code == 404

    def test_db_unavailable_returns_503(self):
        mock_db = make_mock_db()
        mock_db.pool = None
        client = TestClient(_build_app(mock_db))
        resp = client.get("/api/workflows/executions/wf-001/progress")
        assert resp.status_code == 503
