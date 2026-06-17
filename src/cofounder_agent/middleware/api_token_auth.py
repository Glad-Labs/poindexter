"""
Bearer token authentication for the worker API.

All API requests must include ``Authorization: Bearer <token>``. The
only token shape accepted is an **OAuth JWT** issued by
``services.auth.oauth_issuer`` (Glad-Labs/poindexter#241 — Phase 3 cleanup
landed in #249, removing the legacy static-Bearer fallback).

The JWT is verified by signature + expiry + issuer. Every authenticated
request is logged with the resolved client_id and scope set so the audit
trail captures who made the call.

A narrow ``dev-token`` bypass remains behind ``app_settings.development_mode``
for local-only smoke tests (see ``_is_dev_token_blocked``). It FAILS CLOSED:
the bypass is refused unless the runtime is positively a development
environment — derived from the DB ``environment`` setting OR-combined with
the ``ENVIRONMENT`` env var (Glad-Labs/poindexter#606), because config is
DB-first and ``ENVIRONMENT`` is usually unset on the worker. It is not a
fallback for missing OAuth credentials; production deployments must mint
JWTs through ``/token``.
"""

import hmac
import os
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services.auth.oauth_issuer import InvalidToken, verify_token
from services.logger_config import get_logger

logger = get_logger(__name__)


def _looks_like_jwt(token: str) -> bool:
    """Cheap shape check: a JWT is three base64url segments joined by '.'.

    Lets us reject obviously-malformed tokens (including legacy static
    bearers that may still be in operator shell history) with a clean
    401 BEFORE invoking ``signing_key()`` — that lookup raises
    ``OAuthError`` when the server isn't configured, which would
    otherwise surface as a 500 to a request that should just be a 401.
    """
    parts = token.split(".")
    return len(parts) == 3 and all(parts)


def _verify_oauth_jwt(token: str) -> str:
    """Verify an OAuth JWT minted by ``services.auth.oauth_issuer``.

    Returns the token on success. Raises ``HTTPException(401)`` on any
    failure (malformed shape, expired, bad signature, wrong issuer) —
    there is no fall-through path now that static Bearer is gone.
    """
    if not _looks_like_jwt(token):
        logger.warning("OAuth JWT rejected: malformed shape")
        raise HTTPException(
            status_code=401,
            detail="invalid_token: malformed",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    try:
        claims = verify_token(token)
    except InvalidToken as e:
        logger.warning("OAuth JWT rejected: %s", e)
        raise HTTPException(
            status_code=401,
            detail=f"invalid_token: {e}",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from e
    logger.debug(
        "auth=oauth client_id=%s scopes=%s",
        claims.client_id,
        sorted(claims.scopes),
    )
    return token


security = HTTPBearer(auto_error=False)


def _request_site_config(request: Request) -> Any:
    """Pull the SiteConfig from the app's ``AppContainer`` (DI seam, #272).

    Mirrors ``utils.route_utils.get_site_config_dependency``: prefer
    ``app.state.container.site_config``, then the process-wide registered
    container, else ``None``. Returns ``None`` when the lifespan hasn't
    built the container yet — callers coalesce to fail-safe defaults so
    middleware never crashes a request on a missing config (we'd rather
    refuse the request via the downstream auth path than 500).
    """
    container = getattr(getattr(request.app, "state", None), "container", None)
    if container is not None:
        return getattr(container, "site_config", None)
    from services.container_registry import get_container

    registered = get_container()
    if registered is not None:
        return registered.site_config
    return None


# Environment values that positively assert a genuine local/dev deployment
# where the dev-token bypass is acceptable. Mirrors the dev-detection used
# elsewhere (utils/connection_health.py, services/database_service.py).
_DEV_ENVIRONMENTS = frozenset({"development", "dev", "local"})


def _is_dev_environment(sc: Any) -> bool:
    """Positively assert the runtime is a genuine development environment.

    Config is DB-first (app_settings, not env vars — see CLAUDE.md
    "Config in DB, not code"), so the worker often runs with ``ENVIRONMENT``
    unset. We therefore read the DB ``environment`` setting (the value the
    rest of the app uses via ``site_config.get("environment")`` /
    ``get_config().environment``) AND the env var, and require BOTH to point
    at a dev environment — if either source says production (or is unknown),
    the runtime is not positively a dev box.

    Fail-closed: returns ``False`` whenever the environment cannot be
    positively determined to be development (no SiteConfig, blank/unset
    value, ``staging``, ``production``, a typo, etc.).
    """
    if sc is None:
        return False
    db_env = (sc.get("environment", "") or "").strip().lower()
    env_var = os.getenv("ENVIRONMENT", "").strip().lower()
    # The DB setting is the source of truth (always populated — baseline
    # seeds it to "development"). The env var, when set, must agree; an
    # unset env var is treated as "no opinion" rather than a veto so a
    # DB-only dev box still works.
    if db_env not in _DEV_ENVIRONMENTS:
        return False
    if env_var and env_var not in _DEV_ENVIRONMENTS:
        return False
    return True


def _is_dev_token_blocked(sc: Any) -> bool:
    """Refuse the dev-token bypass unless the runtime is genuinely a dev box.

    Previously this only fired when ``DEVELOPMENT_MODE`` was on AND the
    ``ENVIRONMENT`` env var equalled ``"production"``. But config is
    DB-first, so ``ENVIRONMENT`` is usually unset on the worker — a stray
    ``development_mode=true`` row would then re-enable ``dev-token`` as full
    auth on the Tailscale-Funnel-exposed MCP/voice routes
    (Glad-Labs/poindexter#606).

    Now FAIL-CLOSED: the bypass is blocked whenever we cannot positively
    assert a development environment (via the DB ``environment`` setting
    OR-combined with the ``ENVIRONMENT`` env var). Evaluated per-request
    against the live config.
    """
    if _is_dev_environment(sc):
        return False
    # Not positively a dev environment → refuse the bypass. Log for operator
    # visibility (this only runs when a caller actually presented dev-token).
    logger.critical(
        "Dev-token bypass REFUSED: environment is not positively a development "
        "environment (DB environment=%r, ENVIRONMENT=%r). Mint an OAuth JWT via "
        "/token, or set environment=development for a genuine local box.",
        (sc.get("environment", "") if sc is not None else None),
        os.getenv("ENVIRONMENT", ""),
    )
    return True


async def verify_api_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """Verify the Bearer token as an OAuth JWT.

    Also allows the existing dev-token bypass when ``DEVELOPMENT_MODE=true``
    AND the runtime is positively a development environment — refused
    (fail-closed) otherwise (Glad-Labs/poindexter#606).

    Returns:
        The verified token string.
    """
    sc = _request_site_config(request)
    dev_mode = sc.get("development_mode", "").lower() == "true" if sc is not None else False

    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    token = credentials.credentials

    # Dev mode bypass: only accept the explicit dev-token (timing-safe match).
    if dev_mode and hmac.compare_digest(token, "dev-token"):
        if _is_dev_token_blocked(sc):
            raise HTTPException(
                status_code=401,
                detail="Dev-token rejected: DEVELOPMENT_MODE is not allowed in production",
            )
        logger.warning(
            "REQUEST AUTHENTICATED VIA DEV-TOKEN BYPASS. "
            "This is insecure and must not be used in production."
        )
        return token

    # OAuth JWT path (#241). Phase 3 (#249) removed the static-Bearer
    # fallback — every other request must present a JWT.
    return _verify_oauth_jwt(token)


# Fixed operator identity for solo-operator mode.
# In a single-operator system, all authenticated requests come from the owner.
#
# Kept as a module-level constant for test back-compat (conftest fixtures
# monkeypatch this to TEST_USER's id). Production callers should use
# ``_operator_id(sc)`` to get the LIVE value from the request-scoped
# SiteConfig — the constant defaults to "operator" because the singleton
# was empty at import time, which is the bug the migration fixes.
OPERATOR_ID = "operator"


def _operator_id(sc: Any) -> str:
    """Operator identity, deferred to request time (was a module-level constant
    that read the singleton before lifespan startup populated it)."""
    if sc is None:
        return OPERATOR_ID
    return str(sc.get("operator_id", OPERATOR_ID))


def get_operator_identity(request: Request | None = None) -> dict[str, Any]:
    """Return a fixed operator identity dict for solo-operator mode."""
    sc = _request_site_config(request) if request is not None else None
    return {
        "id": _operator_id(sc),
        "email": "operator@glad-labs.ai",
        "username": "operator",
        "auth_provider": "oauth",
        "is_active": True,
    }


async def verify_api_token_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str | None:
    """Like verify_api_token but returns None instead of raising 401.

    Used for public endpoints that optionally accept auth (e.g. list_posts
    shows drafts only when authenticated).
    """
    sc = _request_site_config(request)
    dev_mode = sc.get("development_mode", "").lower() == "true" if sc is not None else False

    if not credentials:
        return None

    token = credentials.credentials

    if dev_mode and hmac.compare_digest(token, "dev-token"):
        if _is_dev_token_blocked(sc):
            return None
        logger.warning(
            "REQUEST AUTHENTICATED VIA DEV-TOKEN BYPASS (optional auth). "
            "This is insecure and must not be used in production."
        )
        return token

    # OAuth JWT path. The optional helper returns None on any verification
    # failure rather than raising — matches the "missing creds" behaviour
    # that callers of this dependency expect. Shape-check first so a
    # legacy non-JWT token doesn't trigger the signing-key lookup.
    if not _looks_like_jwt(token):
        return None
    try:
        verify_token(token)
        return token
    except InvalidToken:
        return None
