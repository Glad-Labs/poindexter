"""Posts-approval gate routes — HTTP mirror of ``posts_approval_service`` (#1343).

Operator surfaces previously only reachable in-process:

- ``GET  /api/posts-approval/pending``          → list posts paused at publish gates
- ``GET  /api/posts-approval/pending/{post_id}`` → inspect one paused post
- ``POST /api/posts-approval/{post_id}/approve`` → clear the gate (→ scheduled)
- ``POST /api/posts-approval/{post_id}/reject``  → reject at the gate
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from middleware.api_token_auth import verify_api_token
from schemas.task_schemas import PostApprovalListResponse
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency, get_site_config_dependency

logger = get_logger(__name__)
router = APIRouter(
    prefix="/api/posts-approval",
    tags=["posts-approval"],
    # Operator surface — auth enforced on every route (poindexter#752 item 2).
    dependencies=[Depends(verify_api_token)],
)


class ApprovePublishRequest(BaseModel):
    gate_name: str | None = None
    feedback: str | None = None


class RejectPublishRequest(BaseModel):
    gate_name: str | None = None
    reason: str | None = None


@router.get(
    "/pending",
    summary="List posts paused at a publish gate",
    response_model=PostApprovalListResponse,
    status_code=200,
)
async def list_pending_publish(
    gate_name: str | None = Query(None, description="Filter to a specific gate"),
    limit: int = Query(100, ge=1, le=500),
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> PostApprovalListResponse:
    """Return every post currently paused at any publish gate (or one gate)."""
    from services.posts_approval_service import list_pending_publish as _list_pending

    posts = await _list_pending(pool=db_service.pool, gate_name=gate_name, limit=limit)
    # Canonical offset envelope (poindexter#745): `posts` → `items`. The list is
    # `limit`-capped with no cursor, so offset is always 0. Pydantic validates
    # each row into a PostApprovalItem.
    return PostApprovalListResponse(
        items=posts,  # type: ignore[arg-type]
        total=len(posts),
        limit=limit,
        offset=0,
    )


@router.get(
    "/pending/{post_id}",
    summary="Inspect a single post paused at a publish gate",
    response_model=dict[str, Any],
    status_code=200,
)
async def show_pending_publish(
    post_id: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """Return the gate state and artifact for a single paused post."""
    from services.posts_approval_service import (
        PostGateMismatchError,
        PostNotFoundError,
        PostNotPausedError,
    )
    from services.posts_approval_service import (
        show_pending_publish as _show_pending,
    )

    try:
        return await _show_pending(pool=db_service.pool, post_id=post_id)
    except PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PostNotPausedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PostGateMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/{post_id}/approve",
    summary="Clear the publish gate — post proceeds to scheduled",
    response_model=dict[str, Any],
    status_code=200,
)
async def approve_publish(
    post_id: str,
    body: ApprovePublishRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Approve the publish gate on a post so the scheduler can publish it."""
    from services.posts_approval_service import (
        PostGateMismatchError,
        PostNotFoundError,
        PostNotPausedError,
    )
    from services.posts_approval_service import (
        approve_publish as _approve,
    )

    try:
        return await _approve(
            post_id=post_id,
            gate_name=body.gate_name,
            feedback=body.feedback,
            site_config=site_config,
            pool=db_service.pool,
        )
    except PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PostNotPausedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PostGateMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/{post_id}/reject",
    summary="Reject the publish gate — post moves to rejected status",
    response_model=dict[str, Any],
    status_code=200,
)
async def reject_publish(
    post_id: str,
    body: RejectPublishRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Reject the publish gate on a post."""
    from services.posts_approval_service import (
        PostGateMismatchError,
        PostNotFoundError,
        PostNotPausedError,
    )
    from services.posts_approval_service import (
        reject_publish as _reject,
    )

    try:
        return await _reject(
            post_id=post_id,
            gate_name=body.gate_name,
            reason=body.reason,
            site_config=site_config,
            pool=db_service.pool,
        )
    except PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PostNotPausedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PostGateMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
