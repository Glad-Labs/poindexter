"""
Unit tests for routes/metrics_routes.py.

Tests cover:
- GET /api/metrics               — get_metrics
- GET /api/metrics/summary       — get_metrics_summary
- GET /api/metrics/usage         — get_usage_metrics
- GET /api/metrics/costs         — get_cost_metrics (db-backed + tracker fallback)
- POST /api/metrics/track-usage  — track_usage
- GET /api/metrics/costs/breakdown/phase  — get_costs_by_phase
- GET /api/metrics/costs/breakdown/model  — get_costs_by_model
- GET /api/metrics/costs/history          — get_cost_history
- GET /api/metrics/costs/budget           — get_budget_status
- GET /api/metrics/performance            — get_performance_metrics
- GET /api/metrics/analytics/kpis — deprecated tombstone (308 redirect)
- GET /api/metrics/operational   — get_operational_metrics (no auth)

Auth and DB overridden where needed.
UsageTracker is patched to avoid global state dependency.
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


def _make_mock_tracker():
    tracker = MagicMock()
    tracker.completed_operations = []
    tracker.active_operations = {}
    return tracker


# ---------------------------------------------------------------------------
# GET /api/metrics (get_metrics)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetMetrics:
    def test_returns_200(self):
        with patch("routes.metrics_routes.get_usage_tracker", return_value=_make_mock_tracker()):
            client = TestClient(_build_app())
            resp = client.get("/api/metrics")
        assert resp.status_code == 200

    def test_response_has_status_key(self):
        with patch("routes.metrics_routes.get_usage_tracker", return_value=_make_mock_tracker()):
            client = TestClient(_build_app())
            data = client.get("/api/metrics").json()
        assert "status" in data
        assert data["status"] == "healthy"

    def test_response_has_uptime_and_task_counts(self):
        with patch("routes.metrics_routes.get_usage_tracker", return_value=_make_mock_tracker()):
            client = TestClient(_build_app())
            data = client.get("/api/metrics").json()
        assert "uptime_seconds" in data
        assert "active_tasks" in data
        assert "completed_tasks" in data

    def test_requires_auth(self):
        """When auth dependency is not overridden, requests without token should 401."""
        app = FastAPI()
        app.include_router(metrics_router)
        app.dependency_overrides[get_database_dependency] = lambda: make_mock_db()
        # No auth override — let the real verify_api_token run
        with patch.dict("os.environ", {"DEVELOPMENT_MODE": "false", "API_TOKEN": "secret"}):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/metrics")
        assert resp.status_code == 401

    def test_tracker_error_returns_500(self):
        with patch(
            "routes.metrics_routes.get_usage_tracker",
            side_effect=RuntimeError("tracker init failed"),
        ):
            client = TestClient(_build_app())
            resp = client.get("/api/metrics")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/metrics/summary (get_metrics_summary)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetMetricsSummary:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/metrics/summary")
        assert resp.status_code == 200

    def test_response_has_costs_and_performance(self):
        client = TestClient(_build_app())
        data = client.get("/api/metrics/summary").json()
        assert "costs" in data
        assert "performance" in data

    def test_costs_has_expected_fields(self):
        client = TestClient(_build_app())
        data = client.get("/api/metrics/summary").json()
        assert "total_cost_usd" in data["costs"]
        assert "total_tokens" in data["costs"]


# ---------------------------------------------------------------------------
# GET /api/metrics/analytics/kpis (deprecated 308 tombstone)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeprecatedKpiEndpoint:
    def test_returns_308_redirect(self):
        client = TestClient(_build_app(), follow_redirects=False)
        resp = client.get(
            "/api/metrics/analytics/kpis",
            headers={"Authorization": "Bearer token"},
        )
        # The tombstone returns a 308 redirect
        assert resp.status_code == 308

    def test_redirect_location_points_to_canonical(self):
        client = TestClient(_build_app(), follow_redirects=False)
        resp = client.get("/api/metrics/analytics/kpis")
        assert "/api/analytics/kpis" in resp.headers.get("location", "")


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


# ---------------------------------------------------------------------------
# GET /api/metrics/usage  (get_usage_metrics)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetUsageMetrics:
    def test_returns_200_with_no_operations(self):
        with patch("routes.metrics_routes.get_usage_tracker", return_value=_make_mock_tracker()):
            client = TestClient(_build_app())
            resp = client.get("/api/metrics/usage")
        assert resp.status_code == 200

    def test_response_has_required_fields_when_empty(self):
        with patch("routes.metrics_routes.get_usage_tracker", return_value=_make_mock_tracker()):
            client = TestClient(_build_app())
            data = client.get("/api/metrics/usage").json()
        assert "total_operations" in data
        assert "tokens" in data
        assert "costs" in data
        assert "operations" in data

    def test_zero_operations_when_empty(self):
        with patch("routes.metrics_routes.get_usage_tracker", return_value=_make_mock_tracker()):
            client = TestClient(_build_app())
            data = client.get("/api/metrics/usage").json()
        assert data["total_operations"] == 0

    def test_period_param_echoed(self):
        with patch("routes.metrics_routes.get_usage_tracker", return_value=_make_mock_tracker()):
            client = TestClient(_build_app())
            data = client.get("/api/metrics/usage?period=last_7d").json()
        assert data["period"] == "last_7d"

    def test_requires_auth(self):
        app = FastAPI()
        app.include_router(metrics_router)
        with patch.dict("os.environ", {"DEVELOPMENT_MODE": "false", "API_TOKEN": "secret"}):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/metrics/usage")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/metrics/costs  (get_cost_metrics)
# ---------------------------------------------------------------------------


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


@pytest.mark.unit
class TestGetCostMetrics:
    def test_returns_200_with_db_backend(self):
        mock_db = make_mock_db()
        with patch(
            "routes.metrics_routes.CostAggregationService", return_value=_make_mock_cost_service()
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.get("/api/metrics/costs")
        assert resp.status_code == 200

    def test_response_has_source_postgresql_when_db_succeeds(self):
        mock_db = make_mock_db()
        with patch(
            "routes.metrics_routes.CostAggregationService", return_value=_make_mock_cost_service()
        ):
            client = TestClient(_build_app(mock_db))
            data = client.get("/api/metrics/costs").json()
        assert data["source"] == "postgresql"

    def test_response_has_total_cost_key(self):
        mock_db = make_mock_db()
        with patch(
            "routes.metrics_routes.CostAggregationService", return_value=_make_mock_cost_service()
        ):
            client = TestClient(_build_app(mock_db))
            data = client.get("/api/metrics/costs").json()
        assert "total_cost" in data

    def test_falls_back_to_tracker_when_db_fails(self):
        """When CostAggregationService raises, fallback to tracker (use_db=False)."""
        mock_db = make_mock_db()
        failing_svc = AsyncMock()
        failing_svc.get_summary = AsyncMock(side_effect=RuntimeError("DB error"))
        with patch("routes.metrics_routes.CostAggregationService", return_value=failing_svc):
            with patch(
                "routes.metrics_routes.get_usage_tracker", return_value=_make_mock_tracker()
            ):
                client = TestClient(_build_app(mock_db))
                data = client.get("/api/metrics/costs").json()
        # Fell back to tracker path
        assert data["source"] == "tracker"

    def test_no_db_fallback_with_use_db_false(self):
        """use_db=false forces tracker path."""
        with patch("routes.metrics_routes.get_usage_tracker", return_value=_make_mock_tracker()):
            client = TestClient(_build_app())
            data = client.get("/api/metrics/costs?use_db=false").json()
        assert data["source"] == "tracker"

    def test_requires_auth(self):
        app = FastAPI()
        app.include_router(metrics_router)
        with patch.dict("os.environ", {"DEVELOPMENT_MODE": "false", "API_TOKEN": "secret"}):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/metrics/costs")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/metrics/track-usage  (track_usage)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTrackUsage:
    def test_returns_200_on_valid_payload(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/metrics/track-usage",
            json={"model": "mistral", "tokens": 500, "cost": 0.001},
        )
        assert resp.status_code == 200

    def test_response_has_success_true(self):
        client = TestClient(_build_app())
        data = client.post(
            "/api/metrics/track-usage",
            json={"model": "gpt-4", "tokens": 100, "cost": 0.01},
        ).json()
        assert data["success"] is True

    def test_response_message_contains_model_name(self):
        client = TestClient(_build_app())
        data = client.post(
            "/api/metrics/track-usage",
            json={"model": "claude-3", "tokens": 200, "cost": 0.005},
        ).json()
        assert "claude-3" in data["message"]

    def test_missing_model_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/metrics/track-usage",
            json={"tokens": 100, "cost": 0.01},
        )
        assert resp.status_code == 422

    def test_negative_tokens_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/metrics/track-usage",
            json={"model": "gpt-4", "tokens": -1, "cost": 0.01},
        )
        assert resp.status_code == 422

    def test_negative_cost_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/metrics/track-usage",
            json={"model": "gpt-4", "tokens": 100, "cost": -0.01},
        )
        assert resp.status_code == 422

    def test_requires_auth(self):
        app = FastAPI()
        app.include_router(metrics_router)
        with patch.dict("os.environ", {"DEVELOPMENT_MODE": "false", "API_TOKEN": "secret"}):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/metrics/track-usage",
                json={"model": "mistral", "tokens": 100, "cost": 0.0},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/metrics/costs/breakdown/phase  (get_costs_by_phase)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetCostsByPhase:
    def test_returns_200(self):
        mock_svc = _make_mock_cost_service()
        mock_svc.get_breakdown_by_phase = AsyncMock(
            return_value={"phases": [{"phase": "draft", "cost": 1.0}]}
        )
        with patch("routes.metrics_routes.CostAggregationService", return_value=mock_svc):
            client = TestClient(_build_app())
            resp = client.get("/api/metrics/costs/breakdown/phase?period=week")
        assert resp.status_code == 200

    def test_invalid_period_returns_422(self):
        client = TestClient(_build_app())
        resp = client.get("/api/metrics/costs/breakdown/phase?period=invalid")
        assert resp.status_code == 422

    def test_db_error_returns_500(self):
        failing_svc = AsyncMock()
        failing_svc.get_breakdown_by_phase = AsyncMock(side_effect=RuntimeError("DB error"))
        with patch("routes.metrics_routes.CostAggregationService", return_value=failing_svc):
            client = TestClient(_build_app())
            resp = client.get("/api/metrics/costs/breakdown/phase")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/metrics/costs/breakdown/model  (get_costs_by_model)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetCostsByModel:
    def test_returns_200(self):
        mock_svc = _make_mock_cost_service()
        mock_svc.get_breakdown_by_model = AsyncMock(return_value={"models": []})
        with patch("routes.metrics_routes.CostAggregationService", return_value=mock_svc):
            client = TestClient(_build_app())
            resp = client.get("/api/metrics/costs/breakdown/model?period=month")
        assert resp.status_code == 200

    def test_invalid_period_returns_422(self):
        client = TestClient(_build_app())
        resp = client.get("/api/metrics/costs/breakdown/model?period=invalid")
        assert resp.status_code == 422

    def test_db_error_returns_500(self):
        failing_svc = AsyncMock()
        failing_svc.get_breakdown_by_model = AsyncMock(side_effect=RuntimeError("DB error"))
        with patch("routes.metrics_routes.CostAggregationService", return_value=failing_svc):
            client = TestClient(_build_app())
            resp = client.get("/api/metrics/costs/breakdown/model")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/metrics/costs/history  (get_cost_history)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetCostHistory:
    def test_returns_200_for_week(self):
        mock_svc = _make_mock_cost_service()
        mock_svc.get_history = AsyncMock(return_value={"entries": [], "trend": "stable"})
        with patch("routes.metrics_routes.CostAggregationService", return_value=mock_svc):
            client = TestClient(_build_app())
            resp = client.get("/api/metrics/costs/history?period=week")
        assert resp.status_code == 200

    def test_returns_200_for_month(self):
        mock_svc = _make_mock_cost_service()
        mock_svc.get_history = AsyncMock(return_value={"entries": [], "trend": "up"})
        with patch("routes.metrics_routes.CostAggregationService", return_value=mock_svc):
            client = TestClient(_build_app())
            resp = client.get("/api/metrics/costs/history?period=month")
        assert resp.status_code == 200

    def test_invalid_period_returns_422(self):
        client = TestClient(_build_app())
        resp = client.get("/api/metrics/costs/history?period=today")
        assert resp.status_code == 422

    def test_db_error_returns_500(self):
        failing_svc = AsyncMock()
        failing_svc.get_history = AsyncMock(side_effect=RuntimeError("DB error"))
        with patch("routes.metrics_routes.CostAggregationService", return_value=failing_svc):
            client = TestClient(_build_app())
            resp = client.get("/api/metrics/costs/history")
        assert resp.status_code == 500


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


# ---------------------------------------------------------------------------
# GET /api/metrics/performance  (get_performance_metrics)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPerformanceMetrics:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/metrics/performance")
        assert resp.status_code == 200

    def test_response_has_overall_stats(self):
        client = TestClient(_build_app())
        data = client.get("/api/metrics/performance").json()
        assert "overall_stats" in data

    def test_response_has_route_latencies(self):
        client = TestClient(_build_app())
        data = client.get("/api/metrics/performance").json()
        assert "route_latencies" in data

    def test_requires_auth(self):
        app = FastAPI()
        app.include_router(metrics_router)
        with patch.dict("os.environ", {"DEVELOPMENT_MODE": "false", "API_TOKEN": "secret"}):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/metrics/performance")
        assert resp.status_code == 401
