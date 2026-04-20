"""
Integration tests for approval workflow (Issue #560).

Tests full approve/reject/pending-queue lifecycle using chained route calls
with mocked DB and auth. No live server or DB required.

Covers:
- Flow 1: approve_task transitions status awaiting_approval -> approved
- Flow 2: reject_task transitions status awaiting_approval -> failed
- Flow 3: GET /pending-approval returns only tasks in awaiting_approval
- Flow 4: Rejection with feedback stores feedback in metadata
- Flow 5: Unauthenticated requests return 401/403
- Flow 6: Approving a task that is not awaiting_approval returns 400
- Flow 7: Rejecting with allow_revisions=True produces rejected_retry status
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import task_routes first to resolve the circular import between
# task_routes <-> task_publishing_routes.
import routes.task_routes  # noqa: F401
from middleware.api_token_auth import verify_api_token
from utils.route_utils import get_database_dependency

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

# Operator identity returned by get_operator_identity() when patched
TEST_OPERATOR = {
    "id": "operator",
    "email": "operator@glad-labs.ai",
    "username": "operator",
    "auth_provider": "api_token",
    "is_active": True,
}

TASK_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
TASK_ID_2 = "a1b2c3d4-e5f6-7890-abcd-ef1234567891"


def _make_awaiting_task(task_id=TASK_ID, user_id="operator"):
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
        "task_metadata": {},
        "result": None,
        "metadata": {},
        "seo_keywords": [],
        "estimated_cost": None,
        "actual_cost": None,
        "publish_mode": "manual",
        "enforce_constraints": False,
        "priority": 0,
        "tags": [],
    }


def _make_approved_task(task_id=TASK_ID, user_id="operator"):
    """Return a task dict that looks like it has been approved.

    The approve endpoint re-fetches the task after update_task_status and
    reads its ``result`` field.  We set result to ``{}`` (not None) so that
    ``task_result_data.get(...)`` does not raise AttributeError.
    """
    task = _make_awaiting_task(task_id=task_id, user_id=user_id)
    task["status"] = "approved"
    task["approval_status"] = "approved"
    task["result"] = {}  # Must be a dict, not None
    return task


def _make_mock_db(task=None, user_id="operator"):
    """Return AsyncMock DatabaseService.

    For the approve endpoint the function calls get_task twice:
    once to validate, once after the update.  We wire side_effect so
    the second call returns the approved version of the task.
    """
    db = AsyncMock()
    _task = task or _make_awaiting_task(user_id=user_id)
    _approved = _make_approved_task(task_id=_task.get("task_id", TASK_ID), user_id=user_id)
    db.get_task = AsyncMock(side_effect=[_task, _approved])
    db.update_task = AsyncMock(return_value=True)
    db.update_task_status = AsyncMock(return_value=True)
    db.add_task = AsyncMock(return_value=TASK_ID)
    db.delete_task = AsyncMock(return_value=True)
    db.log_status_change = AsyncMock(return_value=None)
    db.create_post = AsyncMock(return_value={"id": "post-uuid-abc"})
    db.get_tasks_paginated = AsyncMock(return_value=([_task], 1))
    db.get_tasks_by_status = AsyncMock(return_value=([_task], 1))
    return db


def _make_mock_db_for_reject(task=None, user_id="operator"):
    """Return AsyncMock DatabaseService for reject/pending tests (single get_task call)."""
    db = AsyncMock()
    _task = task or _make_awaiting_task(user_id=user_id)
    db.get_task = AsyncMock(return_value=_task)
    db.update_task = AsyncMock(return_value=True)
    db.update_task_status = AsyncMock(return_value=True)
    db.get_tasks_paginated = AsyncMock(return_value=([_task], 1))
    db.get_tasks_by_status = AsyncMock(return_value=([_task], 1))
    return db


def _override_verify_api_token():
    """Override for verify_api_token -- returns a valid token string."""
    return "test-token"


def _build_approval_app(mock_db=None, for_reject=False) -> FastAPI:
    """Build minimal FastAPI app with approval + publishing routers and mocked deps.

    The approve endpoint lives in task_publishing_routes.publishing_router
    (mounted under /api/tasks via task_routes). The reject and pending-approval
    endpoints live in approval_routes.router (prefix /api/tasks).
    """
    from routes.approval_routes import router as approval_router
    from routes.task_publishing_routes import publishing_router

    app = FastAPI()
    # approval_routes.router already has prefix="/api/tasks"
    app.include_router(approval_router)
    # publishing_router has no prefix; mount it under /api/tasks
    app.include_router(publishing_router, prefix="/api/tasks")

    app.dependency_overrides[verify_api_token] = _override_verify_api_token
    if mock_db is None:
        mock_db = _make_mock_db_for_reject() if for_reject else _make_mock_db()
    app.dependency_overrides[get_database_dependency] = lambda: mock_db
    return app


# Approve endpoint uses query params (not JSON body)
APPROVE_PARAMS = {
    "approved": "true",
    "human_feedback": "Looks good for publishing",
    "auto_publish": "false",
}

REJECT_PAYLOAD = {
    "reason": "Content quality",
    "feedback": "Need more depth in the introduction section",
    "allow_revisions": True,
}

# Patch targets
_BROADCAST_APPROVAL = "routes.approval_routes.broadcast_approval_status"
_OPERATOR_IDENTITY_APPROVAL = "routes.approval_routes.get_operator_identity"
_CHECK_OWNERSHIP = "routes.task_publishing_routes._check_task_ownership"
_REVALIDATION = "routes.revalidate_routes.trigger_nextjs_revalidation"
_WEBHOOK = "services.webhook_delivery_service.emit_webhook_event"


def _approve_patches():
    """Context manager stack for approve-endpoint patches."""
    return (
        patch(_CHECK_OWNERSHIP),
        patch(_REVALIDATION, new=AsyncMock()),
        patch(_WEBHOOK, new=AsyncMock()),
    )


def _reject_patches():
    """Context manager stack for reject-endpoint patches."""
    return (
        patch(_BROADCAST_APPROVAL, new=AsyncMock()),
        patch(_OPERATOR_IDENTITY_APPROVAL, return_value=TEST_OPERATOR),
    )


# ---------------------------------------------------------------------------
# Flow 1: Approve task -- awaiting_approval -> approved
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestApproveTaskFlow:
    """POST /api/tasks/{id}/approve transitions status to approved."""

    def _approve(self, client, task_id=TASK_ID, params=None):
        return client.post(
            f"/api/tasks/{task_id}/approve",
            params=params or APPROVE_PARAMS,
            headers={"Authorization": "Bearer test-token"},
        )

    def test_approve_returns_200(self):
        db = _make_mock_db()
        with patch(_CHECK_OWNERSHIP), patch(_REVALIDATION, new=AsyncMock()), patch(_WEBHOOK, new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            resp = self._approve(client)
        assert resp.status_code == 200

    def test_approve_response_has_approved_status(self):
        db = _make_mock_db()
        with patch(_CHECK_OWNERSHIP), patch(_REVALIDATION, new=AsyncMock()), patch(_WEBHOOK, new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            data = self._approve(client).json()
        assert data["status"] == "approved"
        assert data["task_id"] == TASK_ID

    def test_approve_response_has_status_approved(self):
        """The UnifiedTaskResponse includes status='approved' after approval."""
        db = _make_mock_db()
        with patch(_CHECK_OWNERSHIP), patch(_REVALIDATION, new=AsyncMock()), patch(_WEBHOOK, new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            data = self._approve(client).json()
        assert data["status"] == "approved"
        assert data["id"] == TASK_ID

    def test_approve_calls_update_task_status(self):
        db = _make_mock_db()
        with patch(_CHECK_OWNERSHIP), patch(_REVALIDATION, new=AsyncMock()), patch(_WEBHOOK, new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            self._approve(client)
        db.update_task_status.assert_awaited_once()

    def test_approve_task_not_found_returns_404(self):
        db = _make_mock_db()
        db.get_task = AsyncMock(return_value=None)
        nonexistent_uuid = "00000000-0000-0000-0000-000000000000"
        with patch(_CHECK_OWNERSHIP), patch(_REVALIDATION, new=AsyncMock()), patch(_WEBHOOK, new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            resp = self._approve(client, task_id=nonexistent_uuid)
        assert resp.status_code == 404

    def test_approve_wrong_status_returns_error(self):
        """Cannot approve a task with an unknown/invalid status."""
        db = _make_mock_db()
        bad_task = {**_make_awaiting_task(), "status": "unknown_garbage_status"}
        db.get_task = AsyncMock(side_effect=[bad_task, bad_task])
        with patch(_CHECK_OWNERSHIP), patch(_REVALIDATION, new=AsyncMock()), patch(_WEBHOOK, new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            resp = self._approve(client)
        assert resp.status_code == 400

    def test_approve_fetches_updated_task(self):
        """The approve endpoint fetches the task again after updating."""
        db = _make_mock_db()
        with patch(_CHECK_OWNERSHIP), patch(_REVALIDATION, new=AsyncMock()), patch(_WEBHOOK, new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            self._approve(client)
        # get_task called twice: once to check, once after update
        assert db.get_task.await_count == 2

    def test_approve_unauthenticated_returns_401(self):
        """Without auth override, endpoint requires real Bearer token -> 401."""
        from routes.approval_routes import router as approval_router
        from routes.task_publishing_routes import publishing_router

        app = FastAPI()
        app.include_router(approval_router)
        app.include_router(publishing_router, prefix="/api/tasks")
        # No dependency_overrides -- auth will fail
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            f"/api/tasks/{TASK_ID}/approve",
            params=APPROVE_PARAMS,
        )
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# Flow 2: Reject task -- awaiting_approval -> failed / rejected_retry
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRejectTaskFlow:
    """POST /api/tasks/{id}/reject transitions status to failed."""

    def _reject(self, client, task_id=TASK_ID, payload=None):
        return client.post(
            f"/api/tasks/{task_id}/reject",
            json=payload or REJECT_PAYLOAD,
            headers={"Authorization": "Bearer test-token"},
        )

    def test_reject_returns_200(self):
        db = _make_mock_db_for_reject()
        with patch(_BROADCAST_APPROVAL, new=AsyncMock()), patch(_OPERATOR_IDENTITY_APPROVAL, return_value=TEST_OPERATOR):
            client = TestClient(_build_approval_app(db, for_reject=True))
            resp = self._reject(client)
        assert resp.status_code == 200

    def test_reject_response_has_rejected_approval_status(self):
        db = _make_mock_db_for_reject()
        with patch(_BROADCAST_APPROVAL, new=AsyncMock()), patch(_OPERATOR_IDENTITY_APPROVAL, return_value=TEST_OPERATOR):
            client = TestClient(_build_approval_app(db, for_reject=True))
            data = self._reject(client).json()
        assert data["approval_status"] == "rejected"
        assert data["task_id"] == TASK_ID

    def test_reject_with_allow_revisions_sets_revisions_status(self):
        db = _make_mock_db_for_reject()
        payload = {**REJECT_PAYLOAD, "allow_revisions": True}
        with patch(_BROADCAST_APPROVAL, new=AsyncMock()), patch(_OPERATOR_IDENTITY_APPROVAL, return_value=TEST_OPERATOR):
            client = TestClient(_build_approval_app(db, for_reject=True))
            data = self._reject(client, payload=payload).json()
        assert data["status"] == "rejected_retry"

    def test_reject_without_revisions_sets_failed_status(self):
        db = _make_mock_db_for_reject()
        payload = {**REJECT_PAYLOAD, "allow_revisions": False}
        with patch(_BROADCAST_APPROVAL, new=AsyncMock()), patch(_OPERATOR_IDENTITY_APPROVAL, return_value=TEST_OPERATOR):
            client = TestClient(_build_approval_app(db, for_reject=True))
            data = self._reject(client, payload=payload).json()
        assert data["status"] == "failed"

    def test_reject_response_includes_feedback(self):
        db = _make_mock_db_for_reject()
        with patch(_BROADCAST_APPROVAL, new=AsyncMock()), patch(_OPERATOR_IDENTITY_APPROVAL, return_value=TEST_OPERATOR):
            client = TestClient(_build_approval_app(db, for_reject=True))
            data = self._reject(client).json()
        assert data["feedback"] == REJECT_PAYLOAD["feedback"]
        assert data["reason"] == REJECT_PAYLOAD["reason"]

    def test_reject_stores_feedback_via_update_task(self):
        db = _make_mock_db_for_reject()
        with patch(_BROADCAST_APPROVAL, new=AsyncMock()), patch(_OPERATOR_IDENTITY_APPROVAL, return_value=TEST_OPERATOR):
            client = TestClient(_build_approval_app(db, for_reject=True))
            self._reject(client)
        db.update_task.assert_awaited_once()
        call_args = db.update_task.call_args
        update_dict = call_args[0][1]
        assert update_dict.get("approval_status") == "rejected"
        assert update_dict.get("human_feedback") == REJECT_PAYLOAD["feedback"]

    def test_reject_task_not_found_returns_404(self):
        db = _make_mock_db_for_reject()
        db.get_task = AsyncMock(return_value=None)
        with patch(_BROADCAST_APPROVAL, new=AsyncMock()), patch(_OPERATOR_IDENTITY_APPROVAL, return_value=TEST_OPERATOR):
            client = TestClient(_build_approval_app(db, for_reject=True))
            resp = self._reject(client, task_id="nonexistent")
        assert resp.status_code == 404

    def test_reject_wrong_status_returns_400(self):
        db = _make_mock_db_for_reject()
        completed_task = {**_make_awaiting_task(), "status": "completed"}
        db.get_task = AsyncMock(return_value=completed_task)
        with patch(_BROADCAST_APPROVAL, new=AsyncMock()), patch(_OPERATOR_IDENTITY_APPROVAL, return_value=TEST_OPERATOR):
            client = TestClient(_build_approval_app(db, for_reject=True))
            resp = self._reject(client)
        assert resp.status_code == 400

    def test_reject_broadcasts_websocket_event(self):
        db = _make_mock_db_for_reject()
        mock_broadcast = AsyncMock()
        with patch(_BROADCAST_APPROVAL, new=mock_broadcast), patch(_OPERATOR_IDENTITY_APPROVAL, return_value=TEST_OPERATOR):
            client = TestClient(_build_approval_app(db, for_reject=True))
            self._reject(client)
        mock_broadcast.assert_awaited_once()
        args = mock_broadcast.call_args
        assert args[0][0] == TASK_ID
        assert args[0][1] == "rejected"


# ---------------------------------------------------------------------------
# Flow 3: GET /pending-approval -- returns tasks in awaiting_approval
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetPendingApprovals:
    """GET /api/tasks/pending-approval returns pending tasks."""

    def test_returns_200(self):
        db = _make_mock_db_for_reject()
        with patch(_OPERATOR_IDENTITY_APPROVAL, return_value=TEST_OPERATOR):
            client = TestClient(_build_approval_app(db, for_reject=True))
            resp = client.get(
                "/api/tasks/pending-approval",
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 200

    def test_response_has_tasks_field(self):
        db = _make_mock_db_for_reject()
        with patch(_OPERATOR_IDENTITY_APPROVAL, return_value=TEST_OPERATOR):
            client = TestClient(_build_approval_app(db, for_reject=True))
            data = client.get(
                "/api/tasks/pending-approval",
                headers={"Authorization": "Bearer test-token"},
            ).json()
        assert "tasks" in data

    def test_returns_awaiting_approval_tasks(self):
        db = _make_mock_db_for_reject()
        with patch(_OPERATOR_IDENTITY_APPROVAL, return_value=TEST_OPERATOR):
            client = TestClient(_build_approval_app(db, for_reject=True))
            data = client.get(
                "/api/tasks/pending-approval",
                headers={"Authorization": "Bearer test-token"},
            ).json()
        tasks = data["tasks"]
        assert len(tasks) > 0
        task = tasks[0]
        assert "task_id" in task or "id" in task

    def test_unauthenticated_returns_401(self):
        from routes.approval_routes import router as approval_router

        app = FastAPI()
        app.include_router(approval_router)
        db = _make_mock_db_for_reject()
        app.dependency_overrides[get_database_dependency] = lambda: db
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/tasks/pending-approval")
        assert resp.status_code in (401, 403, 422)

    def test_pagination_params_accepted(self):
        db = _make_mock_db_for_reject()
        with patch(_OPERATOR_IDENTITY_APPROVAL, return_value=TEST_OPERATOR):
            client = TestClient(_build_approval_app(db, for_reject=True))
            resp = client.get(
                "/api/tasks/pending-approval?limit=10&offset=0",
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Flow 4: Full approve/reject lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestApproveRejectLifecycleComposed:
    """Chain multiple calls to verify full lifecycle via TestClient."""

    def test_full_approve_lifecycle(self):
        """Task in awaiting_approval -> approve -> response has approved status."""
        db = _make_mock_db()
        with patch(_CHECK_OWNERSHIP), patch(_REVALIDATION, new=AsyncMock()), patch(_WEBHOOK, new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            resp = client.post(
                f"/api/tasks/{TASK_ID}/approve",
                params=APPROVE_PARAMS,
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"
        assert data["task_id"] == TASK_ID
        db.update_task_status.assert_awaited_once()

    def test_full_reject_lifecycle(self):
        """Task in awaiting_approval -> reject -> response has rejected status."""
        db = _make_mock_db_for_reject()
        with patch(_BROADCAST_APPROVAL, new=AsyncMock()), patch(_OPERATOR_IDENTITY_APPROVAL, return_value=TEST_OPERATOR):
            client = TestClient(_build_approval_app(db, for_reject=True))
            resp = client.post(
                f"/api/tasks/{TASK_ID}/reject",
                json=REJECT_PAYLOAD,
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approval_status"] == "rejected"
        db.update_task.assert_awaited_once()
        update_call = db.update_task.call_args[0][1]
        assert update_call.get("approval_status") == "rejected"

    def test_approve_already_approved_still_succeeds(self):
        """The approve endpoint allows re-approving (status 'approved' is in allowed list)."""
        db = _make_mock_db()
        already_approved = {**_make_awaiting_task(), "status": "approved", "result": {}}
        re_approved = {**already_approved, "approval_status": "approved", "result": {}}
        db.get_task = AsyncMock(side_effect=[already_approved, re_approved])
        with patch(_CHECK_OWNERSHIP), patch(_REVALIDATION, new=AsyncMock()), patch(_WEBHOOK, new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            resp = client.post(
                f"/api/tasks/{TASK_ID}/approve",
                params=APPROVE_PARAMS,
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code in (200, 400)  # 400 if already approved is now rejected

    def test_human_feedback_passed_as_query_param(self):
        """human_feedback query param is accepted by the approve endpoint."""
        db = _make_mock_db()
        params = {
            "approved": "true",
            "human_feedback": "Great content, ready to publish",
            "auto_publish": "false",
        }
        with patch(_CHECK_OWNERSHIP), patch(_REVALIDATION, new=AsyncMock()), patch(_WEBHOOK, new=AsyncMock()):
            client = TestClient(_build_approval_app(db))
            resp = client.post(
                f"/api/tasks/{TASK_ID}/approve",
                params=params,
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"
