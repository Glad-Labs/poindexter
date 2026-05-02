"""OAuth 2.1 Authorization Server routes (Phase 1, #241).

Wires the MCP SDK's ``mcp.server.auth.routes.create_auth_routes`` and
``create_protected_resource_routes`` onto the FastAPI worker. The SDK
provides:

- ``GET /.well-known/oauth-authorization-server`` (RFC 8414 metadata)
- ``GET /.well-known/oauth-protected-resource``  (RFC 9728 metadata)
- ``GET /authorize``                              (Auth Code Grant + PKCE)
- ``POST /token``                                 (token exchange — auth_code,
                                                   refresh_token, plus a
                                                   client_credentials wrapper
                                                   we layer on top)
- ``POST /register``                              (RFC 7591 DCR)
- ``POST /revoke``                                (token revocation)

Plus a few Poindexter-specific extras:

- ``GET /.well-known/openid-configuration`` aliased to RFC 8414 metadata
  (some clients try OIDC discovery first; redirecting them to the same
  document is cheaper than 404→fallback)
- ``POST /token`` falls through to a small ``client_credentials`` handler
  before the SDK gets the request — keeps the headless CLI / scripts
  flow working alongside the browser-driven Custom Connector flow

The provider implementation lives in
:mod:`services.auth.oauth_provider` — see that module for storage and
JWT plumbing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse

from mcp.server.auth.provider import TokenError
from mcp.server.auth.routes import (
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import (
    ClientRegistrationOptions,
    RevocationOptions,
)

from services.auth.oauth_issuer import (
    ALLOWED_SCOPES,
    DEFAULT_TTL_SECONDS,
    issue_token,
)
from services.auth.oauth_provider import PoindexterOAuthProvider
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency, get_site_config_dependency

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Route module discovery — exported as a single APIRouter named ``router``
# so route_registration.py can mount it like every other route module. The
# SDK gives us Starlette ``Route`` objects; we adapt each to a FastAPI
# endpoint via ``add_api_route``.
# ---------------------------------------------------------------------------


def _issuer_url(request: Request, site_config: Any) -> str:
    """Best-effort canonical issuer URL.

    Prefers ``app_settings.oauth_issuer_url`` so the operator controls
    what Custom Connectors see; falls back to reconstructing from the
    incoming request — handy for local dev but not what we want from
    behind Tailscale Funnel.
    """
    explicit = site_config.get("oauth_issuer_url", "") if site_config else ""
    if explicit:
        return explicit.rstrip("/")
    return f"{request.url.scheme}://{request.url.netloc}".rstrip("/")


def _resource_url(issuer: str) -> str:
    """The MCP HTTP server's external URL — what gets advertised as the
    protected resource. Same hostname as the issuer, with ``/mcp/``."""
    return f"{issuer}/mcp/"


metadata_router = APIRouter(tags=["OAuth"])
authorization_router = APIRouter(tags=["OAuth"])


# ---------------------------------------------------------------------------
# Build SDK routes lazily, per-request
# ---------------------------------------------------------------------------
#
# create_auth_routes() needs the issuer URL at construction time to bake it
# into the metadata document. Building it once at startup would freeze the
# value, but the operator might rotate ``oauth_issuer_url`` after the
# worker is up. So we build the SDK route on each request and dispatch.
# Cheap — it's a few Pydantic ctor calls.
# ---------------------------------------------------------------------------


def _build_sdk_routes(issuer_url: str, db_service: DatabaseService):
    pool = getattr(db_service, "pool", None)
    if pool is None:
        raise RuntimeError("database pool unavailable")
    provider = PoindexterOAuthProvider(pool)
    auth_routes = create_auth_routes(
        provider=provider,
        issuer_url=AnyHttpUrl(issuer_url),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=sorted(ALLOWED_SCOPES),
            default_scopes=["mcp:read", "mcp:write"],
        ),
        revocation_options=RevocationOptions(enabled=True),
    )
    resource_routes = create_protected_resource_routes(
        resource_url=AnyHttpUrl(_resource_url(issuer_url)),
        authorization_servers=[AnyHttpUrl(issuer_url)],
        scopes_supported=sorted(ALLOWED_SCOPES),
        resource_name="Poindexter MCP",
    )
    return provider, auth_routes + resource_routes


def _find_route(routes, path: str):
    for r in routes:
        if getattr(r, "path", None) == path:
            return r
    return None


async def _dispatch_sdk(
    request: Request,
    path: str,
    db_service: DatabaseService,
    site_config: Any,
    *,
    body_override: bytes | None = None,
):
    """Adapter — find the SDK route by path and run its endpoint with
    the FastAPI Request. The SDK's CORS-wrapped handlers expect an
    ASGI app interface; FastAPI gave us a Request, so we pass through
    via a buffered scope/receive/send.

    Body buffering is mandatory: FastAPI may have already consumed the
    request body (e.g. our /token wrapper reads the form once before
    deciding whether to delegate to the SDK). The original ``receive``
    channel can only be drained once, so we read the body up-front and
    replay it as a single ``http.request`` message.
    """
    issuer = _issuer_url(request, site_config)
    _, routes = _build_sdk_routes(issuer, db_service)
    target = _find_route(routes, path)
    if target is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    # If the caller already drained the body (e.g. /token reads it
    # before deciding whether to delegate), use that buffer; otherwise
    # read it now from the underlying stream.
    body_bytes = body_override if body_override is not None else await request.body()
    body_sent = False

    async def _replay_receive():
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": body_bytes, "more_body": False}
        # The SDK shouldn't ask twice, but if it does we keep returning
        # an empty disconnect so it doesn't hang.
        return {"type": "http.disconnect"}

    response_holder: dict[str, Any] = {}

    async def _send(message):
        if message["type"] == "http.response.start":
            response_holder["status"] = message["status"]
            response_holder["headers"] = message.get("headers", [])
        elif message["type"] == "http.response.body":
            response_holder.setdefault("body", b"")
            response_holder["body"] += message.get("body", b"")

    await target.app(request.scope, _replay_receive, _send)
    from starlette.responses import Response
    return Response(
        content=response_holder.get("body", b""),
        status_code=response_holder.get("status", 500),
        headers={k.decode(): v.decode() for k, v in response_holder.get("headers", [])},
    )


# ---------------------------------------------------------------------------
# RFC 8414 + RFC 9728 metadata
# ---------------------------------------------------------------------------


@metadata_router.get("/.well-known/oauth-authorization-server")
async def oauth_authorization_server(
    request: Request,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
):
    return await _dispatch_sdk(
        request, "/.well-known/oauth-authorization-server", db_service, site_config,
    )


@metadata_router.get("/.well-known/openid-configuration")
async def oidc_alias(
    request: Request,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
):
    """OIDC discovery alias — returns the same document as RFC 8414.

    Some clients (notably the Anthropic Custom Connector) probe OIDC
    first before falling back to RFC 8414. Serving the same body at
    both URLs avoids the round-trip and the noisy 404 in our logs.
    """
    return await _dispatch_sdk(
        request, "/.well-known/oauth-authorization-server", db_service, site_config,
    )


@metadata_router.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource_root(
    request: Request,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
):
    """Host-level resource metadata.

    RFC 9728 §3.1 actually mandates the path-based form
    ``/.well-known/oauth-protected-resource/mcp`` for our resource at
    ``/mcp/``, but a lot of clients (including the Custom Connector)
    only probe the host-default URL. Serve both — same body."""
    return await _dispatch_sdk(
        request, "/.well-known/oauth-protected-resource/mcp/", db_service, site_config,
    )


@metadata_router.get("/.well-known/oauth-protected-resource/mcp")
async def oauth_protected_resource_mcp(
    request: Request,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
):
    return await _dispatch_sdk(
        request, "/.well-known/oauth-protected-resource/mcp/", db_service, site_config,
    )


# ---------------------------------------------------------------------------
# /authorize, /register, /revoke — straight passthrough
# ---------------------------------------------------------------------------


@authorization_router.api_route("/authorize", methods=["GET", "POST"])
async def authorize(
    request: Request,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
):
    return await _dispatch_sdk(request, "/authorize", db_service, site_config)


@authorization_router.post("/register")
async def register(
    request: Request,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
):
    return await _dispatch_sdk(request, "/register", db_service, site_config)


@authorization_router.post("/revoke")
async def revoke(
    request: Request,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
):
    return await _dispatch_sdk(request, "/revoke", db_service, site_config)


# ---------------------------------------------------------------------------
# /token — wraps SDK to also accept grant_type=client_credentials
# ---------------------------------------------------------------------------


@authorization_router.post("/token")
async def token(
    request: Request,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
):
    """Token endpoint.

    The SDK's TokenHandler natively supports ``authorization_code`` and
    ``refresh_token`` grant types. We additionally accept
    ``client_credentials`` so the headless CLI / scripts / brain daemon
    can keep minting tokens without a browser hop.

    The body is read once up-front (Starlette's Request stream can only
    be drained once) and then parsed manually as urlencoded so the
    dispatcher can replay the same bytes to the SDK if we don't handle
    it locally.
    """
    body_bytes = await request.body()
    parsed = _parse_form_bytes(body_bytes)
    grant_type = parsed.get("grant_type")
    if grant_type == "client_credentials":
        return await _client_credentials(parsed, db_service)
    return await _dispatch_sdk(
        request, "/token", db_service, site_config, body_override=body_bytes,
    )


def _parse_form_bytes(body: bytes) -> dict[str, str]:
    """Parse application/x-www-form-urlencoded body into a flat dict.

    The /token endpoint always uses urlencoded form bodies per RFC 6749
    §4.1.3, so we don't bother with multipart. Last-value-wins for
    repeated keys, matching :func:`urllib.parse.parse_qs` defaults.
    """
    from urllib.parse import parse_qsl
    if not body:
        return {}
    return dict(parse_qsl(body.decode("utf-8"), keep_blank_values=False))


async def _client_credentials(form, db_service: DatabaseService) -> JSONResponse:
    """Minimal Client Credentials Grant — we keep it inline rather than
    wiring it through the SDK because the SDK's TokenRequest discriminator
    rejects unknown grant_types at the Pydantic layer.
    """
    client_id = form.get("client_id")
    client_secret = form.get("client_secret")
    requested_scopes = form.get("scope")

    if not client_id or not client_secret:
        return _oauth_error("invalid_client", "client_id and client_secret are required", 401)

    pool = getattr(db_service, "pool", None)
    if pool is None:
        return _oauth_error("server_error", "database unavailable", 503)

    provider = PoindexterOAuthProvider(pool)
    client = await provider.get_client(str(client_id))
    if client is None:
        return _oauth_error("invalid_client", "invalid client credentials", 401)

    import hmac
    if not client.client_secret or not hmac.compare_digest(
        client.client_secret.encode(), str(client_secret).encode()
    ):
        return _oauth_error("invalid_client", "invalid client credentials", 401)

    if "client_credentials" not in client.grant_types:
        return _oauth_error(
            "unauthorized_client",
            "client is not authorized for the client_credentials grant",
            400,
        )

    granted = set((client.scope or "").split())
    if requested_scopes:
        requested = set(str(requested_scopes).split())
        extras = requested - granted
        if extras:
            return _oauth_error("invalid_scope", f"scope(s) not granted: {sorted(extras)}", 400)
        issued_scopes = requested
    else:
        issued_scopes = granted

    try:
        access_token, claims = issue_token(
            str(client_id), issued_scopes, ttl_seconds=DEFAULT_TTL_SECONDS,
        )
    except TokenError as e:
        return _oauth_error(e.error, e.error_description or "", 400)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE oauth_clients SET last_used_at = NOW() WHERE client_id = $1",
            client_id,
        )

    logger.info(
        "issued client_credentials token client_id=%s scopes=%s",
        client_id, sorted(claims.scopes),
    )
    return JSONResponse(
        content={
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": claims.expires_at - claims.issued_at,
            "scope": " ".join(sorted(claims.scopes)),
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _oauth_error(error: str, description: str, status: int) -> JSONResponse:
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    if status == 401:
        headers["WWW-Authenticate"] = 'Basic realm="poindexter-oauth"'
    return JSONResponse(
        status_code=status,
        content={"error": error, "error_description": description},
        headers=headers,
    )
