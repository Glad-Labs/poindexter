"""
Cache Revalidation Routes

Handles secure communication with public site ISR revalidation endpoint.
Called by the Oversight Hub React app after publishing content via FastAPI CMS.
The FastAPI backend owns the REVALIDATE_SECRET, keeping it safe from browser exposure.

Endpoints:
- POST /api/revalidate-cache - Revalidate paths on public site (requires auth token)
"""

from services.logger_config import get_logger
import os
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from middleware.api_token_auth import verify_api_token

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["cache"])


class RevalidateCacheRequest(BaseModel):
    """Request to revalidate specific paths"""

    paths: Optional[list] = None


async def trigger_nextjs_revalidation(paths: Optional[list] = None) -> bool:
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
        or os.getenv("NEXT_PUBLIC_PUBLIC_SITE_URL")
        or os.getenv("SITE_URL")
        or os.getenv("NEXT_PUBLIC_API_BASE_URL", "http://localhost:3000")
    )
    if nextjs_url.endswith("/api"):
        nextjs_url = nextjs_url[:-4]

    revalidate_url = f"{nextjs_url}/api/revalidate"
    revalidate_secret = site_config.get("revalidate_secret") or os.getenv("REVALIDATE_SECRET", "")
    environment = os.getenv("ENVIRONMENT", "development").lower()

    if not revalidate_secret:
        logger.error("REVALIDATE_SECRET is not set — refusing to revalidate in %s", environment)
        return False

    try:
        logger.info("🔄 Triggering Next.js ISR revalidation...")
        logger.info(f"   URL: {revalidate_url}")
        logger.info(f"   Paths: {paths}")

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
                logger.info("✅ ISR revalidation successful")
                return True
            else:
                logger.warning(f"⚠️ ISR revalidation returned {response.status_code}")
                logger.warning(f"   Response: {response.text[:200]}")
                return False

    except httpx.TimeoutException:
        logger.warning("⚠️ ISR revalidation timed out (10s)", exc_info=True)
        return False
    except Exception as e:
        logger.warning(
            f"⚠️ Failed to trigger ISR revalidation: {type(e).__name__}: {e}", exc_info=True
        )
        return False


@router.post("/revalidate-cache")
async def revalidate_cache(
    request_data: RevalidateCacheRequest,
    token: str = Depends(verify_api_token),
) -> Dict[str, Any]:
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
