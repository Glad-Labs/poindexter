"""
Unit tests for routes/approval_routes.py.

Tests cover:
- POST /api/tasks/{task_id}/reject        — reject_task
- GET  /api/tasks/pending-approval        — get_pending_approvals

NOTE: POST /api/tasks/{task_id}/approve is tested in test_task_publishing_routes.py
(the approve endpoint was moved there per issue #1335).
Bulk approve, bulk reject, and approval-status endpoints have been removed.

Auth and DB are overridden so no real I/O occurs.
broadcast_approval_status is patched so WebSocket calls don't fail.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.approval_routes as approval_module
from middleware.api_token_auth import verify_api_token
from routes.approval_routes import router
from tests.unit.routes.conftest import TEST_USER, make_mock_db
from utils.route_utils import get_database_dependency

# ---------------------------------------------------------------------------
# App / client factory
# ---------------------------------------------------------------------------


def _build_app(mock_db=None) -> FastAPI:
    if mock_db is None:
        mock_db = make_mock_db()

    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    app.dependency_overrides[get_database_dependency] = lambda: mock_db

    return app


AWAITING_TASK = {
    "id": "task-001",
    "task_id": "task-001",
    "task_type": "blog_post",
    "status": "awaiting_approval",
    "topic": "AI Trends",
    "task_name": "Blog: AI Trends",
    "quality_score": 85.0,
    "metadata": {},
}

APPROVED_TASK = {
    **AWAITING_TASK,
    "status": "approved",
}


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/reject
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRejectTask:
    def test_reject_awaiting_task_returns_200(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=AWAITING_TASK)
        client = TestClient(_build_app(mock_db))

        with patch.object(approval_module, "broadcast_approval_status", new=AsyncMock()):
            resp = client.post(
                "/api/tasks/task-001/reject",
                json={
                    "reason": "Content quality",
                    "feedback": "Needs more depth",
                    "allow_revisions": True,
                },
            )
        assert resp.status_code == 200

    def test_reject_with_revisions_sets_failed_revisions_status(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=AWAITING_TASK)
        client = TestClient(_build_app(mock_db))

        with patch.object(approval_module, "broadcast_approval_status", new=AsyncMock()):
            resp = client.post(
                "/api/tasks/task-001/reject",
                json={
                    "reason": "Poor quality",
                    "feedback": "Too short",
                    "allow_revisions": True,
                },
            )
        data = resp.json()
        assert data["status"] == "failed_revisions_requested"

    def test_reject_without_revisions_sets_failed_status(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=AWAITING_TASK)
        client = TestClient(_build_app(mock_db))

        with patch.object(approval_module, "broadcast_approval_status", new=AsyncMock()):
            resp = client.post(
                "/api/tasks/task-001/reject",
                json={
                    "reason": "Off-topic",
                    "feedback": "Not relevant",
                    "allow_revisions": False,
                },
            )
        data = resp.json()
        assert data["status"] == "failed"

    def test_reject_nonexistent_task_returns_404(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks/ghost-task/reject",
            json={"reason": "x", "feedback": "y"},
        )
        assert resp.status_code == 404

    def test_reject_task_wrong_status_returns_400(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value={**AWAITING_TASK, "status": "pending"})
        client = TestClient(_build_app(mock_db))

        with patch.object(approval_module, "broadcast_approval_status", new=AsyncMock()):
            resp = client.post(
                "/api/tasks/task-001/reject",
                json={"reason": "x", "feedback": "y"},
            )
        assert resp.status_code == 400

    def test_reject_missing_required_fields_returns_422(self):
        client = TestClient(_build_app())
        # "reason" is required
        resp = client.post("/api/tasks/task-001/reject", json={"feedback": "only feedback"})
        assert resp.status_code == 422

    def test_db_update_called_on_rejection(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=AWAITING_TASK)
        client = TestClient(_build_app(mock_db))

        with patch.object(approval_module, "broadcast_approval_status", new=AsyncMock()):
            client.post(
                "/api/tasks/task-001/reject",
                json={"reason": "Quality", "feedback": "Need improvement"},
            )

        assert mock_db.update_task.called
        call_args = mock_db.update_task.call_args
        updates = call_args[0][1]
        assert updates["approval_status"] == "rejected"




# ---------------------------------------------------------------------------
# GET /api/tasks/pending-approval
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPendingApprovals:
    def test_returns_200_with_empty_list(self):
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([], 0))
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/pending-approval")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["tasks"] == []

    def test_returns_pending_tasks(self):
        pending_task = {
            "task_id": "p1",
            "id": "p1",
            "task_type": "blog_post",
            "status": "awaiting_approval",
            "topic": "Blockchain",
            "title": "Blockchain Blog",
            "task_name": "Blockchain Blog",
            "quality_score": 80,
            "content": "Some content here",
            "featured_image_url": None,
            "task_metadata": {},
            "created_at": "2026-01-01T00:00:00Z",
        }
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([pending_task], 1))
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/pending-approval")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["topic"] == "Blockchain"

    def test_pagination_defaults_are_correct(self):
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([], 0))
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/pending-approval")
        data = resp.json()
        assert data["limit"] == 20
        assert data["offset"] == 0

    def test_passes_status_filter_to_db(self):
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([], 0))
        client = TestClient(_build_app(mock_db))

        client.get("/api/tasks/pending-approval")
        call_kwargs = mock_db.get_tasks_paginated.call_args.kwargs
        assert call_kwargs["status"] == "awaiting_approval"

    def test_invalid_limit_returns_422(self):
        client = TestClient(_build_app())
        resp = client.get("/api/tasks/pending-approval?limit=999")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Integration-level approval lifecycle tests (Issue #560)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovalLifecycle:
    """Multi-step approval lifecycle — simulates the real approval flow."""

    def test_feedback_field_persisted_in_db_update(self):
        """Reject: feedback text must be included in the DB update call."""
        feedback_text = "The content was off-brand and too short."
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=AWAITING_TASK)
        client = TestClient(_build_app(mock_db))

        with patch.object(approval_module, "broadcast_approval_status", new=AsyncMock()):
            client.post(
                "/api/tasks/task-001/reject",
                json={"reason": "Quality", "feedback": feedback_text},
            )

        call_args = mock_db.update_task.call_args
        updates = call_args[0][1]
        metadata = updates.get("metadata") or updates.get("task_metadata") or {}
        stored_feedback = (
            updates.get("rejection_feedback")
            or updates.get("feedback")
            or metadata.get("rejection_feedback")
            or metadata.get("feedback")
        )
        assert stored_feedback == feedback_text

    def test_unauthenticated_pending_approvals_returns_401_or_403(self):
        """Without auth override, pending-approval listing should be rejected."""
        mock_db = make_mock_db()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_database_dependency] = lambda: mock_db
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api/tasks/pending-approval")
        assert resp.status_code in (401, 403, 422)

    def test_websocket_broadcast_called_on_rejection(self):
        """broadcast_approval_status is invoked after a successful reject."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=AWAITING_TASK)
        client = TestClient(_build_app(mock_db))

        with patch.object(
            approval_module, "broadcast_approval_status", new=AsyncMock()
        ) as mock_broadcast:
            client.post(
                "/api/tasks/task-001/reject",
                json={"reason": "Quality", "feedback": "Improve tone"},
            )

        mock_broadcast.assert_awaited_once()
        assert mock_broadcast.call_args[0][0] == "task-001"
