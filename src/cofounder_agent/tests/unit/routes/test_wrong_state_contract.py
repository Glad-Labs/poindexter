"""
Contract tests: wrong-state transitions must respond with 409 Conflict.

Addresses poindexter#743 — prior to this fix each endpoint used a different
status code for the same semantic ("you can't do that to this task right now"):

  - POST /approve       → 400  (now 409)
  - POST /reject        → 400  (now 409)
  - POST /publish       → 400  (now 409)
  - POST /go-live       → 400  (now 409)
  - PUT  /status/validated → 200 {success: false}  (now 409)

Tests here use unit mocks — no real DB or LLM calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.approval_routes as approval_module
from middleware.api_token_auth import verify_api_token
from routes.approval_routes import router as approval_router
from tests.unit.routes.conftest import make_mock_db
from utils.route_utils import get_database_dependency

# task_status_routes imports from task_routes which in turn imports status_router
# back — a circular that crashes if imported at module level.  Import lazily
# inside the _build_status_validated_app helper (called only at test-run time,
# after pytest has already set up sys.modules so the cycle resolves).
# This lazy strategy also avoids polluting the global import order for tests
# that don't need the status router.

# ---------------------------------------------------------------------------
# Minimal published / in-progress task fixtures
# ---------------------------------------------------------------------------

_PUBLISHED_TASK = {
    "id": "pub-task-001",
    "task_id": "pub-task-001",
    "status": "published",
    "task_type": "blog_post",
    "topic": "AI Trends",
    "metadata": {},
    "result": "{}",
    "task_metadata": "{}",
}

_PENDING_TASK = {
    "id": "pending-task-001",
    "task_id": "pending-task-001",
    "status": "pending",
    "task_type": "blog_post",
    "topic": "AI Trends",
    "metadata": {},
    "result": "{}",
    "task_metadata": "{}",
}

_AWAITING_TASK = {
    "id": "awaiting-task-001",
    "task_id": "awaiting-task-001",
    "status": "awaiting_approval",
    "task_type": "blog_post",
    "topic": "AI Trends",
    "metadata": {},
    "result": "{}",
    "task_metadata": "{}",
}


# ---------------------------------------------------------------------------
# App builder helpers
# ---------------------------------------------------------------------------


def _build_approval_app(mock_db=None) -> FastAPI:
    """Approval router app (POST /api/tasks/{task_id}/reject)."""
    if mock_db is None:
        mock_db = make_mock_db()
    app = FastAPI()
    app.include_router(approval_router)
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    app.dependency_overrides[get_database_dependency] = lambda: mock_db
    return app


def _build_status_validated_client(mock_db=None, mock_status_service=None):
    """Return (TestClient, patcher) for the status/validated endpoint.

    Imports ``routes.task_routes.router`` (the full task router that includes
    the status sub-router) rather than ``status_router`` directly.  Importing
    ``task_status_routes`` at module-load time triggers a circular import
    because it imports from ``task_routes`` which then tries to import
    ``status_router`` back from the still-partially-initialized module.
    Using the full ``task_routes.router`` works because ``task_routes`` is the
    one that includes the sub-routers at its bottom — by the time it's fully
    initialized, both routers are ready.

    The status_router's Depends on ``get_enhanced_status_change_service`` is a
    lambda wrapper, so it can't be overridden via ``dependency_overrides``
    directly; instead we patch the underlying function in ``utils.route_utils``
    (same pattern as ``test_task_status_routes.py``).
    """
    from routes.task_routes import router as task_router  # noqa: PLC0415
    from services.enhanced_status_change_service import EnhancedStatusChangeService  # noqa: PLC0415

    if mock_db is None:
        mock_db = make_mock_db()
    if mock_status_service is None:
        mock_status_service = MagicMock(spec=EnhancedStatusChangeService)
        mock_status_service.validate_and_change_status = AsyncMock(
            return_value=(True, "ok", [])
        )
    app = FastAPI()
    # task_router already carries prefix="/api/tasks" — don't add another prefix.
    app.include_router(task_router)
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    app.dependency_overrides[get_database_dependency] = lambda: mock_db

    patcher = patch(
        "utils.route_utils.get_enhanced_status_change_service",
        return_value=mock_status_service,
    )
    patcher.start()
    client = TestClient(app)
    return client, patcher


# ---------------------------------------------------------------------------
# Helpers for the publishing router which lives in task_publishing_routes.py
# The router is registered via task_routes.py; here we build a minimal
# app that mounts just publishing_router so we can call the endpoints
# without the full task_routes graph.
# ---------------------------------------------------------------------------


def _build_publishing_app(mock_db=None) -> FastAPI:
    """Minimal app with publishing_router (approve + publish + go-live)."""
    from routes.task_publishing_routes import publishing_router

    if mock_db is None:
        mock_db = make_mock_db()
    app = FastAPI()
    # publishing_router uses path params like /{task_id}/approve; mount at
    # /api/tasks so paths match what callers use.
    app.include_router(publishing_router, prefix="/api/tasks")
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    app.dependency_overrides[get_database_dependency] = lambda: mock_db
    # Override site_config dep so no DB config reads happen
    from utils.route_utils import get_site_config_dependency
    mock_cfg = MagicMock()
    mock_cfg.get = MagicMock(return_value=None)
    mock_cfg.require = MagicMock(return_value="http://localhost")
    app.dependency_overrides[get_site_config_dependency] = lambda: mock_cfg
    return app


# ---------------------------------------------------------------------------
# Tests: POST /approve with wrong state → 409
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApproveWrongState409:
    """Approve on a task that is not awaiting_approval or completed → 409."""

    def test_approve_published_task_returns_409(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_PUBLISHED_TASK)
        client = TestClient(_build_publishing_app(mock_db))

        resp = client.post(
            "/api/tasks/pub-task-001/approve",
            json={"approved": True},
        )
        assert resp.status_code == 409, (
            f"Expected 409 for approve on published task, got {resp.status_code}: {resp.text}"
        )

    def test_approve_pending_task_returns_409(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_PENDING_TASK)
        client = TestClient(_build_publishing_app(mock_db))

        resp = client.post(
            "/api/tasks/pending-task-001/approve",
            json={"approved": True},
        )
        assert resp.status_code == 409, (
            f"Expected 409 for approve on pending task, got {resp.status_code}: {resp.text}"
        )

    def test_reject_via_approve_endpoint_published_returns_409(self):
        """approve(approved=false) on a published task should also be 409."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_PUBLISHED_TASK)
        client = TestClient(_build_publishing_app(mock_db))

        resp = client.post(
            "/api/tasks/pub-task-001/approve",
            json={"approved": False},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Tests: POST /reject with wrong state → 409
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRejectWrongState409:
    """POST /api/tasks/{task_id}/reject on a non-awaiting_approval task → 409."""

    def test_reject_pending_task_returns_409(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_PENDING_TASK)
        client = TestClient(_build_approval_app(mock_db))

        with patch.object(approval_module, "broadcast_approval_status", new=AsyncMock()):
            resp = client.post(
                "/api/tasks/pending-task-001/reject",
                json={"reason": "test", "feedback": "test"},
            )
        assert resp.status_code == 409, (
            f"Expected 409 for reject on pending task, got {resp.status_code}: {resp.text}"
        )

    def test_reject_published_task_returns_409(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_PUBLISHED_TASK)
        client = TestClient(_build_approval_app(mock_db))

        with patch.object(approval_module, "broadcast_approval_status", new=AsyncMock()):
            resp = client.post(
                "/api/tasks/pub-task-001/reject",
                json={"reason": "test", "feedback": "test"},
            )
        assert resp.status_code == 409, (
            f"Expected 409 for reject on published task, got {resp.status_code}: {resp.text}"
        )

    def test_reject_correct_state_still_returns_200(self):
        """Regression: awaiting_approval → still succeeds (not broken by 409 change)."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_AWAITING_TASK)
        client = TestClient(_build_approval_app(mock_db))

        with patch.object(approval_module, "broadcast_approval_status", new=AsyncMock()):
            resp = client.post(
                "/api/tasks/awaiting-task-001/reject",
                json={"reason": "quality", "feedback": "needs work"},
            )
        assert resp.status_code == 200, (
            f"Expected 200 for reject on awaiting_approval task, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Tests: POST /publish with wrong state → 409
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPublishWrongState409:
    """POST /api/tasks/{task_id}/publish on a non-approved task → 409."""

    def test_publish_pending_task_returns_409(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_PENDING_TASK)
        client = TestClient(_build_publishing_app(mock_db))

        resp = client.post("/api/tasks/pending-task-001/publish")
        assert resp.status_code == 409, (
            f"Expected 409 for publish on pending task, got {resp.status_code}: {resp.text}"
        )

    def test_publish_awaiting_approval_task_returns_409(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_AWAITING_TASK)
        client = TestClient(_build_publishing_app(mock_db))

        resp = client.post("/api/tasks/awaiting-task-001/publish")
        assert resp.status_code == 409, (
            f"Expected 409 for publish on awaiting_approval task, got {resp.status_code}: {resp.text}"
        )

    def test_publish_already_published_returns_200(self):
        """Re-publishing an already-published task is an idempotent retry: returns 200 (poindexter#747)."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_PUBLISHED_TASK)
        client = TestClient(_build_publishing_app(mock_db))

        resp = client.post("/api/tasks/pub-task-001/publish")
        assert resp.status_code == 200, (
            f"Expected 200 for publish on already-published task (idempotent retry), got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# Tests: PUT /status/validated wrong-state → 409
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStatusValidatedWrongState409:
    """PUT /{task_id}/status/validated on wrong-state transition → 409."""

    def test_wrong_state_transition_returns_409(self):
        """Service returning 'Invalid status transition' should surface as 409."""
        from services.enhanced_status_change_service import EnhancedStatusChangeService

        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_PUBLISHED_TASK)

        mock_svc = MagicMock(spec=EnhancedStatusChangeService)
        mock_svc.validate_and_change_status = AsyncMock(
            return_value=(
                False,
                "Invalid status transition: published → pending",
                ["invalid_transition"],
            )
        )

        client, patcher = _build_status_validated_client(mock_db, mock_svc)
        try:
            resp = client.put(
                "/api/tasks/pub-task-001/status/validated",
                json={"status": "pending"},
            )
        finally:
            patcher.stop()
        assert resp.status_code == 409, (
            f"Expected 409 for wrong-state /status/validated, got {resp.status_code}: {resp.text}"
        )

    def test_wrong_state_transition_body_contains_message(self):
        """The 409 body should echo the transition error message."""
        from services.enhanced_status_change_service import EnhancedStatusChangeService

        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_PUBLISHED_TASK)

        err_msg = "Invalid status transition: published → pending"
        mock_svc = MagicMock(spec=EnhancedStatusChangeService)
        mock_svc.validate_and_change_status = AsyncMock(
            return_value=(False, err_msg, ["invalid_transition"])
        )

        client, patcher = _build_status_validated_client(mock_db, mock_svc)
        try:
            resp = client.put(
                "/api/tasks/pub-task-001/status/validated",
                json={"status": "pending"},
            )
        finally:
            patcher.stop()
        assert "transition" in resp.text.lower(), resp.text

    def test_successful_transition_still_returns_200(self):
        """Regression: valid transition still succeeds."""
        from services.enhanced_status_change_service import EnhancedStatusChangeService

        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_PENDING_TASK)

        mock_svc = MagicMock(spec=EnhancedStatusChangeService)
        mock_svc.validate_and_change_status = AsyncMock(
            return_value=(True, "Status changed: pending → in_progress", [])
        )

        client, patcher = _build_status_validated_client(mock_db, mock_svc)
        try:
            resp = client.put(
                "/api/tasks/pending-task-001/status/validated",
                json={"status": "in_progress"},
            )
        finally:
            patcher.stop()
        assert resp.status_code == 200, (
            f"Expected 200 for valid /status/validated transition, got {resp.status_code}"
        )
        data = resp.json()
        assert data["success"] is True

    def test_deprecation_header_present_on_success(self):
        """The Deprecation response header should be set on every response."""
        from services.enhanced_status_change_service import EnhancedStatusChangeService

        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_PENDING_TASK)

        mock_svc = MagicMock(spec=EnhancedStatusChangeService)
        mock_svc.validate_and_change_status = AsyncMock(
            return_value=(True, "Status changed: pending → in_progress", [])
        )

        client, patcher = _build_status_validated_client(mock_db, mock_svc)
        try:
            resp = client.put(
                "/api/tasks/pending-task-001/status/validated",
                json={"status": "in_progress"},
            )
        finally:
            patcher.stop()
        assert "Deprecation" in resp.headers, (
            "Expected Deprecation header on /status/validated response"
        )

    def test_non_transition_failure_returns_200_success_false(self):
        """Other failures (e.g. update_failed) keep backward-compat 200 body."""
        from services.enhanced_status_change_service import EnhancedStatusChangeService

        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_PENDING_TASK)

        mock_svc = MagicMock(spec=EnhancedStatusChangeService)
        mock_svc.validate_and_change_status = AsyncMock(
            return_value=(False, "Failed to update task status", ["update_failed"])
        )

        client, patcher = _build_status_validated_client(mock_db, mock_svc)
        try:
            resp = client.put(
                "/api/tasks/pending-task-001/status/validated",
                json={"status": "in_progress"},
            )
        finally:
            patcher.stop()
        # Non-transition failure keeps the original 200/{success: false} contract
        # (backward-compat shim for callers checking errors[] rather than status code).
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
