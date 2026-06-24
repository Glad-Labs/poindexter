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

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from middleware.api_token_auth import get_operator_identity, verify_api_token
from schemas.task_schemas import PendingApprovalListResponse
from services.database_service import DatabaseService
from services.error_handler import AppError
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency
from utils.uuid_prefix import resolve_task_id_prefix

# Stable gate name for the legacy `awaiting_approval` HITL flow.
# All route-level approval/rejection writes funnel through this gate so
# the unified `pipeline_gate_history` audit trail can distinguish them
# from the gate-aware HITL flows seeded by `services/approval_service.py`
# (which use per-stage names like `topic_decision`, `final_media`, etc.).
LEGACY_APPROVAL_GATE = "final_approval"

logger = get_logger(__name__)
router = APIRouter(
    prefix="/api/tasks",
    tags=["approval"],
    # Operator surface — auth enforced on every route (poindexter#752 item 2).
    dependencies=[Depends(verify_api_token)],
)


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

        # Fetch task. db_service.get_task accepts a UUID prefix (LIKE match)
        # so the operator-friendly `0bc9badd` form resolves a row here.
        task = await db_service.get_task(task_id)
        if not task:
            # get_task collapses an ambiguous prefix to None — re-probe so an
            # ambiguous paste 409s ("use a longer prefix") instead of a
            # misleading 404. A true miss / full UUID / numeric id passes
            # through to the 404 below.
            await resolve_task_id_prefix(db_service.pool, task_id)
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Canonicalize the id BEFORE any downstream write. update_task and
        # the pipeline_gate_history INSERT require the full UUID — they
        # use exact-match WHERE clauses, so handing them the URL-path
        # prefix silently rolls the transaction back and leaves the task
        # in awaiting_approval. Preserved from b87dc38d.
        full_task_id = str(task.get("task_id") or task.get("id") or task_id)

        # Verify task is awaiting approval
        current_status = task.get("status")
        if current_status != "awaiting_approval":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot reject task with status '{current_status}' — expected 'awaiting_approval'",
            )

        # Determine final status based on revision allowance
        final_status = "rejected_retry" if request.allow_revisions else "rejected_final"
        rejection_date = datetime.now(timezone.utc)

        # Update task with rejection metadata
        metadata_updates: dict[str, Any] = {
            **(task.get("metadata") or {}),
            "rejection_date": rejection_date.isoformat(),
            "rejected_by": operator["id"],
            "rejection_reason": request.reason,
            "rejection_feedback": request.feedback,
            "allow_revisions": request.allow_revisions,
        }

        await db_service.update_task(
            full_task_id,
            {
                "status": final_status,
                "approval_status": "rejected",
                "human_feedback": request.feedback,
                "metadata": metadata_updates,
                "updated_at": rejection_date.isoformat(),
            },
        )

        # Record the rejection on pipeline_gate_history so `content_tasks`
        # view's approval_status / approved_by / human_feedback columns
        # resolve non-NULL. The view's scalar subqueries pull the latest
        # row per task. event_kind reflects the revision policy so the
        # learning signal can distinguish `rejected_retry` (regen) from
        # `rejected_final` (closed out).
        event_kind = "rejected_retry" if request.allow_revisions else "rejected_final"
        try:
            await db_service.pool.execute(
                """
                INSERT INTO pipeline_gate_history
                    (task_id, gate_name, event_kind, feedback, actor, metadata)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """,
                full_task_id,
                LEGACY_APPROVAL_GATE,
                event_kind,
                request.feedback,
                operator.get("id") or "human",
                json.dumps(
                    {
                        "reviewer": operator["id"],
                        "reason": request.reason,
                        "allow_revisions": request.allow_revisions,
                        "decision": "rejected",
                    },
                    default=str,
                ),
            )
        except Exception as review_err:
            logger.warning(
                "[reject_task] pipeline_gate_history write failed for %s: %s",
                full_task_id, review_err,
            )

        # Outcome → variant-weight feedback loop (#361 part 1). The reject
        # path was the gap: before this it did NO atom_runs.decision backfill,
        # so a rejected run never became negative training signal. This
        # backfills the decision AND nudges the variant weight(s) down.
        # Best-effort — never breaks the rejection.
        try:
            from services.router_outcome_feedback import record_task_outcome

            await record_task_outcome(
                pool=db_service.pool,
                task_id=full_task_id,
                decision="rejected",
            )
        except Exception as rfb_err:  # noqa: BLE001
            logger.debug(
                "[reject_task] router outcome feedback failed: %s", rfb_err,
            )

        try:
            await db_service.mark_model_performance_outcome(
                full_task_id, human_approved=False,
            )
        except Exception as mp_err:
            logger.debug(
                "[reject_task] mark_model_performance_outcome failed: %s", mp_err,
            )

        logger.info("Task %s rejected by %s: %s", full_task_id, operator['id'], request.reason)

        # Broadcast rejection status to connected WebSocket clients
        try:
            await broadcast_approval_status(
                full_task_id,
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
            "task_id": full_task_id,
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
    response_model=PendingApprovalListResponse,
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

        # A DB failure here must propagate to the outer handler (→ 5xx), NOT be
        # swallowed into an empty 200. This is the primary HITL surface: making
        # 'database down' indistinguishable from 'healthy and nothing pending'
        # lets work pile up silently unreviewed (poindexter#744, fail-loud).
        result = await db_service.get_tasks_paginated(
            offset=offset,
            limit=limit,
            status="awaiting_approval",
            category=task_type,  # task_type is stored as category in database
            light=True,  # this list only renders a 200-char content_preview (#619)
        )
        # get_tasks_paginated returns tuple of (tasks, total)
        if isinstance(result, tuple):
            pending_tasks, total = result
        else:
            # Fallback for dict response format
            pending_tasks = result.get("tasks", []) if isinstance(result, dict) else []
            total = result.get("total", 0) if isinstance(result, dict) else 0

        # Build response
        # Note: Database pagination is already applied by get_tasks_paginated
        # Don't recalculate total - use the database value

        if pending_tasks:
            # Ensure task_name is set from title column for API consistency
            for task in pending_tasks:
                if not task.get("task_name") and task.get("title"):
                    task["task_name"] = task["title"]

        logger.info("Pending approvals: %d total, returning %d", total, len(pending_tasks))

        # Canonical offset envelope (poindexter#745): {items, total, limit,
        # offset} via the typed PendingApprovalListResponse. The legacy ``tasks``
        # key became ``items`` and the redundant ``count`` (= len(items)) was
        # dropped; the operator console reads ``items`` in lockstep.
        return PendingApprovalListResponse(
            total=total,
            limit=limit,
            offset=offset,
            # Pydantic validates each projection dict into a PendingApprovalItem.
            items=[
                {  # type: ignore[misc]
                    "task_id": task.get("task_id") or task.get("id"),  # Try task_id first, then id
                    "task_name": task.get("title")
                    or task.get("task_name"),  # Title is the main column
                    "topic": task.get("topic"),
                    "task_type": task.get("task_type"),
                    "status": task.get("status"),
                    "created_at": task.get("created_at"),
                    "quality_score": task.get("quality_score"),  # Now in root level, not nested
                    "content_preview": (
                        content[:200].replace("\n", " ")
                        if (content := task.get("content"))
                        else "No content available"
                    ),
                    "featured_image_url": task.get("featured_image_url"),
                    "metadata": task.get(
                        "task_metadata", {}
                    ),  # task_metadata is the main JSON column
                }
                for task in pending_tasks
            ],
        )

    except (ValueError, KeyError, AttributeError, TypeError, RuntimeError) as e:
        logger.error("Failed to fetch pending approvals: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch pending approvals",
        ) from e
