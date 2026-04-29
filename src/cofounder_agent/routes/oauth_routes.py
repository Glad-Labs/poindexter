"""OAuth 2.1 Authorization Server endpoints (Phase 1, #241).

Three routes — the bare minimum for an OAuth 2.1 Client Credentials
Authorization Server that an Anthropic Custom Connector (or any other
MCP 2025-03-26 / RFC-8414 / RFC-9728-aware client) can talk to:

- ``GET /.well-known/oauth-authorization-server`` — RFC 8414 metadata
  (issuer-side; lists token endpoint, supported grants, scopes)
- ``GET /.well-known/oauth-protected-resource`` — RFC 9728 metadata
  (resource-side; tells the client which authorization server protects
  this resource). MCP 2025-03-26 §authorization REQUIRES this for
  discovery — without it Anthropic's Custom Connector aborts before
  ever hitting the token endpoint.
- ``POST /oauth/token`` — Client Credentials Grant only (RFC 6749 §4.4)

Token validation lives next to the rest of the auth surface in
``middleware/api_token_auth.py`` — these routes are the *issuer* side
only.

There's no ``/oauth/register`` (DCR) yet — clients are provisioned by
the operator via ``poindexter auth register-client``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse

from services.auth.oauth_issuer import (
    ALLOWED_SCOPES,
    InvalidClient,
    InvalidScope,
    mint_token_from_credentials,
)
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency, get_site_config_dependency

logger = get_logger(__name__)


# Two routers because they sit at different paths — well-known is at the
# document root, the grant endpoint is under /oauth.
metadata_router = APIRouter(tags=["OAuth"])
token_router = APIRouter(prefix="/oauth", tags=["OAuth"])


# ---------------------------------------------------------------------------
# RFC 8414 — Authorization Server Metadata
# ---------------------------------------------------------------------------


def _issuer_url(request: Request, site_config: Any) -> str:
    """Best-effort canonical issuer URL.

    Prefers the configured public-facing URL (set explicitly in
    ``app_settings.oauth_issuer_url`` so the operator controls what
    Custom Connectors see) and falls back to reconstructing from the
    incoming request — handy for local dev but not what we want from
    behind a Tailscale Funnel.
    """
    explicit = site_config.get("oauth_issuer_url", "") if site_config else ""
    if explicit:
        return explicit.rstrip("/")
    return f"{request.url.scheme}://{request.url.netloc}".rstrip("/")


@metadata_router.get("/.well-known/oauth-authorization-server")
async def oauth_metadata(
    request: Request,
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    issuer = _issuer_url(request, site_config)
    return {
        "issuer": issuer,
        "token_endpoint": f"{issuer}/oauth/token",
        "grant_types_supported": ["client_credentials"],
        "token_endpoint_auth_methods_supported": [
            # Per RFC 6749 we accept both — body params (used by the
            # Anthropic Custom Connector UI) and HTTP Basic.
            "client_secret_post",
            "client_secret_basic",
        ],
        "scopes_supported": sorted(ALLOWED_SCOPES),
        "response_types_supported": [],  # client_credentials has no response type
        "service_documentation": "https://github.com/Glad-Labs/poindexter/issues/241",
    }


@metadata_router.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource_metadata(
    request: Request,
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """RFC 9728 — tells a client which authorization server protects this resource.

    Required by the MCP 2025-03-26 authorization spec for the discovery
    chain to complete. The Custom Connector hits the MCP URL with no
    auth, parses the ``WWW-Authenticate`` header for ``resource_metadata``,
    fetches *this* document, then fetches the listed authorization
    server's ``/.well-known/oauth-authorization-server`` for the token
    endpoint.

    The ``resource`` we name is the MCP HTTP endpoint (``/mcp/``); the
    ``authorization_servers`` array points back at the worker (which is
    the same external host but a logically different role — the worker
    issues tokens, the MCP server consumes them).
    """
    issuer = _issuer_url(request, site_config)
    return {
        "resource": f"{issuer}/mcp/",
        "authorization_servers": [issuer],
        "scopes_supported": sorted(ALLOWED_SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_documentation": "https://github.com/Glad-Labs/poindexter/issues/241",
    }


# ---------------------------------------------------------------------------
# RFC 6749 §4.4 — Client Credentials Grant
# ---------------------------------------------------------------------------


def _oauth_error(error: str, description: str, status: int = 400) -> JSONResponse:
    """Format an RFC 6749 §5.2 error response."""
    return JSONResponse(
        status_code=status,
        content={"error": error, "error_description": description},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@token_router.post("/token")
async def token_endpoint(
    request: Request,
    grant_type: str = Form(...),
    scope: str | None = Form(default=None),
    client_id: str | None = Form(default=None),
    client_secret: str | None = Form(default=None),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """RFC 6749 §4.4 token endpoint.

    Accepts client credentials either as form params (``client_id`` +
    ``client_secret``, the path the Anthropic Custom Connector takes) or
    via HTTP Basic auth. ``scope`` is space-delimited per RFC 6749 §3.3
    and is optional — omitting it issues the client's full registered
    scope set.
    """
    if grant_type != "client_credentials":
        return _oauth_error(
            "unsupported_grant_type",
            "this server only supports grant_type=client_credentials",
        )

    # HTTP Basic fallback — RFC 6749 §2.3.1
    if not (client_id and client_secret):
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("basic "):
            import base64
            try:
                decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
                basic_id, _, basic_secret = decoded.partition(":")
                if basic_id and basic_secret:
                    client_id = client_id or basic_id
                    client_secret = client_secret or basic_secret
            except (ValueError, UnicodeDecodeError):
                pass

    if not client_id or not client_secret:
        # 401 + WWW-Authenticate per RFC 6749 §5.2 when client auth
        # comes via the Authorization header.
        resp = _oauth_error(
            "invalid_client",
            "client_id and client_secret are required",
            status=401,
        )
        resp.headers["WWW-Authenticate"] = 'Basic realm="poindexter-oauth"'
        return resp

    requested_scopes = scope.split() if scope else None

    pool = getattr(db_service, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")

    try:
        token, claims = await mint_token_from_credentials(
            pool,
            client_id=client_id,
            client_secret=client_secret,
            requested_scopes=requested_scopes,
        )
    except InvalidClient as e:
        logger.warning("token mint failed: invalid_client client_id=%s", client_id)
        resp = _oauth_error("invalid_client", str(e), status=401)
        resp.headers["WWW-Authenticate"] = 'Basic realm="poindexter-oauth"'
        return resp
    except InvalidScope as e:
        return _oauth_error("invalid_scope", str(e))

    expires_in = claims.expires_at - claims.issued_at
    logger.info(
        "issued OAuth token client_id=%s scopes=%s expires_in=%ds",
        claims.client_id, sorted(claims.scopes), expires_in,
    )
    return JSONResponse(
        content={
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "scope": " ".join(sorted(claims.scopes)),
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
