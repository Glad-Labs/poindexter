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

import json
import logging
import re as re_module
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4 as uuid_lib_uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from routes.auth_unified import get_current_user
from routes.websocket_routes import broadcast_approval_status
from services.database_service import DatabaseService
from services.error_handler import AppError
from utils.json_encoder import convert_decimals, safe_json_dumps
from utils.route_utils import get_database_dependency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["approval"])


# ============================================================================
# SCHEMAS
# ============================================================================


class ApprovalRequest(BaseModel):
    """Request body for approving a task"""

    approved: bool = Field(True, description="True to approve")
    feedback: Optional[str] = Field(None, description="Approval feedback")
    reviewer_notes: Optional[str] = Field(None, description="Reviewer notes")
    auto_publish: bool = Field(False, description="Automatically publish after approval")
    featured_image_url: Optional[str] = Field(None, description="Featured image URL")
    image_source: Optional[str] = Field(None, description="Image source")
    human_feedback: Optional[str] = Field(None, description="Human feedback (maps to feedback)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "approved": True,
                "auto_publish": True,
                "feedback": "Looks good!",
                "human_feedback": "Ready to publish",
            }
        }
    )


class RejectionRequest(BaseModel):
    """Request body for rejecting a task"""

    reason: str
    feedback: str
    allow_revisions: bool = True


class BulkApprovalRequest(BaseModel):
    """Request body for bulk approving tasks"""

    task_ids: List[str]
    feedback: Optional[str] = None
    reviewer_notes: Optional[str] = None


class BulkRejectionRequest(BaseModel):
    """Request body for bulk rejecting tasks"""

    task_ids: List[str]
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
        logger.info(f"[APPROVAL] approve_task called for task {task_id}")
        logger.info(f"[APPROVAL] request.auto_publish = {request.auto_publish!r}")

        # Map human_feedback to feedback if feedback is empty
        if not request.feedback and request.human_feedback:
            request.feedback = request.human_feedback
            logger.info(f"[APPROVAL] Mapped human_feedback to feedback")

        logger.info(f"[APPROVAL] User {current_user.get('id')} approving task {task_id}")
        logger.info(f"[APPROVAL] ApprovalRequest object: {request}")
        logger.info(
            f"[APPROVAL] Request: approved={request.approved}, auto_publish={request.auto_publish}, type={type(request.auto_publish)}"
        )
        logger.info(f"[APPROVAL] Bool check: auto_publish is True? {request.auto_publish is True}")
        logger.info(f"[APPROVAL] Bool check: auto_publish == True? {request.auto_publish == True}")
        logger.info(f"[APPROVAL] Bool check: bool(auto_publish)? {bool(request.auto_publish)}")
        logger.info(f"[APPROVAL] Has feedback: {bool(request.feedback)}")
        logger.info(f"[APPROVAL] Human feedback: {request.human_feedback}")

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

        # Update task status and approval fields
        approval_date = datetime.now(timezone.utc)

        # Store approval data in dedicated database columns
        await db_service.update_task(
            task_id,
            {
                "status": "approved",
                "approval_status": "approved",
                "approved_by": current_user.get("id"),
                "approval_timestamp": approval_date,
                "approval_notes": request.reviewer_notes or request.feedback,
                "human_feedback": request.feedback,
                "updated_at": approval_date.isoformat(),
            },
        )

        logger.info(f"[OK] [APPROVAL] Task {task_id} approved by {current_user.get('id')}")

        # Handle auto-publish if requested
        logger.info(f"[APPROVAL] ============================================")
        logger.info(f"[APPROVAL] AUTO-PUBLISH CHECK:")
        logger.info(f"[APPROVAL]   request.auto_publish = {request.auto_publish!r}")
        logger.info(f"[APPROVAL]   type = {type(request.auto_publish)}")
        logger.info(f"[APPROVAL]   is True = {request.auto_publish is True}")
        logger.info(f"[APPROVAL]   == True = {request.auto_publish == True}")
        logger.info(f"[APPROVAL]   bool() = {bool(request.auto_publish)}")
        logger.info(f"[APPROVAL]   if check will trigger? {request.auto_publish}")
        logger.info(f"[APPROVAL] ============================================")

        if request.auto_publish:
            logger.info(f"[APPROVAL] AUTO-PUBLISH TRIGGERED!")
            # Enforce minimum quality score for auto-publish
            quality_score = task.get("quality_score")
            MIN_AUTO_PUBLISH_QUALITY = 60  # below-this, require manual publish step
            if quality_score is not None and float(quality_score) < MIN_AUTO_PUBLISH_QUALITY:
                logger.warning(
                    f"[APPROVAL] Auto-publish blocked: quality_score={quality_score} < {MIN_AUTO_PUBLISH_QUALITY}"
                )
                return {
                    **{
                        "task_id": task_id,
                        "status": "approved",
                        "approval_status": "approved",
                        "approval_date": approval_date.isoformat(),
                        "approved_by": current_user.get("id"),
                        "feedback": request.feedback,
                        "message": "Task approved but not auto-published: quality score too low for automatic publishing",
                        "quality_score": quality_score,
                        "min_auto_publish_quality": MIN_AUTO_PUBLISH_QUALITY,
                        "next_action": "Review content and publish manually after quality improvements",
                    }
                }
            try:
                # Get task metadata for post creation
                task_metadata = task.get("task_metadata", {})
                if isinstance(task_metadata, str):
                    try:
                        task_metadata = json.loads(task_metadata) if task_metadata else {}
                    except (json.JSONDecodeError, TypeError):
                        task_metadata = {}
                elif task_metadata is None:
                    task_metadata = {}

                # Get task result
                task_result = task.get("result", {})
                if isinstance(task_result, str):
                    try:
                        task_result = json.loads(task_result) if task_result else {}
                    except (json.JSONDecodeError, TypeError):
                        task_result = {}
                elif task_result is None:
                    task_result = {}

                # Merge for all content data
                merged_result = {**task_metadata, **task_result}
                if request.featured_image_url:
                    merged_result["featured_image_url"] = request.featured_image_url

                # Extract needed fields for post creation
                topic = task.get("topic", "") or merged_result.get("topic", "")
                draft_content = (
                    merged_result.get("draft_content", "") or merged_result.get("content", "") or ""
                )
                seo_description = merged_result.get("seo_description", "")
                seo_keywords = merged_result.get("seo_keywords", [])
                featured_image = request.featured_image_url or merged_result.get(
                    "featured_image_url"
                )
                metadata = merged_result.get("metadata", {})

                # Extract title from content
                def extract_title_from_content(content: str) -> tuple:
                    if not content:
                        return None, content
                    match = re_module.match(r"^#+\s+(.+?)(?:\n|$)", content.strip())
                    if match:
                        title = match.group(1).strip()
                        cleaned_content = re_module.sub(
                            r"^#+\s+.+?(?:\n|$)", "", content.strip(), count=1
                        )
                        return title, cleaned_content.strip()
                    return None, content

                # Extract title
                extracted_title, cleaned_content = extract_title_from_content(draft_content)
                post_title = extracted_title or merged_result.get("title") or topic

                if cleaned_content and post_title:
                    # Create slug
                    slug = re_module.sub(r"[^\w\s-]", "", post_title).lower().replace(" ", "-")[:50]
                    slug = f"{slug}-{task_id[:8]}"

                    # Helper to parse SEO keywords
                    def parse_seo_keywords(keywords):
                        if isinstance(keywords, str):
                            try:
                                kw_list = json.loads(keywords)
                                if isinstance(kw_list, list):
                                    return ", ".join(str(kw).strip() for kw in kw_list if kw)
                                return keywords
                            except (json.JSONDecodeError, TypeError):
                                return keywords
                        elif isinstance(keywords, list):
                            return ", ".join(str(kw).strip() for kw in keywords if kw)
                        return ""

                    # Get or create author and category
                    from services.content_router_service import (
                        _get_or_create_default_author,
                        _select_category_for_topic,
                    )

                    author_id = await _get_or_create_default_author(db_service)
                    category_id = await _select_category_for_topic(post_title, db_service)

                    # Create post
                    post = await db_service.create_post(
                        {
                            "title": post_title,
                            "slug": slug,
                            "content": cleaned_content,
                            "excerpt": seo_description,
                            "featured_image_url": featured_image,
                            "author_id": author_id,
                            "category_id": category_id,
                            "status": "published",
                            "seo_title": post_title,
                            "seo_description": seo_description,
                            "seo_keywords": parse_seo_keywords(seo_keywords),
                            "metadata": metadata,
                        }
                    )
                    logger.info(
                        f"[OK] Post created: {post.id if hasattr(post, 'id') else post.get('id')}"
                    )

                    # Update task status to published and save post_id
                    post_id = str(post.id) if hasattr(post, "id") else str(post.get("id"))
                    publish_metadata = {
                        "published_at": datetime.utcnow().isoformat(),
                        "published_by": current_user.get("id"),
                        "post_id": post_id,
                        "post_slug": slug,
                        "published_url": f"/posts/{slug}",
                    }

                    final_result = convert_decimals(
                        {
                            **merged_result,
                            **publish_metadata,
                        }
                    )

                    await db_service.update_task_status(
                        task_id, "published", result=safe_json_dumps(final_result)
                    )
                    logger.info(f"[OK] Task {task_id} published with post_id: {post_id}")
            except Exception as e:
                logger.warning(f"[WARNING] Auto-publish failed: {str(e)}", exc_info=True)
                # Don't fail the approval if auto-publish fails

        # Broadcast approval status to connected WebSocket clients
        try:
            await broadcast_approval_status(
                task_id,
                "approved",
                {
                    "approved_by": current_user.get("id"),
                    "feedback": request.feedback,
                    "approval_date": approval_date.isoformat(),
                },
            )
        except Exception as e:
            logger.warning(f"Failed to broadcast approval status: {e}", exc_info=True)

        # Build response based on whether auto_publish happened
        response_data = {
            "task_id": task_id,
            "status": "published" if request.auto_publish else "approved",
            "approval_status": "approved",
            "approval_date": approval_date.isoformat(),
            "approval_timestamp": approval_date.isoformat(),
            "approved_by": current_user.get("id"),
            "feedback": request.feedback,
            "message": (
                "Task approved and published"
                if request.auto_publish
                else "Task approved for publishing"
            ),
            "next_action": (
                "Task is published"
                if request.auto_publish
                else "Task will be published by the publishing agent"
            ),
            "_debug_auto_publish_value": request.auto_publish,
            "_debug_auto_publish_type": str(type(request.auto_publish)),
            "_debug_auto_publish_bool": bool(request.auto_publish),
        }

        # If auto_publish was attempted, fetch the task to get post_id and post_slug
        if request.auto_publish:
            try:
                updated_task = await db_service.get_task(task_id)
                task_result = updated_task.get("result", {})
                if isinstance(task_result, str):
                    task_result = json.loads(task_result) if task_result else {}

                if task_result:
                    post_id = task_result.get("post_id")
                    post_slug = task_result.get("post_slug")
                    published_url = task_result.get("published_url")
                    if post_id:
                        response_data["post_id"] = post_id
                    if post_slug:
                        response_data["post_slug"] = post_slug
                    if published_url:
                        response_data["published_url"] = published_url
            except Exception as e:
                logger.warning(f"[WARNING] Could not fetch post_id from updated task: {e}", exc_info=True)

        return response_data

    except HTTPException:
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"❌ [APPROVAL] Failed to approve task {task_id}: {str(e)}", exc_info=True)
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
        logger.info(f"👤 [REJECTION] User {current_user.get('id')} rejecting task {task_id}")

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
        rejection_date = datetime.now(timezone.utc)

        # Update task with rejection metadata
        metadata_updates = {
            **(task.get("metadata") or {}),
            "rejection_date": rejection_date.isoformat(),
            "rejected_by": current_user.get("id"),
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
            f"❌ [REJECTION] Task {task_id} rejected by {current_user.get('id')} (reason: {request.reason})"
        )

        # Broadcast rejection status to connected WebSocket clients
        try:
            await broadcast_approval_status(
                task_id,
                "rejected",
                {
                    "rejected_by": current_user.get("id"),
                    "reason": request.reason,
                    "feedback": request.feedback,
                    "allow_revisions": request.allow_revisions,
                    "rejection_date": rejection_date.isoformat(),
                },
            )
        except Exception as e:
            logger.warning(f"Failed to broadcast rejection status: {e}", exc_info=True)

        return {
            "task_id": task_id,
            "status": final_status,
            "approval_status": "rejected",
            "rejection_date": rejection_date.isoformat(),
            "rejected_by": current_user.get("id"),
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
    except Exception as e:
        logger.error(f"❌ [REJECTION] Failed to reject task {task_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"message": f"Failed to reject task: {str(e)}", "type": "internal_error"},
        )


@router.post(
    "/bulk-approve",
    summary="Bulk approve multiple tasks",
    response_model=Dict[str, Any],
    status_code=200,
)
async def bulk_approve_tasks(
    request: BulkApprovalRequest,
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Approve multiple tasks in a single request.

    **Parameters:**
    - task_ids: List of task UUIDs to approve
    - feedback: Optional approval feedback (applies to all tasks)
    - reviewer_notes: Optional internal notes

    **Returns:**
    ```json
    {
      "approved_count": 5,
      "failed_count": 0,
      "total": 5,
      "successful_task_ids": ["uuid1", "uuid2", ...],
      "failed_task_ids": [],
      "message": "5 tasks approved"
    }
    ```

    **Limitations:**
    - Only approves tasks with status = "awaiting_approval"
    - Skips tasks that are already approved/rejected
    - Returns summary of successful and failed approvals
    """
    try:
        logger.info(
            f"👤 [BULK_APPROVAL] User {current_user.get('id')} bulk approving {len(request.task_ids)} tasks"
        )

        approved_count = 0
        failed_count = 0
        successful_ids = []
        failed_ids = []
        approval_date = datetime.now(timezone.utc).isoformat()

        for task_id in request.task_ids:
            try:
                # Fetch task
                task = await db_service.get_task(task_id)
                if not task:
                    failed_ids.append(task_id)
                    failed_count += 1
                    continue

                # Only approve awaiting_approval tasks
                if task.get("status") != "awaiting_approval":
                    failed_ids.append(task_id)
                    failed_count += 1
                    continue

                # Update task
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

                # Broadcast approval status
                try:
                    await broadcast_approval_status(
                        task_id,
                        "approved",
                        {
                            "approved_by": current_user.get("id"),
                            "feedback": request.feedback,
                            "approval_date": approval_date,
                        },
                    )
                except Exception as e:
                    logger.warning(f"Failed to broadcast approval for {task_id}: {e}", exc_info=True)

                successful_ids.append(task_id)
                approved_count += 1

            except Exception as e:
                logger.error(f"Failed to approve task {task_id}: {e}", exc_info=True)
                failed_ids.append(task_id)
                failed_count += 1

        logger.info(f"✅ [BULK_APPROVAL] Approved {approved_count} tasks, {failed_count} failed")

        return {
            "approved_count": approved_count,
            "failed_count": failed_count,
            "total": len(request.task_ids),
            "successful_task_ids": successful_ids,
            "failed_task_ids": failed_ids,
            "message": f"{approved_count} tasks approved, {failed_count} failed",
        }

    except Exception as e:
        logger.error(f"❌ [BULK_APPROVAL] Failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"message": f"Bulk approval failed: {str(e)}", "type": "internal_error"},
        )


@router.post(
    "/bulk-reject",
    summary="Bulk reject multiple tasks",
    response_model=Dict[str, Any],
    status_code=200,
)
async def bulk_reject_tasks(
    request: BulkRejectionRequest,
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Reject multiple tasks in a single request.

    **Parameters:**
    - task_ids: List of task UUIDs to reject
    - reason: Rejection reason (applies to all tasks)
    - feedback: Rejection feedback (applies to all tasks)
    - allow_revisions: Whether to allow revisions (applies to all tasks)

    **Returns:**
    ```json
    {
      "rejected_count": 3,
      "failed_count": 0,
      "total": 3,
      "successful_task_ids": ["uuid1", "uuid2", "uuid3"],
      "failed_task_ids": [],
      "message": "3 tasks rejected"
    }
    ```
    """
    try:
        logger.info(
            f"👤 [BULK_REJECTION] User {current_user.get('id')} bulk rejecting {len(request.task_ids)} tasks"
        )

        rejected_count = 0
        failed_count = 0
        successful_ids = []
        failed_ids = []
        rejection_date = datetime.now(timezone.utc).isoformat()
        final_status = "failed_revisions_requested" if request.allow_revisions else "failed"

        for task_id in request.task_ids:
            try:
                # Fetch task
                task = await db_service.get_task(task_id)
                if not task:
                    failed_ids.append(task_id)
                    failed_count += 1
                    continue

                # Only reject awaiting_approval tasks
                if task.get("status") != "awaiting_approval":
                    failed_ids.append(task_id)
                    failed_count += 1
                    continue

                # Update task
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

                # Broadcast rejection status
                try:
                    await broadcast_approval_status(
                        task_id,
                        "rejected",
                        {
                            "rejected_by": current_user.get("id"),
                            "reason": request.reason,
                            "feedback": request.feedback,
                            "allow_revisions": request.allow_revisions,
                            "rejection_date": rejection_date,
                        },
                    )
                except Exception as e:
                    logger.warning(f"Failed to broadcast rejection for {task_id}: {e}", exc_info=True)

                successful_ids.append(task_id)
                rejected_count += 1

            except Exception as e:
                logger.error(f"Failed to reject task {task_id}: {e}", exc_info=True)
                failed_ids.append(task_id)
                failed_count += 1

        logger.info(f"✅ [BULK_REJECTION] Rejected {rejected_count} tasks, {failed_count} failed")

        return {
            "rejected_count": rejected_count,
            "failed_count": failed_count,
            "total": len(request.task_ids),
            "successful_task_ids": successful_ids,
            "failed_task_ids": failed_ids,
            "message": f"{rejected_count} tasks rejected, {failed_count} failed",
        }

    except Exception as e:
        logger.error(f"❌ [BULK_REJECTION] Failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"message": f"Bulk rejection failed: {str(e)}", "type": "internal_error"},
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

        # Fetch pending tasks from database with pagination
        # Use get_tasks_paginated which handles status filtering and pagination
        user_id = current_user.get("id") if current_user else None

        try:
            result = await db_service.get_tasks_paginated(
                offset=offset,
                limit=limit,
                status="awaiting_approval",
                category=task_type,  # task_type is stored as category in database
                user_id=user_id,
            )
            # get_tasks_paginated returns tuple of (tasks, total)
            if isinstance(result, tuple):
                pending_tasks, total = result
            else:
                # Fallback for dict response format
                pending_tasks = result.get("tasks", []) if isinstance(result, dict) else []
                total = result.get("total", 0) if isinstance(result, dict) else 0
        except Exception as e:
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

    except Exception as e:
        logger.error(
            f"❌ [PENDING_APPROVAL] Failed to fetch pending approvals: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "message": f"Failed to fetch pending approvals: {str(e)}",
                "type": "internal_error",
            },
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
    except AppError:
        raise
    except Exception as e:
        logger.error(f"❌ Failed to get approval status: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get approval status: {str(e)}")


# ============================================================================
# DEBUG/TEST ENDPOINTS (removed)
