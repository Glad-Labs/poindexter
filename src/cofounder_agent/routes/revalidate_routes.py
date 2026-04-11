"""
Cache Revalidation Routes

Handles secure communication with public site ISR revalidation endpoint.
Called by the Oversight Hub React app after publishing content via FastAPI CMS.
The FastAPI backend owns the REVALIDATE_SECRET, keeping it safe from browser exposure.

Endpoints:
- POST /api/revalidate-cache - Revalidate paths on public site (requires auth token)
"""

import os
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from middleware.api_token_auth import verify_api_token
from services.logger_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["cache"])


class RevalidateCacheRequest(BaseModel):
    """Request to revalidate specific paths"""

    paths: list | None = None


async def trigger_nextjs_revalidation(paths: list | None = None) -> bool:
    """
    Trigger Next.js ISR revalidation on the public site.

    Calls the revalidation endpoint to clear cache and regenerate pages
    when content is published or updated in the FastAPI CMS.

    Args:
        paths: List of paths to revalidate. Defaults to ["/", "/archive"]

    Returns:
        True if revalidation succeeded, False otherwise
    """
    if paths is None:
        paths = ["/", "/archive"]

    # Get Next.js public site URL from DB config, env, or default
    from services.site_config import site_config
    nextjs_url = (
        site_config.get("public_site_url")
        or site_config.get("site_url")
        or site_config.get("next_public_public_site_url")
        or site_config.get("next_public_api_base_url", "http://localhost:3000")
    )
    if nextjs_url.endswith("/api"):
        nextjs_url = nextjs_url[:-4]

    revalidate_url = f"{nextjs_url}/api/revalidate"

    # site_config excludes secrets (is_secret=true), so fetch directly from DB
    revalidate_secret = ""
    try:
        from utils.route_utils import get_database_dependency
        db_service = get_database_dependency()
        pool = getattr(db_service, "pool", None)
        if pool:
            row = await pool.fetchrow(
                "SELECT value FROM app_settings WHERE key = 'revalidate_secret'"
            )
            if row and row["value"]:
                revalidate_secret = row["value"]
    except Exception as e:
        logger.warning("Failed to fetch revalidate_secret from DB: %s", e)

    if not revalidate_secret:
        revalidate_secret = site_config.get("revalidate_secret", "")
    environment = os.getenv("ENVIRONMENT", "development").lower()

    if not revalidate_secret:
        logger.error("REVALIDATE_SECRET is not set — refusing to revalidate in %s", environment)
        return False

    try:
        logger.info("Triggering Next.js ISR revalidation...")
        logger.info("   URL: %s", revalidate_url)
        logger.info("   Paths: %s", paths)

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                revalidate_url,
                json={"paths": paths},
                headers={
                    "x-revalidate-secret": revalidate_secret,
                    "Content-Type": "application/json",
                },
            )

            if response.status_code == 200:
                logger.info("ISR revalidation successful")
                return True
            else:
                logger.warning("ISR revalidation returned %s", response.status_code)
                logger.warning("   Response: %s", response.text[:200])
                return False

    except httpx.TimeoutException:
        logger.warning("ISR revalidation timed out (10s)", exc_info=True)
        return False
    except Exception as e:
        logger.warning(
            "Failed to trigger ISR revalidation: %s: %s", type(e).__name__, e, exc_info=True
        )
        return False


@router.post("/revalidate-cache")
async def revalidate_cache(
    request_data: RevalidateCacheRequest,
    token: str = Depends(verify_api_token),
) -> dict[str, Any]:
    """
    Securely revalidate public site cache after publishing content.

    Called by Oversight Hub React app after a post is published via the FastAPI CMS.
    Only authenticated users (with valid JWT token) can trigger revalidation.
    The REVALIDATE_SECRET is kept server-side, never exposed to browser.

    Args:
        request_data: {"paths": ["/"]} - Paths to revalidate

    Returns:
        {"success": bool, "message": str, "paths": list}
    """
    paths = request_data.paths or ["/", "/archive"]

    # Trigger ISR revalidation on public site
    success = await trigger_nextjs_revalidation(paths)

    return {
        "success": success,
        "message": "Cache revalidation " + ("successful" if success else "failed"),
        "paths": paths,
    }
