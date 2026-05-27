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

from middleware.api_token_auth import verify_api_token
from routes.newsletter_routes import router
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
    db.cloud_pool = None  # Ensure cloud_pool fallback uses db.pool
    return db


def _build_app(db=None) -> FastAPI:
    if db is None:
        db = _make_db()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
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
    """Cycle-5 audit (#252) hardened the unsubscribe endpoint to require a
    per-subscriber token. Pre-fix, anyone who knew an email could
    unsubscribe arbitrary subscribers via rate-limit-only protection.
    Post-fix, the endpoint refuses without ``unsubscribe_token`` and
    looks up by token alone — email is no longer accepted."""

    _VALID_TOKEN = "v_token_abc123def456ghi789jkl012mno345pqr"

    def test_valid_token_unsubscribes_and_returns_200(self):
        pool = _make_pool_mock(execute_return="UPDATE 1")
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post(
            "/api/newsletter/unsubscribe",
            json={"unsubscribe_token": self._VALID_TOKEN},
        )
        assert resp.status_code == 200

    def test_unsubscribe_response_has_success_true(self):
        pool = _make_pool_mock(execute_return="UPDATE 1")
        client = TestClient(_build_app(_make_db(pool)))
        data = client.post(
            "/api/newsletter/unsubscribe",
            json={"unsubscribe_token": self._VALID_TOKEN},
        ).json()
        assert data["success"] is True

    def test_missing_token_returns_422(self):
        """The cycle-5 gate — request without a token must fail
        validation (FastAPI/pydantic 422) before reaching the DB."""
        pool = _make_pool_mock()
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post("/api/newsletter/unsubscribe", json={})
        assert resp.status_code == 422
        # The endpoint must NOT have queried the DB on a malformed request.
        pool.execute.assert_not_called()

    def test_email_only_payload_rejected_with_422(self):
        """The old contract accepted ``{email, reason}``. Pre-fix
        callers who haven't migrated to the new contract must fail
        loud — silently falling through to a no-op would let a stale
        frontend ship and silently break unsubscribe."""
        pool = _make_pool_mock()
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post(
            "/api/newsletter/unsubscribe",
            json={"email": "test@example.com"},
        )
        assert resp.status_code == 422
        pool.execute.assert_not_called()

    def test_invalid_token_returns_200_with_generic_message(self):
        """Unknown token returns the SAME response as a successful
        unsubscribe — refusing to confirm token validity prevents the
        endpoint from being used as a token-validity oracle. Without
        this, an attacker grinding random tokens could distinguish hits
        from misses by status / response body."""
        pool = _make_pool_mock(execute_return="UPDATE 0")
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post(
            "/api/newsletter/unsubscribe",
            json={"unsubscribe_token": "wrong_token_value_42"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "If this link was valid" in data["message"]

    def test_already_unsubscribed_returns_generic_message(self):
        """Re-unsubscribing (UPDATE 0 because the WHERE clause filters
        ``unsubscribed_at IS NULL``) must also return the generic
        message — same oracle protection applies."""
        pool = _make_pool_mock(execute_return="UPDATE 0")
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post(
            "/api/newsletter/unsubscribe",
            json={"unsubscribe_token": self._VALID_TOKEN},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_with_reason_returns_200(self):
        pool = _make_pool_mock(execute_return="UPDATE 1")
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post(
            "/api/newsletter/unsubscribe",
            json={
                "unsubscribe_token": self._VALID_TOKEN,
                "reason": "Too many emails",
            },
        )
        assert resp.status_code == 200

    def test_lookup_query_uses_token_not_email(self):
        """Regression guard against re-introducing email-keyed lookup.
        The UPDATE statement must filter on ``unsubscribe_token``,
        never on ``email`` — that's the cycle-5 fix."""
        pool = _make_pool_mock(execute_return="UPDATE 1")
        client = TestClient(_build_app(_make_db(pool)))
        client.post(
            "/api/newsletter/unsubscribe",
            json={"unsubscribe_token": self._VALID_TOKEN, "reason": "spam"},
        )
        pool.execute.assert_awaited_once()
        sql = pool.execute.await_args.args[0]
        assert "unsubscribe_token = $1" in sql
        # The first positional arg after the SQL is the token, NOT an email.
        assert pool.execute.await_args.args[1] == self._VALID_TOKEN

    def test_db_error_returns_500(self):
        pool = _make_pool_mock()
        pool.execute = AsyncMock(side_effect=RuntimeError("DB failure"))
        client = TestClient(_build_app(_make_db(pool)), raise_server_exceptions=False)
        resp = client.post(
            "/api/newsletter/unsubscribe",
            json={"unsubscribe_token": self._VALID_TOKEN},
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Token mint — subscribe path stamps an unsubscribe_token on every new row
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSubscribeMintsToken:
    """The other half of #252 — every new subscriber row must carry an
    ``unsubscribe_token`` so the unsubscribe path has something to look
    up. A subscribe that silently NULL'd the column would crash the
    NOT NULL constraint added by migration 20260527_180559."""

    def test_subscribe_insert_includes_unsubscribe_token_column(self):
        pool = _make_pool_mock(fetchrow_return=None, fetchval_return=42)
        client = TestClient(_build_app(_make_db(pool)))
        client.post("/api/newsletter/subscribe", json=VALID_SUBSCRIBE_PAYLOAD)
        pool.fetchval.assert_awaited_once()
        sql = pool.fetchval.await_args.args[0]
        assert "unsubscribe_token" in sql, (
            "subscribe INSERT must include unsubscribe_token column — "
            "the migration's NOT NULL constraint will reject otherwise"
        )

    def test_subscribe_mints_high_entropy_token(self):
        """The minted token must look like ``secrets.token_urlsafe(32)``
        output — ≈43 base64url chars. A trivially short or predictable
        token would weaken the unsubscribe-as-auth contract."""
        pool = _make_pool_mock(fetchrow_return=None, fetchval_return=1)
        client = TestClient(_build_app(_make_db(pool)))
        client.post("/api/newsletter/subscribe", json=VALID_SUBSCRIBE_PAYLOAD)
        args = pool.fetchval.await_args.args
        # The 10th positional arg (after the SQL) is the token. Counting:
        # email, first_name, last_name, company, interest, ip, user_agent,
        # marketing_consent, verified, unsubscribe_token = positions 1..10.
        token = args[10]
        assert isinstance(token, str)
        assert len(token) >= 32, f"token too short: {len(token)} chars"
        # base64url alphabet only — no padding, no slashes, no plus signs.
        assert all(c.isalnum() or c in "-_" for c in token), (
            f"token has non-base64url chars: {token!r}"
        )

    def test_resubscribe_rotates_the_token(self):
        """ON CONFLICT branch of the INSERT must update the token —
        treating re-subscribe as a fresh relationship means an old
        unsubscribe link from a prior subscription becomes dead, which
        is the safer default."""
        pool = _make_pool_mock(fetchrow_return=None, fetchval_return=1)
        client = TestClient(_build_app(_make_db(pool)))
        client.post("/api/newsletter/subscribe", json=VALID_SUBSCRIBE_PAYLOAD)
        sql = pool.fetchval.await_args.args[0]
        # The ON CONFLICT branch must reassign unsubscribe_token.
        assert "unsubscribe_token = EXCLUDED.unsubscribe_token" in sql


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
