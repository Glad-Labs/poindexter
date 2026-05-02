"""MCP-server-side OAuth client helper (Glad-Labs/poindexter#243).

Mirrors ``services.auth.oauth_client`` but deliberately ships zero
imports from the worker codebase. ``mcp-server/`` has its own
``pyproject.toml`` (a separate ``poindexter-mcp`` package) and is run
through ``uv --directory mcp-server run server.py`` — the worker's
``cofounder_agent`` package is not installed in this venv, so importing
``services.auth.oauth_client`` would fail at import time.

The brain daemon ships an analogous mirror at ``brain/oauth_client.py``
for the same reason, and the same caveat applies: the shared wire
format is the only contract. If the worker helper's behaviour changes,
this file needs the matching update — there's nothing the type system
or tests can do to enforce that.

Behaviour matches the worker helper exactly:

- Mints + caches OAuth JWTs from ``POST /token`` with
  ``grant_type=client_credentials``.
- Caches by reading the JWT's ``exp`` claim (no signature verification
  client-side — the MCP server doesn't have the signing key) and
  refreshing ~30 s before the deadline.
- Falls back to the legacy static Bearer (``app_settings.api_token``,
  surfaced via ``POINDEXTER_API_TOKEN`` env) when OAuth credentials
  aren't configured, so the MCP server keeps working through the
  migration window.
- 401-aware: invalidates the cache and retries once on a 401 from any
  wrapped downstream call.
- Async-only.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("mcp-server.oauth_client")

# Refresh ~30 s before the JWT actually expires so a request that takes
# a moment to dispatch never lands on a token that just expired.
EXPIRY_SKEW_SECONDS = 30

# Fallback when the JWT's ``exp`` claim can't be parsed. Matches the
# issuer's documented default in
# ``services.auth.oauth_issuer.DEFAULT_TTL_SECONDS``.
DEFAULT_TTL_SECONDS = 3600

# app_settings keys for this consumer's OAuth client. Centralised here
# so the CLI migration helper (``poindexter auth migrate-mcp``) and the
# server agree on row names. The MCP server has its own pair distinct
# from CLI / brain so revoking one doesn't take down the others.
MCP_CLIENT_ID_KEY = "mcp_oauth_client_id"
MCP_CLIENT_SECRET_KEY = "mcp_oauth_client_secret"
MCP_DEFAULT_SCOPES = "api:read api:write"


# ---------------------------------------------------------------------------
# JWT exp parsing — best-effort, no signature check
# ---------------------------------------------------------------------------


def _decode_jwt_exp(token: str) -> int | None:
    """Read the ``exp`` claim from an unverified JWT.

    The MCP server doesn't hold the signing key (and shouldn't — the
    issuer is the only verifier). We just need to know when the token
    will expire so we can refresh it. Returns ``None`` when the token
    doesn't decode cleanly; the caller substitutes
    ``DEFAULT_TTL_SECONDS``.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload_b64 = parts[1]
        # JWT uses urlsafe base64 without padding.
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


@dataclass
class _CachedToken:
    token: str
    refresh_at: float


# ---------------------------------------------------------------------------
# Secret loading from app_settings — pgcrypto-aware
# ---------------------------------------------------------------------------


async def read_app_setting(pool, key: str, default: str = "") -> str:
    """Fetch one app_settings value, decrypting if it's marked secret.

    Mirrors the equivalent helper in ``brain/oauth_client.py`` — same
    pgcrypto call, same fallback behaviour. Decryption uses
    ``pgp_sym_decrypt`` against ``POINDEXTER_SECRET_KEY``, matching
    ``services.plugins.secrets.get_secret``. Returns ``default`` when
    the row is missing, the value is empty, or decryption fails.
    """
    row = await pool.fetchrow(
        "SELECT value, is_secret FROM app_settings WHERE key = $1", key,
    )
    if not row:
        return default
    val = row["value"]
    if not val:
        return default
    if not row["is_secret"] or not val.startswith("enc:v1:"):
        return val
    pkey = os.getenv("POINDEXTER_SECRET_KEY")
    if not pkey:
        logger.warning(
            "[MCP.OAUTH] POINDEXTER_SECRET_KEY unset — cannot decrypt %s", key,
        )
        return default
    try:
        return await pool.fetchval(
            "SELECT pgp_sym_decrypt(decode($1, 'base64'), $2)::text",
            val[len("enc:v1:"):],
            pkey,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MCP.OAUTH] decrypt %s failed: %s", key, exc)
        return default


# ---------------------------------------------------------------------------
# McpOAuthClient — the public surface
# ---------------------------------------------------------------------------


class McpOAuthClient:
    """Async OAuth client + downstream HTTP wrapper for the MCP server.

    Parameters mirror ``services.auth.oauth_client.OAuthClient``. Like
    the brain helper, this class has no ``site_config`` dependency —
    credentials are loaded externally (typically via
    ``oauth_client_from_pool``) and passed in.
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

    async def __aenter__(self) -> "McpOAuthClient":
        self._ensure_http()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._http = None

    def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.base_url, timeout=self._timeout,
            )
        return self._http

    # ------------------------------------------------------------------
    # Token resolution
    # ------------------------------------------------------------------

    @property
    def using_oauth(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def invalidate_cache(self) -> None:
        """Drop the cached JWT so the next call re-mints."""
        self._cached = None

    async def get_token(self) -> str:
        if not self.using_oauth:
            if not self._static_bearer_token:
                raise RuntimeError(
                    "McpOAuthClient: neither client_id/client_secret nor a "
                    "static bearer token was configured. Run "
                    "`poindexter auth migrate-mcp`, or set "
                    "app_settings.api_token (surfaced via "
                    "POINDEXTER_API_TOKEN env)."
                )
            return self._static_bearer_token

        cached = self._cached
        now = time.time()
        if cached is not None and now < cached.refresh_at:
            return cached.token

        async with self._mint_lock:
            cached = self._cached
            if cached is not None and time.time() < cached.refresh_at:
                return cached.token
            self._cached = await self._mint()
            return self._cached.token

    async def _mint(self) -> _CachedToken:
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
            raise RuntimeError(
                f"OAuth token mint returned no access_token: {payload}"
            )

        exp = _decode_jwt_exp(token)
        if exp is not None:
            refresh_at = max(time.time(), exp - EXPIRY_SKEW_SECONDS)
        else:
            refresh_at = time.time() + DEFAULT_TTL_SECONDS - EXPIRY_SKEW_SECONDS

        logger.debug(
            "[MCP.OAUTH] minted token client_id=%s scopes=%s expires_in=%s",
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
        http = self._ensure_http()
        token = await self.get_token()
        headers = dict(kwargs.pop("headers", None) or {})
        headers["Authorization"] = f"Bearer {token}"

        resp = await http.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401 and retry_on_401 and self.using_oauth:
            logger.info(
                "[MCP.OAUTH] 401 on %s %s — invalidating cache and retrying",
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
# Pool-backed convenience constructor
# ---------------------------------------------------------------------------


async def oauth_client_from_pool(
    pool,
    *,
    base_url: str,
    client_id_key: str = MCP_CLIENT_ID_KEY,
    client_secret_key: str = MCP_CLIENT_SECRET_KEY,
    api_token_key: str = "api_token",
    static_bearer_fallback: str = "",
    scopes: str | None = None,
    timeout: float = 30.0,
) -> McpOAuthClient:
    """Build a ``McpOAuthClient`` by reading creds from app_settings.

    Args:
        pool: an ``asyncpg`` pool against the local Poindexter DB.
        base_url: worker API base (e.g. ``http://localhost:8002``).
        client_id_key / client_secret_key: app_settings rows that hold
            the OAuth credentials. Override these to provision a
            distinct client per consumer (the gladlabs MCP uses
            ``mcp_gladlabs_oauth_client_id`` / ``..._secret``).
        api_token_key: legacy fallback row in app_settings.
        static_bearer_fallback: extra fallback bearer token to use when
            both the OAuth keys AND the api_token row are blank.
            Lets callers thread the existing ``POINDEXTER_API_TOKEN``
            env value in so we don't break setups where the operator
            still provides the token via env (the current MCP server
            entry points do exactly this).
        scopes: optional space-delimited subset of the client's grant.
            ``None`` requests the full grant.
        timeout: httpx timeout for both mints and downstream calls.

    Resolution order, per consumer:

    1. ``app_settings[client_id_key]`` + ``app_settings[client_secret_key]``
       → use OAuth client credentials grant.
    2. ``app_settings[api_token_key]`` → legacy static Bearer.
    3. ``static_bearer_fallback`` (e.g. ``POINDEXTER_API_TOKEN`` env) →
       legacy static Bearer.
    4. Neither set → the returned client raises on first ``get_token()``
       — fail loud per the codebase's no-silent-defaults rule (#198).
    """
    client_id = await read_app_setting(pool, client_id_key, "")
    client_secret = await read_app_setting(pool, client_secret_key, "")
    api_token = await read_app_setting(pool, api_token_key, "")
    return McpOAuthClient(
        base_url=base_url,
        client_id=client_id,
        client_secret=client_secret,
        static_bearer_token=api_token or static_bearer_fallback,
        scopes=scopes,
        timeout=timeout,
    )
