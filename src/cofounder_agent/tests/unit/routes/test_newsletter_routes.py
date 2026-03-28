"""
Unit tests for routes/newsletter_routes.py.

Tests cover:
- POST /api/newsletter/subscribe       — subscribe_to_newsletter
- POST /api/newsletter/unsubscribe     — unsubscribe_from_newsletter
- GET  /api/newsletter/subscribers/count — get_subscriber_count

DB calls (db.pool.fetchrow / fetchval / execute) are mocked.
Rate limiter is bypassed in tests.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.auth_unified import get_current_user
from routes.newsletter_routes import router
from tests.unit.routes.conftest import TEST_USER
from utils.rate_limiter import limiter
from utils.route_utils import get_database_dependency


@pytest.fixture(autouse=True)
def disable_rate_limiter():
    """Disable the slow-api rate limiter for all newsletter tests."""
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


def _make_pool_mock(
    fetchrow_return=None,
    fetchval_return=1,
    execute_return="UPDATE 1",
):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.execute = AsyncMock(return_value=execute_return)
    return pool


def _make_db(pool=None):
    db = MagicMock()
    db.pool = pool or _make_pool_mock()
    return db


def _build_app(db=None) -> FastAPI:
    if db is None:
        db = _make_db()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    app.dependency_overrides[get_database_dependency] = lambda: db
    return app


VALID_SUBSCRIBE_PAYLOAD = {
    "email": "test@example.com",
    "first_name": "Test",
    "last_name": "User",
    "marketing_consent": True,
}


# ---------------------------------------------------------------------------
# POST /api/newsletter/subscribe
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSubscribeToNewsletter:
    def test_new_subscription_returns_200(self):
        """Fresh email, not already subscribed."""
        pool = _make_pool_mock(fetchrow_return=None, fetchval_return=42)
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post("/api/newsletter/subscribe", json=VALID_SUBSCRIBE_PAYLOAD)
        assert resp.status_code == 200

    def test_new_subscription_response_has_success_true(self):
        pool = _make_pool_mock(fetchrow_return=None, fetchval_return=42)
        client = TestClient(_build_app(_make_db(pool)))
        data = client.post("/api/newsletter/subscribe", json=VALID_SUBSCRIBE_PAYLOAD).json()
        assert data["success"] is True
        assert data["subscriber_id"] == 42

    def test_already_subscribed_returns_generic_success_to_prevent_enumeration(self):
        """Re-subscribing active email returns 200 with success=True and generic message.

        Returning success=False with the email address would allow an attacker to enumerate
        valid email addresses by observing different response bodies (issue #744).
        The caller cannot infer whether the email was already registered.
        """
        existing = {"id": 99, "unsubscribed_at": None}
        pool = _make_pool_mock(fetchrow_return=existing)
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post("/api/newsletter/subscribe", json=VALID_SUBSCRIBE_PAYLOAD)
        assert resp.status_code == 200
        data = resp.json()
        # Must return success=True regardless (anti-enumeration)
        assert data["success"] is True
        # Must NOT include the email address in the message
        assert VALID_SUBSCRIBE_PAYLOAD["email"] not in data.get("message", "")

    def test_with_interest_categories(self):
        pool = _make_pool_mock(fetchrow_return=None, fetchval_return=10)
        client = TestClient(_build_app(_make_db(pool)))
        payload = {**VALID_SUBSCRIBE_PAYLOAD, "interest_categories": ["AI", "Technology"]}
        resp = client.post("/api/newsletter/subscribe", json=payload)
        assert resp.status_code == 200

    def test_db_error_returns_500(self):
        pool = _make_pool_mock()
        pool.fetchrow = AsyncMock(side_effect=RuntimeError("DB failure"))
        client = TestClient(_build_app(_make_db(pool)), raise_server_exceptions=False)
        resp = client.post("/api/newsletter/subscribe", json=VALID_SUBSCRIBE_PAYLOAD)
        assert resp.status_code == 500

    def test_missing_email_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post("/api/newsletter/subscribe", json={"first_name": "No Email"})
        assert resp.status_code == 422

    def test_invalid_email_format_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/newsletter/subscribe",
            json={"email": "not-an-email"},
        )
        assert resp.status_code == 422

    def test_minimal_payload_only_email(self):
        pool = _make_pool_mock(fetchrow_return=None, fetchval_return=5)
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post(
            "/api/newsletter/subscribe",
            json={"email": "minimal@example.com"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/newsletter/unsubscribe
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnsubscribeFromNewsletter:
    def test_valid_unsubscribe_returns_200(self):
        pool = _make_pool_mock(execute_return="UPDATE 1")
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post(
            "/api/newsletter/unsubscribe",
            json={"email": "test@example.com"},
        )
        assert resp.status_code == 200

    def test_unsubscribe_response_has_success_true(self):
        pool = _make_pool_mock(execute_return="UPDATE 1")
        client = TestClient(_build_app(_make_db(pool)))
        data = client.post(
            "/api/newsletter/unsubscribe",
            json={"email": "test@example.com"},
        ).json()
        assert data["success"] is True

    def test_email_not_found_returns_200_with_generic_message(self):
        """When email not found, returns 200 with success=True to prevent enumeration."""
        pool = _make_pool_mock(execute_return="UPDATE 0")
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post(
            "/api/newsletter/unsubscribe",
            json={"email": "notfound@example.com"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "If this email was subscribed" in data["message"]

    def test_with_reason_returns_200(self):
        pool = _make_pool_mock(execute_return="UPDATE 1")
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post(
            "/api/newsletter/unsubscribe",
            json={"email": "test@example.com", "reason": "Too many emails"},
        )
        assert resp.status_code == 200

    def test_db_error_returns_500(self):
        pool = _make_pool_mock()
        pool.execute = AsyncMock(side_effect=RuntimeError("DB failure"))
        client = TestClient(_build_app(_make_db(pool)), raise_server_exceptions=False)
        resp = client.post(
            "/api/newsletter/unsubscribe",
            json={"email": "test@example.com"},
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/newsletter/subscribers/count
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSubscriberCount:
    def test_returns_200(self):
        pool = _make_pool_mock(fetchval_return=150)
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.get("/api/newsletter/subscribers/count")
        assert resp.status_code == 200

    def test_response_has_subscriber_count(self):
        pool = _make_pool_mock(fetchval_return=42)
        client = TestClient(_build_app(_make_db(pool)))
        data = client.get("/api/newsletter/subscribers/count").json()
        assert data["success"] is True
        assert data["subscriber_count"] == 42

    def test_zero_count_when_no_subscribers(self):
        pool = _make_pool_mock(fetchval_return=None)  # type: ignore[arg-type]
        client = TestClient(_build_app(_make_db(pool)))
        data = client.get("/api/newsletter/subscribers/count").json()
        assert data["subscriber_count"] == 0

    def test_requires_auth_returns_401_when_unauthenticated(self):
        """subscriber_count is an admin metric — must require authentication (issue #744)."""
        app = FastAPI()
        app.include_router(router)
        pool = _make_pool_mock(fetchval_return=5)
        app.dependency_overrides[get_database_dependency] = lambda: _make_db(pool)
        # No get_current_user override — simulate unauthenticated request
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/newsletter/subscribers/count")
        assert resp.status_code == 401

    def test_authenticated_user_receives_subscriber_count(self):
        """Authenticated user should get the subscriber count as before."""
        pool = _make_pool_mock(fetchval_return=77)
        client = TestClient(_build_app(_make_db(pool)))
        data = client.get("/api/newsletter/subscribers/count").json()
        assert data["success"] is True
        assert data["subscriber_count"] == 77

    def test_db_error_returns_500(self):
        pool = _make_pool_mock()
        pool.fetchval = AsyncMock(side_effect=RuntimeError("DB failure"))
        client = TestClient(_build_app(_make_db(pool)), raise_server_exceptions=False)
        resp = client.get("/api/newsletter/subscribers/count")
        assert resp.status_code == 500
