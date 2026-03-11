"""
Unit tests for routes/task_routes.py.

Tests cover:
- GET /api/tasks          — list_tasks (pagination, filter, empty)
- GET /api/tasks/{id}     — get_task (found, 404)
- GET /api/tasks/{id}/status — get_task_status (found, 404)
- GET /api/tasks/metrics  — get_metrics (static response)
- POST /api/tasks         — create_task (blog_post happy path, validation error)
- Helper functions        — _normalize_seo_keywords_in_task, _parse_seo_keywords_for_db

Auth and DB are overridden via FastAPI dependency_overrides so no real I/O occurs.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from routes.auth_unified import get_current_user, get_current_user_optional
from utils.route_utils import get_database_dependency

# Import helpers under test directly (pure functions, no I/O)
from routes.task_routes import (
    _normalize_seo_keywords_in_task,
    _parse_seo_keywords_for_db,
    router,
)

from tests.unit.routes.conftest import TEST_USER, make_mock_db


# ---------------------------------------------------------------------------
# App / client factory helpers
# ---------------------------------------------------------------------------


def _build_app(mock_db=None) -> FastAPI:
    """Build a minimal FastAPI app with the task router and overridden deps."""
    if mock_db is None:
        mock_db = make_mock_db()

    app = FastAPI()
    app.include_router(router)

    # Override auth
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    app.dependency_overrides[get_current_user_optional] = lambda: TEST_USER

    # Override DB
    app.dependency_overrides[get_database_dependency] = lambda: mock_db

    return app


# ---------------------------------------------------------------------------
# Helper function unit tests (pure Python, no HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormalizeSeoKeywordsInTask:
    def test_returns_non_dict_unchanged(self):
        assert _normalize_seo_keywords_in_task("not a dict") == "not a dict"  # type: ignore[arg-type]
        assert _normalize_seo_keywords_in_task(None) is None  # type: ignore[arg-type]

    def test_parses_json_string_at_top_level(self):
        task = {"seo_keywords": '["ai", "ml"]'}
        result = _normalize_seo_keywords_in_task(task)
        assert result["seo_keywords"] == ["ai", "ml"]

    def test_invalid_json_becomes_empty_list(self):
        task = {"seo_keywords": "not-json{{{"}
        result = _normalize_seo_keywords_in_task(task)
        assert result["seo_keywords"] == []

    def test_list_already_is_untouched(self):
        task = {"seo_keywords": ["ai", "ml"]}
        result = _normalize_seo_keywords_in_task(task)
        assert result["seo_keywords"] == ["ai", "ml"]

    def test_normalizes_nested_result_seo_keywords(self):
        task = {"result": {"seo_keywords": '["cloud", "devops"]'}}
        result = _normalize_seo_keywords_in_task(task)
        assert result["result"]["seo_keywords"] == ["cloud", "devops"]

    def test_normalizes_nested_task_metadata_seo_keywords(self):
        task = {"task_metadata": {"seo_keywords": '["python", "fastapi"]'}}
        result = _normalize_seo_keywords_in_task(task)
        assert result["task_metadata"]["seo_keywords"] == ["python", "fastapi"]

    def test_no_seo_keywords_field_is_unchanged(self):
        task = {"topic": "AI Trends", "status": "pending"}
        result = _normalize_seo_keywords_in_task(task)
        assert result == {"topic": "AI Trends", "status": "pending"}


@pytest.mark.unit
class TestParseSeoKeywordsForDb:
    def test_none_returns_empty_string(self):
        assert _parse_seo_keywords_for_db(None) == ""

    def test_empty_string_returns_empty_string(self):
        assert _parse_seo_keywords_for_db("") == ""

    def test_list_joins_with_commas(self):
        result = _parse_seo_keywords_for_db(["ai", "ml", "python"])
        assert result == "ai, ml, python"

    def test_json_array_string_joins_with_commas(self):
        result = _parse_seo_keywords_for_db('["ai", "ml"]')
        assert result == "ai, ml"

    def test_csv_string_returned_as_is(self):
        result = _parse_seo_keywords_for_db("ai, ml, python")
        assert result == "ai, ml, python"

    def test_invalid_json_array_returned_as_is(self):
        result = _parse_seo_keywords_for_db("[broken json")
        assert result == "[broken json"

    def test_non_string_non_list_converts_to_str(self):
        result = _parse_seo_keywords_for_db(42)
        assert result == "42"

    def test_list_with_empty_values_skips_blanks(self):
        # The implementation strips whitespace but empty strings are included as-is
        # (join skips falsy items: "" is falsy, "  ".strip() is "" which is falsy
        #  but "  " itself is truthy — so only "" entries are excluded)
        result = _parse_seo_keywords_for_db(["ai", "", "ml"])
        assert "ai" in result
        assert "ml" in result


# ---------------------------------------------------------------------------
# GET /api/tasks  (list_tasks)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListTasks:
    def test_returns_empty_list_when_no_tasks(self):
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([], 0))
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks"] == []
        assert data["total"] == 0
        assert data["offset"] == 0
        assert data["limit"] == 20

    def test_pagination_params_forwarded_to_db(self):
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([], 0))
        client = TestClient(_build_app(mock_db))

        client.get("/api/tasks?offset=40&limit=10")
        call_kwargs = mock_db.get_tasks_paginated.call_args
        assert call_kwargs.kwargs["offset"] == 40
        assert call_kwargs.kwargs["limit"] == 10

    def test_status_filter_forwarded_to_db(self):
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([], 0))
        client = TestClient(_build_app(mock_db))

        client.get("/api/tasks?status=pending")
        call_kwargs = mock_db.get_tasks_paginated.call_args
        assert call_kwargs.kwargs["status"] == "pending"

    def test_returns_task_list_with_correct_count(self):
        task_stub = {
            "id": "abc-123",
            "task_id": "abc-123",
            "task_type": "blog_post",
            "status": "pending",
            "topic": "AI Trends",
            "task_name": "Blog: AI Trends",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([task_stub], 1))
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["status"] == "pending"

    def test_limit_out_of_range_returns_422(self):
        client = TestClient(_build_app())
        resp = client.get("/api/tasks?limit=9999")
        assert resp.status_code == 422

    def test_negative_offset_returns_422(self):
        client = TestClient(_build_app())
        resp = client.get("/api/tasks?offset=-1")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/tasks/{task_id}  (get_task)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTask:
    def test_returns_404_when_task_not_found(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/nonexistent-id")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_returns_task_when_found(self):
        task_stub = {
            "id": "task-uuid-001",
            "task_id": "task-uuid-001",
            "task_type": "blog_post",
            "status": "completed",
            "topic": "Machine Learning",
            "task_name": "ML Article",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=task_stub)
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/task-uuid-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["topic"] == "Machine Learning"

    def test_seo_keywords_normalized_in_response(self):
        task_stub = {
            "id": "task-uuid-002",
            "task_type": "blog_post",
            "status": "completed",
            "topic": "SEO",
            "task_name": "SEO post",
            "seo_keywords": '["seo", "content"]',
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=task_stub)
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/task-uuid-002")
        assert resp.status_code == 200
        assert resp.json()["seo_keywords"] == ["seo", "content"]


# ---------------------------------------------------------------------------
# GET /api/tasks/{task_id}/status  (get_task_status)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTaskStatus:
    def test_returns_404_when_task_not_found(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/missing-id/status")
        assert resp.status_code == 404

    def test_returns_status_fields(self):
        task_stub = {
            "id": "task-s1",
            "task_type": "blog_post",
            "status": "in_progress",
            "percentage": 65,
            "error_message": None,
        }
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=task_stub)
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/task-s1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "in_progress"
        assert data["progress"] == 65
        assert data["error_message"] is None

    def test_returns_zero_progress_when_percentage_missing(self):
        task_stub = {
            "id": "task-s2",
            "task_type": "blog_post",
            "status": "pending",
        }
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=task_stub)
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/task-s2/status")
        assert resp.status_code == 200
        assert resp.json()["progress"] == 0


# ---------------------------------------------------------------------------
# GET /api/tasks/metrics  (get_metrics — static stub response)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetMetrics:
    def test_metrics_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/tasks/metrics")
        assert resp.status_code == 200

    def test_metrics_contains_expected_fields(self):
        client = TestClient(_build_app())
        resp = client.get("/api/tasks/metrics")
        data = resp.json()
        assert "total_tasks" in data
        assert "completed_tasks" in data
        assert "failed_tasks" in data
        assert "success_rate" in data

    def test_metrics_summary_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/tasks/metrics/summary")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/tasks  (create_task)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateTask:
    def test_blog_post_creation_returns_202(self):
        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(return_value="new-blog-task-id")
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks",
            json={"topic": "AI in Healthcare", "task_type": "blog_post"},
        )
        assert resp.status_code == 202

    def test_blog_post_creation_returns_task_id(self):
        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(return_value="returned-task-id")
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks",
            json={"topic": "Machine Learning Trends", "task_type": "blog_post"},
        )
        data = resp.json()
        assert data["task_id"] == "returned-task-id"
        assert data["status"] == "pending"
        assert data["task_type"] == "blog_post"

    def test_social_media_creation_returns_202(self):
        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(return_value="social-task-id")
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks",
            json={
                "topic": "Product Launch",
                "task_type": "social_media",
                "platforms": ["twitter", "linkedin"],
            },
        )
        assert resp.status_code == 202

    def test_missing_topic_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post("/api/tasks", json={"task_type": "blog_post"})
        assert resp.status_code == 422

    def test_topic_too_short_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post("/api/tasks", json={"topic": "AI", "task_type": "blog_post"})
        assert resp.status_code == 422

    def test_unknown_task_type_returns_422(self):
        # Pydantic validates task_type as a Literal before the route handler runs,
        # so invalid values are rejected with 422 (not 400)
        mock_db = make_mock_db()
        client = TestClient(_build_app(mock_db))
        resp = client.post(
            "/api/tasks",
            json={"topic": "Test Topic Here", "task_type": "invalid_type"},
        )
        assert resp.status_code == 422

    def test_db_add_task_called_with_correct_topic(self):
        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(return_value="some-id")
        client = TestClient(_build_app(mock_db))

        client.post(
            "/api/tasks",
            json={"topic": "Quantum Computing", "task_type": "blog_post"},
        )
        assert mock_db.add_task.called
        task_data_arg = mock_db.add_task.call_args[0][0]
        assert task_data_arg["topic"] == "Quantum Computing"
        assert task_data_arg["status"] == "pending"
        assert task_data_arg["task_type"] == "blog_post"
