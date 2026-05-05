"""Brain-side OAuth client helper (Glad-Labs/poindexter#245).

Mirrors ``services.auth.oauth_client`` but deliberately ships zero
imports from the worker codebase. The brain container's pyproject
pins only ``asyncpg + httpx + pyyaml`` (see ``brain/pyproject.toml``
for the rationale), so importing the worker's helper would either
fail at import time (PyJWT not installed) or force us to widen the
brain dependency closure.

Behaviour matches the worker helper exactly:

- Mints + caches OAuth JWTs from ``POST /token`` with
  ``grant_type=client_credentials``.
- Caches by reading the JWT's ``exp`` claim (no signature verification
  client-side — brain doesn't have the signing key) and refreshing
  ~30 s before the deadline.
- 401-aware: invalidates the cache and retries once on a 401 from
  any wrapped downstream call.
- Async-only.
- OAuth credentials are required. The legacy static-Bearer fallback
  was removed in Phase 3 (#249).

The shared wire format is the only contract; if the worker helper's
behaviour is updated, this file needs the matching update — there's
nothing the type system or tests can do to enforce that for us.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

# Shared brain secret reader — re-exported as ``read_app_setting`` for
# backwards compatibility with anything that imports it from here. The
# actual decrypt logic lives in ``brain.secret_reader`` so the brain
# notify path, glitchtip probe, and oauth helper all share one
# implementation (closes Glad-Labs/poindexter#342).
try:  # Flat import when brain/ is on sys.path (container runtime).
    from secret_reader import read_app_setting
except ImportError:  # pragma: no cover — package-qualified path
    from brain.secret_reader import read_app_setting

logger = logging.getLogger("brain.oauth_client")

# Refresh ~30 s before the JWT actually expires so a request that takes
# a moment to dispatch never lands on a token that just expired.
EXPIRY_SKEW_SECONDS = 30

# Fallback when the JWT's ``exp`` claim can't be parsed. Matches the
# issuer's documented default in ``services.auth.oauth_issuer.DEFAULT_TTL_SECONDS``.
DEFAULT_TTL_SECONDS = 3600


# ---------------------------------------------------------------------------
# JWT exp parsing — best-effort, no signature check
# ---------------------------------------------------------------------------


def _decode_jwt_exp(token: str) -> int | None:
    """Read the ``exp`` claim from an unverified JWT.

    Brain doesn't hold the signing key (and shouldn't — the issuer is
    the only verifier). We just need to know when the token will expire
    so we can refresh it. Returns ``None`` when the token doesn't
    decode cleanly; the caller substitutes ``DEFAULT_TTL_SECONDS``.
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
# BrainOAuthClient — the public surface
# ---------------------------------------------------------------------------

# ``read_app_setting`` is re-exported from ``brain.secret_reader`` at
# the top of this module for backwards compatibility — keep it here so
# ``from brain.oauth_client import read_app_setting`` still works for
# any existing call sites (CLI helpers, tests).


class BrainOAuthClient:
    """Async OAuth client + downstream HTTP wrapper for the brain daemon.

    Parameters mirror ``services.auth.oauth_client.OAuthClient``. The
    only behavioural difference is that this class has no
    ``site_config`` dependency — credentials are loaded externally
    (typically via ``oauth_client_from_pool``) and passed in.
    """

    def __init__(
        self,
        base_url: str,
        *,
        client_id: str = "",
        client_secret: str = "",
        scopes: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = scopes
        self._timeout = timeout

        self._cached: _CachedToken | None = None
        self._mint_lock = asyncio.Lock()
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "BrainOAuthClient":
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
            raise RuntimeError(
                "BrainOAuthClient: client_id/client_secret are required. "
                "Run `poindexter auth migrate-brain` to provision an "
                "OAuth client. Static-Bearer fallback was removed in #249."
            )

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
            raise RuntimeError(f"OAuth token mint returned no access_token: {payload}")

        exp = _decode_jwt_exp(token)
        if exp is not None:
            refresh_at = max(time.time(), exp - EXPIRY_SKEW_SECONDS)
        else:
            refresh_at = time.time() + DEFAULT_TTL_SECONDS - EXPIRY_SKEW_SECONDS

        logger.debug(
            "[BRAIN.OAUTH] minted token client_id=%s scopes=%s expires_in=%s",
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
        if resp.status_code == 401 and retry_on_401:
            logger.info(
                "[BRAIN.OAUTH] 401 on %s %s — invalidating cache and retrying",
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


# ---------------------------------------------------------------------------
# Pool-backed convenience constructor
# ---------------------------------------------------------------------------


# app_settings keys for the brain client. Centralised here so the CLI
# migration helper (``poindexter auth migrate-brain``) and the daemon
# itself agree on the row names.
BRAIN_CLIENT_ID_KEY = "brain_oauth_client_id"
BRAIN_CLIENT_SECRET_KEY = "brain_oauth_client_secret"
BRAIN_DEFAULT_SCOPES = "api:read api:write"


async def oauth_client_from_pool(
    pool,
    *,
    base_url: str,
    client_id_key: str = BRAIN_CLIENT_ID_KEY,
    client_secret_key: str = BRAIN_CLIENT_SECRET_KEY,
    scopes: str | None = None,
    timeout: float = 30.0,
) -> BrainOAuthClient:
    """Build a ``BrainOAuthClient`` by reading creds from app_settings.

    The brain calls this once per daemon startup with the same pool it
    uses for everything else. Uses the brain-local
    ``read_app_setting`` decryption helper rather than importing the
    worker's plugins.secrets module (which isn't on the brain
    container's PYTHONPATH).
    """
    client_id = await read_app_setting(pool, client_id_key, "")
    client_secret = await read_app_setting(pool, client_secret_key, "")
    return BrainOAuthClient(
        base_url=base_url,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
        timeout=timeout,
    )
