"""http_server.py — Remote (HTTP) entry point for the Poindexter MCP server.

The legacy entry point (``server.py`` invoked directly) speaks **stdio**,
which only works for clients that spawn the MCP server as a subprocess
(Claude Code CLI, Claude Desktop). Mobile and web Claude apps can only
talk to a **remote MCP server** registered as a Custom Connector at
https://claude.ai/settings/connectors — that requires an HTTPS endpoint.

This entry point exposes the same FastMCP instance + tool set over the
``streamable-http`` transport, wrapped in a tiny FastAPI app that
enforces **OAuth 2.1 Bearer-JWT** auth — closes Glad-Labs/poindexter#240
as Phase 1 of the umbrella OAuth migration (#241).

## Auth

Every HTTP request must carry ``Authorization: Bearer <jwt>`` where
``<jwt>`` was minted by the worker at ``POST /oauth/token``. Mint one
via the operator CLI:

    poindexter auth register-client \\
        --name custom-connector --scopes "mcp:read mcp:write"
    # captures client_id + client_secret (shown ONCE)

    curl -s -X POST http://localhost:8002/oauth/token \\
        -d grant_type=client_credentials \\
        -d client_id=<id> -d client_secret=<secret> \\
      | jq -r .access_token

The static-Bearer path that earlier revisions of this file accepted is
GONE — there's no longer any way to call this server with the legacy
``app_settings.api_token``. The worker's other endpoints align with
the same posture: Phase 3 (#249) closed the dual-auth window across
the entire surface — every consumer must mint OAuth JWTs.

## Run

    uv run http_server.py

Env vars:

- ``POINDEXTER_MCP_HTTP_HOST``  (default ``127.0.0.1``)
- ``POINDEXTER_MCP_HTTP_PORT``  (default ``8004``)
- ``POINDEXTER_API_URL``        (default ``http://localhost:8002``)
- ``POINDEXTER_SECRET_KEY``     (sourced from bootstrap.toml; required —
                                 the JWT signing key shared with the issuer)
- ``OLLAMA_URL``                (default ``http://localhost:11434``)
- ``POINDEXTER_MCP_HTTP_TOOL_ALLOWLIST``  (optional, comma-separated tool
                                 names — see ``DEFAULT_VOICE_MOBILE_ALLOWLIST``
                                 below for the recommended read-only set
                                 used for phone/voice connectors. Unset
                                 means "expose all registered tools",
                                 matching pre-#239 behaviour. Only the HTTP
                                 transport honours this; stdio is unaffected.)

## Reaching it from a phone

Front the worker with Tailscale Funnel (or any reverse proxy giving
you a stable public hostname) — e.g.
``https://<your-funnel-host>.ts.net`` → ``localhost:8002``. Add a
sub-path rule that routes ``/mcp`` to this server's port:

    tailscale serve --bg --https=443 --set-path=/mcp \\
        proxy http://127.0.0.1:8004

Then register at https://claude.ai/settings/connectors with:

- URL:                ``https://<your-funnel-host>.ts.net/mcp``
- Auth:               OAuth 2.1
- Authorization URL:  (none — Client Credentials Grant)
- Token URL:          ``https://<your-funnel-host>.ts.net/oauth/token``
- Client ID:          ``pdx_…`` from ``poindexter auth register-client``
- Client Secret:      shown by the same command (capture it once)

The Custom Connector UI handles the token exchange, refresh, and
expiry transparently.

Tracking issues: Glad-Labs/poindexter#237 (parent), #240 (this file),
#241 (OAuth umbrella).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, MutableMapping

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("poindexter-mcp-http")


# ---------------------------------------------------------------------------
# Tool allowlist (#239)
# ---------------------------------------------------------------------------

#: Recommended read-only set for "phone voice mode" / mobile Custom
#: Connector use. Set ``POINDEXTER_MCP_HTTP_TOOL_ALLOWLIST`` to this
#: comma-joined list to drop every write-capable tool from the HTTP
#: surface — covers "what's the system state", "what did I decide
#: about X", "what's in the queue" without risking a misheard query
#: publishing or rejecting content. See Glad-Labs/poindexter#239.
#:
#: Adjust per-deployment by exporting your own comma-separated list;
#: this constant is documentation, not the default at runtime — an
#: unset env var means "expose ALL registered tools" so behaviour
#: stays backwards compatible with pre-#239 deployments.
DEFAULT_VOICE_MOBILE_ALLOWLIST: tuple[str, ...] = (
    "search_memory",
    "recall_decision",
    "find_similar_posts",
    "list_tasks",
    "get_post_count",
    "get_setting",
    "list_settings",
    "get_audit_log",
    "get_audit_summary",
    "get_brain_knowledge",
    "check_health",
    "get_budget",
    "memory_stats",
)


def _parse_tool_allowlist(raw: str | None) -> frozenset[str] | None:
    """Parse the comma-separated allowlist env var.

    Returns:
        ``None`` when ``raw`` is unset (env var absent) — caller
        should NOT filter the registry, matching pre-#239 behaviour.
        A ``frozenset`` of names otherwise — including the empty
        set, which is the explicit "no tools exposed" choice for
        operators who want to hard-stop access without unmounting
        the route. Whitespace is stripped from each entry; empty
        entries (e.g. trailing commas) are dropped.
    """
    if raw is None:
        return None
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


def _apply_tool_allowlist(mcp_instance, allowlist: frozenset[str]) -> list[str]:
    """Trim the FastMCP tool manager's registry down to ``allowlist``.

    Mutates ``mcp_instance._tool_manager`` in place via the public
    ``remove_tool`` method, so subsequent ``list_tools`` / ``call_tool``
    calls (whether reached via HTTP or any other transport sharing the
    same instance) see only the allowed names. Run this BEFORE
    ``streamable_http_app()`` to keep the HTTP ``tools/list`` response
    in sync with the filter.

    Names in the allowlist that aren't actually registered are silently
    ignored — easier to maintain a static "voice mode" list across
    versions than to keep it perfectly aligned with the current tool
    set.

    Returns the names that were REMOVED — useful for logging.
    """
    tm = mcp_instance._tool_manager
    registered = {t.name for t in tm.list_tools()}
    to_remove = sorted(registered - allowlist)
    for name in to_remove:
        tm.remove_tool(name)
    return to_remove


# ---------------------------------------------------------------------------
# Bootstrap helpers
# ---------------------------------------------------------------------------


def _ensure_brain_on_path() -> None:
    """Walk up parents until ``brain/bootstrap.py`` is reachable."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "brain" / "bootstrap.py").is_file():
            p = str(parent)
            if p not in sys.path:
                sys.path.insert(0, p)
            return
    raise RuntimeError("Could not locate brain/bootstrap.py from this file's parents")


def _seed_env_from_bootstrap() -> None:
    """Populate the env vars ``server.setup_runtime`` expects, sourcing
    from ``~/.poindexter/bootstrap.toml`` where appropriate.

    Pulls one secret:

    - ``poindexter_secret_key`` → ``POINDEXTER_SECRET_KEY`` — the JWT
      signing key. Required for *inbound* auth on this server; without
      it we can't verify any token.

    Outbound worker calls authenticate via OAuth JWTs minted from
    ``app_settings.mcp_oauth_client_*`` — no env var is required.
    Phase 3 (#249) removed the legacy static-Bearer path.
    """
    _ensure_brain_on_path()
    from brain.bootstrap import get_bootstrap_value  # type: ignore[import-not-found]

    secret_key = (
        os.environ.get("POINDEXTER_SECRET_KEY")
        or get_bootstrap_value("poindexter_secret_key", "")
    )
    if not secret_key:
        raise RuntimeError(
            "POINDEXTER_SECRET_KEY not found — required for OAuth JWT "
            "verification (#241). Set the env var or ensure "
            "~/.poindexter/bootstrap.toml has poindexter_secret_key = '...'.",
        )

    os.environ.setdefault("POINDEXTER_API_URL", "http://localhost:8002")
    os.environ["POINDEXTER_SECRET_KEY"] = secret_key
    os.environ.setdefault("OLLAMA_URL", "http://localhost:11434")


# ---------------------------------------------------------------------------
# ASGI auth wrapper
# ---------------------------------------------------------------------------


def _ensure_poindexter_on_path() -> None:
    """Add ``src/cofounder_agent/`` to sys.path so we can import the
    OAuth issuer module that ships in the worker tree.

    Mirrors the trick in ``server._ensure_poindexter_on_path`` — the
    poindexter package isn't installed into this server's venv (we run
    via ``uv --directory mcp-server run http_server.py``), so we rely
    on the side-by-side checkout layout.
    """
    here = Path(__file__).resolve().parent
    candidate = (here / ".." / "src" / "cofounder_agent").resolve()
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)


def _oauth_jwt_wrapper(
    inner_app: Callable[
        [MutableMapping[str, Any], Callable, Callable], Awaitable[None],
    ],
) -> Callable[[MutableMapping[str, Any], Callable, Callable], Awaitable[None]]:
    """Wrap an ASGI app so every HTTP request requires a valid OAuth
    JWT in ``Authorization: Bearer <jwt>``.

    Verification is delegated to :mod:`services.auth.oauth_issuer` so
    the wire format / signing key / claim shape stay defined in one
    place. RFC 6750 §3.1 error responses (``WWW-Authenticate`` with the
    ``error`` attribute) so a Custom Connector can distinguish "token
    expired, refresh me" from "I never had a valid token".
    """
    _ensure_poindexter_on_path()
    from services.auth.oauth_issuer import (  # type: ignore[import-not-found]
        InvalidToken,
        verify_token,
    )

    def _resource_metadata_url(scope: MutableMapping[str, Any]) -> str:
        """Build the public ``/.well-known/oauth-protected-resource`` URL.

        Reads the Host header (Tailscale Funnel / reverse proxy sets
        this to the public hostname clients connect to) and the
        forwarded scheme so the URL is what the *client* should fetch, not what
        we'd see internally on 127.0.0.1:8004.
        """
        host = ""
        scheme = "https"  # behind Funnel, always TLS-fronted
        for name, value in scope.get("headers", ()):
            lname = name.lower()
            if lname == b"host" and not host:
                host = value.decode("latin-1", errors="replace")
            elif lname == b"x-forwarded-host":
                host = value.decode("latin-1", errors="replace")
            elif lname == b"x-forwarded-proto":
                scheme = value.decode("latin-1", errors="replace")
        if not host:
            host = "127.0.0.1:8004"
            scheme = "http"
        return f"{scheme}://{host}/.well-known/oauth-protected-resource"

    async def asgi_app(
        scope: MutableMapping[str, Any],
        receive: Callable,
        send: Callable,
    ) -> None:
        if scope["type"] != "http":
            await inner_app(scope, receive, send)
            return

        async def _reject(status: int, error: str, description: str) -> None:
            # MCP 2025-03-26 §authorization REQUIRES resource_metadata=
            # so the client can discover the authorization server. Without
            # it Anthropic's Custom Connector aborts before the OAuth dance.
            metadata_url = _resource_metadata_url(scope)
            challenge = (
                f'Bearer realm="poindexter-mcp", '
                f'error="{error}", error_description="{description}", '
                f'resource_metadata="{metadata_url}"'
            ).encode("ascii")
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", challenge),
                    ],
                },
            )
            body = (
                b'{"error":"' + error.encode("ascii")
                + b'","error_description":"' + description.encode("ascii") + b'"}'
            )
            await send({"type": "http.response.body", "body": body})

        provided = ""
        for name, value in scope.get("headers", ()):
            if name == b"authorization":
                provided = value.decode("latin-1", errors="replace")
                break

        if not provided.lower().startswith("bearer "):
            await _reject(401, "invalid_request", "missing bearer token")
            return

        token = provided[len("Bearer ") :].strip()
        try:
            claims = verify_token(token)
        except InvalidToken as e:
            await _reject(401, "invalid_token", str(e))
            return

        # Stash the verified claims on the scope so downstream handlers
        # (or logging middleware) can attribute the request. Doesn't
        # affect FastMCP's behaviour — it ignores keys it doesn't know.
        scope["oauth_client_id"] = claims.client_id
        scope["oauth_scopes"] = sorted(claims.scopes)

        await inner_app(scope, receive, send)

    return asgi_app


# ---------------------------------------------------------------------------
# App + entry point
# ---------------------------------------------------------------------------


def build_app():
    """Construct the FastAPI app: health route + OAuth-JWT-wrapped MCP mount.

    The MCP session manager owns a long-lived task group that handles
    streaming responses. FastMCP wires that into its inner Starlette app
    via ``lifespan=lambda app: self.session_manager.run()`` (see
    ``mcp/server/fastmcp/server.py`` ``streamable_http_app``). When we
    mount the inner Starlette app as a sub-app, FastAPI does NOT
    auto-invoke nested lifespans — so without the explicit hook below
    every MCP request fails with ``RuntimeError: Task group is not
    initialized``.
    """
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    import server as poindexter_mcp  # type: ignore[import-not-found]

    # FastMCP defaults its internal route to ``/mcp``. We're already
    # FastAPI-mounting at ``/mcp`` — if we leave the inner path at
    # ``/mcp`` too, the Custom Connector URL becomes ``/mcp/mcp`` which
    # is confusing. Re-anchoring the inner path to ``/`` gives a clean
    # external URL of ``/mcp/`` (single segment).
    poindexter_mcp.mcp.settings.streamable_http_path = "/"

    # FastMCP auto-enables DNS-rebinding protection when bound to
    # localhost, with an empty allowed_hosts list. Behind a reverse
    # proxy / Tailscale Funnel the Host header is the public hostname
    # (e.g. ``<your-funnel-host>.ts.net``) which the empty allowlist
    # rejects with HTTP 421 "Invalid Host header". The OAuth-JWT
    # wrapper above already gates access; turning off the (redundant)
    # rebinding check lets the proxy work without per-deployment
    # hostname configuration. Operators who need both can set
    # ``POINDEXTER_MCP_ALLOWED_HOSTS`` (comma-separated) instead.
    from mcp.server.transport_security import TransportSecuritySettings
    allowed = [
        h.strip()
        for h in os.environ.get("POINDEXTER_MCP_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    ]
    poindexter_mcp.mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(allowed),
        allowed_hosts=allowed,
    )

    # Tool allowlist (#239) — drop write-capable tools from the HTTP
    # surface BEFORE building the streamable_http app so that
    # ``tools/list`` and ``tools/call`` both reflect the trimmed set.
    # Stdio (``server.py`` direct entry point) doesn't go through
    # build_app, so it stays unaffected and exposes the full registry.
    allowlist = _parse_tool_allowlist(
        os.environ.get("POINDEXTER_MCP_HTTP_TOOL_ALLOWLIST"),
    )
    if allowlist is not None:
        removed = _apply_tool_allowlist(poindexter_mcp.mcp, allowlist)
        logger.info(
            "Tool allowlist active: %d tool(s) exposed, %d removed (%s)",
            len({t.name for t in poindexter_mcp.mcp._tool_manager.list_tools()}),
            len(removed),
            ", ".join(removed) if removed else "none",
        )

    inner = poindexter_mcp.mcp.streamable_http_app()  # also creates session_manager

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Drive the FastMCP session_manager's task group for the lifetime
        # of the worker. Without this, handle_request() raises because
        # the task group never enters its async context.
        async with poindexter_mcp.mcp.session_manager.run():
            yield

    app = FastAPI(
        title="Poindexter MCP (HTTP)",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/healthz")
    async def _healthz() -> dict[str, Any]:
        tools = []
        try:
            tm = getattr(poindexter_mcp.mcp, "_tool_manager", None)
            if tm is not None:
                tools = list(getattr(tm, "_tools", {}).keys())
        except Exception:  # noqa: BLE001
            pass
        return {
            "status": "ok",
            "service": "poindexter-mcp-http",
            "transport": "streamable-http",
            "tool_count": len(tools),
        }

    wrapped = _oauth_jwt_wrapper(inner)
    app.mount("/mcp", wrapped)
    return app


def main() -> None:
    _seed_env_from_bootstrap()

    # mcp-server/server.py is in the same dir as this file
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))

    import server as poindexter_mcp  # type: ignore[import-not-found]

    poindexter_mcp.setup_runtime()

    import uvicorn

    host = os.environ.get("POINDEXTER_MCP_HTTP_HOST", "127.0.0.1")
    port = int(os.environ.get("POINDEXTER_MCP_HTTP_PORT", "8004"))
    logger.info(
        "Poindexter MCP HTTP transport starting on %s:%d (OAuth JWT required)",
        host, port,
    )
    app = build_app()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
