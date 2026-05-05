"""Shared worker-API HTTP client for poindexter CLI subcommands.

Every subcommand group that hits the local FastAPI worker (tasks,
posts, costs, quality, settings) imports ``WorkerClient`` from this
module so they all share authentication, URL resolution, and error
handling logic.

## Authentication (Glad-Labs/poindexter#242, finalised in #249)

OAuth 2.1 Client Credentials only. When
``app_settings.cli_oauth_client_id`` + ``cli_oauth_client_secret``
are present, the CLI mints a JWT via ``POST /token`` and caches it
in-memory until ~30 s before expiry. 401 from a downstream call
invalidates the cache and retries once with a fresh token.

If the OAuth credentials aren't configured, the client raises loudly —
run ``poindexter auth migrate-cli`` to register a new OAuth client and
persist the credentials. The legacy static-Bearer fallback (and the
``POINDEXTER_KEY`` / ``GLADLABS_KEY`` env vars) was removed in Phase 3
(#249).

## URL resolution (#198: no silent defaults)

Same as before:
    1. POINDEXTER_API_URL env var
    2. WORKER_API_URL env var (legacy)
    3. raises RuntimeError loudly — no localhost fallback
"""

from __future__ import annotations

import os
from contextlib import suppress
from typing import Any

import httpx


# Default app_settings keys for the CLI's OAuth client. The migration
# helper (``poindexter auth migrate-cli``) writes here.
CLI_CLIENT_ID_KEY = "cli_oauth_client_id"
CLI_CLIENT_SECRET_KEY = "cli_oauth_client_secret"
CLI_DEFAULT_SCOPES = "api:read api:write"


def _resolve_base_url(base_url: str | None) -> str:
    resolved = (
        base_url
        or os.getenv("POINDEXTER_API_URL")
        or os.getenv("WORKER_API_URL")
    )
    if not resolved:
        raise RuntimeError(
            "No worker API URL configured. Set POINDEXTER_API_URL (preferred) "
            "or WORKER_API_URL in the environment. For local dev this is "
            "typically http://localhost:8002, but there is no hardcoded "
            "default — you must configure it explicitly (#198)."
        )
    return resolved.rstrip("/")


async def _resolve_credentials(
    base_url: str,
) -> tuple[str, str]:
    """Pull (client_id, client_secret) from app_settings.

    DSN resolution mirrors ``cli/auth.py`` and ``cli/migrate.py`` —
    bootstrap-toml first, then the env-var triplet. We open a tiny
    pool, read two rows, close the pool. This adds one DB round
    trip per CLI invocation; the alternative is a long-lived pool
    that races CLI shutdown.
    """
    dsn = _dsn_or_none()
    if not dsn:
        # No DSN reachable — return empty creds so the OAuthClient
        # raises loudly with the migrate-cli pointer.
        return "", ""

    # Make sure the secrets key is loaded from bootstrap.toml so the
    # encrypted client_id/client_secret can decrypt — otherwise we'd
    # see empty creds and fail loudly without a useful pointer.
    from poindexter.cli._bootstrap import ensure_secret_key
    ensure_secret_key()

    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        # Local import — we already pull plugins.secrets in main app
        # paths, but the CLI runs in a thinner subset and importing
        # it lazily keeps cold-start fast.
        from plugins.secrets import get_secret as _plugin_get_secret

        async with pool.acquire() as conn:
            client_id = await _plugin_get_secret(conn, CLI_CLIENT_ID_KEY) or ""
            client_secret = (
                await _plugin_get_secret(conn, CLI_CLIENT_SECRET_KEY) or ""
            )
    finally:
        await pool.close()

    return client_id, client_secret


def _dsn_or_none() -> str:
    """Best-effort DSN resolution. Returns "" if nothing is configured.

    Same resolution order as ``cli/auth.py`` — uses the shared
    ``poindexter.cli._bootstrap.resolve_dsn`` helper which prefers
    bootstrap.toml over env vars (the resolver previously imported
    ``brain.bootstrap`` which isn't on sys.path for installed CLI
    invocations, silently failing and reverting to env-var-only).

    Doesn't raise; the caller is expected to be tolerant of missing
    DSN (env-var-only setups still work).
    """
    try:
        from poindexter.cli._bootstrap import resolve_dsn
        return resolve_dsn()
    except Exception:  # noqa: BLE001
        return ""


class WorkerClient:
    """Minimal async httpx wrapper around the Poindexter worker API.

    Holds an inner ``OAuthClient`` (or, in the legacy fallback, just a
    static bearer token) for authentication. The wire-level surface
    (``get`` / ``post`` / ``put`` / ``json_or_raise``) is unchanged
    from the pre-#242 implementation, so subcommand modules don't need
    edits to migrate.
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,  # noqa: ARG002 — accepted for back-compat, unused post-#249
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        scopes: str | None = None,
    ) -> None:
        self.base_url = _resolve_base_url(base_url)
        # Hold the explicit overrides; finalise during __aenter__ so the
        # async DB lookup happens off the main constructor path.
        self._explicit_client_id = client_id
        self._explicit_client_secret = client_secret
        self._scopes = scopes
        self._oauth: Any | None = None
        self._http: httpx.AsyncClient | None = None
        # ``token`` legacy attribute — populated post-resolution so
        # callers that introspect it for logging keep working.
        self.token: str = ""

    async def __aenter__(self) -> "WorkerClient":
        # Resolve credentials. Explicit args (for tests) win over
        # app_settings.
        if self._explicit_client_id is not None or self._explicit_client_secret is not None:
            client_id = self._explicit_client_id or ""
            client_secret = self._explicit_client_secret or ""
        else:
            try:
                client_id, client_secret = await _resolve_credentials(self.base_url)
            except Exception:  # noqa: BLE001
                # DB-unreachable still gives the OAuthClient a chance
                # to raise its own canonical "run migrate-cli" error.
                client_id, client_secret = "", ""

        if not (client_id and client_secret):
            raise RuntimeError(
                "No CLI OAuth credentials configured. Run `poindexter auth "
                "migrate-cli` to register an OAuth client. The legacy "
                "static-Bearer fallback (POINDEXTER_KEY / GLADLABS_KEY env "
                "vars, app_settings.api_token) was removed in #249."
            )

        # Lazy import so the CLI doesn't hard-depend on the worker
        # module graph at parse time. (services.auth.oauth_client only
        # imports ``services.logger_config`` + httpx — both safe.)
        from services.auth.oauth_client import OAuthClient

        self._oauth = OAuthClient(
            base_url=self.base_url,
            client_id=client_id,
            client_secret=client_secret,
            scopes=self._scopes,
        )
        # Backwards-compat introspection: callers that read
        # ``client.token`` (none in the codebase, but third-party
        # scripts might) continue to see a string.
        self.token = await self._oauth.get_token()

        # Mirror the pre-migration httpx client setup so subcommand
        # code that drops to ``client._client.<method>`` keeps working
        # — that was technically a private attribute, but it's relied
        # on by tests today.
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=30.0,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # Close inner clients best-effort. Suppress because this runs
        # in finally-style teardown and a raise here would mask the
        # caller's exception.
        if self._http is not None:
            with suppress(Exception):
                await self._http.aclose()
            self._http = None
        if self._oauth is not None:
            with suppress(Exception):
                await self._oauth.aclose()
            self._oauth = None

    # ------------------------------------------------------------------
    # Public HTTP surface (unchanged signature from pre-migration)
    # ------------------------------------------------------------------

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._authed_request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._authed_request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._authed_request("PUT", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._authed_request("DELETE", path, **kwargs)

    async def _authed_request(
        self, method: str, path: str, **kwargs: Any,
    ) -> httpx.Response:
        """Run a request through the OAuthClient (handles 401-retry)."""
        assert self._oauth is not None, "WorkerClient must be used as async context manager"
        # Default content-type for JSON bodies, matching the previous
        # WorkerClient behaviour. httpx sets it automatically when you
        # pass json=, but explicit POST/PUT calls that pass data= miss
        # it without a header here.
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("Content-Type", "application/json")
        return await self._oauth.request(method, path, headers=headers, **kwargs)

    async def json_or_raise(self, resp: httpx.Response) -> Any:
        """Return parsed JSON on 2xx, otherwise raise a click-friendly error."""
        if 200 <= resp.status_code < 300:
            try:
                return resp.json()
            except ValueError:
                return {"raw": resp.text}
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        raise RuntimeError(
            f"HTTP {resp.status_code} from {resp.request.method} {resp.request.url}: {body}"
        )


# ---------------------------------------------------------------------------
# Re-exports for convenience
# ---------------------------------------------------------------------------

__all__ = [
    "WorkerClient",
    "CLI_CLIENT_ID_KEY",
    "CLI_CLIENT_SECRET_KEY",
    "CLI_DEFAULT_SCOPES",
]
