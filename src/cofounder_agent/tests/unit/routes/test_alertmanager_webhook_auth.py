"""Unit tests for the alertmanager webhook Bearer-token guard.

Scope: the auth dependency itself. The main endpoint behavior tests
(persistence, paging, remediation) live in test_alertmanager_webhook_routes.py
and override this dep to a no-op.

Covers:
- Missing Authorization header → 401
- Missing ``Bearer `` prefix → 401
- Token mismatch → 401
- Correct token → 200 (endpoint processes the payload)
- Empty token in app_settings → 503 (fail-closed, misconfigured install)
- Uses ``hmac.compare_digest`` — timing-safe comparison (sanity check
  that we imported hmac, not just ==)
- OAuth JWT (Glad-Labs/poindexter#247) — verified via
  ``services.auth.oauth_issuer`` when token shape matches; rejected
  401 when JWT is bad; falls through to static-Bearer when token
  doesn't look like a JWT.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.alertmanager_webhook_routes import router
from utils.route_utils import get_database_dependency


class _FakeConn:
    pass


class _FakePoolCtx:
    async def __aenter__(self) -> _FakeConn:
        return _FakeConn()

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _FakePool:
    def acquire(self) -> _FakePoolCtx:
        return _FakePoolCtx()


class _FakeDb:
    def __init__(self):
        self.pool = _FakePool()


def _build_app() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database_dependency] = _FakeDb
    return TestClient(app)


class TestBearerTokenAuth:
    def test_missing_header_returns_401(self):
        client = _build_app()
        resp = client.post("/api/webhooks/alertmanager", json={"alerts": []})
        assert resp.status_code == 401
        assert "Bearer" in resp.json().get("detail", "")

    def test_missing_bearer_prefix_returns_401(self):
        client = _build_app()
        resp = client.post(
            "/api/webhooks/alertmanager",
            json={"alerts": []},
            headers={"Authorization": "Basic somevalue"},
        )
        assert resp.status_code == 401

    def test_token_mismatch_returns_401(self):
        client = _build_app()
        with patch(
            "plugins.secrets.get_secret",
            new=AsyncMock(return_value="the-real-token-ABC123"),
        ):
            resp = client.post(
                "/api/webhooks/alertmanager",
                json={"alerts": []},
                headers={"Authorization": "Bearer WRONG"},
            )
        assert resp.status_code == 401

    def test_correct_token_passes(self):
        client = _build_app()
        with patch(
            "plugins.secrets.get_secret",
            new=AsyncMock(return_value="correct-token"),
        ), patch(
            "routes.alertmanager_webhook_routes._ensure_table",
            new=AsyncMock(return_value=None),
        ):
            resp = client.post(
                "/api/webhooks/alertmanager",
                json={"alerts": []},
                headers={"Authorization": "Bearer correct-token"},
            )
        assert resp.status_code == 200

    def test_empty_token_in_db_returns_503(self):
        """Fail-closed — misconfigured install must not silently accept
        unsigned webhooks."""
        client = _build_app()
        with patch(
            "plugins.secrets.get_secret",
            new=AsyncMock(return_value=""),
        ):
            resp = client.post(
                "/api/webhooks/alertmanager",
                json={"alerts": []},
                headers={"Authorization": "Bearer anything"},
            )
        assert resp.status_code == 503

    def test_none_token_in_db_returns_503(self):
        """Same as empty — get_secret returning None means the row
        doesn't exist at all. Also fail-closed."""
        client = _build_app()
        with patch(
            "plugins.secrets.get_secret",
            new=AsyncMock(return_value=None),
        ):
            resp = client.post(
                "/api/webhooks/alertmanager",
                json={"alerts": []},
                headers={"Authorization": "Bearer anything"},
            )
        assert resp.status_code == 503


class TestImplementationDetails:
    def test_uses_hmac_compare_digest(self):
        """Make sure we didn't regress to ``==`` — timing-safe comparison
        matters when the attacker can observe response latency."""
        from routes import alertmanager_webhook_routes as mod
        source = __import__("inspect").getsource(mod.verify_alertmanager_token)
        assert "compare_digest" in source


class TestOAuthJWT:
    """Glad-Labs/poindexter#247 — OAuth JWT path on the alertmanager webhook.

    Three branches:

    - JWT-shaped + verifies → 200 (no static-Bearer round trip).
    - JWT-shaped + verify raises ``InvalidToken`` → 401, no fall-through
      (a malformed JWT is a real auth failure, not "give them another
      chance with the static path").
    - Not-JWT-shaped → fall through to static-Bearer. Already covered by
      ``TestBearerTokenAuth`` against pre-existing behaviour, but
      regress-tested here against the new dispatch order.
    """

    def test_valid_jwt_passes_without_touching_static_token(self):
        """A valid JWT short-circuits before ``get_secret`` is called —
        we patch ``get_secret`` to raise so a successful 200 means the
        JWT path returned without falling through."""
        client = _build_app()

        async def _boom(*_args, **_kwargs):
            raise AssertionError(
                "get_secret should not be called when JWT verifies"
            )

        # JWT shape is "header.payload.signature" — three non-empty
        # base64url segments. Real verification is mocked.
        jwt_token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.fake-sig"

        with patch(
            "plugins.secrets.get_secret",
            new=AsyncMock(side_effect=_boom),
        ), patch(
            "services.auth.oauth_issuer.verify_token",
            return_value=AsyncMock(),
        ), patch(
            "routes.alertmanager_webhook_routes._ensure_table",
            new=AsyncMock(return_value=None),
        ):
            resp = client.post(
                "/api/webhooks/alertmanager",
                json={"alerts": []},
                headers={"Authorization": f"Bearer {jwt_token}"},
            )
        assert resp.status_code == 200, resp.text

    def test_invalid_jwt_returns_401_without_static_fallthrough(self):
        """A JWT-shaped token that fails verification must NOT fall
        through to the static-Bearer path. That would let an attacker
        try a malformed JWT, get rejected, and immediately retry the
        static credentials in the same request — which is silly, but
        also semantically wrong: a JWT-shaped payload is an OAuth
        client claim and a failed verify is a real auth failure."""
        from services.auth.oauth_issuer import InvalidToken

        client = _build_app()

        async def _boom(*_args, **_kwargs):
            raise AssertionError(
                "static-Bearer path must not run after a JWT verify failure"
            )

        jwt_token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.fake"

        with patch(
            "plugins.secrets.get_secret",
            new=AsyncMock(side_effect=_boom),
        ), patch(
            "services.auth.oauth_issuer.verify_token",
            side_effect=InvalidToken("bad signature"),
        ):
            resp = client.post(
                "/api/webhooks/alertmanager",
                json={"alerts": []},
                headers={"Authorization": f"Bearer {jwt_token}"},
            )
        assert resp.status_code == 401
        # WWW-Authenticate hint per RFC 6750 §3.
        assert "Bearer" in resp.headers.get("WWW-Authenticate", "")

    def test_non_jwt_token_falls_through_to_static_path(self):
        """A 32-char static-Bearer token doesn't look like a JWT
        (no dots) and must take the legacy ``app_settings`` path."""
        client = _build_app()

        with patch(
            "plugins.secrets.get_secret",
            new=AsyncMock(return_value="legacy-static-token-abc123"),
        ), patch(
            "routes.alertmanager_webhook_routes._ensure_table",
            new=AsyncMock(return_value=None),
        ):
            resp = client.post(
                "/api/webhooks/alertmanager",
                json={"alerts": []},
                headers={"Authorization": "Bearer legacy-static-token-abc123"},
            )
        assert resp.status_code == 200, resp.text
