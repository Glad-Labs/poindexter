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

Because each ``poindexter`` invocation is a fresh process, that in-memory
cache never survives — so the minted JWT is *also* persisted to
``~/.poindexter/cli_token_cache.json`` (see ``_token_cache``). When a
still-fresh token is on disk, ``__aenter__`` skips **both** the app_settings
credential read **and** the mint: credentials are resolved lazily, only when
a mint is actually required (cache miss or a 401). This cuts the per-command
host-port-proxy round-trips that intermittently wedge on Windows + Docker
Desktop (``WinError 64`` and friends). Disable with
``POINDEXTER_CLI_TOKEN_CACHE=0``.

If the OAuth credentials aren't configured, the client raises loudly —
run ``poindexter auth migrate-cli`` to register a new OAuth client and
persist the credentials. The legacy static-Bearer fallback (and the
``POINDEXTER_KEY`` / ``GLADLABS_KEY`` env vars) was removed in Phase 3
(#249).

## URL resolution (#198: no silent defaults)

    1. POINDEXTER_API_URL env var
    2. WORKER_API_URL env var (legacy)
    3. ``app_settings.api_base_url`` (DB-first config) — the stack seeds it
       as ``http://worker:8002``, a compose-network name only containers
       resolve, so a compose-internal host is rewritten to ``localhost``
       with the same published port. Without this step the README's
       ``poindexter tasks create`` died on a fresh install, because nothing
       in the quick start sets an env var.
    4. raises RuntimeError loudly — never a hardcoded localhost fallback
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from contextlib import suppress
from typing import Any

import httpx

# Default app_settings keys for the CLI's OAuth client. The migration
# helper (``poindexter auth migrate-cli``) writes here.
CLI_CLIENT_ID_KEY = "cli_oauth_client_id"
CLI_CLIENT_SECRET_KEY = "cli_oauth_client_secret"
CLI_DEFAULT_SCOPES = "api:read api:write"

# Credential-store read resilience. The CLI reads its OAuth client from
# app_settings on *every* invocation. On Windows + Docker Desktop the
# host->container port-proxy for Postgres intermittently resets the
# connection mid-handshake (``WinError 64``) or wedges entirely. These
# bound the read so a flaky proxy surfaces as a fast, truthful
# "database unreachable" error instead of an indefinite hang that the
# old broad ``except`` mislabelled as "missing credentials". They live
# in code (not app_settings) because this runs at CLI bootstrap, before
# the settings DB is reachable — the same exemption as the other
# bootstrap-direct paths (``setup`` / ``migrate`` / ``auth``).
_CRED_READ_TIMEOUT_S = 5.0
_CRED_READ_ATTEMPTS = 3
_CRED_READ_BACKOFF_S = 0.5


# The app_settings key the stack seeds with the worker's URL (baseline seed +
# the brain's free-tier seed both write ``http://worker:8002``).
API_BASE_URL_KEY = "api_base_url"

# Compose-network names the stack's URLs use. A container resolves them; the
# host CLI cannot, and reaches the same service on the port the stack publishes
# to the host. Same set ``poindexter setup --check`` rewrites.
_COMPOSE_INTERNAL_HOSTS = frozenset({"worker", "poindexter-worker", "host.docker.internal"})


def _resolve_base_url(base_url: str | None) -> str | None:
    """Explicit argument or env var. ``None`` means "read app_settings".

    The DB step is async, so ``WorkerClient.__aenter__`` finishes the
    resolution (``_base_url_from_settings``).
    """
    resolved = (
        base_url
        or os.getenv("POINDEXTER_API_URL")
        or os.getenv("WORKER_API_URL")
    )
    return resolved.rstrip("/") if resolved else None


def host_reachable_url(url: str) -> str:
    """``url`` as the host reaches it: a compose-internal host becomes localhost.

    ``http://worker:8002`` -> ``http://localhost:8002``. Any other URL comes
    back unchanged, so an operator who set ``api_base_url`` to a real host
    (a tailnet name, a LAN IP) gets exactly that.
    """
    from urllib.parse import urlparse, urlunparse

    parts = urlparse(url)
    if (parts.hostname or "").lower() not in _COMPOSE_INTERNAL_HOSTS:
        return url
    netloc = f"localhost:{parts.port}" if parts.port else "localhost"
    return urlunparse(parts._replace(netloc=netloc))


_NO_URL_HELP = (
    "Set POINDEXTER_API_URL (e.g. http://localhost:8002), or make sure "
    f"app_settings.{API_BASE_URL_KEY} is set — `poindexter setup` seeds it. "
    "There is no hardcoded default (#198)."
)


async def _base_url_from_settings() -> str:
    """``app_settings.api_base_url`` as the host reaches it.

    Raises RuntimeError naming both remedies when the value cannot be had:
    no DSN, an unreachable database, or an empty row. Read through
    ``plugins.secrets.get_secret`` (plaintext for a non-secret row) with one
    bounded connection, like ``_resolve_credentials`` below.
    """
    dsn = _dsn_or_none()
    if not dsn:
        raise RuntimeError(
            "No worker API URL configured: POINDEXTER_API_URL / WORKER_API_URL "
            "are unset and there is no database to read "
            f"app_settings.{API_BASE_URL_KEY} from (no bootstrap.toml "
            f"database_url). {_NO_URL_HELP}"
        )

    import asyncpg

    from poindexter.plugins.secrets import get_secret as _plugin_get_secret

    conn = None
    try:
        conn = await asyncpg.connect(dsn, timeout=_CRED_READ_TIMEOUT_S)
        value = (await _plugin_get_secret(conn, API_BASE_URL_KEY) or "").strip()
    except (OSError, asyncio.TimeoutError) as exc:
        raise CredentialStoreUnreachable(
            f"Could not reach Postgres at {_safe_dsn_hint(dsn)} to read "
            f"app_settings.{API_BASE_URL_KEY} (the worker API URL): "
            f"{type(exc).__name__}: {exc}. Check the stack is up "
            f"(`docker ps`), or {_NO_URL_HELP}"
        ) from exc
    finally:
        if conn is not None:
            with suppress(Exception):  # silent-ok: best-effort close; a raise here would mask the read result/error
                await conn.close()

    if not value:
        raise RuntimeError(
            f"No worker API URL configured: app_settings.{API_BASE_URL_KEY} is "
            f"empty in {_safe_dsn_hint(dsn)}. {_NO_URL_HELP}"
        )
    return host_reachable_url(value).rstrip("/")


async def wait_for_worker(
    timeout_s: float,
    *,
    base_url: str | None = None,
    poll_s: float = 3.0,
) -> str:
    """Block until the worker API answers anything, up to ``timeout_s``.

    ``bash scripts/start-stack.sh up -d`` returns as soon as the containers
    exist, but the worker spends about a minute in lifespan startup before it
    serves a request. A quick start pasted as one block reaches
    ``poindexter tasks create`` inside that window and used to die on an
    uncaught ``httpx.ConnectError``. This polls ``/api/health`` (a GET, so a
    retry can never double-submit anything) and returns the resolved base
    URL. Any HTTP status counts as "answering" — the caller's own request
    reports a real error properly. Announces the wait once on stderr; raises
    RuntimeError naming where to look when the deadline passes.
    """
    url = _resolve_base_url(base_url) or await _base_url_from_settings()
    deadline = time.monotonic() + max(0.0, timeout_s)
    announced = False
    async with httpx.AsyncClient(timeout=5.0) as http:
        while True:
            try:
                await http.get(f"{url}/api/health")
                return url
            except httpx.TransportError as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"The worker at {url} did not answer within "
                        f"{timeout_s:.0f}s ({type(exc).__name__}). Is the stack "
                        "up? Check `docker ps` and `docker logs poindexter-worker`."
                    ) from exc
                if not announced:
                    print(
                        f"Waiting for the worker at {url} to answer (it takes "
                        "about a minute to start after start-stack.sh)...",
                        file=sys.stderr,
                    )
                    announced = True
                await asyncio.sleep(poll_s)


class CredentialStoreUnreachable(RuntimeError):
    """The CLI could not reach the credential store (the app_settings DB)
    to read its OAuth client — distinct from the credentials being *absent*.

    A connection reset / timeout against the local Postgres (commonly a
    wedged Docker-Desktop host port-proxy on Windows, ``WinError 64``)
    lands here, NOT in the "run migrate-cli" path: re-provisioning a client
    can't fix a database you can't connect to. The caller surfaces this
    verbatim so the operator gets a connectivity remediation instead of a
    misleading credentials one.
    """


def _safe_dsn_hint(dsn: str) -> str:
    """``host:port/dbname`` for error messages — never the password."""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(dsn)
        netloc = parsed.hostname or "?"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return f"{netloc}{parsed.path}"
    except Exception:  # noqa: BLE001  # silent-ok: hint-only formatting; fall back to a generic label
        return "the configured Postgres host"


async def _resolve_credentials(
    base_url: str,  # noqa: ARG001 — kept for signature stability / callers
) -> tuple[str, str]:
    """Pull (client_id, client_secret) from app_settings.

    DSN resolution mirrors ``cli/auth.py`` and ``cli/migrate.py`` —
    bootstrap-toml first, then the env-var triplet. We open a single
    short-lived connection with an explicit ``timeout``, read two rows,
    and close it. A pool is overkill for two reads, and (critically)
    ``asyncpg.create_pool`` takes no connect timeout — so a wedged host
    port-proxy made the CLI hang *indefinitely*; a bounded ``connect``
    fails fast instead.

    Connection-level failures (reset / timeout) are retried a few times
    to ride over the sub-second ``WinError 64`` resets seen on Docker
    Desktop. A *persistent* failure raises :class:`CredentialStoreUnreachable`
    so the caller reports a DB-connectivity problem rather than a
    misleading "no credentials" pointer. An empty read (DB reachable,
    rows absent) returns ``("", "")`` — that genuinely means "run
    migrate-cli".
    """
    dsn = _dsn_or_none()
    if not dsn:
        # No DSN reachable — return empty creds so the caller raises the
        # migrate-cli pointer (env-var-only setups have no creds to read).
        return "", ""

    # Make sure the secrets key is loaded from bootstrap.toml so the
    # encrypted client_id/client_secret can decrypt — otherwise we'd
    # see empty creds and fail loudly without a useful pointer.
    from poindexter.cli._bootstrap import ensure_secret_key
    ensure_secret_key()

    import asyncpg

    # Local import — we already pull plugins.secrets in main app paths,
    # but the CLI runs in a thinner subset and importing it lazily keeps
    # cold-start fast.
    from poindexter.plugins.secrets import get_secret as _plugin_get_secret

    last_err: BaseException | None = None
    for attempt in range(_CRED_READ_ATTEMPTS):
        conn = None
        try:
            conn = await asyncpg.connect(dsn, timeout=_CRED_READ_TIMEOUT_S)
            client_id = await _plugin_get_secret(conn, CLI_CLIENT_ID_KEY) or ""
            client_secret = (
                await _plugin_get_secret(conn, CLI_CLIENT_SECRET_KEY) or ""
            )
            return client_id, client_secret
        except (OSError, asyncio.TimeoutError) as exc:
            # Connection-level failure (reset / timeout / wedged proxy).
            # ``TimeoutError`` is itself an ``OSError`` subclass; both are
            # listed for clarity. A ``PostgresError`` (DB reachable but
            # rejected the query) is deliberately NOT caught here — that's
            # a real error and should propagate raw, not look "unreachable".
            last_err = exc
            if attempt + 1 < _CRED_READ_ATTEMPTS:
                await asyncio.sleep(_CRED_READ_BACKOFF_S * (attempt + 1))
        finally:
            if conn is not None:
                with suppress(Exception):  # silent-ok: best-effort close; a raise here would mask the read result/error
                    await conn.close()

    raise CredentialStoreUnreachable(
        f"Could not reach the credential store (Postgres at "
        f"{_safe_dsn_hint(dsn)}) to read the CLI's OAuth client after "
        f"{_CRED_READ_ATTEMPTS} attempts: "
        f"{type(last_err).__name__ if last_err else 'unknown error'}: {last_err}. "
        f"This is a DATABASE CONNECTIVITY problem, not missing credentials — "
        f"do NOT re-run `poindexter auth migrate-cli`. Check that the Postgres "
        f"container is up (`docker ps`); on Docker Desktop / WSL2 the host "
        f"port-proxy can wedge under connection churn — retry in a moment, or "
        f"restart the Postgres container."
    )


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
        # silent-ok: the docstring states the contract — "Doesn't raise; the
        # caller is expected to be tolerant of missing DSN (env-var-only
        # setups still work)". Returning "" IS the env-var-only path.
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
        timeout: float = 30.0,
    ) -> None:
        # Explicit argument or env var. When neither is set this stays None
        # and __aenter__ reads app_settings.api_base_url (async, so it can't
        # happen here). Nothing reads base_url before __aenter__.
        self.base_url: str | None = _resolve_base_url(base_url)
        # Hold the explicit overrides; finalise during __aenter__ so the
        # async DB lookup happens off the main constructor path.
        self._explicit_client_id = client_id
        self._explicit_client_secret = client_secret
        self._scopes = scopes
        self._timeout = timeout
        self._oauth: Any | None = None
        self._http: httpx.AsyncClient | None = None
        # ``token`` legacy attribute — populated post-resolution so
        # callers that introspect it for logging keep working.
        self.token: str = ""

    async def __aenter__(self) -> WorkerClient:
        # Lazy import so the CLI doesn't hard-depend on the worker module
        # graph at parse time. (services.auth.oauth_client only imports
        # ``services.logger_config`` + httpx — both safe.)
        from poindexter.services.auth.oauth_client import OAuthClient

        if self.base_url is None:
            self.base_url = await _base_url_from_settings()
        base_url: str = self.base_url

        if (
            self._explicit_client_id is not None
            or self._explicit_client_secret is not None
        ):
            # Explicit creds (tests / embedding) win over app_settings and
            # deliberately bypass the disk cache — keep them deterministic and
            # preserve the fail-loud-on-enter contract for empty creds.
            client_id = self._explicit_client_id or ""
            client_secret = self._explicit_client_secret or ""
            if not (client_id and client_secret):
                raise RuntimeError(
                    "No CLI OAuth credentials configured. Run `poindexter auth "
                    "migrate-cli` to register an OAuth client. The legacy "
                    "static-Bearer fallback (POINDEXTER_KEY / GLADLABS_KEY env "
                    "vars, app_settings.api_token) was removed in #249."
                )
            self._oauth = OAuthClient(
                base_url=base_url,
                client_id=client_id,
                client_secret=client_secret,
                scopes=self._scopes,
                timeout=self._timeout,
            )
        else:
            # Normal CLI path. A cross-process disk cache holds the last minted
            # JWT; when it's still fresh, ``get_token()`` returns it without
            # reading the app_settings DB (the WinError-64-prone host->5433 hop)
            # or minting. Credentials are resolved *lazily* — only when a mint
            # actually becomes necessary (cache miss or a 401) — via a provider
            # wrapping ``_resolve_credentials``. That resolver still raises
            # ``CredentialStoreUnreachable`` on a wedged proxy and returns
            # ("", "") only on a genuinely empty read, so the DB-connectivity
            # and missing-credentials errors stay distinct (never conflated).
            from poindexter.cli._token_cache import CliTokenStore

            async def _provider() -> tuple[str, str]:
                return await _resolve_credentials(base_url)

            self._oauth = OAuthClient(
                base_url=base_url,
                credential_provider=_provider,
                token_store=CliTokenStore(base_url),
                scopes=self._scopes,
                timeout=self._timeout,
            )

        # Resolve a usable token now (cached, or minted on a miss). This is the
        # point a genuinely unprovisioned install fails loud, and where a
        # wedged proxy surfaces as ``CredentialStoreUnreachable``.
        # Backwards-compat introspection: callers that read ``client.token``
        # continue to see a string.
        self.token = await self._oauth.get_token()

        # Mirror the pre-migration httpx client setup so subcommand
        # code that drops to ``client._client.<method>`` keeps working
        # — that was technically a private attribute, but it's relied
        # on by tests today.
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # Close inner clients best-effort. Suppress because this runs
        # in finally-style teardown and a raise here would mask the
        # caller's exception.
        if self._http is not None:
            with suppress(Exception):  # silent-ok: teardown close; raise would mask the caller's exception
                await self._http.aclose()
            self._http = None
        if self._oauth is not None:
            with suppress(Exception):  # silent-ok: teardown close; raise would mask the caller's exception
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
    "CredentialStoreUnreachable",
    "CLI_CLIENT_ID_KEY",
    "CLI_CLIENT_SECRET_KEY",
    "CLI_DEFAULT_SCOPES",
]
