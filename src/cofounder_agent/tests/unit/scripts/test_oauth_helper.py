"""Unit tests for ``scripts/_oauth_helper.py`` (Glad-Labs/poindexter#248).

Mirrors ``tests/unit/services/auth/test_oauth_client.py`` — same five
behaviours, same MockTransport rig — but exercises the standalone
``ScriptsOAuthClient`` mirror that ships in ``scripts/`` for consumers
that don't have ``services/`` on PYTHONPATH.

Also covers the bootstrap.toml + app_settings credential resolution
chain unique to this helper.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

# ``scripts/`` isn't on the default test PYTHONPATH; insert it so we
# can import the helper as a top-level module the same way the real
# scripts do.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_SCRIPTS = _REPO_ROOT / "scripts"
import sys

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _oauth_helper import (  # noqa: E402
    ScriptsOAuthClient,
    _decode_jwt_exp,
    _read_bootstrap_value,
    resolve_credentials,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jwt(exp_offset: int = 3600, sub: str = "pdx_test") -> str:
    """Build a JWT-shaped string with the given expiry offset.

    No signature verification client-side; we just need something that
    decodes through ``_decode_jwt_exp``.
    """
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": "poindexter",
        "sub": sub,
        "scope": "api:read api:write",
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_offset,
        "jti": "test-jti",
    }

    def _b64(d):
        raw = json.dumps(d, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return f"{_b64(header)}.{_b64(payload)}.signature"


# ---------------------------------------------------------------------------
# JWT exp parsing
# ---------------------------------------------------------------------------


class TestDecodeJWTExp:
    def test_valid_jwt_returns_exp(self):
        token = _make_jwt(exp_offset=600)
        exp = _decode_jwt_exp(token)
        assert exp is not None
        assert exp > int(time.time())

    def test_non_jwt_returns_none(self):
        assert _decode_jwt_exp("plaintext-static-token") is None

    def test_two_segment_token_returns_none(self):
        assert _decode_jwt_exp("aaa.bbb") is None

    def test_garbage_payload_returns_none(self):
        assert _decode_jwt_exp("aaa.not-base64.zzz") is None


# ---------------------------------------------------------------------------
# Caching + expiry
# ---------------------------------------------------------------------------


class TestScriptsOAuthClientCaching:
    @pytest.mark.asyncio
    async def test_first_call_mints_subsequent_calls_use_cache(self):
        mint_count = 0
        token = _make_jwt(exp_offset=3600)

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal mint_count
            assert request.url.path == "/token"
            mint_count += 1
            return httpx.Response(
                200,
                json={
                    "access_token": token,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": "api:read api:write",
                },
            )

        client = ScriptsOAuthClient(
            base_url="http://test",
            client_id="pdx_test",
            client_secret="secret",
        )
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )

        t1 = await client.get_token()
        t2 = await client.get_token()
        t3 = await client.get_token()
        assert t1 == t2 == t3 == token
        assert mint_count == 1
        await client.aclose()


class TestScriptsOAuthClientExpiry:
    @pytest.mark.asyncio
    async def test_cache_expiry_triggers_refresh(self):
        mint_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal mint_count
            mint_count += 1
            return httpx.Response(
                200,
                json={
                    "access_token": _make_jwt(exp_offset=3600),
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )

        client = ScriptsOAuthClient(
            base_url="http://test",
            client_id="pdx_test",
            client_secret="secret",
        )
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )

        await client.get_token()
        # Force the cached refresh deadline into the past.
        client._cached.refresh_at = time.time() - 1  # noqa: SLF001
        await client.get_token()
        assert mint_count == 2
        await client.aclose()


# ---------------------------------------------------------------------------
# 401 retry
# ---------------------------------------------------------------------------


class TestScriptsOAuthClient401Retry:
    @pytest.mark.asyncio
    async def test_401_invalidates_and_retries(self):
        mint_count = 0
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal mint_count, request_count
            if request.url.path == "/token":
                mint_count += 1
                return httpx.Response(
                    200,
                    json={
                        "access_token": _make_jwt(exp_offset=3600),
                        "expires_in": 3600,
                    },
                )
            request_count += 1
            if request_count == 1:
                return httpx.Response(401, json={"error": "invalid_token"})
            return httpx.Response(200, json={"ok": True})

        client = ScriptsOAuthClient(
            base_url="http://test",
            client_id="pdx_test",
            client_secret="secret",
        )
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )

        resp = await client.get("/api/posts")
        assert resp.status_code == 200
        assert mint_count == 2  # initial + post-401 refresh
        assert request_count == 2  # original + retry
        await client.aclose()

    @pytest.mark.asyncio
    async def test_401_retry_off_propagates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/token":
                return httpx.Response(
                    200,
                    json={"access_token": _make_jwt(), "expires_in": 3600},
                )
            return httpx.Response(401, json={"error": "invalid_token"})

        client = ScriptsOAuthClient(
            base_url="http://test",
            client_id="pdx_test",
            client_secret="secret",
        )
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )
        resp = await client.request("GET", "/api/posts", retry_on_401=False)
        assert resp.status_code == 401
        await client.aclose()


# ---------------------------------------------------------------------------
# Fail-loud when OAuth credentials are missing (Phase 3 #249)
# ---------------------------------------------------------------------------


class TestScriptsOAuthClientFailLoud:
    @pytest.mark.asyncio
    async def test_no_credentials_at_all_raises(self):
        """Phase 3 (#249): no static-Bearer fallback. Fail loud."""
        client = ScriptsOAuthClient(base_url="http://test")
        with pytest.raises(RuntimeError, match="client_id/client_secret are required"):
            await client.get_token()


# ---------------------------------------------------------------------------
# bootstrap.toml + app_settings credential resolution
# ---------------------------------------------------------------------------


class TestBootstrapValueReader:
    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        # Point HOME at a directory with no bootstrap.toml — the helper
        # is expected to swallow the missing-file case silently.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Reload module so the cached _BOOTSTRAP_PATH picks up the new HOME.
        import importlib

        import _oauth_helper as helper
        importlib.reload(helper)
        assert helper._read_bootstrap_value("scripts_oauth_client_id") == ""

    def test_present_value_returned(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        bootstrap_dir = tmp_path / ".poindexter"
        bootstrap_dir.mkdir(parents=True, exist_ok=True)
        (bootstrap_dir / "bootstrap.toml").write_text(
            'scripts_oauth_client_id = "pdx_from_toml"\n'
            'scripts_oauth_client_secret = "toml-secret"\n',
            encoding="utf-8",
        )

        import importlib

        import _oauth_helper as helper
        importlib.reload(helper)
        assert helper._read_bootstrap_value("scripts_oauth_client_id") == "pdx_from_toml"
        assert helper._read_bootstrap_value("scripts_oauth_client_secret") == "toml-secret"
        assert helper._read_bootstrap_value("missing_key") == ""


class TestResolveCredentialsResolutionOrder:
    """The resolver layers three sources: explicit args >
    bootstrap.toml > app_settings. Walk through each layer to make
    sure higher-priority values win.

    Phase 3 (#249) removed the legacy ``api_token`` fallback layer.
    """

    @pytest.mark.asyncio
    async def test_explicit_args_win_over_everything(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Pre-seed bootstrap.toml with a value that should be ignored.
        bootstrap_dir = tmp_path / ".poindexter"
        bootstrap_dir.mkdir(parents=True, exist_ok=True)
        (bootstrap_dir / "bootstrap.toml").write_text(
            'scripts_oauth_client_id = "ignored-from-toml"\n'
            'scripts_oauth_client_secret = "ignored-from-toml"\n',
            encoding="utf-8",
        )
        import importlib

        import _oauth_helper as helper
        importlib.reload(helper)

        client_id, client_secret = await helper.resolve_credentials(
            pool=None,
            explicit_client_id="explicit-id",
            explicit_client_secret="explicit-secret",
        )
        assert client_id == "explicit-id"
        assert client_secret == "explicit-secret"

    @pytest.mark.asyncio
    async def test_bootstrap_toml_used_when_no_explicit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        bootstrap_dir = tmp_path / ".poindexter"
        bootstrap_dir.mkdir(parents=True, exist_ok=True)
        (bootstrap_dir / "bootstrap.toml").write_text(
            'scripts_oauth_client_id = "pdx_from_toml"\n'
            'scripts_oauth_client_secret = "toml-secret"\n',
            encoding="utf-8",
        )
        import importlib

        import _oauth_helper as helper
        importlib.reload(helper)

        client_id, client_secret = await helper.resolve_credentials(
            pool=None,
        )
        assert client_id == "pdx_from_toml"
        assert client_secret == "toml-secret"

    @pytest.mark.asyncio
    async def test_app_settings_consulted_when_bootstrap_blank(self, tmp_path, monkeypatch):
        # No bootstrap.toml — the helper must fall through to the pool.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib

        import _oauth_helper as helper
        importlib.reload(helper)

        # Stub the pool's fetchrow to return a different value per key.
        # The real read path uses ``await pool.fetchrow`` directly (no
        # ``async with pool.acquire()``), matching the brain's helper.
        seen_keys = []

        async def _fetchrow(query, key):
            seen_keys.append(key)
            mapping = {
                "scripts_oauth_client_id": ("pdx_from_db", False),
                "scripts_oauth_client_secret": ("db-secret", False),
            }
            value, is_secret = mapping.get(key, (None, False))
            if value is None:
                return None
            return {"value": value, "is_secret": is_secret}

        class _StubPool:
            fetchrow = staticmethod(_fetchrow)

        pool = _StubPool()
        client_id, client_secret = await helper.resolve_credentials(pool=pool)

        assert client_id == "pdx_from_db"
        assert client_secret == "db-secret"
        # Only the OAuth keys are queried — api_token is no longer read (#249).
        assert "scripts_oauth_client_id" in seen_keys
        assert "scripts_oauth_client_secret" in seen_keys
        assert "api_token" not in seen_keys

    @pytest.mark.asyncio
    async def test_no_creds_anywhere_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib

        import _oauth_helper as helper
        importlib.reload(helper)

        client_id, client_secret = await helper.resolve_credentials(
            pool=None,
        )
        assert client_id == ""
        assert client_secret == ""
