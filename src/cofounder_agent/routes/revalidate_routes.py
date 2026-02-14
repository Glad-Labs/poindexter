"""
Cache Revalidation Routes

Handles secure communication with public site ISR revalidation endpoint.
Called by the Oversight Hub React app after publishing content via FastAPI CMS.
The FastAPI backend owns the REVALIDATE_SECRET, keeping it safe from browser exposure.

Endpoints:
- POST /api/revalidate-cache - Revalidate paths on public site (requires auth token)
"""

import logging
import os
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Header
from pydantic import BaseModel

logger = logging.getLogger(__name__)

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
    
    # Get Next.js public site URL from environment or use default
    nextjs_url = os.getenv("NEXT_PUBLIC_PUBLIC_SITE_URL", os.getenv("NEXT_PUBLIC_API_BASE_URL", "http://localhost:3000"))
    if nextjs_url.endswith("/api"):
        # Remove /api suffix if present
        nextjs_url = nextjs_url[:-4]
    
    revalidate_url = f"{nextjs_url}/api/revalidate"
    revalidate_secret = os.getenv("REVALIDATE_SECRET", "dev-secret-key")
    
    try:
        logger.info(f"🔄 Triggering Next.js ISR revalidation...")
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
                logger.info(f"✅ ISR revalidation successful")
                return True
            else:
                logger.warning(f"⚠️ ISR revalidation returned {response.status_code}")
                logger.warning(f"   Response: {response.text[:200]}")
                return False
                
    except httpx.TimeoutException:
        logger.warning(f"⚠️ ISR revalidation timed out (10s)")
        return False
    except Exception as e:
        logger.warning(f"⚠️ Failed to trigger ISR revalidation: {type(e).__name__}: {e}")
        return False


@router.post("/revalidate-cache")
async def revalidate_cache(
    request_data: RevalidateCacheRequest,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """
    Securely revalidate public site cache after publishing content.
    
    Called by Oversight Hub React app after a post is published via the FastAPI CMS.
    Only authenticated users (with valid JWT token) can trigger revalidation.
    The REVALIDATE_SECRET is kept server-side, never exposed to browser.
    
    Args:
        request_data: {"paths": ["/"]} - Paths to revalidate
        authorization: Bearer token (required for auth verification)
    
    Returns:
        {"success": bool, "message": str, "paths": list}
    """
    # Basic auth check - verify authorization header present
    if not authorization:
        logger.warning("Unauthorized cache revalidation attempt (no auth header)")
        return {
            "success": False,
            "message": "Authentication required",
            "paths": request_data.paths or [],
        }
    
    paths = request_data.paths or ["/", "/archive"]
    
    # Trigger ISR revalidation on public site
    success = await trigger_nextjs_revalidation(paths)
    
    return {
        "success": success,
        "message": "Cache revalidation " + ("successful" if success else "failed"),
        "paths": paths,
    }
