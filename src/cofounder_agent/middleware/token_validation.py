"""
Token Validation Middleware

Validates JWT token expiration on authenticated requests.
Provides optional OAuth token status checking for Phase 1 OAuth security.

Uses:
- JWTTokenValidator for JWT token validation
- TokenManager for OAuth token status checking (optional)
"""

from collections.abc import Callable

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from services.logger_config import get_logger

logger = get_logger(__name__)


class TokenValidationMiddleware(BaseHTTPMiddleware):
    """
    Middleware for validating JWT tokens on protected endpoints.

    Features:
    - Validates JWT token expiration and signature
    - Checks OAuth token status (optional, non-blocking)
    - Allows development mode token bypass via DISABLE_AUTH_FOR_DEV
    - Skips validation for public and WebSocket endpoints

    Protected Endpoints (require valid token):
    - /api/* (except public routes)

    Public Endpoints (no token required):
    - GET /api/public/*
    - /api/auth/* (login/logout)
    - /health

    Scope-path policy (CVE-2026-48710 "BadHost"):
    All auth-bypass decisions in this middleware MUST use
    ``request.scope["path"]`` (the raw ASGI path the server routed
    against). ``request.url.path`` is reconstructed from the Host
    header and can be shifted by a crafted
    ``Host: target/public-prefix`` so the reconstructed path starts
    with a public prefix while the ASGI router still dispatches the
    protected handler. Starlette >= 1.0.1 closes the underlying
    parse hole; using ``scope["path"]`` here is the defence-in-depth
    pattern that survives any future URL-reconstruction regression.
    Do not reintroduce ``request.url.path`` for security decisions.
    """

    # Routes that require authentication
    PROTECTED_PATHS = {
        "/api/tasks",
        "/api/workflows",
        "/api/custom-workflows",
        "/api/agents",
        "/api/capability-tasks",
        "/api/bulk-tasks",
        "/api/commands",
        "/api/services",
        "/api/cms",
    }

    # Routes that don't require authentication
    PUBLIC_PATHS = {
        "/api/public",
        "/api/auth",
        "/api/cms/status",
        # `/api/track` removed 2026-05-28 — the /api/track/view endpoint
        # was deleted along with the Vercel proxy that fronted it. Beacon
        # is now a Cloudflare Worker → CF Analytics Engine path; backend
        # ingest is via sync_cloudflare_analytics job, no inbound route.
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/ws",  # WebSocket
    }

    # Paths hit by scrapers/health checks — we don't want one INFO log per
    # poll, so the dev-bypass message is demoted to DEBUG for these.
    _NOISY_PATH_PREFIXES = ("/api/prometheus", "/api/health", "/api/metrics")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process incoming request with token validation"""

        # CVE-2026-48710 ("BadHost"): use the raw ASGI scope path for every
        # auth decision. ``request.url.path`` is reconstructed from the Host
        # header by Starlette and can be shifted by a crafted
        # ``Host: target/public-prefix`` so the reconstructed path starts
        # with a public prefix while the ASGI router still dispatches the
        # protected handler. ``scope["path"]`` is the path the server
        # actually routed against and cannot be shifted by header content.
        path = request.scope["path"]

        try:
            # DI seam (glad-labs-stack#330) — read site_config from
            # app.state, populated by main.py's lifespan.
            sc = getattr(request.app.state, "site_config", None)
            # Development mode: Allow bypassing authentication for testing.
            # Guard: DISABLE_AUTH_FOR_DEV only honoured when DEVELOPMENT_MODE=true,
            # ensuring it never works on staging or production (#1219).
            if (
                sc is not None
                and sc.get("disable_auth_for_dev", "false").lower() == "true"
                and sc.get("development_mode", "false").lower() == "true"
            ):
                if path.startswith(self._NOISY_PATH_PREFIXES):
                    logger.debug(
                        "[TokenValidation] DISABLE_AUTH_FOR_DEV=true, bypassing auth for %s", path
                    )
                else:
                    logger.info(
                        "[TokenValidation] DISABLE_AUTH_FOR_DEV=true, bypassing auth for %s", path
                    )
                return await call_next(request)

            # Skip validation for WebSocket connections
            if request.headers.get("upgrade", "").lower() == "websocket":
                return await call_next(request)

            # Skip validation for public routes
            if any(path.startswith(public) for public in self.PUBLIC_PATHS):
                return await call_next(request)

            # Skip validation for protected paths not matched (let them through)
            # Validation happens at dependency level via get_current_user()
            is_protected = any(path.startswith(protected) for protected in self.PROTECTED_PATHS)

            if not is_protected:
                # Not a protected endpoint, proceed
                return await call_next(request)

            # For protected endpoints, validate token is present
            auth_header = request.headers.get("Authorization", "")

            if not auth_header:
                logger.warning(
                    f"[TokenValidation] Missing auth header for protected path: {path}"
                )
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Missing authorization header"},
                )

            # Token format validation
            if not auth_header.startswith("Bearer "):
                logger.warning(
                    f"[TokenValidation] Invalid auth header format for {path}"
                )
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Invalid authorization header format. Use: Bearer <token>"},
                )

            # Full validation (expiration, signature) happens in get_current_user()
            # via the JWTTokenValidator dependency. This middleware only verifies
            # that the Bearer header is present and correctly formatted — it
            # intentionally does not parse or validate the token value itself.

            # Continue to next middleware/endpoint
            response = await call_next(request)
            return response

        except Exception as e:
            logger.error(
                f"[TokenValidation] Error validating token: {type(e).__name__}: {e!s}",
                exc_info=True,
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Token validation error"},
            )
