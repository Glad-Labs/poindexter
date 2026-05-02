"""
Bearer token authentication for the worker API.

All API requests must include ``Authorization: Bearer <token>``. Two
token shapes are accepted during the OAuth migration window
(Glad-Labs/poindexter#241):

1. **OAuth JWT** issued by ``services.auth.oauth_issuer`` — looks like
   ``xxx.yyy.zzz``. Verified by signature + expiry + issuer.
2. **Static Bearer** — the legacy ``app_settings.api_token``. Will be
   removed in Phase 3 (#249) once every client has migrated.

The dispatch order is JWT-first: tokens with three dot-separated
segments go through JWT verify; everything else falls through to the
static path. Both paths log which way the request authenticated so the
Phase 3 cutover has data on whether any client is still on the legacy
token.

OpenClaw skills and Grafana alerts use the static-bearer path today.
"""

import os

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)


def _looks_like_jwt(token: str) -> bool:
    """Cheap shape check: a JWT is three base64url segments joined by '.'.

    We don't decode here — that's the issuer's job. This just lets us
    skip the JWT path for legacy 32-char static tokens without paying
    for a failed verify.
    """
    parts = token.split(".")
    return len(parts) == 3 and all(parts)


def _try_oauth_jwt(token: str) -> str | None:
    """Try the OAuth-JWT path. Returns the token on success, None when
    it isn't a JWT we issued (caller falls through to static).

    Raises HTTPException(401) when the token IS a JWT but fails
    verification (expired, bad sig, wrong issuer) — that's a real auth
    failure, not a fall-through case.
    """
    if not _looks_like_jwt(token):
        return None
    try:
        from services.auth.oauth_issuer import verify_token, InvalidToken
    except Exception:  # noqa: BLE001
        # Issuer module unavailable (e.g. in a minimal-app test) — fall
        # back to static so existing tests that don't load the issuer
        # keep working.
        return None
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
        claims.client_id, sorted(claims.scopes),
    )
    return token

security = HTTPBearer(auto_error=False)

# Startup safety check: refuse dev-token bypass in production
_dev_mode = site_config.get("development_mode", "").lower() == "true"
_environment = os.getenv("ENVIRONMENT", "").lower()
_dev_token_blocked = False

if _dev_mode and _environment == "production":
    logger.critical(
        "DEVELOPMENT_MODE is enabled in a PRODUCTION environment! "
        "Dev-token bypass will be REFUSED. Unset DEVELOPMENT_MODE or fix ENVIRONMENT."
    )
    _dev_token_blocked = True


async def verify_api_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """Verify the Bearer token against ``app_settings.api_token``.

    Also allows the existing dev-token bypass when ``DEVELOPMENT_MODE=true``.

    Returns:
        The verified token string.
    """
    # api_token is is_secret=true (encrypted at rest with enc:v1: prefix).
    # Sync site_config.get() returns the raw ciphertext (or empty) for
    # secret rows because the in-memory cache filters secrets out per the
    # Phase H DI-injection design. Use the async get_secret accessor so
    # we compare against the decrypted plaintext. Closes Glad-Labs/poindexter#325.
    api_token = await site_config.get_secret("api_token", "")
    dev_mode = site_config.get("development_mode", "").lower() == "true"

    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    token = credentials.credentials

    # Dev mode bypass: only accept the explicit dev-token
    if dev_mode and token == "dev-token":
        if _dev_token_blocked:
            raise HTTPException(
                status_code=401,
                detail="Dev-token rejected: DEVELOPMENT_MODE is not allowed in production",
            )
        logger.warning(
            "REQUEST AUTHENTICATED VIA DEV-TOKEN BYPASS. "
            "This is insecure and must not be used in production."
        )
        return token

    # OAuth JWT path (#241). Returns the token on success, None when it
    # doesn't look like a JWT (fall through to static), or raises 401
    # when it is a JWT but verification fails.
    jwt_token = _try_oauth_jwt(token)
    if jwt_token is not None:
        return jwt_token

    if not api_token:
        raise HTTPException(status_code=500, detail="API_TOKEN not configured")

    import hmac
    if not hmac.compare_digest(token, api_token):
        raise HTTPException(status_code=401, detail="Invalid token")

    logger.debug("auth=static_bearer")
    return token



# Fixed operator identity for solo-operator mode.
# In a single-operator system, all authenticated requests come from the owner.
OPERATOR_ID = site_config.get("operator_id", "operator")


def get_operator_identity() -> dict:
    """Return a fixed operator identity dict for solo-operator mode."""
    return {
        "id": OPERATOR_ID,
        "email": "operator@glad-labs.ai",
        "username": "operator",
        "auth_provider": "api_token",
        "is_active": True,
    }


async def verify_api_token_optional(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str | None:
    """Like verify_api_token but returns None instead of raising 401.

    Used for public endpoints that optionally accept auth (e.g. list_posts
    shows drafts only when authenticated).
    """
    # See note on verify_api_token — sync .get() returns ciphertext for
    # is_secret rows, so the comparison silently fails. Same fix here.
    api_token = await site_config.get_secret("api_token", "")
    dev_mode = site_config.get("development_mode", "").lower() == "true"

    if not credentials:
        return None

    token = credentials.credentials

    if dev_mode and token == "dev-token":
        if _dev_token_blocked:
            return None
        logger.warning(
            "REQUEST AUTHENTICATED VIA DEV-TOKEN BYPASS (optional auth). "
            "This is insecure and must not be used in production."
        )
        return token

    # OAuth JWT path (#241). The optional helper differs from the
    # required version: a malformed/expired JWT returns None (no auth)
    # rather than raising 401 — matches the "missing creds" behaviour.
    if _looks_like_jwt(token):
        try:
            from services.auth.oauth_issuer import verify_token, InvalidToken
            try:
                verify_token(token)
                return token
            except InvalidToken:
                return None
        except Exception:  # noqa: BLE001
            pass  # fall through to static

    import hmac
    if not api_token or not hmac.compare_digest(token, api_token):
        return None

    return token
