"""
Approval Routes - Content Approval Workflow

Handles human approval of generated content before publishing.

Endpoints:
- POST /api/tasks/{task_id}/approve - Approve a task for publishing
- POST /api/tasks/{task_id}/reject - Reject a task with feedback  
- GET /api/tasks/pending-approval - List all tasks awaiting approval

Workflow:
1. Task reaches end of orchestrator pipeline
2. Status set to "awaiting_approval"
3. User views in ApprovalQueue UI
4. User clicks Approve/Reject
5. Route updates task status + stores feedback
6. Task can proceed to publishing (if approved)
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from uuid import uuid4 as uuid_lib_uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from middleware.auth import get_current_user
from services.database_service import DatabaseService, get_database_dependency


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["approval"])


# ============================================================================
# SCHEMAS
# ============================================================================


class ApprovalRequest(BaseModel):
    """Request body for approving a task"""

    approved: bool = True
    feedback: Optional[str] = None
    reviewer_notes: Optional[str] = None


class RejectionRequest(BaseModel):
    """Request body for rejecting a task"""

    reason: str
    feedback: str
    allow_revisions: bool = True


class PendingApprovalResponse(BaseModel):
    """Response for a task awaiting approval"""

    task_id: str
    task_name: str
    topic: str
    task_type: str
    status: str
    created_at: str
    quality_score: Optional[float] = None
    content_preview: Optional[str] = None
    featured_image_url: Optional[str] = None
    metadata: Dict[str, Any] = {}


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.post(
    "/{task_id}/approve",
    summary="Approve a task for publishing",
    response_model=Dict[str, Any],
    status_code=200,
)
async def approve_task(
    task_id: str,
    request: ApprovalRequest,
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Approve a task that's awaiting review.

    **Parameters:**
    - task_id: UUID of the task to approve
    - approved: true (automatically set by form)
    - feedback: Optional approval notes/feedback
    - reviewer_notes: Optional internal notes

    **Returns:**
    ```json
    {
      "task_id": "uuid",
      "status": "approved",
      "approval_date": "2026-01-21T...",
      "approved_by": "user_id",
      "message": "Task approved for publishing"
    }
    ```

    **Status Transitions:**
    - awaiting_approval → approved ✅
    - Other statuses → 400 Bad Request

    **Side Effects:**
    - Task marked as approved
    - Approval feedback stored in metadata
    - Approval timestamp recorded
    - Task eligible for publishing
    """
    try:
        logger.info(
            f"👤 [APPROVAL] User {current_user.get('id')} approving task {task_id}"
        )

        # Fetch task from database
        task = await db_service.get_task(task_id)
        if not task:
            logger.warning(f"❌ [APPROVAL] Task not found: {task_id}")
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Verify task is awaiting approval
        current_status = task.get("status")
        if current_status != "awaiting_approval":
            logger.warning(
                f"❌ [APPROVAL] Task {task_id} has status '{current_status}', not awaiting_approval"
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "message": f"Cannot approve task with status '{current_status}'",
                    "current_status": current_status,
                    "expected": "awaiting_approval",
                },
            )

        # Update task status
        approval_date = datetime.now(timezone.utc).isoformat()
        metadata_updates = {
            **(task.get("metadata") or {}),
            "approval_date": approval_date,
            "approved_by": current_user.get("id"),
            "approval_status": "approved",
            "approval_feedback": request.feedback,
            "approval_notes": request.reviewer_notes,
        }

        await db_service.update_task(
            task_id,
            {
                "status": "approved",
                "metadata": metadata_updates,
                "updated_at": approval_date,
            },
        )

        logger.info(f"✅ [APPROVAL] Task {task_id} approved by {current_user.get('id')}")

        return {
            "task_id": task_id,
            "status": "approved",
            "approval_date": approval_date,
            "approved_by": current_user.get("id"),
            "feedback": request.feedback,
            "message": "Task approved for publishing",
            "next_action": f"Task will be published by the publishing agent",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"❌ [APPROVAL] Failed to approve task {task_id}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"message": f"Failed to approve task: {str(e)}", "type": "internal_error"},
        )


@router.post(
    "/{task_id}/reject",
    summary="Reject a task with feedback",
    response_model=Dict[str, Any],
    status_code=200,
)
async def reject_task(
    task_id: str,
    request: RejectionRequest,
    current_user: dict = Depends(get_current_user),
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
        logger.info(
            f"👤 [REJECTION] User {current_user.get('id')} rejecting task {task_id}"
        )

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
                detail={
                    "message": f"Cannot reject task with status '{current_status}'",
                    "current_status": current_status,
                    "expected": "awaiting_approval",
                },
            )

        # Determine final status based on revision allowance
        final_status = "failed_revisions_requested" if request.allow_revisions else "failed"
        rejection_date = datetime.now(timezone.utc).isoformat()

        # Update task with rejection metadata
        metadata_updates = {
            **(task.get("metadata") or {}),
            "rejection_date": rejection_date,
            "rejected_by": current_user.get("id"),
            "rejection_reason": request.reason,
            "rejection_feedback": request.feedback,
            "allow_revisions": request.allow_revisions,
        }

        await db_service.update_task(
            task_id,
            {
                "status": final_status,
                "metadata": metadata_updates,
                "updated_at": rejection_date,
            },
        )

        logger.info(
            f"❌ [REJECTION] Task {task_id} rejected by {current_user.get('id')} (reason: {request.reason})"
        )

        return {
            "task_id": task_id,
            "status": final_status,
            "rejection_date": rejection_date,
            "rejected_by": current_user.get("id"),
            "reason": request.reason,
            "feedback": request.feedback,
            "allow_revisions": request.allow_revisions,
            "message": f"Task rejected - {request.reason}. {'Revisions can be requested.' if request.allow_revisions else 'Task archived (no revisions).'}",
            "next_action": "Task removed from publishing queue. Content team can review feedback and make revisions if allowed.",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"❌ [REJECTION] Failed to reject task {task_id}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"message": f"Failed to reject task: {str(e)}", "type": "internal_error"},
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
    task_type: Optional[str] = Query(None, description="Filter by task type (blog_post, email, etc.)"),        sort_by: str = Query("created_at", description="Sort field: created_at|quality_score|topic",),
    sort_order: str = Query("desc", description="Sort order: asc|desc"),
    current_user: dict = Depends(get_current_user),
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
        logger.info(
            f"📋 [PENDING_APPROVAL] User {current_user.get('id')} fetching pending approvals"
        )

        # Fetch pending tasks from database
        # This is a placeholder - actual implementation uses database_service
        # For now, we'll calculate from all tasks with "awaiting_approval" status
        
        # Build query filter
        filters = {
            "status": "awaiting_approval",
            "user_id": current_user.get("id"),  # Only user's own tasks (or admin sees all)
        }
        if task_type:
            filters["task_type"] = task_type

        # Fetch all matching tasks (simplified - real impl uses DB query)
        # For now, returning empty list - will implement when database methods ready
        pending_tasks = []  # TODO: Use db_service.query_tasks(filters) when available

        # Build response
        total = len(pending_tasks) if pending_tasks else 0
        
        # Simple in-memory pagination and sorting
        if pending_tasks:
            # Sort
            if sort_by == "quality_score":
                pending_tasks.sort(
                    key=lambda t: t.get("metadata", {}).get("quality_score", 0),
                    reverse=(sort_order == "desc"),
                )
            elif sort_by == "topic":
                pending_tasks.sort(
                    key=lambda t: t.get("topic", ""),
                    reverse=(sort_order == "desc"),
                )
            else:  # created_at (default)
                pending_tasks.sort(
                    key=lambda t: t.get("created_at", ""),
                    reverse=(sort_order == "desc"),
                )

            # Paginate
            pending_tasks = pending_tasks[offset : offset + limit]

        logger.info(
            f"📋 [PENDING_APPROVAL] Found {total} tasks, returning {len(pending_tasks)}"
        )

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "count": len(pending_tasks),
            "tasks": [
                {
                    "task_id": task.get("id"),
                    "task_name": task.get("task_name"),
                    "topic": task.get("topic"),
                    "task_type": task.get("task_type"),
                    "status": task.get("status"),
                    "created_at": task.get("created_at"),
                    "quality_score": task.get("metadata", {}).get("quality_score"),
                    "content_preview": (
                        task.get("metadata", {})
                        .get("content", "")[:200]
                        .replace("\n", " ")
                    ),
                    "featured_image_url": task.get("metadata", {}).get("featured_image_url"),
                    "metadata": task.get("metadata", {}),
                }
                for task in pending_tasks
            ],
        }

    except Exception as e:
        logger.error(
            f"❌ [PENDING_APPROVAL] Failed to fetch pending approvals: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"message": f"Failed to fetch pending approvals: {str(e)}", "type": "internal_error"},
        )


# ============================================================================
# HELPER ENDPOINTS (Optional)
# ============================================================================


@router.get(
    "/{task_id}/approval-status",
    summary="Get approval status for a specific task",
    response_model=Dict[str, Any],
    status_code=200,
)
async def get_task_approval_status(
    task_id: str,
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Get the approval status for a specific task.

    **Returns:**
    ```json
    {
      "task_id": "uuid",
      "status": "awaiting_approval|approved|failed",
      "approval_date": "2026-01-21T...",
      "approved_by": "user_id",
      "rejection_reason": null,
      "can_be_approved": true
    }
    ```
    """
    try:
        task = await db_service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        metadata = task.get("metadata", {})
        return {
            "task_id": task_id,
            "status": task.get("status"),
            "approval_date": metadata.get("approval_date"),
            "approved_by": metadata.get("approved_by"),
            "approval_feedback": metadata.get("approval_feedback"),
            "rejection_reason": metadata.get("rejection_reason"),
            "rejection_feedback": metadata.get("rejection_feedback"),
            "can_be_approved": task.get("status") == "awaiting_approval",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Failed to get approval status: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get approval status: {str(e)}")
