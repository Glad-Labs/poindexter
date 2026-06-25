"""Social draft management routes — thin adapter over SocialDraftsService.

No SQL or business logic here (transport adapter contract, ADR 2026-06-10).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
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
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    drafts = await _svc.list_drafts(post_id, task_id, status, db_service.pool)
    return {"drafts": [_serialize(d) for d in drafts]}


@router.post("/drafts/{draft_id}/approve")
async def approve_draft(
    draft_id: str,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    return await _svc.approve_draft(draft_id, db_service.pool, site_config)


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
    }
