"""Media-approval routes — HTTP mirror of ``media_approval_service`` (#1343).

Operator surfaces previously only reachable in-process:

- ``GET  /api/media-approval/pending``                   → list pending media
- ``POST /api/media-approval/{post_id}/{medium}/decide`` → approve or reject
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from middleware.api_token_auth import get_operator_identity, verify_api_token
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)
router = APIRouter(prefix="/api/media-approval", tags=["media-approval"])


class DecideRequest(BaseModel):
    approved: bool
    notes: str | None = None


@router.get(
    "/pending",
    summary="List pending media awaiting operator review",
    response_model=dict[str, Any],
    status_code=200,
)
async def list_pending(
    medium: str | None = Query(None, description="Filter by medium: podcast, video, etc."),
    limit: int = Query(50, ge=1, le=500),
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """Return pending media rows joined with post title/slug."""
    from services.media_approval_service import list_pending as _list_pending

    rows = await _list_pending(db_service.pool, medium=medium, limit=limit)
    # Convert datetime objects for JSON serialisation
    for row in rows:
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
    return {"media": rows, "total": len(rows)}


@router.post(
    "/{post_id}/{medium}/decide",
    summary="Approve or reject a generated media asset",
    response_model=dict[str, Any],
    status_code=200,
)
async def decide(
    post_id: str,
    medium: str,
    body: DecideRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """Approve or reject a generated media asset for a post.

    ``medium`` must be a recognised value (``podcast``, ``video``, etc.).
    ``approved=true`` clears the post for dispatch; ``approved=false`` marks
    it rejected so it can be regenerated.
    """
    from services.media_approval_service import decide as _decide

    operator = get_operator_identity()
    decided_by = operator.get("id") or "operator:http"

    try:
        await _decide(
            db_service.pool,
            post_id,
            medium,
            approved=body.approved,
            decided_by=decided_by,
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "ok": True,
        "post_id": post_id,
        "medium": medium,
        "approved": body.approved,
        "decided_by": decided_by,
    }
