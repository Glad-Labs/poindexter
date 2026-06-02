"""Unit tests for the OAuth client_credentials token mint (Glad-Labs/poindexter#617).

The ``POST /token`` endpoint (``routes/oauth_routes.py``) layers a
``client_credentials`` grant on top of the MCP SDK token handler. The
inline ``_client_credentials`` helper is the headless CLI / scripts /
brain-daemon path — every non-browser consumer mints JWTs through it.
It was previously untested.

Pinned contract (read from ``_client_credentials`` ~line 356 + the
``token`` dispatcher ~line 316):

- valid creds → 200 with an access_token + the client's scopes
- requesting a SUBSET of granted scopes → only that subset is issued
- wrong client_secret → 401 ``invalid_client``
- unknown client_id → 401 ``invalid_client``
- requesting an UN-GRANTED scope → 400 ``invalid_scope``
- client lacking the ``client_credentials`` grant → 400
  ``unauthorized_client``
- missing client_id / client_secret → 401 ``invalid_client``

These tests stub ``PoindexterOAuthProvider`` (so no DB is touched) and
set ``POINDEXTER_SECRET_KEY`` so the real ``issue_token`` mints a real
HS256 JWT on the happy path.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.oauth_routes import authorization_router
from utils.route_utils import get_database_dependency, get_site_config_dependency

# ---------------------------------------------------------------------------
# Signing key — issue_token reads POINDEXTER_SECRET_KEY at call time.
# Set it for the whole module so the happy-path mint produces a real JWT.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv("POINDEXTER_SECRET_KEY", "test-oauth-signing-key-0123456789")


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def _make_pool():
    """asyncpg pool double — _client_credentials updates last_used_at via
    ``pool.acquire()`` + ``conn.execute`` on the happy path."""
    from contextlib import asynccontextmanager

    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool


def _make_db(pool=None):
    db = MagicMock()
    db.pool = pool if pool is not None else _make_pool()
    return db


def _make_site_config():
    sc = MagicMock()
    sc.get = MagicMock(side_effect=lambda key, default=None: default)
    return sc


def _fake_client(
    *,
    client_id="pdx_testclient",
    client_secret="s3cr3t-value",
    grant_types=("client_credentials",),
    scope="mcp:read mcp:write",
):
    """Stand-in for the SDK's OAuthClientInformationFull. The
    ``_client_credentials`` helper only reads ``.client_secret``,
    ``.grant_types``, ``.scope`` off it."""
    return SimpleNamespace(
        client_id=client_id,
        client_secret=client_secret,
        grant_types=list(grant_types),
        scope=scope,
    )


@pytest.fixture
def patch_provider(monkeypatch):
    """Return a helper that patches PoindexterOAuthProvider in the route
    module to yield a stub provider with the given get_client result."""

    def _apply(provider_client):
        provider = MagicMock()
        provider.get_client = AsyncMock(return_value=provider_client)
        monkeypatch.setattr(
            "routes.oauth_routes.PoindexterOAuthProvider",
            lambda pool: provider,
        )
        return provider

    return _apply


def _post_token(client: TestClient, form: dict):
    return client.post(
        "/token",
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClientCredentialsHappyPath:
    def test_valid_creds_returns_token_with_granted_scopes(self, patch_provider):
        patch_provider(_fake_client(scope="mcp:read mcp:write"))
        app = FastAPI()
        app.include_router(authorization_router)
        db = _make_db()
        app.dependency_overrides[get_database_dependency] = lambda: db
        app.dependency_overrides[get_site_config_dependency] = lambda: _make_site_config()

        client = TestClient(app)
        resp = _post_token(
            client,
            {
                "grant_type": "client_credentials",
                "client_id": "pdx_testclient",
                "client_secret": "s3cr3t-value",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["token_type"] == "Bearer"
        assert data["access_token"]
        # No scope requested → all granted scopes issued.
        assert set(data["scope"].split()) == {"mcp:read", "mcp:write"}
        assert data["expires_in"] > 0

    def test_requesting_subset_of_granted_scopes_issues_only_subset(
        self, patch_provider
    ):
        patch_provider(_fake_client(scope="mcp:read mcp:write api:read"))
        app = FastAPI()
        app.include_router(authorization_router)
        app.dependency_overrides[get_database_dependency] = lambda: _make_db()
        app.dependency_overrides[get_site_config_dependency] = lambda: _make_site_config()

        client = TestClient(app)
        resp = _post_token(
            client,
            {
                "grant_type": "client_credentials",
                "client_id": "pdx_testclient",
                "client_secret": "s3cr3t-value",
                "scope": "mcp:read",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["scope"] == "mcp:read"


# ---------------------------------------------------------------------------
# Auth failures
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClientCredentialsAuthFailures:
    def test_wrong_secret_returns_401(self, patch_provider):
        patch_provider(_fake_client(client_secret="the-real-secret"))
        app = FastAPI()
        app.include_router(authorization_router)
        app.dependency_overrides[get_database_dependency] = lambda: _make_db()
        app.dependency_overrides[get_site_config_dependency] = lambda: _make_site_config()

        client = TestClient(app)
        resp = _post_token(
            client,
            {
                "grant_type": "client_credentials",
                "client_id": "pdx_testclient",
                "client_secret": "WRONG-secret",
            },
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_client"

    def test_unknown_client_returns_401(self, patch_provider):
        # get_client returns None → unknown client.
        patch_provider(None)
        app = FastAPI()
        app.include_router(authorization_router)
        app.dependency_overrides[get_database_dependency] = lambda: _make_db()
        app.dependency_overrides[get_site_config_dependency] = lambda: _make_site_config()

        client = TestClient(app)
        resp = _post_token(
            client,
            {
                "grant_type": "client_credentials",
                "client_id": "pdx_does_not_exist",
                "client_secret": "whatever",
            },
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_client"

    def test_missing_credentials_returns_401(self, patch_provider):
        # Provider should never even be consulted — but stub it anyway.
        patch_provider(_fake_client())
        app = FastAPI()
        app.include_router(authorization_router)
        app.dependency_overrides[get_database_dependency] = lambda: _make_db()
        app.dependency_overrides[get_site_config_dependency] = lambda: _make_site_config()

        client = TestClient(app)
        resp = _post_token(
            client,
            {
                "grant_type": "client_credentials",
                # No client_id / client_secret.
            },
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_client"

    def test_missing_only_secret_returns_401(self, patch_provider):
        patch_provider(_fake_client())
        app = FastAPI()
        app.include_router(authorization_router)
        app.dependency_overrides[get_database_dependency] = lambda: _make_db()
        app.dependency_overrides[get_site_config_dependency] = lambda: _make_site_config()

        client = TestClient(app)
        resp = _post_token(
            client,
            {
                "grant_type": "client_credentials",
                "client_id": "pdx_testclient",
            },
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_client"


# ---------------------------------------------------------------------------
# Scope + grant-type policy failures
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClientCredentialsPolicyFailures:
    def test_requesting_ungranted_scope_returns_invalid_scope_400(
        self, patch_provider
    ):
        # Client granted only mcp:read; request mcp:write too.
        patch_provider(_fake_client(scope="mcp:read"))
        app = FastAPI()
        app.include_router(authorization_router)
        app.dependency_overrides[get_database_dependency] = lambda: _make_db()
        app.dependency_overrides[get_site_config_dependency] = lambda: _make_site_config()

        client = TestClient(app)
        resp = _post_token(
            client,
            {
                "grant_type": "client_credentials",
                "client_id": "pdx_testclient",
                "client_secret": "s3cr3t-value",
                "scope": "mcp:read mcp:write",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_scope"

    def test_client_without_client_credentials_grant_returns_400(
        self, patch_provider
    ):
        # Client registered with only authorization_code — not allowed
        # to use the client_credentials grant.
        patch_provider(_fake_client(grant_types=("authorization_code",)))
        app = FastAPI()
        app.include_router(authorization_router)
        app.dependency_overrides[get_database_dependency] = lambda: _make_db()
        app.dependency_overrides[get_site_config_dependency] = lambda: _make_site_config()

        client = TestClient(app)
        resp = _post_token(
            client,
            {
                "grant_type": "client_credentials",
                "client_id": "pdx_testclient",
                "client_secret": "s3cr3t-value",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "unauthorized_client"
