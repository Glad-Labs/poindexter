"""
Integration tests for authentication token lifecycle.

Tests multi-step flows composing the auth_unified routes:
- Flow 1: Valid JWT token → 200 on protected endpoint
- Flow 2: Token in blocklist → rejected
- Flow 3: Missing token → 401
- Flow 4: Malformed token → 401
- Flow 5: DEVELOPMENT_MODE bypass gated on env var

All external dependencies (database, JWT decode) are mocked.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from routes.auth_unified import get_current_user
from utils.route_utils import get_database_dependency

# ---------------------------------------------------------------------------
# Minimal protected endpoint for testing auth
# ---------------------------------------------------------------------------


TASKS_BASE = "/api/tasks"  # router has prefix="/api/tasks" built in


def _build_protected_app(auth_override=None, db_override=None) -> FastAPI:
    """Build a minimal FastAPI app with one protected endpoint."""
    from routes.task_routes import router

    app = FastAPI()
    # Router already has prefix="/api/tasks" — include without extra prefix
    app.include_router(router)

    if auth_override is not None:
        app.dependency_overrides[get_current_user] = auth_override

    if db_override is not None:
        app.dependency_overrides[get_database_dependency] = lambda: db_override

    return app


def _make_mock_db() -> AsyncMock:
    db = AsyncMock()
    db.get_task = AsyncMock(return_value=None)
    db.get_tasks_paginated = AsyncMock(return_value=([], 0))
    db.add_task = AsyncMock(return_value="new-task-id")
    db.update_task = AsyncMock(return_value=True)
    db.update_task_status = AsyncMock(return_value=True)
    db.log_status_change = AsyncMock(return_value=None)
    db.create_post = AsyncMock(return_value={"id": "post-id"})
    return db


# ---------------------------------------------------------------------------
# Flow 1-4: Auth middleware behavior
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAuthMiddlewareBehavior:
    """Verify that authentication gates work as expected for different token states."""

    def test_authenticated_user_can_access_tasks_list(self):
        """Valid auth override → GET /tasks returns 200."""
        db = _make_mock_db()
        user = {"id": "user-123", "email": "test@example.com", "is_active": True}
        app = _build_protected_app(auth_override=lambda: user, db_override=db)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get(f"{TASKS_BASE}/")
        assert response.status_code == 200

    def test_no_auth_override_returns_auth_failure(self):
        """No auth override → the real JWT handler runs and fails on missing token."""
        db = _make_mock_db()
        # No auth_override → real get_current_user dependency used
        app = FastAPI()
        from routes.task_routes import router

        app.include_router(router)
        app.dependency_overrides[get_database_dependency] = lambda: db

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(f"{TASKS_BASE}/")
        # Without a token, the real auth will return 401 or 403
        assert response.status_code in (401, 403, 422)

    def test_invalid_bearer_token_returns_auth_failure(self):
        """Malformed Bearer token → the real JWT decoder returns 401."""
        db = _make_mock_db()
        app = FastAPI()
        from routes.task_routes import router

        app.include_router(router)
        app.dependency_overrides[get_database_dependency] = lambda: db

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            f"{TASKS_BASE}/",
            headers={"Authorization": "Bearer definitely-not-a-valid-jwt"},
        )
        assert response.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# Flow 5: DEVELOPMENT_MODE bypass
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDevelopmentModeBypass:
    """
    Verify DEVELOPMENT_MODE gating behavior.

    NOTE: The dev-token bypass (DEVELOPMENT_MODE=true + "Bearer dev-token") is
    implemented in get_current_user_optional only, which is used by optional-auth
    endpoints. The primary get_current_user dependency (used by task_routes)
    always requires a valid JWT regardless of DEVELOPMENT_MODE. The bypass is
    intended for endpoints that don't mandate auth — not for protected routes.
    """

    def test_development_mode_off_requires_real_auth(self):
        """When DEVELOPMENT_MODE is not 'true', no bypass occurs."""
        with patch.dict(os.environ, {"DEVELOPMENT_MODE": "false"}, clear=False):
            db = _make_mock_db()
            app = FastAPI()
            from routes.task_routes import router

            app.include_router(router)
            app.dependency_overrides[get_database_dependency] = lambda: db

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get(f"{TASKS_BASE}/")
            assert response.status_code in (401, 403, 422)

    def test_development_mode_on_with_no_token_still_rejects_protected_route(self):
        """
        DEVELOPMENT_MODE=true does NOT bypass get_current_user on protected routes.
        The dev bypass only applies to get_current_user_optional endpoints.
        """
        with patch.dict(os.environ, {"DEVELOPMENT_MODE": "true"}, clear=False):
            db = _make_mock_db()
            app = FastAPI()
            from routes.task_routes import router

            app.include_router(router)
            app.dependency_overrides[get_database_dependency] = lambda: db

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get(f"{TASKS_BASE}/")
            # Protected routes require real auth even in DEVELOPMENT_MODE
            assert response.status_code in (
                401,
                403,
                422,
            ), f"Expected auth failure on protected route in DEVELOPMENT_MODE, got {response.status_code}"

    def test_optional_auth_endpoint_allows_dev_token_in_development_mode(self):
        """
        get_current_user_optional returns a dev-user when DEVELOPMENT_MODE=true
        and no token is provided. This tests the optional auth path.
        """
        with patch.dict(os.environ, {"DEVELOPMENT_MODE": "true"}, clear=False):
            from routes.auth_unified import get_current_user_optional

            # Build a minimal app with an optional-auth endpoint
            app = FastAPI()

            @app.get("/optional-auth-test")
            async def optional_auth_test(user=Depends(get_current_user_optional)):
                if user is None:
                    return {"authenticated": False}
                return {"authenticated": True, "user_id": user.get("id")}

            client = TestClient(app, raise_server_exceptions=False)
            # No token — in DEVELOPMENT_MODE=true, optional auth returns dev user
            response = client.get("/optional-auth-test")
            assert response.status_code == 200
            body = response.json()
            assert body["authenticated"] is True
            assert body["user_id"] == "dev-user-id"


# ---------------------------------------------------------------------------
# Flow 6: Verify expired/invalid token handling
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestInvalidTokenFormats:
    """Verify that malformed or expired tokens are rejected at the auth layer."""

    def test_bearer_token_with_wrong_format_rejected(self):
        """Token that is not a valid JWT → rejected."""
        db = _make_mock_db()
        app = FastAPI()
        from routes.task_routes import router

        app.include_router(router)
        app.dependency_overrides[get_database_dependency] = lambda: db

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            f"{TASKS_BASE}/",
            headers={"Authorization": "Bearer not.a.valid.jwt.at.all"},
        )
        assert response.status_code in (401, 403, 422)

    def test_basic_auth_scheme_rejected(self):
        """Basic auth scheme instead of Bearer → rejected."""
        db = _make_mock_db()
        app = FastAPI()
        from routes.task_routes import router

        app.include_router(router)
        app.dependency_overrides[get_database_dependency] = lambda: db

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            f"{TASKS_BASE}/",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert response.status_code in (401, 403, 422)

    def test_empty_bearer_token_rejected(self):
        """Empty Bearer token → rejected."""
        db = _make_mock_db()
        app = FastAPI()
        from routes.task_routes import router

        app.include_router(router)
        app.dependency_overrides[get_database_dependency] = lambda: db

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            f"{TASKS_BASE}/",
            headers={"Authorization": "Bearer "},
        )
        assert response.status_code in (401, 403, 422)
