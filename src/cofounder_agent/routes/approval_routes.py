"""
Approval Routes - Content Approval Workflow

Handles human approval of generated content before publishing.

NOTE: The approve endpoint (POST /api/tasks/{task_id}/approve) lives in
task_publishing_routes.py — it handles images, auto-publish, and numeric
ID fallback.  This module owns rejection and the pending-approval listing.

Endpoints:
- POST /api/tasks/{task_id}/reject - Reject a task with feedback
- GET /api/tasks/pending-approval - List all tasks awaiting approval
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from middleware.api_token_auth import get_operator_identity, verify_api_token
from services.database_service import DatabaseService
from services.error_handler import AppError
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)
router = APIRouter(prefix="/api/tasks", tags=["approval"])


async def broadcast_approval_status(
    task_id: str, status: str, details: dict | None = None
) -> None:
    """No-op stub — websocket routes removed (no connected clients)."""
    pass


# ============================================================================
# SCHEMAS
# ============================================================================


class RejectionRequest(BaseModel):
    """Request body for rejecting a task"""

    reason: str
    feedback: str
    allow_revisions: bool = True


# ============================================================================
# ENDPOINTS
# ============================================================================

# NOTE: POST /{task_id}/approve is defined in task_publishing_routes.py
# (registered via task_routes.py → publishing_router). It was removed from
# this file to eliminate a duplicate endpoint (#1335).


@router.post(
    "/{task_id}/reject",
    summary="Reject a task with feedback",
    response_model=dict[str, Any],
    status_code=200,
)
async def reject_task(
    task_id: str,
    request: RejectionRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """Reject a task and send it back for revisions.

    Only tasks with status 'awaiting_approval' can be rejected.
    If allow_revisions=true, status becomes 'rejected_retry'.
    If allow_revisions=false, status becomes 'rejected_final'.
    """
    try:
        operator = get_operator_identity()

        # Fetch task
        task = await db_service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Verify task is awaiting approval
        current_status = task.get("status")
        if current_status != "awaiting_approval":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reject task with status '{current_status}' — expected 'awaiting_approval'",
            )

        # Determine final status based on revision allowance
        final_status = "rejected_retry" if request.allow_revisions else "rejected_final"
        rejection_date = datetime.now(timezone.utc)

        # Update task with rejection metadata
        metadata_updates = {
            **(task.get("metadata") or {}),
            "rejection_date": rejection_date.isoformat(),
            "rejected_by": operator["id"],
            "rejection_reason": request.reason,
            "rejection_feedback": request.feedback,
            "allow_revisions": request.allow_revisions,
        }

        await db_service.update_task(
            task_id,
            {
                "status": final_status,
                "approval_status": "rejected",
                "human_feedback": request.feedback,
                "metadata": metadata_updates,
                "updated_at": rejection_date.isoformat(),
            },
        )

        # Record the rejection on pipeline_reviews so `content_tasks` view's
        # approval_status / approved_by columns resolve non-NULL.
        try:
            from services.pipeline_db import PipelineDB
            await PipelineDB(db_service.pool).add_review(
                task_id=task_id,
                decision="rejected",
                reviewer=operator["id"],
                feedback=request.feedback,
            )
        except Exception as review_err:
            logger.warning(
                "[reject_task] pipeline_reviews write failed for %s: %s",
                task_id, review_err,
            )

        logger.info("Task %s rejected by %s: %s", task_id, operator['id'], request.reason)

        # Broadcast rejection status to connected WebSocket clients
        try:
            await broadcast_approval_status(
                task_id,
                "rejected",
                {
                    "rejected_by": operator["id"],
                    "reason": request.reason,
                    "feedback": request.feedback,
                    "allow_revisions": request.allow_revisions,
                    "rejection_date": rejection_date.isoformat(),
                },
            )
        except (ValueError, KeyError, AttributeError, TypeError, RuntimeError) as e:
            logger.warning("Failed to broadcast rejection status: %s", e, exc_info=True)

        return {
            "task_id": task_id,
            "status": final_status,
            "approval_status": "rejected",
            "rejection_date": rejection_date.isoformat(),
            "rejected_by": operator["id"],
            "reason": request.reason,
            "feedback": request.feedback,
            "allow_revisions": request.allow_revisions,
            "message": f"Task rejected - {request.reason}. {'Revisions can be requested.' if request.allow_revisions else 'Task archived (no revisions).'}",
            "next_action": "Task removed from publishing queue. Content team can review feedback and make revisions if allowed.",
        }

    except HTTPException:
        raise
    except AppError:
        raise
    except (ValueError, KeyError, AttributeError, TypeError, RuntimeError) as e:
        logger.error("Failed to reject task %s: %s", task_id, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to reject task",
        ) from e


@router.get(
    "/pending-approval",
    summary="List all tasks awaiting approval",
    response_model=dict[str, Any],
    status_code=200,
)
async def get_pending_approvals(
    limit: int = Query(20, ge=1, le=100, description="Results per page (1-100)"),
    offset: int = Query(0, ge=0, description="Pagination offset (page * limit)"),
    task_type: str | None = Query(
        None, description="Filter by task type (blog_post, email, etc.)"
    ),
    sort_by: str = Query(
        "created_at",
        description="Sort field: created_at|quality_score|topic",
    ),
    sort_order: str = Query("desc", description="Sort order: asc|desc"),
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """Get all tasks awaiting human approval, with pagination and optional task_type filter."""
    try:
        get_operator_identity()  # Verify operator identity is resolvable

        try:
            result = await db_service.get_tasks_paginated(
                offset=offset,
                limit=limit,
                status="awaiting_approval",
                category=task_type,  # task_type is stored as category in database
            )
            # get_tasks_paginated returns tuple of (tasks, total)
            if isinstance(result, tuple):
                pending_tasks, total = result
            else:
                # Fallback for dict response format
                pending_tasks = result.get("tasks", []) if isinstance(result, dict) else []
                total = result.get("total", 0) if isinstance(result, dict) else 0
        except (ValueError, KeyError, AttributeError, TypeError, RuntimeError) as e:
            logger.error("Pending approval query failed: %s", e, exc_info=True)
            pending_tasks = []
            total = 0

        # Build response
        # Note: Database pagination is already applied by get_tasks_paginated
        # Don't recalculate total - use the database value

        if pending_tasks:
            # Ensure task_name is set from title column for API consistency
            for task in pending_tasks:
                if not task.get("task_name") and task.get("title"):
                    task["task_name"] = task["title"]

        logger.info("Pending approvals: %d total, returning %d", total, len(pending_tasks))

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "count": len(pending_tasks),
            "tasks": [
                {
                    "task_id": task.get("task_id") or task.get("id"),  # Try task_id first, then id
                    "task_name": task.get("title")
                    or task.get("task_name"),  # Title is the main column
                    "topic": task.get("topic"),
                    "task_type": task.get("task_type"),
                    "status": task.get("status"),
                    "created_at": task.get("created_at"),
                    "quality_score": task.get("quality_score"),  # Now in root level, not nested
                    "content_preview": (
                        task.get("content", "")[:200].replace("\n", " ")
                        if task.get("content")
                        else "No content available"
                    ),
                    "featured_image_url": task.get("featured_image_url"),
                    "metadata": task.get(
                        "task_metadata", {}
                    ),  # task_metadata is the main JSON column
                }
                for task in pending_tasks
            ],
        }

    except (ValueError, KeyError, AttributeError, TypeError, RuntimeError) as e:
        logger.error("Failed to fetch pending approvals: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch pending approvals",
        ) from e
