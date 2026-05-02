"""Unit tests for ``brain/oauth_client.py`` (Glad-Labs/poindexter#245).

Mirrors the worker-side coverage in
``tests/unit/services/auth/test_oauth_client.py`` so the brain helper
stays behaviourally aligned with the worker helper. We test the
brain-local copy independently because the brain ships a separate
dependency closure (asyncpg + httpx + pyyaml only — no PyJWT, no MCP
SDK), and silent drift between the two implementations would only show
up in production.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

# Put brain/ on sys.path so we can import the brain-local module.
# Same prelude as test_brain_alert_sync.py.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import oauth_client as oac  # noqa: E402


def _make_jwt(exp_offset: int = 3600) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": "poindexter",
        "sub": "pdx_brain",
        "scope": "api:read api:write",
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_offset,
        "jti": "test-brain",
    }

    def _b64(d):
        raw = json.dumps(d, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return f"{_b64(header)}.{_b64(payload)}.signature"


# ---------------------------------------------------------------------------
# Decode + caching
# ---------------------------------------------------------------------------


class TestDecodeJWTExp:
    def test_decodes_exp(self):
        token = _make_jwt(600)
        exp = oac._decode_jwt_exp(token)  # noqa: SLF001
        assert exp is not None and exp > int(time.time())

    def test_returns_none_for_static_bearer(self):
        assert oac._decode_jwt_exp("plaintext-static-token") is None  # noqa: SLF001


class TestBrainOAuthClient:
    @pytest.mark.asyncio
    async def test_mint_then_cache(self):
        mints = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal mints
            assert request.url.path == "/token"
            mints += 1
            return httpx.Response(
                200,
                json={"access_token": _make_jwt(3600), "expires_in": 3600},
            )

        c = oac.BrainOAuthClient(
            base_url="http://test",
            client_id="pdx_brain", client_secret="bsecret",
        )
        c._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )
        await c.get_token()
        await c.get_token()
        assert mints == 1
        await c.aclose()

    @pytest.mark.asyncio
    async def test_expiry_triggers_refresh(self):
        mints = 0

        def handler(_):
            nonlocal mints
            mints += 1
            return httpx.Response(
                200,
                json={"access_token": _make_jwt(3600), "expires_in": 3600},
            )

        c = oac.BrainOAuthClient(
            base_url="http://test",
            client_id="pdx_brain", client_secret="bsecret",
        )
        c._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )
        await c.get_token()
        c._cached.refresh_at = time.time() - 1  # noqa: SLF001
        await c.get_token()
        assert mints == 2
        await c.aclose()

    @pytest.mark.asyncio
    async def test_401_invalidates_cache(self):
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            if request.url.path == "/token":
                return httpx.Response(
                    200,
                    json={"access_token": _make_jwt(), "expires_in": 3600},
                )
            request_count += 1
            if request_count == 1:
                return httpx.Response(401, json={"error": "invalid_token"})
            return httpx.Response(200, json={"ok": True})

        c = oac.BrainOAuthClient(
            base_url="http://test",
            client_id="pdx_brain", client_secret="bsecret",
        )
        c._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )
        resp = await c.get("/api/health")
        assert resp.status_code == 200
        await c.aclose()

    @pytest.mark.asyncio
    async def test_legacy_static_bearer_when_oauth_unset(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/health"
            assert request.headers["Authorization"] == "Bearer brain-legacy"
            return httpx.Response(200, json={"ok": True})

        c = oac.BrainOAuthClient(
            base_url="http://test",
            client_id="", client_secret="",
            static_bearer_token="brain-legacy",
        )
        c._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )
        assert c.using_oauth is False
        resp = await c.get("/api/health")
        assert resp.status_code == 200
        await c.aclose()

    @pytest.mark.asyncio
    async def test_no_credentials_raises(self):
        c = oac.BrainOAuthClient(base_url="http://test")
        with pytest.raises(RuntimeError, match="neither client_id"):
            await c.get_token()


# ---------------------------------------------------------------------------
# Pool-backed constructor
# ---------------------------------------------------------------------------


class TestPoolConstructor:
    @pytest.mark.asyncio
    async def test_loads_credentials_from_app_settings(self, monkeypatch):
        # Pool fakes ``fetchrow`` for read_app_setting + ``fetchval`` for
        # the decryption path (we don't exercise it here — values are
        # plain).
        rows_by_key = {
            "brain_oauth_client_id": {"value": "pdx_brain123", "is_secret": True},
            "brain_oauth_client_secret": {"value": "bsecret", "is_secret": True},
            "api_token": {"value": "legacy-token", "is_secret": True},
        }

        async def _fetchrow(_sql, key):
            return rows_by_key.get(key)

        async def _fetchval(*args, **_kw):
            # No encrypted values in this test, so we should never hit
            # this — assert that to catch accidental decryption attempts.
            raise AssertionError(f"unexpected fetchval call: {args}")

        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        pool.fetchval = AsyncMock(side_effect=_fetchval)

        client = await oac.oauth_client_from_pool(
            pool, base_url="http://test",
        )
        assert client.using_oauth is True
        assert client._client_id == "pdx_brain123"  # noqa: SLF001
        assert client._client_secret == "bsecret"  # noqa: SLF001
        assert client._static_bearer_token == "legacy-token"  # noqa: SLF001
        await client.aclose()

    @pytest.mark.asyncio
    async def test_falls_back_when_oauth_keys_missing(self):
        async def _fetchrow(_sql, key):
            if key == "api_token":
                return {"value": "legacy-token", "is_secret": False}
            return None

        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        pool.fetchval = AsyncMock()

        client = await oac.oauth_client_from_pool(
            pool, base_url="http://test",
        )
        assert client.using_oauth is False
        token = await client.get_token()
        assert token == "legacy-token"
        await client.aclose()
