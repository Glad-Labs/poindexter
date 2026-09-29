"""
Unit tests for routes/newsletter_routes.py.

Tests cover:
- POST /api/newsletter/subscribe       — subscribe_to_newsletter, including the
  retired request fields (accepted, ignored, answered with a Deprecation header)
- POST /api/newsletter/unsubscribe     — unsubscribe_from_newsletter
- GET  /api/newsletter/subscribers/count — get_subscriber_count

DB calls (db.pool.fetchrow / fetchval / execute) are mocked.
Rate limiter is bypassed in tests.
"""

import logging
import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from poindexter.routes.newsletter_routes import router
from poindexter.utils.rate_limiter import limiter
from poindexter.utils.route_utils import get_database_dependency


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
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    app.dependency_overrides[get_database_dependency] = lambda: db
    return app


VALID_SUBSCRIBE_PAYLOAD = {
    "email": "test@example.com",
    "first_name": "Test",
    "last_name": "User",
}

#: The request shape older callers (and the public site before 2026-09-28) sent.
OLD_SUBSCRIBE_PAYLOAD = {
    **VALID_SUBSCRIBE_PAYLOAD,
    "company": "Acme Corp",
    "interest_categories": ["AI", "Technology"],
    "marketing_consent": True,
}

#: Exactly what a subscribe INSERT may write (Glad-Labs/poindexter#1109).
STORED_COLUMNS = ["email", "first_name", "last_name", "verified", "unsubscribe_token"]


def _inserted_row(pool) -> dict:
    """The subscribe INSERT as ``{column: value}``, read from the SQL's column list.

    Keyed by column name rather than argument position, so the assertions
    follow the statement when a column is added or removed.
    """
    sql, *args = pool.fetchval.await_args.args
    columns = re.search(r"INSERT INTO newsletter_subscribers\s*\(([^)]*)\)", sql)
    values = re.search(r"VALUES\s*\(([^)]*)\)", sql)
    assert columns and values, sql
    names = [c.strip() for c in columns.group(1).split(",")]
    placeholders = [v.strip() for v in values.group(1).split(",")]
    # The zip below is only sound when VALUES binds $1..$n in column order.
    assert placeholders == [f"${i}" for i in range(1, len(names) + 1)], placeholders
    assert len(args) == len(names), (names, args)
    return dict(zip(names, args, strict=True))


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
# Retired request fields: company / interest_categories / marketing_consent
# (Glad-Labs/poindexter#1109). Accepted so older callers keep working, never
# stored, and answered with a Deprecation header.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRetiredSubscribeFields:
    def test_old_payload_still_subscribes(self):
        """An older caller that still sends the retired fields gets its signup,
        not a 422."""
        pool = _make_pool_mock(fetchrow_return=None, fetchval_return=42)
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post("/api/newsletter/subscribe", json=OLD_SUBSCRIBE_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["subscriber_id"] == 42

    def test_old_payload_gets_the_deprecation_headers(self):
        pool = _make_pool_mock(fetchrow_return=None, fetchval_return=42)
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post("/api/newsletter/subscribe", json=OLD_SUBSCRIBE_PAYLOAD)
        assert resp.headers["Deprecation"] == "true"
        warning = resp.headers["Warning"]
        assert warning.startswith('299 - "')
        assert (
            "Ignored retired field(s): company, interest_categories, marketing_consent." in warning
        )

    def test_old_payload_values_are_not_stored(self):
        pool = _make_pool_mock(fetchrow_return=None, fetchval_return=42)
        client = TestClient(_build_app(_make_db(pool)))
        client.post("/api/newsletter/subscribe", json=OLD_SUBSCRIBE_PAYLOAD)
        row = _inserted_row(pool)
        assert sorted(row) == sorted(STORED_COLUMNS)
        assert row["email"] == "test@example.com"
        assert (row["first_name"], row["last_name"]) == ("Test", "User")
        assert row["verified"] is True
        assert "Acme Corp" not in row.values()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("company", "Acme Corp"),
            ("company", None),
            ("interest_categories", ["AI"]),
            ("interest_categories", []),
            ("marketing_consent", True),
            ("marketing_consent", False),
        ],
    )
    def test_any_retired_field_alone_is_flagged(self, field, value):
        """Presence is the old shape whatever the value: an explicit null or
        false still comes from a caller that has not been updated."""
        pool = _make_pool_mock(fetchrow_return=None, fetchval_return=1)
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post(
            "/api/newsletter/subscribe", json={**VALID_SUBSCRIBE_PAYLOAD, field: value}
        )
        assert resp.status_code == 200
        assert resp.headers["Deprecation"] == "true"
        assert f"Ignored retired field(s): {field}." in resp.headers["Warning"]
        assert sorted(_inserted_row(pool)) == sorted(STORED_COLUMNS)

    def test_current_payload_gets_no_deprecation_headers(self):
        pool = _make_pool_mock(fetchrow_return=None, fetchval_return=1)
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post("/api/newsletter/subscribe", json=VALID_SUBSCRIBE_PAYLOAD)
        assert resp.status_code == 200
        assert "Deprecation" not in resp.headers
        assert "Warning" not in resp.headers

    def test_already_subscribed_reply_carries_the_signal_too(self):
        """The anti-enumeration early reply still answers the old shape. The
        header depends only on the request, so it reveals nothing about
        whether the address was already subscribed."""
        pool = _make_pool_mock(fetchrow_return={"id": 99, "unsubscribed_at": None})
        client = TestClient(_build_app(_make_db(pool)))
        resp = client.post("/api/newsletter/subscribe", json=OLD_SUBSCRIBE_PAYLOAD)
        assert resp.status_code == 200
        assert resp.headers["Deprecation"] == "true"
        pool.fetchval.assert_not_called()

    def test_old_payload_logs_a_warning_naming_the_fields(self, caplog):
        pool = _make_pool_mock(fetchrow_return=None, fetchval_return=1)
        client = TestClient(_build_app(_make_db(pool)))
        with caplog.at_level(logging.WARNING):
            client.post("/api/newsletter/subscribe", json=OLD_SUBSCRIBE_PAYLOAD)
        # get_logger() renders through structlog, so match substrings only.
        warned = [
            r
            for r in caplog.records
            if r.levelname == "WARNING" and "ignored retired field(s)" in r.message
        ]
        assert len(warned) == 1
        assert "company, interest_categories, marketing_consent" in warned[0].message
        # The subscriber's address stays out of the line.
        assert "test@example.com" not in warned[0].message

    def test_current_payload_logs_no_deprecation_warning(self, caplog):
        pool = _make_pool_mock(fetchrow_return=None, fetchval_return=1)
        client = TestClient(_build_app(_make_db(pool)))
        with caplog.at_level(logging.WARNING):
            client.post("/api/newsletter/subscribe", json=VALID_SUBSCRIBE_PAYLOAD)
        assert not [r for r in caplog.records if "retired field" in r.message]

    def test_openapi_marks_the_retired_fields_deprecated(self):
        """Swagger and client codegen read the schema, not the headers."""
        schemas = _build_app().openapi()["components"]["schemas"]
        props = schemas["NewsletterSubscribeRequest"]["properties"]
        for name in ("company", "interest_categories", "marketing_consent"):
            assert props[name].get("deprecated") is True, name
        for name in ("email", "first_name", "last_name"):
            assert not props[name].get("deprecated"), name


@pytest.mark.unit
def test_subscribe_does_not_record_the_callers_ip_or_user_agent():
    """They describe whatever called the route, never the subscriber, and
    nothing read them (Glad-Labs/poindexter#1109)."""
    pool = _make_pool_mock(fetchrow_return=None, fetchval_return=1)
    client = TestClient(_build_app(_make_db(pool)))
    client.post(
        "/api/newsletter/subscribe",
        json=VALID_SUBSCRIBE_PAYLOAD,
        headers={"User-Agent": "caller-agent/1.0"},
    )
    row = _inserted_row(pool)
    assert "ip_address" not in row
    assert "user_agent" not in row
    assert "caller-agent/1.0" not in row.values()
    assert "testclient" not in row.values()  # TestClient's request.client.host


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
        token = _inserted_row(pool)["unsubscribe_token"]
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


# ---------------------------------------------------------------------------
# Resend segment mirror + token mint — delegated to services.newsletter_audience
# (the mirror's own behaviour is tested in tests/unit/services/test_newsletter_audience.py)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_subscribe_mirrors_the_signup_into_the_segment(monkeypatch):
    """A direct API signup is copied into the Resend segment so Resend-side
    tooling sees every subscriber. The route only delegates."""
    from poindexter.routes import newsletter_routes as nr

    calls: list[dict] = []

    async def fake_mirror(site_config, **kw):
        calls.append(kw)

    monkeypatch.setattr(nr, "mirror_signup_to_segment", fake_mirror)
    pool = _make_pool_mock(fetchrow_return=None, fetchval_return=7)
    client = TestClient(_build_app(_make_db(pool)))
    resp = client.post("/api/newsletter/subscribe", json=VALID_SUBSCRIBE_PAYLOAD)
    assert resp.status_code == 200
    assert calls == [
        {"email": "test@example.com", "first_name": "Test", "last_name": "User"}
    ]


@pytest.mark.unit
def test_subscribe_mints_through_the_shared_token_function(monkeypatch):
    """The route and the segment sync mint through one function, so every
    row carries the 43-char token shape the unsubscribe relay validates."""
    from poindexter.routes import newsletter_routes as nr

    monkeypatch.setattr(nr, "mint_unsubscribe_token", lambda: "T" * 43)

    async def no_mirror(site_config, **kw):
        return None

    monkeypatch.setattr(nr, "mirror_signup_to_segment", no_mirror)
    pool = _make_pool_mock(fetchrow_return=None, fetchval_return=1)
    client = TestClient(_build_app(_make_db(pool)))
    client.post("/api/newsletter/subscribe", json=VALID_SUBSCRIBE_PAYLOAD)
    assert _inserted_row(pool)["unsubscribe_token"] == "T" * 43
