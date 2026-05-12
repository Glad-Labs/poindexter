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
from services.revalidation_service import (
    trigger_nextjs_revalidation,  # noqa: F401 — re-exported for legacy tests
    trigger_nextjs_revalidation_detailed,
)

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
        On success::

            {"success": true, "message": "Cache revalidation successful",
             "paths": [...], "tags": [...], "status_code": 200,
             "duration_ms": 142, "url": "https://.../api/revalidate"}

        On failure (poindexter#458 — surface the upstream cause so
        the operator doesn't have to grep `docker logs`)::

            {"success": false, "message": "Cache revalidation failed (http 401)",
             "paths": [...], "tags": [...], "status_code": 401,
             "error": "Unauthorized", "error_kind": "http",
             "duration_ms": 88, "url": "https://.../api/revalidate"}
    """
    paths = request_data.paths or ["/", "/archive"]
    tags = request_data.tags or ["posts", "post-index"]

    # Use the detailed variant so the operator (or whoever called this
    # route) sees the upstream HTTP status + body on failure instead of
    # a blank "success": false.
    result = await trigger_nextjs_revalidation_detailed(paths, tags)

    if result.success:
        message = "Cache revalidation successful"
    elif result.skipped:
        message = "Cache revalidation skipped (revalidate_secret unset)"
    elif result.error_kind == "timeout":
        message = "Cache revalidation timed out"
    elif result.error_kind == "http":
        message = f"Cache revalidation failed (http {result.status_code})"
    else:
        message = "Cache revalidation failed"

    payload: dict[str, Any] = {
        "success": result.success,
        "message": message,
        "paths": paths,
        "tags": tags,
        "status_code": result.status_code,
        "duration_ms": result.duration_ms,
        "url": result.url,
    }
    if not result.success:
        payload["error"] = result.error
        payload["error_kind"] = result.error_kind
    return payload
