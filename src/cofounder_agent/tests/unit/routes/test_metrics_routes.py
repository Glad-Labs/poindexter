"""
Unit tests for routes/metrics_routes.py.

Tests cover:
- GET /api/metrics/costs/budget           — get_budget_status
- GET /api/metrics/operational   — get_operational_metrics (auth required)

Auth and DB overridden where needed.

The operational endpoint was rewired in poindexter#395 to read directly
from ``pipeline_tasks`` (LIVE table) and the running ``PluginScheduler``
instead of the stale ``content_tasks`` + ``TaskExecutor`` surfaces. Tests
mock the asyncpg pool + ``app.state.plugin_scheduler`` accordingly.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.metrics_routes import metrics_router
from tests.unit.routes.conftest import make_mock_db
from utils.route_utils import get_database_dependency

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


class _FakePoolCtx:
    """Async context manager that yields a connection mock."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _make_pipeline_tasks_db(*, open_rows=None, recent_rows=None, raise_on_fetch=False):
    """Build a mock ``DatabaseService`` exposing a ``pool`` whose
    ``acquire().fetch()`` returns the seeded rows. The endpoint issues
    two ``fetch`` calls in order — first the open-status query, then the
    recent failed/completed query — so we side-effect them in sequence.
    """
    open_rows = open_rows or []
    recent_rows = recent_rows or []

    conn = MagicMock()
    if raise_on_fetch:
        conn.fetch = AsyncMock(side_effect=RuntimeError("DB error"))
    else:
        conn.fetch = AsyncMock(side_effect=[open_rows, recent_rows])

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolCtx(conn))

    db = make_mock_db()
    db.pool = pool
    return db, conn


def _build_app(mock_db=None, with_auth=True, plugin_scheduler=None) -> FastAPI:
    if mock_db is None:
        mock_db, _ = _make_pipeline_tasks_db()

    app = FastAPI()
    app.include_router(metrics_router)
    # The endpoint reads from ``request.app.state.plugin_scheduler`` and
    # ``request.app.state.site_config`` — leave them unset by default to
    # exercise the safe-default path, override per test where needed.
    app.state.plugin_scheduler = plugin_scheduler

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
        """Operational metrics requires auth (#1011)."""
        mock_db, _ = _make_pipeline_tasks_db()

        # Build app with NO auth override — should require auth
        app = FastAPI()
        app.include_router(metrics_router)
        app.state.plugin_scheduler = None
        app.dependency_overrides[get_database_dependency] = lambda: mock_db
        with patch.dict("os.environ", {"DEVELOPMENT_MODE": "false", "API_TOKEN": "secret"}):
            client = TestClient(app)
            resp = client.get("/api/metrics/operational")
        assert resp.status_code == 401

    def test_response_has_required_operational_fields(self):
        """Sanity: top-level shape stays stable across the #395 rewire."""
        mock_db, _ = _make_pipeline_tasks_db()

        client = TestClient(_build_app(mock_db))
        data = client.get("/api/metrics/operational").json()
        assert "task_queue" in data
        assert "executor" in data  # legacy back-compat block
        assert "scheduler" in data  # new canonical block
        assert "websocket_connections" in data
        assert "uptime_seconds" in data
        assert "timestamp" in data

    def test_task_queue_has_status_breakdown_and_window(self):
        """task_queue exposes the four status counters + the window the
        recent-status counters were aggregated over."""
        mock_db, _ = _make_pipeline_tasks_db()

        client = TestClient(_build_app(mock_db))
        data = client.get("/api/metrics/operational").json()
        tq = data["task_queue"]
        assert "pending" in tq
        assert "in_progress" in tq
        assert "failed" in tq
        assert "completed" in tq
        assert tq["window_hours"] == 24
        assert tq["source"] == "pipeline_tasks"

    def test_task_counts_match_seeded_pipeline_tasks_rows(self):
        """With seeded pipeline_tasks rows the response counts match."""
        open_rows = [
            {"status": "pending", "n": 4},
            {"status": "in_progress", "n": 2},
        ]
        recent_rows = [
            {"status": "failed", "n": 1},
            {"status": "completed", "n": 17},
        ]
        mock_db, _ = _make_pipeline_tasks_db(open_rows=open_rows, recent_rows=recent_rows)

        client = TestClient(_build_app(mock_db))
        data = client.get("/api/metrics/operational").json()
        tq = data["task_queue"]
        assert tq["pending"] == 4
        assert tq["in_progress"] == 2
        assert tq["failed"] == 1
        assert tq["completed"] == 17

    def test_db_unavailable_still_returns_200(self):
        """When the DB raises, operational metrics gracefully degrade."""
        mock_db, _ = _make_pipeline_tasks_db(raise_on_fetch=True)

        client = TestClient(_build_app(mock_db))
        resp = client.get("/api/metrics/operational")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_queue"]["pending"] == 0
        assert data["task_queue"]["completed"] == 0

    def test_executor_fields_present_for_legacy_compat(self):
        """The legacy ``executor`` block is still emitted so the existing
        Grafana panel + ``poindexter costs operational`` CLI keep
        resolving until consumers migrate to ``scheduler``."""
        mock_db, _ = _make_pipeline_tasks_db()

        client = TestClient(_build_app(mock_db))
        data = client.get("/api/metrics/operational").json()
        executor = data["executor"]
        assert "task_count" in executor
        assert "success_count" in executor
        assert "error_count" in executor
        assert "is_running" in executor
        # Marked as legacy so future readers don't grow new dependencies on it.
        assert "_deprecated_legacy" in executor

    def test_scheduler_block_uses_plugin_scheduler_stats(self):
        """When PluginScheduler is bound to ``app.state``, its get_stats
        output is surfaced under ``scheduler`` AND mirrored into the
        legacy ``executor`` block."""
        mock_db, _ = _make_pipeline_tasks_db()
        scheduler = MagicMock()
        scheduler.get_stats = MagicMock(
            return_value={
                "is_running": True,
                "registered_job_count": 5,
                "jobs_run": 42,
                "jobs_succeeded": 40,
                "jobs_failed": 2,
                "last_tick_epoch": 1730000000.0,
                "next_run_epoch": 1730000600.0,
            }
        )

        client = TestClient(_build_app(mock_db, plugin_scheduler=scheduler))
        data = client.get("/api/metrics/operational").json()
        sched = data["scheduler"]
        assert sched["is_running"] is True
        assert sched["registered_job_count"] == 5
        assert sched["jobs_run"] == 42
        assert sched["jobs_succeeded"] == 40
        assert sched["jobs_failed"] == 2
        assert sched["last_tick_epoch"] == 1730000000.0
        assert sched["next_run_epoch"] == 1730000600.0
        # Legacy executor block mirrors the same numbers under the
        # pre-#395 field names.
        executor = data["executor"]
        assert executor["is_running"] is True
        assert executor["task_count"] == 42
        assert executor["success_count"] == 40
        assert executor["error_count"] == 2

    def test_scheduler_unavailable_returns_200_with_safe_defaults(self):
        """When ``app.state.plugin_scheduler`` is None (worker not started
        yet, or alternate deployment mode), the endpoint still returns
        200 and the scheduler block is filled with safe zeros."""
        mock_db, _ = _make_pipeline_tasks_db()

        # plugin_scheduler defaults to None in _build_app
        client = TestClient(_build_app(mock_db, plugin_scheduler=None))
        resp = client.get("/api/metrics/operational")
        assert resp.status_code == 200
        data = resp.json()
        sched = data["scheduler"]
        assert sched["is_running"] is False
        assert sched["jobs_run"] == 0
        assert sched["jobs_failed"] == 0
        assert sched["last_tick_epoch"] is None
        # Legacy executor block also gets the safe zeros.
        assert data["executor"]["is_running"] is False
        assert data["executor"]["task_count"] == 0


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


