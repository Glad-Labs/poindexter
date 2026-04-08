"""
Unit tests for routes/metrics_routes.py.

Tests cover:
- GET /api/metrics/costs/budget           — get_budget_status
- GET /api/metrics/operational   — get_operational_metrics (auth required)

Auth and DB overridden where needed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.metrics_routes import metrics_router
from tests.unit.routes.conftest import TEST_USER, make_mock_db
from utils.route_utils import get_database_dependency

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _build_app(mock_db=None, with_auth=True) -> FastAPI:
    if mock_db is None:
        mock_db = make_mock_db()

    app = FastAPI()
    app.include_router(metrics_router)

    if with_auth:
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
    app.dependency_overrides[get_database_dependency] = lambda: mock_db

    return app


# ---------------------------------------------------------------------------
# GET /api/metrics/operational (auth required — #1011)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetOperationalMetrics:
    def test_returns_401_without_auth(self):
        """Operational metrics now requires auth (#1011)."""
        mock_db = make_mock_db()
        mock_db.tasks = MagicMock()
        mock_db.tasks.get_task_counts = AsyncMock(
            return_value={"pending": 2, "in_progress": 1, "failed": 0, "completed": 10}
        )

        # Build app with NO auth override — should now require auth
        app = FastAPI()
        app.include_router(metrics_router)
        app.dependency_overrides[get_database_dependency] = lambda: mock_db
        with patch.dict("os.environ", {"DEVELOPMENT_MODE": "false", "API_TOKEN": "secret"}):
            client = TestClient(app)
            resp = client.get("/api/metrics/operational")
        assert resp.status_code == 401

    def test_response_has_required_operational_fields(self):
        mock_db = make_mock_db()
        mock_db.tasks = MagicMock()
        mock_db.tasks.get_task_counts = AsyncMock(
            return_value={"pending": 0, "in_progress": 0, "failed": 0, "completed": 0}
        )

        client = TestClient(_build_app(mock_db))
        data = client.get("/api/metrics/operational").json()
        assert "task_queue" in data
        assert "executor" in data
        assert "websocket_connections" in data
        assert "uptime_seconds" in data
        assert "timestamp" in data

    def test_task_queue_has_status_breakdown(self):
        mock_db = make_mock_db()
        mock_db.tasks = MagicMock()
        mock_db.tasks.get_task_counts = AsyncMock(
            return_value={"pending": 3, "in_progress": 2, "failed": 1, "completed": 50}
        )

        client = TestClient(_build_app(mock_db))
        data = client.get("/api/metrics/operational").json()
        tq = data["task_queue"]
        assert "pending" in tq
        assert "in_progress" in tq
        assert "failed" in tq
        assert "completed" in tq

    def test_db_unavailable_still_returns_200(self):
        """When DB is down, operational metrics gracefully degrade."""
        mock_db = make_mock_db()
        mock_db.tasks = MagicMock()
        mock_db.tasks.get_task_counts = AsyncMock(side_effect=RuntimeError("DB error"))

        client = TestClient(_build_app(mock_db))
        resp = client.get("/api/metrics/operational")
        # Should still return 200 with degraded zeros (graceful failure)
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_queue"]["pending"] == 0

    def test_executor_fields_present(self):
        mock_db = make_mock_db()
        mock_db.tasks = MagicMock()
        mock_db.tasks.get_task_counts = AsyncMock(return_value={})

        client = TestClient(_build_app(mock_db))
        data = client.get("/api/metrics/operational").json()
        executor = data["executor"]
        assert "task_count" in executor
        assert "success_count" in executor
        assert "error_count" in executor
        assert "is_running" in executor


def _make_mock_cost_service():
    svc = AsyncMock()
    svc.get_summary = AsyncMock(
        return_value={
            "month_cost": 12.50,
            "today_cost": 0.50,
            "week_cost": 3.00,
            "projected_monthly": 15.00,
            "tasks_completed": 10,
            "avg_cost_per_task": 1.25,
            "last_updated": "2026-03-01T00:00:00Z",
        }
    )
    svc.get_breakdown_by_phase = AsyncMock(return_value={"phases": []})
    svc.get_breakdown_by_model = AsyncMock(return_value={"models": []})
    svc.get_budget_status = AsyncMock(
        return_value={
            "monthly_budget": 150.0,
            "amount_spent": 12.50,
            "amount_remaining": 137.50,
            "percent_used": 8.33,
            "status": "healthy",
            "alerts": [],
        }
    )
    return svc


# ---------------------------------------------------------------------------
# GET /api/metrics/costs/budget  (get_budget_status)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetBudgetStatus:
    def test_returns_200(self):
        mock_svc = _make_mock_cost_service()
        with patch("routes.metrics_routes.CostAggregationService", return_value=mock_svc):
            client = TestClient(_build_app())
            resp = client.get("/api/metrics/costs/budget")
        assert resp.status_code == 200

    def test_custom_monthly_budget_forwarded(self):
        mock_svc = _make_mock_cost_service()
        with patch("routes.metrics_routes.CostAggregationService", return_value=mock_svc):
            client = TestClient(_build_app())
            client.get("/api/metrics/costs/budget?monthly_budget=500")
        mock_svc.get_budget_status.assert_awaited_once_with(monthly_budget=500.0)

    def test_budget_below_minimum_returns_422(self):
        client = TestClient(_build_app())
        resp = client.get("/api/metrics/costs/budget?monthly_budget=5.0")
        assert resp.status_code == 422

    def test_db_error_returns_500(self):
        failing_svc = AsyncMock()
        failing_svc.get_budget_status = AsyncMock(side_effect=RuntimeError("DB error"))
        with patch("routes.metrics_routes.CostAggregationService", return_value=failing_svc):
            client = TestClient(_build_app())
            resp = client.get("/api/metrics/costs/budget")
        assert resp.status_code == 500


