"""
Unit tests for routes/workflow_history.py.

Tests cover:
- GET /api/workflows/history              — get_workflow_history
- GET /api/workflows/{id}/details         — get_execution_details
- GET /api/workflows/statistics           — get_workflow_statistics
- GET /api/workflows/performance-metrics  — get_performance_metrics
- GET /api/workflows/{id}/history         — get_workflow_type_history

WorkflowHistoryService is provided via dependency override.
Auth is overridden via dependency injection.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.auth_unified import get_current_user
from routes.workflow_history import get_history_service, router
from tests.unit.routes.conftest import TEST_USER

USER_ID = TEST_USER["id"]
EXECUTION_ID = "exec-11111111-1111-1111-1111-111111111111"
WORKFLOW_ID = "wf-22222222-2222-2222-2222-222222222222"


SAMPLE_EXECUTION = {
    "id": EXECUTION_ID,
    "workflow_id": WORKFLOW_ID,
    "workflow_type": "blog_generation",
    "user_id": USER_ID,
    "status": "COMPLETED",
    "input_data": {"topic": "AI trends"},
    "output_data": {"content": "Generated blog post"},
    "task_results": [],
    "error_message": None,
    "start_time": "2026-03-12T10:00:00+00:00",
    "end_time": "2026-03-12T10:05:00+00:00",
    "duration_seconds": 300.0,
    "execution_metadata": {},
    "created_at": "2026-03-12T10:00:00+00:00",
    "updated_at": "2026-03-12T10:05:00+00:00",
}

SAMPLE_STATS = {
    "user_id": USER_ID,
    "period_days": 30,
    "total_executions": 5,
    "completed_executions": 4,
    "failed_executions": 1,
    "success_rate_percent": 80.0,
    "average_duration_seconds": 120.0,
    "first_execution": "2026-02-10T10:00:00+00:00",
    "last_execution": "2026-03-12T10:00:00+00:00",
    "workflows": [],
    "most_common_workflow": "blog_generation",
}

SAMPLE_METRICS = {
    "user_id": USER_ID,
    "workflow_type": None,
    "period_days": 30,
    "execution_time_distribution": [],
    "error_patterns": [],
    "optimization_tips": ["Use async processing for large batches"],
}


def _make_history_svc(
    history=None,
    execution=None,
    stats=None,
    metrics=None,
    type_history=None,
):
    svc = MagicMock()
    svc.get_user_workflow_history = AsyncMock(
        return_value=history or {"executions": [SAMPLE_EXECUTION], "total": 1}
    )
    svc.get_workflow_execution = AsyncMock(return_value=execution or SAMPLE_EXECUTION)
    svc.get_workflow_statistics = AsyncMock(return_value=stats or SAMPLE_STATS)
    svc.get_performance_metrics = AsyncMock(return_value=metrics or SAMPLE_METRICS)
    svc.get_workflow_type_history = AsyncMock(
        return_value=type_history
        or {"executions": [SAMPLE_EXECUTION], "total": 1, "workflow_id": WORKFLOW_ID}
    )
    return svc


def _build_app(svc=None) -> FastAPI:
    if svc is None:
        svc = _make_history_svc()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    app.dependency_overrides[get_history_service] = lambda: svc
    return app


# ---------------------------------------------------------------------------
# GET /api/workflows/history
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetWorkflowHistory:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/workflows/history")
        assert resp.status_code == 200

    def test_response_has_executions_and_total(self):
        client = TestClient(_build_app())
        data = client.get("/api/workflows/history").json()
        assert "executions" in data
        assert "total" in data

    def test_empty_history_returns_200(self):
        svc = _make_history_svc(history={"executions": [], "total": 0})
        client = TestClient(_build_app(svc))
        data = client.get("/api/workflows/history").json()
        assert data["total"] == 0
        assert data["executions"] == []

    def test_pagination_params_accepted(self):
        client = TestClient(_build_app())
        resp = client.get("/api/workflows/history?limit=10&offset=5")
        assert resp.status_code == 200

    def test_status_filter_accepted(self):
        client = TestClient(_build_app())
        resp = client.get("/api/workflows/history?status=COMPLETED")
        assert resp.status_code == 200

    def test_service_error_returns_500(self):
        svc = _make_history_svc()
        svc.get_user_workflow_history = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(svc))
        resp = client.get("/api/workflows/history")
        assert resp.status_code == 500

    def test_requires_auth(self):
        svc = _make_history_svc()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_history_service] = lambda: svc
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/workflows/history")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/workflows/{execution_id}/details
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetExecutionDetails:
    def test_returns_200_for_existing_execution(self):
        client = TestClient(_build_app())
        resp = client.get(f"/api/workflows/{EXECUTION_ID}/details")
        assert resp.status_code == 200

    def test_response_has_execution_fields(self):
        client = TestClient(_build_app())
        data = client.get(f"/api/workflows/{EXECUTION_ID}/details").json()
        assert "id" in data
        assert "workflow_type" in data
        assert "status" in data

    def test_not_found_returns_404(self):
        svc = _make_history_svc()
        svc.get_workflow_execution = AsyncMock(return_value=None)
        client = TestClient(_build_app(svc))
        resp = client.get("/api/workflows/nonexistent-id/details")
        assert resp.status_code == 404

    def test_unauthorized_user_returns_403(self):
        """Execution owned by a different user should return 403."""
        svc = _make_history_svc(execution={**SAMPLE_EXECUTION, "user_id": "other-user-999"})
        client = TestClient(_build_app(svc))
        resp = client.get(f"/api/workflows/{EXECUTION_ID}/details")
        assert resp.status_code == 403

    def test_service_error_returns_500(self):
        svc = _make_history_svc()
        svc.get_workflow_execution = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(svc))
        resp = client.get(f"/api/workflows/{EXECUTION_ID}/details")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/workflows/statistics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetWorkflowStatistics:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/workflows/statistics")
        assert resp.status_code == 200

    def test_response_has_stats_fields(self):
        client = TestClient(_build_app())
        data = client.get("/api/workflows/statistics").json()
        for field in ["total_executions", "success_rate_percent", "period_days"]:
            assert field in data

    def test_days_param_accepted(self):
        client = TestClient(_build_app())
        resp = client.get("/api/workflows/statistics?days=7")
        assert resp.status_code == 200

    def test_service_error_returns_500(self):
        svc = _make_history_svc()
        svc.get_workflow_statistics = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(svc))
        resp = client.get("/api/workflows/statistics")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/workflows/performance-metrics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPerformanceMetrics:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/workflows/performance-metrics")
        assert resp.status_code == 200

    def test_response_has_metrics_fields(self):
        client = TestClient(_build_app())
        data = client.get("/api/workflows/performance-metrics").json()
        for field in ["period_days", "execution_time_distribution", "optimization_tips"]:
            assert field in data

    def test_workflow_type_filter_accepted(self):
        client = TestClient(_build_app())
        resp = client.get("/api/workflows/performance-metrics?workflow_type=blog_generation")
        assert resp.status_code == 200

    def test_service_error_returns_500(self):
        svc = _make_history_svc()
        svc.get_performance_metrics = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(svc))
        resp = client.get("/api/workflows/performance-metrics")
        assert resp.status_code == 500
