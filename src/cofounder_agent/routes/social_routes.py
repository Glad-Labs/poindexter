"""Social draft management routes — thin adapter over SocialDraftsService.

No SQL or business logic here (transport adapter contract, ADR 2026-06-10).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from middleware.api_token_auth import verify_api_token
from services.database_service import DatabaseService
from services.social_drafts import SocialDraftRow, SocialDraftsService
from utils.route_utils import get_database_dependency, get_site_config_dependency

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/social",
    tags=["social"],
    dependencies=[Depends(verify_api_token)],
)

_svc = SocialDraftsService()


class EditDraftRequest(BaseModel):
    content: str
    platform_config: dict[str, Any] | None = None


@router.get("/drafts")
async def list_drafts(
    post_id: str | None = Query(None),
    task_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500, description="Max drafts to return"),
    offset: int = Query(0, ge=0, description="Drafts to skip"),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """Window of social drafts, live (pending/failed) rows first.

    ``status_counts`` is per-status totals for the post/task scope, spanning
    every status regardless of ``limit`` or the ``status`` filter — the
    console's KPI row and rail badge read it instead of counting the returned
    page, which would otherwise report the window and call it the table.

    The body keeps its ``drafts`` key rather than the canonical ``items``
    (#745): converting the envelope is a separate step with three consumers
    to move, and it is not what this endpoint was unbounded for. ``total`` /
    ``limit`` / ``offset`` already use the canonical names and semantics, so
    that step is a rename.
    """
    page = await _svc.list_drafts(
        post_id, task_id, status, db_service.pool, limit=limit, offset=offset
    )
    return {
        "drafts": [_serialize(d) for d in page.rows],
        "total": page.total,
        "limit": limit,
        "offset": offset,
        "status_counts": page.status_counts,
    }


@router.post("/drafts/{draft_id}/approve")
async def approve_draft(
    draft_id: str,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    result = await _svc.approve_draft(draft_id, db_service.pool, site_config)
    if not result.get("success"):
        # Blocked (post not live yet, no integration UUID, Postiz call
        # failed, ...) must surface as a non-2xx status — the console's
        # error handling only inspects the HTTP status code (same contract
        # approval_routes.py uses, poindexter#743), so a 200 body here reads
        # as a silent success and the operator never learns the draft never
        # went out.
        raise HTTPException(status_code=409, detail=result.get("error"))
    return result


@router.post("/drafts/{draft_id}/reject")
async def reject_draft(
    draft_id: str,
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    await _svc.reject_draft(draft_id, db_service.pool)
    return {"ok": True}


@router.patch("/drafts/{draft_id}")
async def edit_draft(
    draft_id: str,
    body: EditDraftRequest,
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    await _svc.edit_draft(draft_id, body.content, body.platform_config, db_service.pool)
    return {"ok": True}


def _serialize(d: SocialDraftRow) -> dict[str, Any]:
    return {
        "id": d.id,
        "pipeline_task_id": d.pipeline_task_id,
        "post_id": d.post_id,
        "post_status": d.post_status,
        "platform": d.platform,
        "content": d.content,
        "platform_config": d.platform_config,
        "status": d.status,
        "postiz_post_id": d.postiz_post_id,
        "error": d.error,
        "retry_count": d.retry_count,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "approved_at": d.approved_at.isoformat() if d.approved_at else None,
        "posted_at": d.posted_at.isoformat() if d.posted_at else None,
        "title": d.title,
        "resolved_post_id": d.resolved_post_id,
    }
