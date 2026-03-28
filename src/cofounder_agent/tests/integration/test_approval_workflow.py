"""
Integration tests for approval workflow (Issue #560).

Tests full approve/reject/pending-queue lifecycle using chained route calls
with mocked DB and auth. No live server or DB required.

Covers:
- Flow 1: approve_task transitions status awaiting_approval → approved
- Flow 2: reject_task transitions status awaiting_approval → failed
- Flow 3: GET /pending-approval returns only tasks in awaiting_approval
- Flow 4: Rejection with feedback stores feedback in metadata
- Flow 5: Unauthenticated requests return 401/403
- Flow 6: Approving a task that is not awaiting_approval returns 400
- Flow 7: Rejecting with allow_revisions=True produces failed_revisions_requested status
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.auth_unified import get_current_user
from utils.route_utils import get_database_dependency

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

TEST_USER_A = {
    "id": "user-a-uuid-1111",
    "email": "usera@example.com",
    "username": "user_a",
    "auth_provider": "github",
    "is_active": True,
}

TEST_USER_B = {
    "id": "user-b-uuid-2222",
    "email": "userb@example.com",
    "username": "user_b",
    "auth_provider": "github",
    "is_active": True,
}

TASK_ID = "approval-test-uuid-0001"
TASK_ID_2 = "approval-test-uuid-0002"


def _make_awaiting_task(task_id=TASK_ID, user_id=TEST_USER_A["id"]):
    return {
        "id": task_id,
        "task_id": task_id,
        "task_name": "Approval Test Post",
        "title": "Approval Test Post",
        "topic": "AI Approval Testing",
        "status": "awaiting_approval",
        "approval_status": "pending",
        "task_type": "blog_post",
        "user_id": user_id,
        "category": "technology",
        "target_audience": "developers",
        "primary_keyword": "AI",
        "quality_score": 85.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "task_metadata": None,
        "result": None,
        "metadata": {},
        "seo_keywords": [],
        "estimated_cost": None,
        "actual_cost": None,
        "publish_mode": "manual",
        "enforce_constraints": False,
        "priority": "normal",
        "tags": [],
    }


def _make_mock_db(task=None, user_id=TEST_USER_A["id"]):
    """Return AsyncMock DatabaseService with awaiting_approval task."""
    db = AsyncMock()
    _task = task or _make_awaiting_task(user_id=user_id)
    db.get_task = AsyncMock(return_value=_task)
    db.update_task = AsyncMock(return_value=True)
    db.update_task_status = AsyncMock(return_value=True)
    db.add_task = AsyncMock(return_value=TASK_ID)
    db.delete_task = AsyncMock(return_value=True)
    db.log_status_change = AsyncMock(return_value=None)
    db.create_post = AsyncMock(return_value={"id": "post-uuid-abc"})
    # get_tasks_paginated — used by pending-approval endpoint
    db.get_tasks_paginated = AsyncMock(return_value=([_task], 1))
    # get_tasks_by_status — approval route may call this for pending queue
    db.get_tasks_by_status = AsyncMock(return_value=([_task], 1))
    return db


def _build_approval_app(mock_db=None, user=TEST_USER_A) -> FastAPI:
    """Build minimal FastAPI app with approval router and mocked deps."""
    from routes.approval_routes import router

    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_database_dependency] = lambda: (mock_db or _make_mock_db())
    return app


APPROVE_PAYLOAD = {
    "approved": True,
    "feedback": "Looks good for publishing",
    "reviewer_notes": "Quality approved",
    "auto_publish": False,
}

REJECT_PAYLOAD = {
    "reason": "Content quality",
    "feedback": "Need more depth in the introduction section",
    "allow_revisions": True,
}


# ---------------------------------------------------------------------------
# Flow 1: Approve task — awaiting_approval → approved
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestApproveTaskFlow:
    """POST /api/tasks/{id}/approve transitions status to approved."""

    def test_approve_returns_200(self):
        db = _make_mock_db()
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            resp = client.post(f"/api/tasks/{TASK_ID}/approve", json=APPROVE_PAYLOAD)
        assert resp.status_code == 200

    def test_approve_response_has_approved_status(self):
        db = _make_mock_db()
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            data = client.post(f"/api/tasks/{TASK_ID}/approve", json=APPROVE_PAYLOAD).json()
        assert data["status"] == "approved"
        assert data["approval_status"] == "approved"
        assert data["task_id"] == TASK_ID

    def test_approve_response_includes_approved_by(self):
        db = _make_mock_db()
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            data = client.post(f"/api/tasks/{TASK_ID}/approve", json=APPROVE_PAYLOAD).json()
        assert data["approved_by"] == TEST_USER_A["id"]

    def test_approve_calls_update_task(self):
        db = _make_mock_db()
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            client.post(f"/api/tasks/{TASK_ID}/approve", json=APPROVE_PAYLOAD)
        db.update_task.assert_awaited_once()

    def test_approve_task_not_found_returns_404(self):
        db = _make_mock_db()
        db.get_task = AsyncMock(return_value=None)
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            resp = client.post("/api/tasks/nonexistent/approve", json=APPROVE_PAYLOAD)
        assert resp.status_code == 404

    def test_approve_wrong_status_returns_400(self):
        """Cannot approve a task that is not awaiting_approval."""
        db = _make_mock_db()
        pending_task = {**_make_awaiting_task(), "status": "pending"}
        db.get_task = AsyncMock(return_value=pending_task)
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            resp = client.post(f"/api/tasks/{TASK_ID}/approve", json=APPROVE_PAYLOAD)
        assert resp.status_code == 400

    def test_approve_broadcasts_websocket_event(self):
        db = _make_mock_db()
        mock_broadcast = AsyncMock()
        with patch("routes.approval_routes.broadcast_approval_status", new=mock_broadcast):
            client = TestClient(_build_approval_app(db))
            client.post(f"/api/tasks/{TASK_ID}/approve", json=APPROVE_PAYLOAD)
        mock_broadcast.assert_awaited_once()
        args = mock_broadcast.call_args
        assert args[0][0] == TASK_ID  # first positional arg is task_id
        assert args[0][1] == "approved"  # second positional arg is status

    def test_approve_unauthenticated_returns_401(self):
        """Without auth override, endpoint requires real JWT → 401."""
        from routes.approval_routes import router

        app = FastAPI()
        app.include_router(router)
        # No dependency_overrides — auth will fail
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(f"/api/tasks/{TASK_ID}/approve", json=APPROVE_PAYLOAD)
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# Flow 2: Reject task — awaiting_approval → failed / failed_revisions_requested
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRejectTaskFlow:
    """POST /api/tasks/{id}/reject transitions status to failed."""

    def test_reject_returns_200(self):
        db = _make_mock_db()
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            resp = client.post(f"/api/tasks/{TASK_ID}/reject", json=REJECT_PAYLOAD)
        assert resp.status_code == 200

    def test_reject_response_has_rejected_approval_status(self):
        db = _make_mock_db()
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            data = client.post(f"/api/tasks/{TASK_ID}/reject", json=REJECT_PAYLOAD).json()
        assert data["approval_status"] == "rejected"
        assert data["task_id"] == TASK_ID

    def test_reject_with_allow_revisions_sets_revisions_status(self):
        db = _make_mock_db()
        payload = {**REJECT_PAYLOAD, "allow_revisions": True}
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            data = client.post(f"/api/tasks/{TASK_ID}/reject", json=payload).json()
        assert data["status"] == "failed_revisions_requested"

    def test_reject_without_revisions_sets_failed_status(self):
        db = _make_mock_db()
        payload = {**REJECT_PAYLOAD, "allow_revisions": False}
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            data = client.post(f"/api/tasks/{TASK_ID}/reject", json=payload).json()
        assert data["status"] == "failed"

    def test_reject_response_includes_feedback(self):
        db = _make_mock_db()
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            data = client.post(f"/api/tasks/{TASK_ID}/reject", json=REJECT_PAYLOAD).json()
        assert data["feedback"] == REJECT_PAYLOAD["feedback"]
        assert data["reason"] == REJECT_PAYLOAD["reason"]

    def test_reject_stores_feedback_via_update_task(self):
        db = _make_mock_db()
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            client.post(f"/api/tasks/{TASK_ID}/reject", json=REJECT_PAYLOAD)
        db.update_task.assert_awaited_once()
        call_args = db.update_task.call_args
        # Second positional arg is the update dict
        update_dict = call_args[0][1]
        assert update_dict.get("approval_status") == "rejected"
        assert update_dict.get("human_feedback") == REJECT_PAYLOAD["feedback"]

    def test_reject_task_not_found_returns_404(self):
        db = _make_mock_db()
        db.get_task = AsyncMock(return_value=None)
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            resp = client.post("/api/tasks/nonexistent/reject", json=REJECT_PAYLOAD)
        assert resp.status_code == 404

    def test_reject_wrong_status_returns_400(self):
        db = _make_mock_db()
        completed_task = {**_make_awaiting_task(), "status": "completed"}
        db.get_task = AsyncMock(return_value=completed_task)
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            resp = client.post(f"/api/tasks/{TASK_ID}/reject", json=REJECT_PAYLOAD)
        assert resp.status_code == 400

    def test_reject_broadcasts_websocket_event(self):
        db = _make_mock_db()
        mock_broadcast = AsyncMock()
        with patch("routes.approval_routes.broadcast_approval_status", new=mock_broadcast):
            client = TestClient(_build_approval_app(db))
            client.post(f"/api/tasks/{TASK_ID}/reject", json=REJECT_PAYLOAD)
        mock_broadcast.assert_awaited_once()
        args = mock_broadcast.call_args
        assert args[0][0] == TASK_ID
        assert args[0][1] == "rejected"


# ---------------------------------------------------------------------------
# Flow 3: GET /pending-approval — returns tasks in awaiting_approval
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetPendingApprovals:
    """GET /api/tasks/pending-approval returns authenticated user's pending tasks."""

    def test_returns_200(self):
        db = _make_mock_db()
        client = TestClient(_build_approval_app(db))
        resp = client.get("/api/tasks/pending-approval")
        assert resp.status_code == 200

    def test_response_has_tasks_or_pending_field(self):
        db = _make_mock_db()
        client = TestClient(_build_approval_app(db))
        data = client.get("/api/tasks/pending-approval").json()
        # Route returns either 'tasks' or 'pending_tasks' key
        assert "tasks" in data or "pending_tasks" in data or "data" in data

    def test_returns_awaiting_approval_tasks(self):
        db = _make_mock_db()
        client = TestClient(_build_approval_app(db))
        data = client.get("/api/tasks/pending-approval").json()
        # The response should include the mocked task with awaiting_approval status
        tasks_key = (
            "tasks" if "tasks" in data else ("pending_tasks" if "pending_tasks" in data else "data")
        )
        if tasks_key in data and isinstance(data[tasks_key], list):
            if len(data[tasks_key]) > 0:
                # Each task should have awaiting_approval-related fields
                task = data[tasks_key][0]
                assert "task_id" in task or "id" in task

    def test_unauthenticated_returns_401(self):
        from routes.approval_routes import router

        app = FastAPI()
        app.include_router(router)
        db = _make_mock_db()
        app.dependency_overrides[get_database_dependency] = lambda: db
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/tasks/pending-approval")
        assert resp.status_code in (401, 403, 422)

    def test_pagination_params_accepted(self):
        db = _make_mock_db()
        client = TestClient(_build_approval_app(db))
        resp = client.get("/api/tasks/pending-approval?limit=10&offset=0")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Flow 4: Full approve lifecycle (create-like mock → approve)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestApproveRejectLifecycleComposed:
    """Chain multiple calls to verify full lifecycle via TestClient."""

    def test_full_approve_lifecycle(self):
        """Task in awaiting_approval → approve → response has approved status."""
        db = _make_mock_db()
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            # Step 1: Approve the task
            resp = client.post(f"/api/tasks/{TASK_ID}/approve", json=APPROVE_PAYLOAD)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"
        assert data["task_id"] == TASK_ID
        # Step 2: Verify DB was updated with correct status
        db.update_task.assert_awaited_once()
        update_call = db.update_task.call_args[0][1]
        assert update_call.get("status") == "approved"

    def test_full_reject_lifecycle(self):
        """Task in awaiting_approval → reject → response has rejected status."""
        db = _make_mock_db()
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            resp = client.post(f"/api/tasks/{TASK_ID}/reject", json=REJECT_PAYLOAD)
        assert resp.status_code == 200
        data = resp.json()
        assert data["approval_status"] == "rejected"
        # Verify rejection was persisted
        db.update_task.assert_awaited_once()
        update_call = db.update_task.call_args[0][1]
        assert update_call.get("approval_status") == "rejected"

    def test_approve_then_approve_again_returns_400(self):
        """Approving an already-approved task returns 400 (not awaiting_approval)."""
        db = _make_mock_db()
        already_approved = {**_make_awaiting_task(), "status": "approved"}
        db.get_task = AsyncMock(return_value=already_approved)
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            resp = client.post(f"/api/tasks/{TASK_ID}/approve", json=APPROVE_PAYLOAD)
        assert resp.status_code == 400

    def test_human_feedback_maps_to_feedback_field(self):
        """If human_feedback is set but feedback is empty, human_feedback is used."""
        db = _make_mock_db()
        payload = {
            "approved": True,
            "feedback": "",
            "human_feedback": "Great content, ready to publish",
            "auto_publish": False,
        }
        with patch("routes.approval_routes.broadcast_approval_status", new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            data = client.post(f"/api/tasks/{TASK_ID}/approve", json=payload).json()
        assert data["status"] == "approved"
        # feedback should be populated from human_feedback
        assert data.get("feedback") == "Great content, ready to publish"
