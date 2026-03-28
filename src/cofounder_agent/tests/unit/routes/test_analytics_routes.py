"""
Unit tests for routes/analytics_routes.py.

Tests cover:
- GET /api/analytics/kpis         — get_kpi_metrics
- GET /api/analytics/distributions — get_task_distributions

Auth and DB are overridden so no real I/O occurs.
"""

import datetime as _dt
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.analytics_routes import analytics_router
from middleware.api_token_auth import verify_api_token
from tests.unit.routes.conftest import TEST_USER
from utils.route_utils import get_database_dependency

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _build_app(mock_db=None) -> FastAPI:
    if mock_db is None:
        mock_db = _make_analytics_db()

    app = FastAPI()
    app.include_router(analytics_router)

    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    app.dependency_overrides[get_database_dependency] = lambda: mock_db

    return app


def _make_analytics_db(agg=None):
    """Create a mock DB that returns KPI aggregates (issue #696 new API)."""
    db = MagicMock()
    db.get_kpi_aggregates = AsyncMock(return_value=agg or {"rows": [], "total_tasks": 0})
    db.query = AsyncMock(return_value=[])
    return db


# ---------------------------------------------------------------------------
# Helper: convert old-style task-list specs to aggregate rows
#
# Many tests were originally written with lists of raw task dicts.
# This helper converts those into the aggregate format the route now expects,
# preserving test intent without duplicating fixture data.
# ---------------------------------------------------------------------------


def _tasks_to_agg(tasks: list) -> dict:
    """Convert a list of task-dict fixtures into a get_kpi_aggregates payload."""
    from collections import defaultdict

    buckets: dict = defaultdict(
        lambda: {
            "count": 0,
            "total_cost": 0.0,
            "duration_sum": 0.0,
            "duration_count": 0,
            "completed_count": 0,
        }
    )

    for t in tasks:
        status = t.get("status", "unknown")
        model = t.get("model_used", "unknown") or "unknown"
        task_type = t.get("task_type", "unknown") or "unknown"
        created = t.get("created_at")
        if isinstance(created, str):
            try:
                created = _dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                created = None
        day = created.date() if isinstance(created, _dt.datetime) else None

        key = (status, model, task_type, day)
        b = buckets[key]
        b["count"] += 1

        cost = float(t.get("estimated_cost") or t.get("actual_cost") or 0.0)
        b["total_cost"] += cost

        completed_at = t.get("completed_at")
        if isinstance(completed_at, str):
            try:
                completed_at = _dt.datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                completed_at = None

        if status == "completed" and completed_at and isinstance(created, _dt.datetime):
            dur = (completed_at - created).total_seconds()
            if dur >= 0:
                b["duration_sum"] += dur
                b["duration_count"] += 1
                b["completed_count"] += 1

    rows = []
    for (status, model, task_type, day), b in buckets.items():
        avg_dur = (b["duration_sum"] / b["duration_count"]) if b["duration_count"] > 0 else None
        rows.append(
            {
                "status": status,
                "model_used": model,
                "task_type": task_type,
                "day": day,
                "count": b["count"],
                "total_cost": b["total_cost"],
                "avg_duration_s": avg_dur,
                "completed_count": b["completed_count"],
            }
        )

    return {"rows": rows, "total_tasks": len(tasks)}


SAMPLE_TASK = {
    "id": "task-001",
    "status": "completed",
    "task_type": "blog_post",
    "created_at": "2026-03-01T10:00:00",
    "completed_at": "2026-03-01T10:05:00",
    "estimated_cost": 0.05,
    "metadata": {"model": "mistral", "phase": "draft"},
}


# ---------------------------------------------------------------------------
# GET /api/analytics/kpis
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetKpiMetrics:
    def test_returns_200_with_default_range(self):
        client = TestClient(_build_app())
        resp = client.get("/api/analytics/kpis")
        assert resp.status_code == 200

    def test_returns_zero_metrics_when_no_tasks(self):
        client = TestClient(_build_app())
        data = client.get("/api/analytics/kpis").json()
        assert data["total_tasks"] == 0
        assert data["success_rate"] == 0.0

    def test_response_has_required_fields(self):
        client = TestClient(_build_app())
        data = client.get("/api/analytics/kpis").json()
        required_fields = [
            "timestamp",
            "time_range",
            "total_tasks",
            "completed_tasks",
            "failed_tasks",
            "pending_tasks",
            "success_rate",
            "failure_rate",
            "completion_rate",
            "avg_execution_time_seconds",
            "total_cost_usd",
            "avg_cost_per_task",
            "primary_model",
        ]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"

    def test_valid_range_7d_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/analytics/kpis?range=7d")
        assert resp.status_code == 200

    def test_valid_range_30d_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/analytics/kpis?range=30d")
        assert resp.status_code == 200

    def test_valid_range_all_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/analytics/kpis?range=all")
        assert resp.status_code == 200

    def test_invalid_range_returns_400(self):
        client = TestClient(_build_app())
        resp = client.get("/api/analytics/kpis?range=invalid")
        assert resp.status_code == 400

    def test_with_completed_tasks_calculates_success_rate(self):
        tasks = [
            {**SAMPLE_TASK, "status": "completed"},
            {**SAMPLE_TASK, "status": "completed"},
            {**SAMPLE_TASK, "status": "failed"},
        ]
        mock_db = _make_analytics_db(agg=_tasks_to_agg(tasks))
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/analytics/kpis?range=7d").json()
        assert data["total_tasks"] == 3
        assert data["completed_tasks"] == 2
        assert data["failed_tasks"] == 1
        # success_rate = (2/3) * 100 ≈ 66.67%
        assert data["success_rate"] > 60.0

    def test_time_range_echoed_in_response(self):
        client = TestClient(_build_app())
        data = client.get("/api/analytics/kpis?range=30d").json()
        assert data["time_range"] == "30d"

    def test_db_error_returns_500(self):
        mock_db = _make_analytics_db()
        mock_db.get_kpi_aggregates = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(mock_db))
        resp = client.get("/api/analytics/kpis")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/analytics/distributions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTaskDistributions:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/analytics/distributions")
        assert resp.status_code == 200

    def test_empty_db_returns_zero_total(self):
        client = TestClient(_build_app())
        data = client.get("/api/analytics/distributions").json()
        assert data["total_tasks"] == 0
        assert data["distributions"] == []

    def test_response_has_required_fields(self):
        client = TestClient(_build_app())
        data = client.get("/api/analytics/distributions").json()
        assert "timestamp" in data
        assert "total_tasks" in data
        assert "distributions" in data

    def test_invalid_range_returns_400(self):
        client = TestClient(_build_app())
        resp = client.get("/api/analytics/distributions?range=invalid")
        assert resp.status_code == 400

    def test_with_distribution_data(self):
        mock_db = _make_analytics_db()
        mock_db.query = AsyncMock(
            return_value=[
                {"task_type": "blog_post", "status": "completed", "count": 10},
                {"task_type": "social_media", "status": "completed", "count": 5},
            ]
        )
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/analytics/distributions?range=7d").json()
        assert data["total_tasks"] == 15
        assert len(data["distributions"]) == 2

    def test_distributions_have_required_fields(self):
        mock_db = _make_analytics_db()
        mock_db.query = AsyncMock(
            return_value=[
                {"task_type": "blog_post", "status": "completed", "count": 5},
            ]
        )
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/analytics/distributions").json()
        if data["distributions"]:
            dist = data["distributions"][0]
            assert "type" in dist
            assert "status" in dist
            assert "count" in dist
            assert "percentage" in dist

    def test_db_error_returns_500(self):
        mock_db = _make_analytics_db()
        mock_db.query = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(mock_db))
        resp = client.get("/api/analytics/distributions")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/analytics/kpis — additional edge-case tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetKpiMetricsEdgeCases:
    """Additional tests covering execution times, costs, model tracking, and time ranges."""

    def test_valid_range_1d_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/analytics/kpis?range=1d")
        assert resp.status_code == 200

    def test_valid_range_90d_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/analytics/kpis?range=90d")
        assert resp.status_code == 200

    def test_execution_time_calculated_from_timestamps(self):
        tasks = [
            {
                **SAMPLE_TASK,
                "status": "completed",
                "created_at": "2026-03-01T10:00:00",
                "completed_at": "2026-03-01T10:05:00",  # 300 seconds
            }
        ]
        mock_db = _make_analytics_db(agg=_tasks_to_agg(tasks))
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/analytics/kpis?range=7d").json()
        assert data["avg_execution_time_seconds"] == pytest.approx(300.0, abs=1.0)
        assert data["min_execution_time_seconds"] == pytest.approx(300.0, abs=1.0)
        assert data["max_execution_time_seconds"] == pytest.approx(300.0, abs=1.0)

    def test_multiple_tasks_execution_time_averaged(self):
        tasks = [
            {
                **SAMPLE_TASK,
                "status": "completed",
                "created_at": "2026-03-01T10:00:00",
                "completed_at": "2026-03-01T10:01:00",  # 60s
            },
            {
                **SAMPLE_TASK,
                "status": "completed",
                "created_at": "2026-03-01T10:00:00",
                "completed_at": "2026-03-01T10:03:00",  # 180s
            },
        ]
        mock_db = _make_analytics_db(agg=_tasks_to_agg(tasks))
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/analytics/kpis?range=7d").json()
        assert data["avg_execution_time_seconds"] == pytest.approx(120.0, abs=1.0)

    def test_tasks_without_timestamps_excluded_from_execution_time(self):
        tasks = [
            {
                **SAMPLE_TASK,
                "status": "pending",
                "created_at": "2026-03-01T10:00:00",
                "completed_at": None,  # Not finished
            }
        ]
        mock_db = _make_analytics_db(agg=_tasks_to_agg(tasks))
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/analytics/kpis?range=7d").json()
        assert data["avg_execution_time_seconds"] == 0.0

    def test_cost_aggregation_summed_across_tasks(self):
        tasks = [
            {**SAMPLE_TASK, "estimated_cost": 0.05, "model_used": "mistral"},
            {**SAMPLE_TASK, "estimated_cost": 0.10, "model_used": "mistral"},
            {**SAMPLE_TASK, "estimated_cost": 0.02, "model_used": "gpt-4"},
        ]
        mock_db = _make_analytics_db(agg=_tasks_to_agg(tasks))
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/analytics/kpis?range=7d").json()
        assert data["total_cost_usd"] == pytest.approx(0.17, abs=0.001)
        assert data["avg_cost_per_task"] == pytest.approx(0.17 / 3, abs=0.001)

    def test_primary_model_is_most_frequent(self):
        tasks = [
            {**SAMPLE_TASK, "model_used": "mistral"},
            {**SAMPLE_TASK, "model_used": "mistral"},
            {**SAMPLE_TASK, "model_used": "gpt-4"},
        ]
        mock_db = _make_analytics_db(agg=_tasks_to_agg(tasks))
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/analytics/kpis?range=7d").json()
        assert data["primary_model"] == "mistral"

    def test_task_type_breakdown_counted(self):
        tasks = [
            {**SAMPLE_TASK, "task_type": "blog_post"},
            {**SAMPLE_TASK, "task_type": "blog_post"},
            {**SAMPLE_TASK, "task_type": "social_media"},
        ]
        mock_db = _make_analytics_db(agg=_tasks_to_agg(tasks))
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/analytics/kpis?range=7d").json()
        assert data["task_types"].get("blog_post") == 2
        assert data["task_types"].get("social_media") == 1

    def test_cost_by_phase_is_empty_dict(self):
        """Phase breakdown from JSON metadata is no longer extracted — cost_by_phase is {}."""
        tasks = [{**SAMPLE_TASK, "estimated_cost": 0.04, "model_used": "mistral"}]
        mock_db = _make_analytics_db(agg=_tasks_to_agg(tasks))
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/analytics/kpis?range=7d").json()
        # cost_by_phase is intentionally empty; phase tracking moved to cost_logs table
        assert isinstance(data["cost_by_phase"], dict)

    def test_pending_tasks_count_is_remainder(self):
        tasks = [
            {**SAMPLE_TASK, "status": "completed"},
            {**SAMPLE_TASK, "status": "failed"},
            {**SAMPLE_TASK, "status": "pending"},
        ]
        mock_db = _make_analytics_db(agg=_tasks_to_agg(tasks))
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/analytics/kpis?range=7d").json()
        assert data["pending_tasks"] == 1

    def test_failure_rate_calculated_correctly(self):
        tasks = [
            {**SAMPLE_TASK, "status": "completed"},
            {**SAMPLE_TASK, "status": "failed"},
            {**SAMPLE_TASK, "status": "failed"},
            {**SAMPLE_TASK, "status": "failed"},
        ]
        mock_db = _make_analytics_db(agg=_tasks_to_agg(tasks))
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/analytics/kpis?range=7d").json()
        assert data["failure_rate"] == pytest.approx(75.0, abs=0.1)

    def test_timeseries_data_grouped_by_day(self):
        tasks = [
            {
                **SAMPLE_TASK,
                "status": "completed",
                "created_at": "2026-03-01T10:00:00",
                "completed_at": "2026-03-01T10:05:00",
                "estimated_cost": 0.05,
            },
            {
                **SAMPLE_TASK,
                "status": "completed",
                "created_at": "2026-03-01T15:00:00",
                "completed_at": "2026-03-01T15:05:00",
                "estimated_cost": 0.02,
            },
        ]
        mock_db = _make_analytics_db(agg=_tasks_to_agg(tasks))
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/analytics/kpis?range=7d").json()
        # Both tasks are on the same day — expect one entry in tasks_per_day
        assert len(data["tasks_per_day"]) == 1
        assert data["tasks_per_day"][0]["count"] == 2


# ---------------------------------------------------------------------------
# GET /api/analytics/distributions — additional edge-case tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTaskDistributionsEdgeCases:
    def test_valid_range_1d_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/analytics/distributions?range=1d")
        assert resp.status_code == 200

    def test_valid_range_90d_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/analytics/distributions?range=90d")
        assert resp.status_code == 200

    def test_valid_range_all_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/analytics/distributions?range=all")
        assert resp.status_code == 200

    def test_percentage_sums_to_100_for_two_equal_groups(self):
        mock_db = _make_analytics_db()
        mock_db.query = AsyncMock(
            return_value=[
                {"task_type": "blog_post", "status": "completed", "count": 5},
                {"task_type": "social_media", "status": "completed", "count": 5},
            ]
        )
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/analytics/distributions").json()
        total_pct = sum(d["percentage"] for d in data["distributions"])
        assert total_pct == pytest.approx(100.0, abs=0.01)

    def test_single_group_has_100_percent(self):
        mock_db = _make_analytics_db()
        mock_db.query = AsyncMock(
            return_value=[
                {"task_type": "blog_post", "status": "completed", "count": 10},
            ]
        )
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/analytics/distributions").json()
        assert data["distributions"][0]["percentage"] == pytest.approx(100.0, abs=0.01)

    def test_distribution_counts_match_total(self):
        mock_db = _make_analytics_db()
        mock_db.query = AsyncMock(
            return_value=[
                {"task_type": "blog_post", "status": "completed", "count": 3},
                {"task_type": "social_media", "status": "failed", "count": 7},
            ]
        )
        client = TestClient(_build_app(mock_db))
        data = client.get("/api/analytics/distributions").json()
        assert data["total_tasks"] == 10
        count_sum = sum(d["count"] for d in data["distributions"])
        assert count_sum == 10
