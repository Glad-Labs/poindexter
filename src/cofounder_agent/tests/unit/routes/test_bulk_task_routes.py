"""
Unit tests for routes/bulk_task_routes.py.

Tests cover:
- POST /api/tasks/bulk           — bulk_task_operations
- POST /api/tasks/bulk/create    — bulk_create_tasks

Auth and DB are overridden; no real I/O occurs.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.bulk_task_routes import router
from tests.unit.routes.conftest import TEST_USER, make_mock_db
from utils.route_utils import get_database_dependency

VALID_UUID_1 = "550e8400-e29b-41d4-a716-446655440000"
VALID_UUID_2 = "550e8400-e29b-41d4-a716-446655440001"

SAMPLE_TASK = {
    "id": VALID_UUID_1,
    "task_name": "Test Task",
    "status": "pending",
    "topic": "AI",
}


def _build_app(mock_db=None) -> FastAPI:
    if mock_db is None:
        mock_db = _make_bulk_db()

    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    app.dependency_overrides[get_database_dependency] = lambda: mock_db

    return app


def _make_bulk_db(updated_ids=None, missing_ids=None):
    """Build a mock DB. By default all task_ids are 'found and updated'."""
    db = make_mock_db()
    db.get_task = AsyncMock(return_value=SAMPLE_TASK)
    db.update_task_status = AsyncMock(return_value=True)
    db.create_task = AsyncMock(return_value={"id": VALID_UUID_1})
    # tasks sub-service with bulk_add_tasks (#1089)
    db.tasks = MagicMock()

    async def _mock_bulk_add(tasks):
        """Return one UUID per task submitted."""
        uuids = [VALID_UUID_1, VALID_UUID_2]
        return uuids[: len(tasks)]

    db.tasks.bulk_add_tasks = AsyncMock(side_effect=_mock_bulk_add)
    # bulk_update_task_statuses — new 2-query implementation for #700
    _updated = updated_ids if updated_ids is not None else [VALID_UUID_1, VALID_UUID_2]
    _missing = missing_ids if missing_ids is not None else []
    db.bulk_update_task_statuses = AsyncMock(
        return_value={"updated_ids": _updated, "missing_ids": _missing}
    )
    return db


# ---------------------------------------------------------------------------
# POST /api/tasks/bulk
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBulkTaskOperations:
    def test_cancel_valid_tasks_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/tasks/bulk",
            json={"task_ids": [VALID_UUID_1, VALID_UUID_2], "action": "cancel"},
        )
        assert resp.status_code == 200

    def test_cancel_returns_updated_count(self):
        mock_db = _make_bulk_db(updated_ids=[VALID_UUID_1], missing_ids=[])
        client = TestClient(_build_app(mock_db))
        data = client.post(
            "/api/tasks/bulk",
            json={"task_ids": [VALID_UUID_1], "action": "cancel"},
        ).json()
        assert data["updated"] == 1
        assert data["failed"] == 0
        assert data["total"] == 1

    def test_pause_action_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/tasks/bulk",
            json={"task_ids": [VALID_UUID_1], "action": "pause"},
        )
        assert resp.status_code == 200

    def test_resume_action_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/tasks/bulk",
            json={"task_ids": [VALID_UUID_1], "action": "resume"},
        )
        assert resp.status_code == 200

    def test_reject_action_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/tasks/bulk",
            json={"task_ids": [VALID_UUID_1], "action": "reject"},
        )
        assert resp.status_code == 200

    def test_empty_task_ids_returns_400(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/tasks/bulk",
            json={"task_ids": [], "action": "cancel"},
        )
        assert resp.status_code == 400

    def test_invalid_action_returns_400(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/tasks/bulk",
            json={"task_ids": [VALID_UUID_1], "action": "delete"},
        )
        assert resp.status_code == 400

    def test_invalid_uuid_format_is_counted_as_failed(self):
        mock_db = _make_bulk_db()
        client = TestClient(_build_app(mock_db))
        data = client.post(
            "/api/tasks/bulk",
            json={"task_ids": ["not-a-uuid"], "action": "cancel"},
        ).json()
        assert data["failed"] == 1
        assert data["updated"] == 0

    def test_task_not_found_is_counted_as_failed(self):
        # bulk_update_task_statuses returns the task as missing
        mock_db = _make_bulk_db(updated_ids=[], missing_ids=[VALID_UUID_1])
        client = TestClient(_build_app(mock_db))
        data = client.post(
            "/api/tasks/bulk",
            json={"task_ids": [VALID_UUID_1], "action": "cancel"},
        ).json()
        assert data["failed"] == 1
        assert data["updated"] == 0

    def test_partial_success_tracks_both(self):
        """When one task exists and one doesn't, counts should reflect reality."""
        mock_db = _make_bulk_db(updated_ids=[VALID_UUID_1], missing_ids=[VALID_UUID_2])
        client = TestClient(_build_app(mock_db))
        data = client.post(
            "/api/tasks/bulk",
            json={"task_ids": [VALID_UUID_1, VALID_UUID_2], "action": "cancel"},
        ).json()
        assert data["total"] == 2
        assert data["updated"] == 1
        assert data["failed"] == 1

    def test_response_has_message_field(self):
        client = TestClient(_build_app())
        data = client.post(
            "/api/tasks/bulk",
            json={"task_ids": [VALID_UUID_1], "action": "cancel"},
        ).json()
        assert "message" in data
        assert "cancel" in data["message"].lower()


# ---------------------------------------------------------------------------
# POST /api/tasks/bulk/create
# ---------------------------------------------------------------------------


VALID_TASK_ITEM = {
    "task_name": "Blog Post about AI",
    "topic": "Artificial Intelligence",
    "primary_keyword": "AI",
    "target_audience": "developers",
    "category": "technology",
    "priority": "high",
}


@pytest.mark.unit
class TestBulkCreateTasks:
    def test_create_single_task_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/tasks/bulk/create",
            json={"tasks": [VALID_TASK_ITEM]},
        )
        assert resp.status_code == 200

    def test_create_returns_created_count(self):
        client = TestClient(_build_app())
        data = client.post(
            "/api/tasks/bulk/create",
            json={"tasks": [VALID_TASK_ITEM, VALID_TASK_ITEM]},
        ).json()
        assert data["created"] == 2
        assert data["total"] == 2
        assert data["failed"] == 0

    def test_create_returns_task_list(self):
        client = TestClient(_build_app())
        data = client.post(
            "/api/tasks/bulk/create",
            json={"tasks": [VALID_TASK_ITEM]},
        ).json()
        assert data["tasks"] is not None
        assert len(data["tasks"]) == 1
        assert "id" in data["tasks"][0]
        assert "status" in data["tasks"][0]

    def test_db_error_on_create_returns_500(self):
        mock_db = _make_bulk_db()
        mock_db.tasks.bulk_add_tasks = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(mock_db))
        resp = client.post(
            "/api/tasks/bulk/create",
            json={"tasks": [VALID_TASK_ITEM]},
        )
        assert resp.status_code == 500

    def test_missing_required_fields_returns_422(self):
        """Missing task_name or topic should fail Pydantic validation."""
        client = TestClient(_build_app())
        resp = client.post(
            "/api/tasks/bulk/create",
            json={"tasks": [{"task_name": "only name, no topic"}]},
        )
        assert resp.status_code == 422
