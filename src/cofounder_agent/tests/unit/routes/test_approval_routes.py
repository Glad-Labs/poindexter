"""
Unit tests for routes/approval_routes.py.

Tests cover:
- POST /api/tasks/{task_id}/reject        — reject_task
- POST /api/tasks/bulk-approve            — bulk_approve_tasks
- POST /api/tasks/bulk-reject             — bulk_reject_tasks
- GET  /api/tasks/pending-approval        — get_pending_approvals
- GET  /api/tasks/{task_id}/approval-status — get_task_approval_status

NOTE: POST /api/tasks/{task_id}/approve is tested in test_task_publishing_routes.py
(the approve endpoint was moved there per issue #1335).

Auth and DB are overridden so no real I/O occurs.
broadcast_approval_status is patched so WebSocket calls don't fail.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.approval_routes as approval_module
from routes.approval_routes import router
from routes.auth_unified import get_current_user
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

    app.dependency_overrides[get_current_user] = lambda: TEST_USER
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
# POST /api/tasks/bulk-approve
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBulkApprove:
    def test_bulk_approve_all_awaiting_tasks(self):
        task_a = {**AWAITING_TASK, "id": "t1", "task_id": "t1"}
        task_b = {**AWAITING_TASK, "id": "t2", "task_id": "t2"}

        mock_db = make_mock_db()
        # get_tasks_by_ids returns a dict keyed by task_id (#665 — replaces N get_task calls)
        mock_db.get_tasks_by_ids = AsyncMock(return_value={"t1": task_a, "t2": task_b})
        client = TestClient(_build_app(mock_db))

        with patch.object(approval_module, "broadcast_approval_status", new=AsyncMock()):
            resp = client.post(
                "/api/tasks/bulk-approve",
                json={"task_ids": ["t1", "t2"], "feedback": "All good"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved_count"] == 2
        assert data["failed_count"] == 0
        assert set(data["successful_task_ids"]) == {"t1", "t2"}

    def test_bulk_approve_skips_nonexistent_tasks(self):
        mock_db = make_mock_db()
        # Empty dict = no tasks found
        mock_db.get_tasks_by_ids = AsyncMock(return_value={})
        client = TestClient(_build_app(mock_db))

        with patch.object(approval_module, "broadcast_approval_status", new=AsyncMock()):
            resp = client.post(
                "/api/tasks/bulk-approve",
                json={"task_ids": ["missing-1", "missing-2"]},
            )
        data = resp.json()
        assert data["approved_count"] == 0
        assert data["failed_count"] == 2

    def test_bulk_approve_skips_tasks_with_wrong_status(self):
        already_approved = {**AWAITING_TASK, "status": "approved", "task_id": "t1"}
        mock_db = make_mock_db()
        mock_db.get_tasks_by_ids = AsyncMock(return_value={"t1": already_approved})
        client = TestClient(_build_app(mock_db))

        with patch.object(approval_module, "broadcast_approval_status", new=AsyncMock()):
            resp = client.post(
                "/api/tasks/bulk-approve",
                json={"task_ids": ["t1"]},
            )
        data = resp.json()
        assert data["approved_count"] == 0
        assert data["failed_count"] == 1

    def test_bulk_approve_empty_task_ids_succeeds(self):
        client = TestClient(_build_app())
        with patch.object(approval_module, "broadcast_approval_status", new=AsyncMock()):
            resp = client.post("/api/tasks/bulk-approve", json={"task_ids": []})
        assert resp.status_code == 200
        assert resp.json()["approved_count"] == 0


# ---------------------------------------------------------------------------
# POST /api/tasks/bulk-reject
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBulkReject:
    def test_bulk_reject_awaiting_tasks(self):
        task_a = {**AWAITING_TASK, "id": "r1", "task_id": "r1"}
        task_b = {**AWAITING_TASK, "id": "r2", "task_id": "r2"}

        mock_db = make_mock_db()
        # get_tasks_by_ids returns a dict keyed by task_id (#665 — replaces N get_task calls)
        mock_db.get_tasks_by_ids = AsyncMock(return_value={"r1": task_a, "r2": task_b})
        client = TestClient(_build_app(mock_db))

        with patch.object(approval_module, "broadcast_approval_status", new=AsyncMock()):
            resp = client.post(
                "/api/tasks/bulk-reject",
                json={
                    "task_ids": ["r1", "r2"],
                    "reason": "Quality issues",
                    "feedback": "Needs revision",
                    "allow_revisions": True,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["rejected_count"] == 2
        assert data["failed_count"] == 0

    def test_bulk_reject_missing_required_fields_returns_422(self):
        client = TestClient(_build_app())
        # "reason" and "feedback" are required
        resp = client.post("/api/tasks/bulk-reject", json={"task_ids": ["t1"]})
        assert resp.status_code == 422


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
# GET /api/tasks/{task_id}/approval-status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTaskApprovalStatus:
    def test_returns_404_for_missing_task(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/nonexistent/approval-status")
        assert resp.status_code == 404

    def test_returns_approval_status_fields(self):
        task = {
            **AWAITING_TASK,
            "metadata": {
                "approval_date": "2026-01-01",
                "approved_by": "user-xyz",
                "rejection_reason": None,
            },
        }
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=task)
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/task-001/approval-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "task-001"
        assert data["status"] == "awaiting_approval"
        assert data["can_be_approved"] is True

    def test_can_be_approved_false_for_non_awaiting_task(self):
        task = {**AWAITING_TASK, "status": "approved", "metadata": {}}
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=task)
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/task-001/approval-status")
        assert resp.status_code == 200
        assert resp.json()["can_be_approved"] is False


# ---------------------------------------------------------------------------
# Integration-level approval lifecycle tests (Issue #560)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApprovalLifecycle:
    """Multi-step approval lifecycle — simulates the real approval flow."""

    def test_reject_then_status_reflects_rejected(self):
        """After reject, approval-status shows non-approvable state."""
        awaiting = {**AWAITING_TASK}
        rejected = {
            **AWAITING_TASK,
            "status": "failed",
            "metadata": {"rejection_reason": "Off-topic"},
        }

        call_counts = {"n": 0}

        async def _get_task(task_id):
            call_counts["n"] += 1
            if call_counts["n"] == 1:
                return awaiting
            return rejected

        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(side_effect=_get_task)
        client = TestClient(_build_app(mock_db))

        with patch.object(approval_module, "broadcast_approval_status", new=AsyncMock()):
            reject_resp = client.post(
                "/api/tasks/task-001/reject",
                json={"reason": "Off-topic", "feedback": "Not relevant", "allow_revisions": False},
            )
        assert reject_resp.status_code == 200
        assert reject_resp.json()["status"] == "failed"

        status_resp = client.get("/api/tasks/task-001/approval-status")
        assert status_resp.status_code == 200
        assert status_resp.json()["can_be_approved"] is False

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
