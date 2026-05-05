"""Standalone OAuth helper for ``scripts/`` consumers (Glad-Labs/poindexter#248).

Phase 2 Round 2B helper for scripts that talk to the Poindexter worker
API. The worker-side helper at ``services.auth.oauth_client.OAuthClient``
lives inside the worker import graph; some scripts under ``scripts/``
run as standalone Python processes (operator helpers, daemons, bots)
that don't have ``services/`` on their PYTHONPATH and shouldn't pay the
cost of pulling in the worker's dependency closure just to mint a JWT.

This module is the operator-side mirror: same wire format, same
back-compat semantics (legacy static-Bearer fallback), zero imports
from ``services/``. Only stdlib + ``httpx`` + ``asyncpg`` are required
— the same dep set the brain daemon ships with.

## Behaviour

- ``ScriptsOAuthClient`` is async-only. It owns one ``httpx.AsyncClient``
  for token mint + downstream API calls.
- Caches the minted JWT in-memory until ~30 s before its ``exp`` claim,
  read from the (unverified) JWT payload. Falls back to
  ``DEFAULT_TTL_SECONDS`` (1 hour) if parsing fails — same default the
  worker's issuer uses.
- 401 from any wrapped downstream call invalidates the cache and
  retries exactly once.
- OAuth credentials are required. The legacy static-Bearer fallback
  (``app_settings.api_token`` / bootstrap.toml ``api_token``) was
  removed in Phase 3 (#249).

## Credential resolution order

Built to match the brain daemon's resolution dance — the brain reads
from app_settings via asyncpg (with pgcrypto decryption), the scripts
helper reads from bootstrap.toml first (operator-readable, useful for
host-side scripts that don't have DB creds) then falls through to
asyncpg + app_settings.

1. Explicit ``--client-id`` / ``--client-secret`` CLI flags (when the
   caller wires them via ``ScriptsOAuthClient.__init__``).
2. ``~/.poindexter/bootstrap.toml`` keys
   ``scripts_oauth_client_id`` / ``scripts_oauth_client_secret``.
3. ``app_settings`` table (decrypted via pgcrypto, mirroring the brain).
4. Failing all of the above, ``get_token()`` raises with a pointer to
   ``poindexter auth migrate-scripts``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("scripts.oauth")

# Refresh ~30 s before the JWT actually expires so a request that takes
# a moment to dispatch never lands on a token that just expired.
EXPIRY_SKEW_SECONDS = 30

# Fallback when the JWT's ``exp`` claim can't be parsed. Matches the
# issuer's ``services.auth.oauth_issuer.DEFAULT_TTL_SECONDS``.
DEFAULT_TTL_SECONDS = 3600

# Canonical app_settings keys for the shared scripts OAuth client.
# Single shared client per operator covers all scripts — per-script
# clients would be overkill at our scale and create rotation churn.
SCRIPTS_CLIENT_ID_KEY = "scripts_oauth_client_id"
SCRIPTS_CLIENT_SECRET_KEY = "scripts_oauth_client_secret"
SCRIPTS_DEFAULT_SCOPES = "api:read api:write"

# bootstrap.toml file location — same path the rest of the operator
# tooling reads from.
_BOOTSTRAP_PATH = Path.home() / ".poindexter" / "bootstrap.toml"


# ---------------------------------------------------------------------------
# JWT exp parsing — best-effort, no signature check
# ---------------------------------------------------------------------------


def _decode_jwt_exp(token: str) -> int | None:
    """Read the ``exp`` claim from an unverified JWT.

    Scripts don't hold the signing key (and shouldn't — the issuer is
    the only verifier). We just need to know when the token expires.
    Returns ``None`` when the token doesn't decode cleanly; the caller
    substitutes ``DEFAULT_TTL_SECONDS``.
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


# ---------------------------------------------------------------------------
# bootstrap.toml + app_settings reads
# ---------------------------------------------------------------------------


def _read_bootstrap_value(key: str) -> str:
    """Best-effort read of a single bootstrap.toml key.

    Returns "" on any error (missing file, missing tomllib, parse
    failure, missing key). Does NOT raise — the caller is expected to
    fall through to app_settings or the legacy Bearer.
    """
    try:
        # Python 3.11+ ships tomllib in stdlib; 3.10 needs tomli (rare
        # on operator boxes but worth the fallback).
        try:
            import tomllib as _tomllib  # type: ignore[import-not-found]
        except ImportError:
            try:
                import tomli as _tomllib  # type: ignore[import-not-found]
            except ImportError:
                return ""
        if not _BOOTSTRAP_PATH.is_file():
            return ""
        with _BOOTSTRAP_PATH.open("rb") as f:
            data = _tomllib.load(f)
        return str(data.get(key) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


async def read_app_setting(pool, key: str, default: str = "") -> str:
    """Fetch one app_settings value, decrypting if it's marked secret.

    Mirrors ``brain.oauth_client.read_app_setting`` so the two helpers
    stay behaviourally identical. Decryption matches
    ``services.plugins.secrets.get_secret``: pgcrypto's
    ``pgp_sym_decrypt`` against ``POINDEXTER_SECRET_KEY``.
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
        # Without the bootstrap secret key we can't decrypt — log and
        # return default. Caller will fall through to legacy Bearer or
        # raise loudly.
        logger.warning(
            "[SCRIPTS.OAUTH] POINDEXTER_SECRET_KEY unset — cannot decrypt %s",
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
        logger.warning("[SCRIPTS.OAUTH] decrypt %s failed: %s", key, exc)
        return default


# ---------------------------------------------------------------------------
# Cached-token bookkeeping
# ---------------------------------------------------------------------------


@dataclass
class _CachedToken:
    token: str
    refresh_at: float


# ---------------------------------------------------------------------------
# ScriptsOAuthClient — the public surface
# ---------------------------------------------------------------------------


class ScriptsOAuthClient:
    """Async OAuth client + downstream HTTP wrapper for ``scripts/``.

    Construct one per script run; reuse it for every downstream call.

    Args mirror ``services.auth.oauth_client.OAuthClient`` so the two
    helpers can be swapped at the call site if a script ever moves into
    the worker container (or vice versa).
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

    async def __aenter__(self) -> "ScriptsOAuthClient":
        self._ensure_http()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:  # noqa: BLE001
                # Closing on teardown is best-effort.
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
        self._cached = None

    async def get_token(self) -> str:
        if not self.using_oauth:
            raise RuntimeError(
                "ScriptsOAuthClient: client_id/client_secret are required. "
                "Run `poindexter auth migrate-scripts` to provision an "
                "OAuth client. Static-Bearer fallback was removed in #249."
            )

        cached = self._cached
        now = time.time()
        if cached is not None and now < cached.refresh_at:
            return cached.token

        # Cache miss / expired — mint. Lock so a thundering herd of
        # concurrent callers ends up doing one POST, not N.
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
            "[SCRIPTS.OAUTH] minted token client_id=%s scopes=%s expires_in=%s",
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
                "[SCRIPTS.OAUTH] 401 on %s %s — invalidating cache and retrying",
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

    async def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("DELETE", url, **kwargs)


# ---------------------------------------------------------------------------
# Convenience: read creds from bootstrap.toml + app_settings, build client
# ---------------------------------------------------------------------------


async def resolve_credentials(
    pool=None,
    *,
    explicit_client_id: str = "",
    explicit_client_secret: str = "",
    client_id_key: str = SCRIPTS_CLIENT_ID_KEY,
    client_secret_key: str = SCRIPTS_CLIENT_SECRET_KEY,
) -> tuple[str, str]:
    """Resolve (client_id, client_secret).

    Resolution order matches the docstring at the top of this module:
    explicit args > bootstrap.toml > app_settings (via pool, decrypts
    secrets).

    ``pool`` is optional — when ``None``, only bootstrap.toml is
    consulted (useful for scripts that genuinely run without DB
    access). Most callers will want to pass an asyncpg pool so the
    operator's app_settings credentials are honoured.
    """
    # Layer 1 — explicit args (test seam + CLI-flag passthrough)
    client_id = explicit_client_id
    client_secret = explicit_client_secret

    # Layer 2 — bootstrap.toml (operator-readable, no DB needed)
    if not client_id:
        client_id = _read_bootstrap_value(client_id_key)
    if not client_secret:
        client_secret = _read_bootstrap_value(client_secret_key)

    # Layer 3 — app_settings (decrypted)
    if pool is not None:
        if not client_id:
            client_id = await read_app_setting(pool, client_id_key, "")
        if not client_secret:
            client_secret = await read_app_setting(pool, client_secret_key, "")

    return client_id, client_secret


async def oauth_client_from_pool(
    pool,
    *,
    base_url: str,
    explicit_client_id: str = "",
    explicit_client_secret: str = "",
    client_id_key: str = SCRIPTS_CLIENT_ID_KEY,
    client_secret_key: str = SCRIPTS_CLIENT_SECRET_KEY,
    scopes: str | None = None,
    timeout: float = 30.0,
) -> ScriptsOAuthClient:
    """Build a ``ScriptsOAuthClient`` by resolving creds from disk + DB.

    The single most common construction site for scripts that already
    open an asyncpg pool to do other work. Mirrors
    ``brain.oauth_client.oauth_client_from_pool``.
    """
    client_id, client_secret = await resolve_credentials(
        pool,
        explicit_client_id=explicit_client_id,
        explicit_client_secret=explicit_client_secret,
        client_id_key=client_id_key,
        client_secret_key=client_secret_key,
    )
    return ScriptsOAuthClient(
        base_url=base_url,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
        timeout=timeout,
    )


async def oauth_client_from_bootstrap_only(
    *,
    base_url: str,
    explicit_client_id: str = "",
    explicit_client_secret: str = "",
    scopes: str | None = None,
    timeout: float = 30.0,
) -> ScriptsOAuthClient:
    """Build a ``ScriptsOAuthClient`` without touching the DB.

    For scripts that don't otherwise hold an asyncpg pool. Reads creds
    from bootstrap.toml only — operators using the OAuth path must run
    ``poindexter auth migrate-scripts`` first (which writes both
    app_settings AND bootstrap.toml).
    """
    client_id = explicit_client_id or _read_bootstrap_value(SCRIPTS_CLIENT_ID_KEY)
    client_secret = explicit_client_secret or _read_bootstrap_value(SCRIPTS_CLIENT_SECRET_KEY)
    return ScriptsOAuthClient(
        base_url=base_url,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
        timeout=timeout,
    )


__all__ = [
    "ScriptsOAuthClient",
    "SCRIPTS_CLIENT_ID_KEY",
    "SCRIPTS_CLIENT_SECRET_KEY",
    "SCRIPTS_DEFAULT_SCOPES",
    "EXPIRY_SKEW_SECONDS",
    "DEFAULT_TTL_SECONDS",
    "resolve_credentials",
    "oauth_client_from_pool",
    "oauth_client_from_bootstrap_only",
    "_decode_jwt_exp",
    "_read_bootstrap_value",
    "read_app_setting",
]
