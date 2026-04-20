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
