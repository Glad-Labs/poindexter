"""gladlabs MCP-server OAuth client helper (Glad-Labs/poindexter#244).

Mirrors ``services.auth.oauth_client`` but ships zero imports from the
worker codebase. ``mcp-server-gladlabs/`` has its own ``pyproject.toml``
and is run via ``uv --directory mcp-server-gladlabs run server.py`` —
the worker's ``cofounder_agent`` package is not installed in this venv.

The public ``mcp-server/`` ships the same mirror at
``mcp-server/oauth_client.py``. The only difference between the two is
the default app_settings keys — this consumer uses
``mcp_gladlabs_oauth_*`` so it's a distinct OAuth client (revoking one
doesn't take down the other).

Behaviour matches the worker helper exactly. See
``services.auth.oauth_client`` (worker) and ``brain/oauth_client.py``
(brain daemon) for the full design notes — re-stating them here would
just rot. The shared wire format is the only contract.
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

logger = logging.getLogger("mcp-server-gladlabs.oauth_client")

# Refresh ~30 s before the JWT actually expires.
EXPIRY_SKEW_SECONDS = 30

# Fallback when ``exp`` can't be parsed. Matches
# ``services.auth.oauth_issuer.DEFAULT_TTL_SECONDS``.
DEFAULT_TTL_SECONDS = 3600

# Distinct app_settings keys for the gladlabs operator MCP. Distinct
# scope set too — operator-only tools generally need write surfaces
# beyond what the public MCP touches.
MCP_GLADLABS_CLIENT_ID_KEY = "mcp_gladlabs_oauth_client_id"
MCP_GLADLABS_CLIENT_SECRET_KEY = "mcp_gladlabs_oauth_client_secret"
MCP_GLADLABS_DEFAULT_SCOPES = "mcp:read mcp:write api:read api:write"


def _decode_jwt_exp(token: str) -> int | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload_b64 = parts[1]
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


async def read_app_setting(pool, key: str, default: str = "") -> str:
    """Fetch one app_settings value, decrypting ``is_secret=true`` rows.

    Same shape as the brain / public-MCP helper: pgcrypto's
    ``pgp_sym_decrypt`` against ``POINDEXTER_SECRET_KEY``. Returns
    ``default`` on missing row, blank value, or decryption failure.
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
            "[MCP.GLADLABS.OAUTH] POINDEXTER_SECRET_KEY unset — cannot decrypt %s",
            key,
        )
        return default
    try:
        return await pool.fetchval(
            "SELECT pgp_sym_decrypt(decode($1, 'base64'), $2)::text",
            val[len("enc:v1:"):],
            pkey,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MCP.GLADLABS.OAUTH] decrypt %s failed: %s", key, exc)
        return default


class GladlabsMcpOAuthClient:
    """Async OAuth client + downstream HTTP wrapper for the gladlabs MCP."""

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

    async def __aenter__(self) -> "GladlabsMcpOAuthClient":
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

    @property
    def using_oauth(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def invalidate_cache(self) -> None:
        self._cached = None

    async def get_token(self) -> str:
        if not self.using_oauth:
            if not self._static_bearer_token:
                raise RuntimeError(
                    "GladlabsMcpOAuthClient: neither client_id/client_secret "
                    "nor a static bearer token was configured. Run "
                    "`poindexter auth migrate-mcp-gladlabs`, or set "
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
            "[MCP.GLADLABS.OAUTH] minted token client_id=%s scopes=%s expires_in=%s",
            self._client_id, payload.get("scope"), payload.get("expires_in"),
        )
        return _CachedToken(token=token, refresh_at=refresh_at)

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
                "[MCP.GLADLABS.OAUTH] 401 on %s %s — invalidating cache and retrying",
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


async def oauth_client_from_pool(
    pool,
    *,
    base_url: str,
    client_id_key: str = MCP_GLADLABS_CLIENT_ID_KEY,
    client_secret_key: str = MCP_GLADLABS_CLIENT_SECRET_KEY,
    api_token_key: str = "api_token",
    static_bearer_fallback: str = "",
    scopes: str | None = None,
    timeout: float = 30.0,
) -> GladlabsMcpOAuthClient:
    """Build a ``GladlabsMcpOAuthClient`` by reading creds from app_settings.

    Mirrors ``oauth_client_from_pool`` in the public MCP server. The only
    difference is the default key names — this consumer's keys are
    ``mcp_gladlabs_oauth_*`` so revoking the public MCP's client doesn't
    take this server down.

    See the public MCP helper docstring for the resolution order.
    """
    client_id = await read_app_setting(pool, client_id_key, "")
    client_secret = await read_app_setting(pool, client_secret_key, "")
    api_token = await read_app_setting(pool, api_token_key, "")
    return GladlabsMcpOAuthClient(
        base_url=base_url,
        client_id=client_id,
        client_secret=client_secret,
        static_bearer_token=api_token or static_bearer_fallback,
        scopes=scopes,
        timeout=timeout,
    )
