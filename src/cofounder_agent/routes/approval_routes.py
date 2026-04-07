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
from typing import Any, Dict, Optional

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
    task_id: str, status: str, details: Optional[Dict] = None
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
    response_model=Dict[str, Any],
    status_code=200,
)
async def reject_task(
    task_id: str,
    request: RejectionRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
       Reject a task and send it back for revisions.

       **Parameters:**
       - task_id: UUID of the task to reject
       - reason: Short reason (e.g., "Content quality", "Factual errors",
    "Tone mismatch")
       - feedback: Detailed feedback for the content team
       - allow_revisions: true/false - allow re-submission with revisions

       **Returns:**
       ```json
       {
         "task_id": "uuid",
         "status": "failed",
         "rejection_date": "2026-01-21T...",
         "rejected_by": "user_id",
         "reason": "Content quality",
         "message": "Task rejected - feedback provided"
       }
       ```

       **Status Transitions:**
       - awaiting_approval → failed ❌
       - Other statuses → 400 Bad Request

       **Side Effects:**
       - Task marked as failed/rejected
       - Rejection reason stored in metadata
       - Rejection feedback stored
       - Rejection timestamp recorded
       - Task removed from publishing queue
       - Task appears in failed/archived section

       **Revision Workflow (Optional):**
       If allow_revisions=true:
       - Task status: "failed_revisions_requested"
       - Content team receives email: "Task rejected, revisions requested"
       - User can edit + resubmit
       - Returns to awaiting_approval on resubmission
    """
    try:
        operator = get_operator_identity()
        logger.info(f"👤 [REJECTION] User {operator['id']} rejecting task {task_id}")

        # Fetch task
        task = await db_service.get_task(task_id)
        if not task:
            logger.warning(f"❌ [REJECTION] Task not found: {task_id}")
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Verify task is awaiting approval
        current_status = task.get("status")
        if current_status != "awaiting_approval":
            logger.warning(
                f"❌ [REJECTION] Task {task_id} has status '{current_status}', not awaiting_approval"
            )
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reject task with status '{current_status}' — expected 'awaiting_approval'",
            )

        # Determine final status based on revision allowance
        final_status = "failed_revisions_requested" if request.allow_revisions else "failed"
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

        logger.info(
            f"❌ [REJECTION] Task {task_id} rejected by {operator['id']} (reason: {request.reason})"
        )

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
            logger.warning(f"Failed to broadcast rejection status: {e}", exc_info=True)

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
        logger.error(f"❌ [REJECTION] Failed to reject task {task_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to reject task",
        )


@router.get(
    "/pending-approval",
    summary="List all tasks awaiting approval",
    response_model=Dict[str, Any],
    status_code=200,
)
async def get_pending_approvals(
    limit: int = Query(20, ge=1, le=100, description="Results per page (1-100)"),
    offset: int = Query(0, ge=0, description="Pagination offset (page * limit)"),
    task_type: Optional[str] = Query(
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
    """
    Get all tasks awaiting human approval.

    **Query Parameters:**
    - limit: Number of results per page (default 20, max 100)
    - offset: Pagination offset (0 = first page, 20 = second page)
    - task_type: Filter by task type (optional)
    - sort_by: Sort by created_at|quality_score|topic (default: created_at)
    - sort_order: asc|desc (default: desc = newest first)

    **Returns:**
    ```json
    {
      "total": 5,
      "limit": 20,
      "offset": 0,
      "count": 5,
      "tasks": [
        {
          "task_id": "uuid",
          "task_name": "Blog Post: AI Trends",
          "topic": "AI Trends",
          "task_type": "blog_post",
          "status": "awaiting_approval",
          "created_at": "2026-01-21T10:30:00Z",
          "quality_score": 8.5,
          "content_preview": "Lorem ipsum dolor sit amet...",
          "featured_image_url": "https://pexels.com/...",
          "metadata": { ... }
        }
      ]
    }
    ```

    **Filters:**
    - Only returns tasks with status = "awaiting_approval"
    - Owner matches current_user or is admin
    - Optionally filtered by task_type

    **Sorting:**
    - created_at: Newest tasks first (default)
    - quality_score: Highest quality first
    - topic: Alphabetical (A-Z)

    **Use Cases:**
    - Dashboard: Load first 20 pending tasks (limit=20, offset=0)
    - Next page: Same request with offset=20, 40, etc.
    - Filter: Only blog posts - task_type=blog_post
    - Sort: By quality - sort_by=quality_score&sort_order=desc
    """
    try:
        operator = get_operator_identity()
        logger.info(
            f"📋 [PENDING_APPROVAL] User {operator['id']} fetching pending approvals"
        )

        # Fetch pending tasks from database with pagination
        # Use get_tasks_paginated which handles status filtering and pagination
        user_id = operator["id"]

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
            logger.error(f"❌ [PENDING_APPROVAL] Database query failed: {e}", exc_info=True)
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

        logger.info(f"📋 [PENDING_APPROVAL] Found {total} tasks, returning {len(pending_tasks)}")

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
        logger.error(
            f"❌ [PENDING_APPROVAL] Failed to fetch pending approvals: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch pending approvals",
        )
