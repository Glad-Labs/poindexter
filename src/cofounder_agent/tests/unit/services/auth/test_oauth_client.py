"""Unit tests for ``services.auth.oauth_client``.

Covers the four behaviours the helper is responsible for (post-#249):

* mint+cache — first call mints a JWT, second call returns the same
  token from cache.
* expiry+refresh — when the cached token's ``exp`` is in the past, the
  next call mints a new one.
* exp-claim parsing — the helper reads ``exp`` from the JWT payload
  segment without verifying the signature.
* 401-invalidate-and-retry — a 401 from a downstream call drops the
  cached token and retries once with a freshly minted one.
* fail-loud — when client_id/secret are blank, ``get_token()`` raises
  with a pointer to ``poindexter auth migrate-cli``. The static-Bearer
  fallback was removed in Phase 3 (Glad-Labs/poindexter#249).

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


class TestTokenIsFresh:
    """``token_is_fresh`` is the shared freshness predicate used by both
    the in-memory cache and the CLI's cross-process disk cache — "fresh"
    must mean the same thing in both places: an ``exp`` more than the
    refresh skew into the future. Undecodable tokens are never fresh (we
    can't prove they're usable, so we force a re-mint)."""

    def test_future_exp_is_fresh(self):
        from services.auth.oauth_client import token_is_fresh

        assert token_is_fresh(_make_jwt(exp_offset=3600)) is True

    def test_expired_is_not_fresh(self):
        from services.auth.oauth_client import token_is_fresh

        assert token_is_fresh(_make_jwt(exp_offset=-10)) is False

    def test_within_skew_window_is_not_fresh(self):
        from services.auth.oauth_client import EXPIRY_SKEW_SECONDS, token_is_fresh

        # exp is in the future but inside the skew window → treat as stale.
        assert token_is_fresh(_make_jwt(exp_offset=EXPIRY_SKEW_SECONDS - 5)) is False

    def test_undecodable_is_not_fresh(self):
        from services.auth.oauth_client import token_is_fresh

        assert token_is_fresh("plaintext-not-a-jwt") is False


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


class TestOAuthClientFailLoud:
    """When OAuth credentials are blank, ``get_token`` raises loudly —
    the static-Bearer fallback was removed in Phase 3 (#249)."""

    @pytest.mark.asyncio
    async def test_no_credentials_raises_with_migrate_pointer(self):
        client = OAuthClient(base_url="http://test")
        with pytest.raises(RuntimeError, match="client_id/client_secret are required"):
            await client.get_token()

    @pytest.mark.asyncio
    async def test_partial_credentials_raises(self):
        """Only client_id, no secret → still no fallback path post-#249."""
        client = OAuthClient(
            base_url="http://test", client_id="pdx_test", client_secret="",
        )
        with pytest.raises(RuntimeError, match="client_id/client_secret are required"):
            await client.get_token()


class _FakeStore:
    """In-memory ``TokenStore`` for exercising the cross-process seam."""

    def __init__(self, initial: str | None = None) -> None:
        self.value = initial
        self.saved: list[str] = []
        self.cleared = 0
        self.load_error: Exception | None = None

    async def load(self) -> str | None:
        if self.load_error is not None:
            raise self.load_error
        return self.value

    async def save(self, token: str) -> None:
        self.value = token
        self.saved.append(token)

    async def clear(self) -> None:
        self.value = None
        self.cleared += 1


def _mint_transport(mint_holder: dict) -> httpx.MockTransport:
    """A transport whose ``/token`` returns a fresh JWT and counts mints."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/token"
        mint_holder["count"] += 1
        return httpx.Response(
            200,
            json={"access_token": _make_jwt(exp_offset=3600), "expires_in": 3600},
        )

    return httpx.MockTransport(handler)


class TestOAuthClientCredentialProvider:
    """A lazy ``credential_provider`` lets the CLI skip the DB credential
    read on the hot path: creds are resolved only when a mint is actually
    needed, not eagerly at construction."""

    @pytest.mark.asyncio
    async def test_provider_resolves_creds_lazily_on_mint(self):
        calls = {"n": 0}

        async def provider() -> tuple[str, str]:
            calls["n"] += 1
            return "pdx_lazy", "lazy-secret"

        mint = {"count": 0}
        client = OAuthClient(base_url="http://test", credential_provider=provider)
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=_mint_transport(mint), base_url="http://test",
        )

        token = await client.get_token()
        assert token  # minted successfully via lazily-resolved creds
        assert calls["n"] == 1
        assert mint["count"] == 1
        await client.aclose()

    @pytest.mark.asyncio
    async def test_using_oauth_true_with_only_provider(self):
        async def provider() -> tuple[str, str]:
            return "pdx", "sec"

        client = OAuthClient(base_url="http://test", credential_provider=provider)
        assert client.using_oauth is True

    @pytest.mark.asyncio
    async def test_provider_returning_empty_creds_fails_loud(self):
        async def provider() -> tuple[str, str]:
            return "", ""

        client = OAuthClient(base_url="http://test", credential_provider=provider)
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=_mint_transport({"count": 0}), base_url="http://test",
        )
        with pytest.raises(RuntimeError, match="client_id/client_secret are required"):
            await client.get_token()
        await client.aclose()


class TestOAuthClientTokenStore:
    """A ``token_store`` is consulted before minting and written after —
    the cross-process cache seam. A fresh stored token means no mint (and,
    with a lazy provider, no credential read either)."""

    @pytest.mark.asyncio
    async def test_fresh_stored_token_skips_mint_and_provider(self):
        calls = {"n": 0}

        async def provider() -> tuple[str, str]:
            calls["n"] += 1
            return "pdx", "sec"

        stored = _make_jwt(exp_offset=3600)
        store = _FakeStore(initial=stored)
        mint = {"count": 0}
        client = OAuthClient(
            base_url="http://test",
            credential_provider=provider,
            token_store=store,
        )
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=_mint_transport(mint), base_url="http://test",
        )

        token = await client.get_token()
        assert token == stored
        assert mint["count"] == 0  # no mint
        assert calls["n"] == 0  # no credential read
        await client.aclose()

    @pytest.mark.asyncio
    async def test_minted_token_is_saved_to_store(self):
        store = _FakeStore(initial=None)
        mint = {"count": 0}
        client = OAuthClient(
            base_url="http://test",
            client_id="pdx",
            client_secret="sec",
            token_store=store,
        )
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=_mint_transport(mint), base_url="http://test",
        )

        token = await client.get_token()
        assert mint["count"] == 1
        assert store.saved == [token]  # persisted for the next process
        await client.aclose()

    @pytest.mark.asyncio
    async def test_stale_stored_token_is_ignored_and_reminted(self):
        store = _FakeStore(initial=_make_jwt(exp_offset=-10))  # expired
        mint = {"count": 0}
        client = OAuthClient(
            base_url="http://test",
            client_id="pdx",
            client_secret="sec",
            token_store=store,
        )
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=_mint_transport(mint), base_url="http://test",
        )

        token = await client.get_token()
        assert mint["count"] == 1  # stale disk token re-minted
        assert store.saved == [token]
        await client.aclose()

    @pytest.mark.asyncio
    async def test_store_read_failure_falls_through_to_mint(self):
        store = _FakeStore(initial=None)
        store.load_error = OSError("wedged")
        mint = {"count": 0}
        client = OAuthClient(
            base_url="http://test",
            client_id="pdx",
            client_secret="sec",
            token_store=store,
        )
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=_mint_transport(mint), base_url="http://test",
        )

        token = await client.get_token()  # store blew up → mint anyway
        assert token
        assert mint["count"] == 1
        await client.aclose()

    @pytest.mark.asyncio
    async def test_401_clears_store_and_remints_and_persists(self):
        stored = _make_jwt(exp_offset=3600, sub="pdx_stored")
        store = _FakeStore(initial=stored)
        mint = {"count": 0}
        request_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/token":
                mint["count"] += 1
                return httpx.Response(
                    200,
                    json={
                        # distinct sub so the re-minted token is provably not
                        # the rejected one (payloads are otherwise identical
                        # within the same wall-clock second).
                        "access_token": _make_jwt(exp_offset=3600, sub="pdx_reminted"),
                        "expires_in": 3600,
                    },
                )
            request_count["n"] += 1
            if request_count["n"] == 1:
                return httpx.Response(401, json={"error": "invalid_token"})
            return httpx.Response(200, json={"ok": True})

        client = OAuthClient(
            base_url="http://test",
            client_id="pdx",
            client_secret="sec",
            token_store=store,
        )
        client._http = httpx.AsyncClient(  # noqa: SLF001
            transport=httpx.MockTransport(handler), base_url="http://test",
        )

        resp = await client.get("/api/posts")
        assert resp.status_code == 200
        assert store.cleared >= 1  # rejected token dropped from disk
        assert mint["count"] == 1  # one fresh mint after the 401
        assert store.value is not None  # fresh token persisted for next process
        assert store.value != stored  # and it's not the rejected one
        await client.aclose()


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
            }.get(key, "")

        client = await oauth_client_from_secret_reader(
            reader,
            base_url="http://test",
            client_id_key="cli_oauth_client_id",
            client_secret_key="cli_oauth_client_secret",
        )
        assert client.using_oauth is True
        # Post-#249 the reader is no longer asked for "api_token".
        assert seen_keys == [
            "cli_oauth_client_id",
            "cli_oauth_client_secret",
        ]
        await client.aclose()
