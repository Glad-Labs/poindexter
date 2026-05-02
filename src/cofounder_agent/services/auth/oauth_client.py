"""Shared OAuth Client Credentials helper (Glad-Labs/poindexter#241 Phase 2).

Round 1 helper for consumers of the worker API that want to migrate off
the static Bearer token onto OAuth 2.1 JWTs without each consumer
reimplementing the mint+cache+401-retry dance.

## Why this exists

Phase 1 (PR #166) shipped:
- ``POST /oauth/token`` with ``grant_type=client_credentials``
- Dual-auth middleware that accepts JWTs OR the legacy static Bearer

Phase 2 starts moving consumers onto JWTs one at a time. The dual-auth
bridge means migrations can land independently — the helper here is
what each consumer plugs into. Round 1 covers the Poindexter CLI
(#242) and the brain daemon (#245); subsequent rounds cover MCP
servers, OpenClaw skills, and Grafana webhooks.

## Behaviour

- ``OAuthClient`` is async-only. It owns one ``httpx.AsyncClient`` for
  token mint + downstream API calls.
- The minted JWT is cached in-memory until ~30 s before its ``exp``
  claim. We don't decode the JWT cryptographically (that would mean
  pulling in PyJWT just to read a public claim); we base64-decode the
  payload segment, parse JSON, and read ``exp``. If parsing fails we
  fall back to ``DEFAULT_TTL_SECONDS`` (1 hour) — same default the
  issuer uses, so the cache window and the issuer's TTL stay aligned.
- On 401 from any downstream call wrapped by ``request()``, the cache
  is invalidated and the call is retried exactly once with a fresh
  token. After that, the 401 propagates.
- If ``client_id`` / ``client_secret`` are empty (i.e., OAuth not yet
  configured for this consumer), the helper falls back to the legacy
  static Bearer (``app_settings.api_token`` via ``site_config``).
  This is the back-compat seam the dual-auth middleware was built for
  — consumers can ship the migration code, defer running
  ``poindexter auth migrate-*`` until they're ready, and nothing
  breaks in the meantime.

## Wiring

Worker-side consumers (CLI, OpenClaw skills, scripts, MCP server) get a
single ``OAuthClient`` per session. The CLI's ``WorkerClient`` wraps
one as its underlying transport.

The brain daemon container deliberately does NOT import PyJWT or the
MCP SDK — its dep set is ``asyncpg + httpx + pyyaml`` only — so it
ships its own minimal mirror at ``brain/oauth_client.py`` that follows
the same semantics over the same wire format.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from services.logger_config import get_logger

logger = get_logger(__name__)


# Refresh the cached token this many seconds before its actual expiry,
# so a request that takes a moment to dispatch never lands on a token
# that has already expired in flight.
EXPIRY_SKEW_SECONDS = 30

# Used when the JWT's ``exp`` claim can't be parsed for any reason —
# matches ``services.auth.oauth_issuer.DEFAULT_TTL_SECONDS`` so we
# never cache longer than the issuer would have minted.
DEFAULT_TTL_SECONDS = 3600


# ---------------------------------------------------------------------------
# Cached-token bookkeeping
# ---------------------------------------------------------------------------


@dataclass
class _CachedToken:
    """One minted JWT and its derived expiry deadline."""

    token: str
    # Wall-clock seconds (time.time()) at which we should refresh.
    # Already includes ``EXPIRY_SKEW_SECONDS`` of slack.
    refresh_at: float


def _decode_jwt_exp(token: str) -> int | None:
    """Best-effort read of the ``exp`` claim from an unverified JWT.

    The signing key isn't available client-side and isn't needed for
    cache management — we just need to know when to refresh. Returns
    ``None`` if the token isn't shaped like a JWT or the payload isn't
    parseable, in which case the caller substitutes
    ``DEFAULT_TTL_SECONDS``.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload_b64 = parts[1]
        # JWT uses urlsafe base64 without padding; restore the padding
        # so ``base64.urlsafe_b64decode`` doesn't choke.
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8")
        )
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    return int(exp)


# ---------------------------------------------------------------------------
# OAuthClient — the public surface
# ---------------------------------------------------------------------------


class OAuthClient:
    """Async HTTP client that mints + caches OAuth JWTs.

    Construct one per session; reuse it for every downstream call. The
    underlying ``httpx.AsyncClient`` is created lazily on first use and
    closed by ``aclose()`` (or the ``async with`` block).

    Args:
        base_url: The worker API base URL (e.g. ``http://localhost:8002``).
            The token endpoint is assumed to live at ``{base_url}/token``;
            the ``/oauth/token`` alias is registered in
            ``services.routes.oauth_routes`` and points to the same
            handler.
        client_id: OAuth client identifier (``pdx_...``). Empty string
            means "OAuth not configured" — the helper falls back to
            ``static_bearer_token``.
        client_secret: OAuth client secret. Same empty-string semantics
            as ``client_id``.
        static_bearer_token: Legacy ``app_settings.api_token`` value.
            Used as the back-compat fallback when OAuth credentials
            aren't configured. Empty string means "no fallback either"
            and the helper raises on first request.
        scopes: Optional space-delimited subset of the client's granted
            scopes. ``None`` requests the client's full grant.
        timeout: httpx request timeout, applied to both token mints and
            downstream calls. Defaults to 30 s.
    """

    def __init__(
        self,
        base_url: str,
        *,
        client_id: str = "",
        client_secret: str = "",
        static_bearer_token: str = "",
        scopes: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._static_bearer_token = static_bearer_token
        self._scopes = scopes
        self._timeout = timeout

        self._cached: _CachedToken | None = None
        self._mint_lock = asyncio.Lock()
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "OAuthClient":
        self._ensure_http()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:  # noqa: BLE001
                # Closing on teardown is best-effort. Raising here would
                # mask the original exception inside an ``async with``.
                pass
            self._http = None

    def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self._timeout,
            )
        return self._http

    # ------------------------------------------------------------------
    # Token resolution
    # ------------------------------------------------------------------

    @property
    def using_oauth(self) -> bool:
        """``True`` when OAuth client credentials are configured."""
        return bool(self._client_id and self._client_secret)

    def invalidate_cache(self) -> None:
        """Drop the cached JWT. Next ``get_token()`` re-mints.

        Called automatically on a 401 from a wrapped downstream call.
        Exposed publicly for the rare case where an operator wants to
        force a refresh (e.g. after rotating the client secret).
        """
        self._cached = None

    async def get_token(self) -> str:
        """Return a usable bearer token.

        OAuth path: returns the cached JWT if it's still fresh, else
        mints a new one from the token endpoint.

        Legacy fallback path: returns the static bearer token directly
        — there's nothing to cache or refresh.
        """
        if not self.using_oauth:
            if not self._static_bearer_token:
                raise RuntimeError(
                    "OAuthClient: neither client_id/client_secret nor a "
                    "static bearer token was configured. Run `poindexter "
                    "auth migrate-cli` (or migrate-brain), or set "
                    "app_settings.api_token."
                )
            return self._static_bearer_token

        cached = self._cached
        now = time.time()
        if cached is not None and now < cached.refresh_at:
            return cached.token

        # Cache miss / expired — mint a fresh one. Lock so a thundering
        # herd of concurrent callers ends up doing one POST, not N.
        async with self._mint_lock:
            cached = self._cached
            if cached is not None and time.time() < cached.refresh_at:
                return cached.token
            self._cached = await self._mint()
            return self._cached.token

    async def _mint(self) -> _CachedToken:
        """Hit ``/token`` with grant_type=client_credentials."""
        http = self._ensure_http()
        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        if self._scopes:
            data["scope"] = self._scopes

        resp = await http.post("/token", data=data)
        if resp.status_code != 200:
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            raise RuntimeError(
                f"OAuth token mint failed: HTTP {resp.status_code} {body}"
            )
        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError(f"OAuth token mint returned no access_token: {payload}")

        exp = _decode_jwt_exp(token)
        if exp is not None:
            refresh_at = max(time.time(), exp - EXPIRY_SKEW_SECONDS)
        else:
            # Fall back to the issuer's documented default TTL.
            refresh_at = time.time() + DEFAULT_TTL_SECONDS - EXPIRY_SKEW_SECONDS

        logger.debug(
            "OAuthClient minted token client_id=%s scopes=%s expires_in=%ds",
            self._client_id, payload.get("scope"), payload.get("expires_in"),
        )
        return _CachedToken(token=token, refresh_at=refresh_at)

    # ------------------------------------------------------------------
    # Authenticated request helper
    # ------------------------------------------------------------------

    async def request(
        self,
        method: str,
        url: str,
        *,
        retry_on_401: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        """Run an HTTP request with the current token attached.

        On 401, invalidate the cache and retry exactly once with a fresh
        token. Pass ``retry_on_401=False`` to skip the retry (useful
        for authentication-probing endpoints where the 401 is the
        signal you actually want).
        """
        http = self._ensure_http()
        token = await self.get_token()
        headers = dict(kwargs.pop("headers", None) or {})
        headers["Authorization"] = f"Bearer {token}"

        resp = await http.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401 and retry_on_401 and self.using_oauth:
            logger.info(
                "OAuthClient got 401 on %s %s — invalidating cache and retrying",
                method, url,
            )
            self.invalidate_cache()
            token = await self.get_token()
            headers["Authorization"] = f"Bearer {token}"
            resp = await http.request(method, url, headers=headers, **kwargs)
        return resp

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("DELETE", url, **kwargs)


# ---------------------------------------------------------------------------
# Convenience constructor for site-config-aware callers
# ---------------------------------------------------------------------------


async def oauth_client_from_site_config(
    site_config: Any,
    *,
    base_url: str,
    client_id_key: str,
    client_secret_key: str,
    api_token_key: str = "api_token",
    scopes: str | None = None,
    timeout: float = 30.0,
) -> OAuthClient:
    """Build an ``OAuthClient`` by reading creds from app_settings.

    Resolution order, per consumer:

    1. ``app_settings[client_id_key]`` + ``app_settings[client_secret_key]``
       → use OAuth client credentials grant.
    2. ``app_settings[api_token_key]`` → fall back to legacy static
       Bearer (the consumer's existing behaviour).
    3. Neither set → the returned client raises on first ``get_token()``
       — fail loud per the codebase's no-silent-defaults rule.

    The two OAuth keys are read with ``site_config.get_secret`` because
    ``oauth_clients`` registration stores plaintext-equivalent secrets;
    the api_token row is also a secret. Using the secret accessor
    ensures decryption happens for ``is_secret=true`` rows.
    """
    client_id = await site_config.get_secret(client_id_key, "") if site_config else ""
    client_secret = (
        await site_config.get_secret(client_secret_key, "") if site_config else ""
    )
    api_token = await site_config.get_secret(api_token_key, "") if site_config else ""
    return OAuthClient(
        base_url=base_url,
        client_id=client_id,
        client_secret=client_secret,
        static_bearer_token=api_token,
        scopes=scopes,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Pluggable secret reader for callers that don't use site_config
# ---------------------------------------------------------------------------


SecretReader = Callable[[str], Awaitable[str]]
"""Adapter type — async function that takes a setting key and returns
the (decrypted, if applicable) value or empty string. Used by callers
that talk to app_settings directly (e.g., tests) instead of going
through ``site_config``."""


async def oauth_client_from_secret_reader(
    reader: SecretReader,
    *,
    base_url: str,
    client_id_key: str,
    client_secret_key: str,
    api_token_key: str = "api_token",
    scopes: str | None = None,
    timeout: float = 30.0,
) -> OAuthClient:
    """Same as ``oauth_client_from_site_config`` but with a generic reader.

    Useful for tests and for the brain daemon where ``site_config``
    isn't imported (the daemon stays out of the worker's import graph
    on purpose). See ``brain/oauth_client.py`` for the brain's
    asyncpg-backed reader implementation.
    """
    client_id = await reader(client_id_key)
    client_secret = await reader(client_secret_key)
    api_token = await reader(api_token_key)
    return OAuthClient(
        base_url=base_url,
        client_id=client_id,
        client_secret=client_secret,
        static_bearer_token=api_token,
        scopes=scopes,
        timeout=timeout,
    )
