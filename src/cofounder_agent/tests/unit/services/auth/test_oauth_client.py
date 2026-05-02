"""Unit tests for ``services.auth.oauth_client``.

Covers the five behaviours the helper is responsible for:

* mint+cache — first call mints a JWT, second call returns the same
  token from cache.
* expiry+refresh — when the cached token's ``exp`` is in the past, the
  next call mints a new one.
* exp-claim parsing — the helper reads ``exp`` from the JWT payload
  segment without verifying the signature.
* 401-invalidate-and-retry — a 401 from a downstream call drops the
  cached token and retries once with a freshly minted one.
* legacy fallback — when client_id/secret are blank, ``get_token()``
  hands back the static bearer instead.

Token endpoint and downstream API are stubbed via httpx's
``MockTransport`` so no real network or DB is touched.
"""

from __future__ import annotations

import base64
import json
import time

import httpx
import pytest

from services.auth.oauth_client import (
    OAuthClient,
    _decode_jwt_exp,
    oauth_client_from_secret_reader,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jwt(exp_offset: int = 3600, sub: str = "pdx_test") -> str:
    """Build a JWT-shaped string with the given expiry offset.

    No signature verification happens client-side, so we just need
    something that decodes through ``_decode_jwt_exp``. Header and
    payload are real base64; the signature segment is a constant
    placeholder.
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
# Tests
# ---------------------------------------------------------------------------


class TestDecodeJWTExp:
    """The exp-claim parser is the riskiest part of the cache logic —
    a wrong answer here means we either mint constantly (false-low exp)
    or hand out an expired token (false-high exp). Cover the obvious
    failure modes."""

    def test_valid_jwt_returns_exp(self):
        token = _make_jwt(exp_offset=600)
        exp = _decode_jwt_exp(token)
        assert exp is not None
        assert exp > int(time.time())

    def test_non_jwt_returns_none(self):
        # Static bearer tokens are 32-char strings — no dots.
        assert _decode_jwt_exp("plaintext-static-token") is None

    def test_two_segment_token_returns_none(self):
        # Malformed JWT (header.payload, no sig) — issuer never produces
        # this shape, but the parser should refuse it cleanly.
        assert _decode_jwt_exp("aaa.bbb") is None

    def test_garbage_payload_returns_none(self):
        assert _decode_jwt_exp("aaa.not-base64.zzz") is None


class TestOAuthClientCaching:
    """mint+cache behaviour."""

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

        client = OAuthClient(
            base_url="http://test",
            client_id="pdx_test",
            client_secret="secret",
        )
        # Inject the mock transport into the client's lazy httpx instance.
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )

        t1 = await client.get_token()
        t2 = await client.get_token()
        t3 = await client.get_token()
        assert t1 == t2 == t3 == token
        assert mint_count == 1
        await client.aclose()


class TestOAuthClientExpiry:
    """When the cached token's deadline has passed, the next call mints."""

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

        client = OAuthClient(
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


class TestOAuthClient401Retry:
    """A 401 on a downstream call invalidates the cache and retries
    exactly once. Subsequent 401s propagate."""

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

        client = OAuthClient(
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
        """retry_on_401=False is honoured — useful when the call is
        deliberately auth-probing."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/token":
                return httpx.Response(
                    200,
                    json={"access_token": _make_jwt(), "expires_in": 3600},
                )
            return httpx.Response(401, json={"error": "invalid_token"})

        client = OAuthClient(
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


class TestOAuthClientLegacyFallback:
    """When OAuth credentials are blank, fall through to the static
    bearer. ``get_token`` returns the bearer as-is, no minting."""

    @pytest.mark.asyncio
    async def test_static_bearer_used_when_oauth_unconfigured(self):
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            # If we hit /token in legacy mode, the test should fail —
            # only the downstream API path is expected.
            if request.url.path == "/token":
                pytest.fail("/token should not be hit in legacy fallback")
            assert request.headers["Authorization"] == "Bearer legacy-static-token"
            return httpx.Response(200, json={"ok": True})

        client = OAuthClient(
            base_url="http://test",
            client_id="",
            client_secret="",
            static_bearer_token="legacy-static-token",
        )
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )
        assert client.using_oauth is False
        token = await client.get_token()
        assert token == "legacy-static-token"
        resp = await client.get("/api/posts")
        assert resp.status_code == 200
        assert called is True
        await client.aclose()

    @pytest.mark.asyncio
    async def test_no_credentials_at_all_raises(self):
        client = OAuthClient(base_url="http://test")
        with pytest.raises(RuntimeError, match="neither client_id"):
            await client.get_token()


class TestSecretReaderConstructor:
    """``oauth_client_from_secret_reader`` resolves creds via an async
    callable and assembles the same client. Lets the brain helper share
    the worker's logic without dragging in site_config."""

    @pytest.mark.asyncio
    async def test_reader_pulls_creds(self):
        seen_keys = []

        async def reader(key: str) -> str:
            seen_keys.append(key)
            return {
                "cli_oauth_client_id": "pdx_from_reader",
                "cli_oauth_client_secret": "reader-secret",
                "api_token": "reader-fallback",
            }.get(key, "")

        client = await oauth_client_from_secret_reader(
            reader,
            base_url="http://test",
            client_id_key="cli_oauth_client_id",
            client_secret_key="cli_oauth_client_secret",
        )
        assert client.using_oauth is True
        assert seen_keys == [
            "cli_oauth_client_id",
            "cli_oauth_client_secret",
            "api_token",
        ]
        await client.aclose()
