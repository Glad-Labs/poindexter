"""
Simple Bearer token authentication for solo operator.

Replaces the JWT + GitHub OAuth system. All API requests must include
``Authorization: Bearer <token>`` where the token matches
``app_settings.api_token`` (read via ``site_config`` on every request,
so operators can rotate without a worker restart).

OpenClaw skills and Grafana alerts use this token.

Phase H (GH#95): the module-level ``from services.site_config import
site_config`` has been removed. Both dependency functions now accept
``request: Request`` and read the config off ``request.app.state.site_config``,
which ``main.py``'s lifespan seeds with the pool-backed instance.
"""

import os

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services.logger_config import get_logger

logger = get_logger(__name__)

security = HTTPBearer(auto_error=False)


class _EnvSiteConfig:
    """Fallback stub used when ``app.state.site_config`` has not been
    wired up (e.g. in minimal unit-test apps that don't run the full
    lifespan). Mirrors the ``site_config.get(key, default)`` surface —
    DB value is always missing, so we fall back to the uppercased env
    var (matching SiteConfig.get's env-var priority) or the default.
    """

    def get(self, key: str, default: str = "") -> str:
        env_val = os.getenv(key.upper())
        if env_val:
            return env_val
        return default


_EMPTY_SITE_CONFIG = _EnvSiteConfig()


def _site_config_from_request(request: Request):
    """Return the per-request SiteConfig from ``app.state``, or the
    env-var stub if the app hasn't attached one.
    """
    sc = getattr(request.app.state, "site_config", None)
    if sc is None:
        return _EMPTY_SITE_CONFIG
    return sc


def _dev_token_blocked(site_config) -> bool:
    """Refuse the dev-token bypass when DEVELOPMENT_MODE is set in a
    production ENVIRONMENT. Computed per-request (cheap — one dict
    lookup on site_config plus one os.getenv), so there are no import-
    time DB reads and the check reflects live config.
    """
    dev_mode = site_config.get("development_mode", "").lower() == "true"
    environment = os.getenv("ENVIRONMENT", "").lower()
    if dev_mode and environment == "production":
        logger.critical(
            "DEVELOPMENT_MODE is enabled in a PRODUCTION environment! "
            "Dev-token bypass will be REFUSED. Unset DEVELOPMENT_MODE or fix ENVIRONMENT."
        )
        return True
    return False


async def verify_api_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """Verify the Bearer token against ``app_settings.api_token``.

    Also allows the existing dev-token bypass when ``DEVELOPMENT_MODE=true``.

    Returns:
        The verified token string.
    """
    site_config = _site_config_from_request(request)
    api_token = site_config.get("api_token", "")
    dev_mode = site_config.get("development_mode", "").lower() == "true"

    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    token = credentials.credentials

    # Dev mode bypass: only accept the explicit dev-token
    if dev_mode and token == "dev-token":
        if _dev_token_blocked(site_config):
            raise HTTPException(
                status_code=401,
                detail="Dev-token rejected: DEVELOPMENT_MODE is not allowed in production",
            )
        logger.warning(
            "REQUEST AUTHENTICATED VIA DEV-TOKEN BYPASS. "
            "This is insecure and must not be used in production."
        )
        return token

    if not api_token:
        raise HTTPException(status_code=500, detail="API_TOKEN not configured")

    import hmac
    if not hmac.compare_digest(token, api_token):
        raise HTTPException(status_code=401, detail="Invalid token")

    return token



def get_operator_identity(site_config=None) -> dict:
    """Return a fixed operator identity dict for solo-operator mode.

    Phase H (GH#95): ``site_config`` is now an explicit parameter so
    ``operator_id`` is read per-call (supports rotation without restart)
    without the module relying on a global singleton. Legacy callers
    that pass no argument fall back to ``"operator"``.
    """
    operator_id = (
        site_config.get("operator_id", "operator") if site_config is not None else "operator"
    )
    return {
        "id": operator_id,
        "email": "operator@glad-labs.ai",
        "username": "operator",
        "auth_provider": "api_token",
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
    site_config = _site_config_from_request(request)
    api_token = site_config.get("api_token", "")
    dev_mode = site_config.get("development_mode", "").lower() == "true"

    if not credentials:
        return None

    token = credentials.credentials

    if dev_mode and token == "dev-token":
        if _dev_token_blocked(site_config):
            return None
        logger.warning(
            "REQUEST AUTHENTICATED VIA DEV-TOKEN BYPASS (optional auth). "
            "This is insecure and must not be used in production."
        )
        return token

    import hmac
    if not api_token or not hmac.compare_digest(token, api_token):
        return None

    return token
