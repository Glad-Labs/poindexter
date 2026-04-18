"""
Cache Revalidation Routes

Handles secure communication with public site ISR revalidation endpoint.
Called by the Oversight Hub React app after publishing content via FastAPI CMS.
The FastAPI backend owns the REVALIDATE_SECRET, keeping it safe from browser exposure.

Endpoints:
- POST /api/revalidate-cache - Revalidate paths on public site (requires auth token)
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from middleware.api_token_auth import verify_api_token
from services.logger_config import get_logger
from services.revalidation_service import trigger_nextjs_revalidation

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["cache"])


class RevalidateCacheRequest(BaseModel):
    """Request to revalidate specific paths and/or tags"""

    paths: list | None = None
    tags: list | None = None




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
    tags = request_data.tags or ["posts", "post-index"]

    # Trigger ISR revalidation on public site (both paths and tags)
    success = await trigger_nextjs_revalidation(paths, tags)

    return {
        "success": success,
        "message": "Cache revalidation " + ("successful" if success else "failed"),
        "paths": paths,
        "tags": tags,
    }
