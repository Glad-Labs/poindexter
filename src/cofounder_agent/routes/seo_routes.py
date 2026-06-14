"""SEO-refresh routes — operator-console read over the ``seo_opportunities`` pipeline.

- ``GET /api/seo`` → SEO-refresh summary (actionable queue + recent refreshes
  with a baseline→outcome delta + by-status/by-tier rollups)

Read-only: the ``seo.refresh`` loop processes opportunities autonomously
(open → queued → refreshed → measured), so the console only observes. The
read function (``services.seo_read.read_seo``) owns the SQL.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from middleware.api_token_auth import verify_api_token
from services.database_service import DatabaseService
from services.seo_read import read_seo
from utils.route_utils import get_database_dependency

router = APIRouter(prefix="/api/seo", tags=["seo"])


@router.get("")
async def get_seo_summary(
    limit: int = Query(30, ge=1, le=100, description="Max queue + refresh rows"),
    _token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """SEO-refresh pipeline summary for the operator console (read-only)."""
    return await read_seo(db_service.pool, limit=limit)
