"""Unit tests for ``poindexter.cli._api_client.WorkerClient`` (#242, #249).

Covers the OAuth-only HTTP transport that ships post-Phase 3.

* **OAuth path** — when ``cli_oauth_client_id`` + ``cli_oauth_client_secret``
  are present, the client mints a JWT and attaches it as ``Bearer <jwt>``.
* **Fail-loud** — when those keys are blank, the client raises a
  ``RuntimeError`` with a pointer to ``poindexter auth migrate-cli``.
  The legacy static-Bearer fallback (``app_settings.api_token`` /
  ``POINDEXTER_KEY`` env var) was removed in Phase 3 (#249).
"""

from __future__ import annotations

import base64
import json
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest


def _make_jwt(exp_offset: int = 3600) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": "poindexter",
        "sub": "pdx_cli",
        "scope": "api:read api:write",
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_offset,
        "jti": "cli-test",
    }

    def _b64(d):
        raw = json.dumps(d, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return f"{_b64(header)}.{_b64(payload)}.signature"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _api_url(monkeypatch):
    monkeypatch.setenv("POINDEXTER_API_URL", "http://test-worker")
    monkeypatch.delenv("WORKER_API_URL", raising=False)
    yield


@pytest.fixture(autouse=True)
def _no_dsn(monkeypatch):
    """Default tests run without DB access. Each test that wants creds
    from app_settings overrides this with a fake pool."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LOCAL_DATABASE_URL", raising=False)
    monkeypatch.delenv("POINDEXTER_MEMORY_DSN", raising=False)


# ---------------------------------------------------------------------------
# OAuth path — credentials supplied via constructor (deterministic input
# without standing up an asyncpg pool in the unit test).
# ---------------------------------------------------------------------------


class TestWorkerClientOAuthPath:
    @pytest.mark.asyncio
    async def test_oauth_explicit_client_id_mints_jwt(self):
        from poindexter.cli._api_client import WorkerClient

        mints = 0
        downstream_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal mints, downstream_calls
            if request.url.path == "/token":
                mints += 1
                # Verify form-encoded grant type
                body = request.content.decode()
                assert "grant_type=client_credentials" in body
                assert "client_id=pdx_cli_test" in body
                return httpx.Response(
                    200,
                    json={
                        "access_token": _make_jwt(3600),
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
            downstream_calls += 1
            assert request.headers["Authorization"].startswith("Bearer ")
            return httpx.Response(200, json={"items": []})

        # Patch the OAuthClient's underlying httpx so no real network.
        # We can't easily reach into `_oauth._http` until __aenter__ has
        # finished, so we monkey-patch httpx.AsyncClient at import-time.
        from services.auth import oauth_client as oac_module

        original_async_client = httpx.AsyncClient

        def mocked_async_client(*args, **kwargs):
            kwargs.pop("transport", None)
            return original_async_client(
                *args,
                transport=httpx.MockTransport(handler),
                **kwargs,
            )

        with patch.object(oac_module.httpx, "AsyncClient", mocked_async_client):
            with patch(
                "poindexter.cli._api_client.httpx.AsyncClient",
                mocked_async_client,
            ):
                async with WorkerClient(
                    client_id="pdx_cli_test",
                    client_secret="cli-secret",
                ) as c:
                    resp = await c.get("/api/posts")
                    assert resp.status_code == 200
        assert mints == 1
        assert downstream_calls == 1


# ---------------------------------------------------------------------------
# Fail-loud — Phase 3 (#249) removed the static-Bearer fallback.
# Empty OAuth credentials should raise on context-manager entry rather
# than silently degrading to a legacy path.
# ---------------------------------------------------------------------------


class TestWorkerClientFailLoud:
    @pytest.mark.asyncio
    async def test_no_creds_at_all_raises_on_enter(self, monkeypatch):
        from poindexter.cli._api_client import WorkerClient

        # Even with the legacy env vars set, the client must refuse —
        # the static-Bearer slot is gone post-#249.
        monkeypatch.setenv("POINDEXTER_KEY", "stale-legacy-value")

        with pytest.raises(RuntimeError, match="No CLI OAuth credentials"):
            async with WorkerClient(client_id="", client_secret="") as _c:
                pass

    @pytest.mark.asyncio
    async def test_legacy_env_var_does_not_satisfy_credentials(self, monkeypatch):
        """Regression test for Glad-Labs/poindexter#249.

        ``POINDEXTER_KEY`` and ``GLADLABS_KEY`` used to populate the
        static-Bearer slot. Phase 3 removed that path — the env vars
        must be ignored entirely.
        """
        from poindexter.cli._api_client import WorkerClient

        monkeypatch.setenv("POINDEXTER_KEY", "legacy-bearer")
        monkeypatch.setenv("GLADLABS_KEY", "another-legacy-bearer")

        with pytest.raises(RuntimeError, match="No CLI OAuth credentials"):
            async with WorkerClient(client_id="", client_secret="") as _c:
                pass


# ---------------------------------------------------------------------------
# Constants are exported for the migrate-cli command.
# ---------------------------------------------------------------------------


class TestExports:
    def test_setting_keys_are_exported(self):
        from poindexter.cli._api_client import (
            CLI_CLIENT_ID_KEY,
            CLI_CLIENT_SECRET_KEY,
            CLI_DEFAULT_SCOPES,
        )

        assert CLI_CLIENT_ID_KEY == "cli_oauth_client_id"
        assert CLI_CLIENT_SECRET_KEY == "cli_oauth_client_secret"
        assert "api:read" in CLI_DEFAULT_SCOPES
        assert "api:write" in CLI_DEFAULT_SCOPES
