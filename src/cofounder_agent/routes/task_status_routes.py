"""
Task Status Routes - Status transitions, history, and content updates.

Sub-router for task_routes.py. Handles:
- PUT /{task_id}/status — Enterprise status update with validation
- PUT /{task_id}/status/validated — Enhanced status update with audit trail
- GET /{task_id}/status — Detailed status information
- GET /{task_id}/status-history — Status change audit trail
- GET /{task_id}/status-history/failures — Validation failure records
- PATCH /{task_id} — Legacy task update
- PATCH /{task_id}/content — Edit task content fields
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from middleware.api_token_auth import verify_api_token
from routes.task_routes import _check_task_ownership, _normalize_seo_keywords_in_task
from schemas.model_converter import ModelConverter
from schemas.task_status_schemas import (
    TaskStatusInfo,
    TaskStatusUpdateRequest,
    TaskStatusUpdateResponse,
)
from schemas.unified_task_response import UnifiedTaskResponse
from services.database_service import DatabaseService
from services.enhanced_status_change_service import EnhancedStatusChangeService
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency
from utils.task_status import TaskStatus, get_allowed_transitions, is_terminal, is_valid_transition

logger = get_logger(__name__)

status_router = APIRouter(tags=["Task Status Management"])


@status_router.put(
    "/{task_id}/status",
    response_model=TaskStatusUpdateResponse,
    summary="Update task status with enterprise validation",
    tags=["Task Status Management"],
)
async def update_task_status_enterprise(
    task_id: str,
    update_data: TaskStatusUpdateRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    **Enterprise-level task status update with validation and audit trail.**

    Updates task status with comprehensive validation including:
    - Valid transition checking (prevents invalid workflows)
    - Audit trail recording (tracks all status changes)
    - Timestamp management (tracks when status changed)
    - User attribution (tracks who made the change)

    **Parameters:**
    - task_id: Task UUID
    - status: Target status (pending, in_progress, awaiting_approval, approved, published, failed, on_hold, rejected, cancelled)
    - updated_by: User/system identifier (optional, defaults to current user)
    - reason: Change reason for audit trail (optional)
    - metadata: Additional metadata for change (optional)

    **Returns:**
    - Success response with old/new status and timestamp

    **Status Transitions:**
    ```
    pending → in_progress, failed, cancelled
    in_progress → awaiting_approval, failed, on_hold, cancelled
    awaiting_approval → approved, rejected, in_progress, cancelled
    approved → published, on_hold, cancelled
    published → on_hold (terminal state)
    failed → pending, cancelled
    on_hold → in_progress, cancelled
    rejected → in_progress, cancelled
    cancelled → (no transitions - terminal state)
    ```

    **Example cURL:**
    ```bash
    curl -X PUT "http://localhost:8000/api/tasks/{task_id}/status" \
      -H "Authorization: Bearer TOKEN" \
      -H "Content-Type: application/json" \
      -d '{
        "status": "awaiting_approval",
        "reason": "Content generation completed",
        "metadata": {"quality_score": 8.5}
      }'
    ```

    **Error Responses:**
    - 404: Task not found
    - 422: Invalid status transition
    - 400: Invalid input data
    """
    try:
        # Validate UUID format
        try:
            UUID(task_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid task ID format: {task_id}",
            ) from exc

        # Fetch current task
        task = await db_service.get_task(task_id)
        if not task:
            raise HTTPException(
                status_code=404,
                detail=f"Task not found: {task_id}",
            )

        # Ownership check
        if isinstance(task, dict):
            _check_task_ownership(task, token)

        # Get current and target status
        current_status_str = task.get("status", "pending")
        target_status_str = update_data.status

        try:
            current_status = TaskStatus(current_status_str)
            target_status = TaskStatus(target_status_str)
        except ValueError as e:
            raise HTTPException(
                status_code=422,
                detail="Invalid status value",
            ) from e

        # Validate transition — 409 Conflict, not 422 (the request body is valid;
        # the current resource state prevents the transition)
        if not is_valid_transition(current_status, target_status):
            allowed = get_allowed_transitions(current_status)
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot transition from '{current_status.value}' to '{target_status.value}'. "
                    f"Allowed transitions from '{current_status.value}': {', '.join(sorted(allowed)) or 'none'}"
                ),
            )

        # Prepare update dictionary
        now = datetime.now(timezone.utc)
        updated_by = update_data.updated_by or (
            "operator"
        )

        update_dict = {
            "status": target_status.value,
            "status_updated_at": now,
            "status_updated_by": updated_by,
        }

        # Handle timestamps based on target status
        if target_status == TaskStatus.IN_PROGRESS and not task.get("started_at"):
            update_dict["started_at"] = now

        if is_terminal(target_status) and not task.get("completed_at"):
            update_dict["completed_at"] = now

        # Merge metadata if provided
        if update_data.metadata:
            existing_metadata = task.get("task_metadata") or {}
            if isinstance(existing_metadata, str):
                existing_metadata = json.loads(existing_metadata)
            update_dict["task_metadata"] = {**existing_metadata, **update_data.metadata}

        # Update task in database
        await db_service.update_task(task_id, update_dict)

        # Return success response
        return TaskStatusUpdateResponse(
            task_id=task_id,
            old_status=current_status.value,
            new_status=target_status.value,
            timestamp=now,
            updated_by=updated_by,
            message=f"Status updated successfully: {current_status.value} → {target_status.value}",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating task status for {task_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to update task status",
        ) from e


@status_router.put(
    "/{task_id}/status/validated",
    response_model=Dict[str, Any],
    summary="Update task status with enhanced validation and audit trail",
    tags=["Task Status Management"],
)
async def update_task_status_validated(
    task_id: str,
    update_data: TaskStatusUpdateRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    status_service: EnhancedStatusChangeService = Depends(
        lambda: (
            __import__(
                "utils.route_utils", fromlist=["get_enhanced_status_change_service"]
            ).get_enhanced_status_change_service()
        )
    ),
):
    """
    **Enhanced task status update with comprehensive validation and audit trail.**

    This endpoint provides enterprise-level status management with:
    - Comprehensive transition validation
    - Full audit trail logging
    - Validation error tracking
    - Context-aware validations

    **Parameters:**
    - task_id: Task ID
    - status: New status
    - updated_by: Optional user identifier
    - reason: Optional change reason
    - metadata: Optional metadata context

    **Returns:**
    - success: Whether update succeeded
    - message: Result message
    - errors: List of validation errors (empty if successful)

    **Example Request:**
    ```json
    {
      "status": "awaiting_approval",
      "updated_by": "user@example.com",
      "reason": "Content generation completed successfully",
      "metadata": {
        "quality_score": 8.5,
        "validation_context": {"ai_model": "claude-3"}
      }
    }
    ```
    """
    try:
        # Ownership check: verify user owns this task
        task = await db_service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if isinstance(task, dict):
            _check_task_ownership(task, token)

        # Get user ID
        user_id = "operator"

        # Validate and execute status change
        success, message, errors = await status_service.validate_and_change_status(
            task_id=task_id,
            new_status=update_data.status,
            reason=update_data.reason,
            metadata=update_data.metadata,
            user_id=user_id,
        )

        return {
            "success": success,
            "task_id": task_id,
            "message": message,
            "errors": errors,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "updated_by": user_id,
        }

    except Exception as e:
        logger.error(f"Error in enhanced status update for {task_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to update task status",
        ) from e


@status_router.get(
    "/{task_id}/status",
    response_model=TaskStatusInfo,
    summary="Get detailed status information for a task",
    tags=["Task Status Management"],
)
async def get_task_status_info(
    task_id: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    **Get comprehensive status information for a task.**

    Retrieves detailed status metadata including:
    - Current status and change timestamp
    - Valid next transitions
    - Whether status is terminal
    - Elapsed time tracking

    **Parameters:**
    - task_id: Task UUID

    **Returns:**
    - Comprehensive status information with allowed transitions

    **Example cURL:**
    ```bash
    curl -X GET "http://localhost:8000/api/tasks/{task_id}/status" \
      -H "Authorization: Bearer TOKEN"
    ```
    """
    try:
        # Validate UUID format
        try:
            UUID(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid task ID format") from exc

        # Fetch task
        task = await db_service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        # Ownership check
        if isinstance(task, dict):
            _check_task_ownership(task, token)

        # Parse status
        status_str = task.get("status", "pending")
        try:
            status = TaskStatus(status_str)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid status in database: {status_str}") from exc

        # Calculate duration
        status_updated_at = task.get("status_updated_at")
        duration_minutes = None
        if status_updated_at:
            if isinstance(status_updated_at, str):
                status_updated_at = datetime.fromisoformat(status_updated_at.replace("Z", "+00:00"))
            duration_minutes = (datetime.now(timezone.utc) - status_updated_at).total_seconds() / 60

        # Get allowed transitions
        allowed_transitions = sorted(get_allowed_transitions(status))

        return TaskStatusInfo(
            task_id=task_id,
            current_status=status.value,
            status_updated_at=status_updated_at or task.get("created_at"),  # type: ignore[arg-type]
            status_updated_by=task.get("status_updated_by"),
            created_at=task.get("created_at"),  # type: ignore[arg-type]
            started_at=task.get("started_at"),
            completed_at=task.get("completed_at"),
            is_terminal=is_terminal(status),
            allowed_transitions=allowed_transitions,
            duration_minutes=duration_minutes,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching status info for {task_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch status info") from e


@status_router.get(
    "/{task_id}/status-history",
    response_model=Dict[str, Any],
    summary="Get status change history for a task",
    tags=["Task Status Management"],
)
async def get_task_status_history(
    task_id: str,
    limit: int = Query(50, ge=1, le=200, description="Maximum number of history entries"),
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    **Get complete audit trail of status changes for a task.**

    Retrieves all status changes with timestamps and reasons,
    providing full traceability of task lifecycle.

    **Parameters:**
    - task_id: Task UUID
    - limit: Maximum entries to return (default 50, max 200)

    **Returns:**
    - List of status history entries with timestamps and metadata

    **Example cURL:**
    ```bash
    curl -X GET "http://localhost:8000/api/tasks/{task_id}/status-history?limit=20" \
      -H "Authorization: Bearer TOKEN"
    ```

    **Response:**
    ```json
    {
      "task_id": "550e8400-e29b-41d4-a716-446655440000",
      "history_count": 3,
      "history": [
        {
          "id": 1,
          "task_id": "550e8400-e29b-41d4-a716-446655440000",
          "old_status": "pending",
          "new_status": "in_progress",
          "reason": "Task started",
          "timestamp": "2025-12-22T10:30:00",
          "metadata": {"user_id": "user@example.com"}
        }
      ]
    }
    ```
    """
    try:
        # Ownership check: verify user owns this task before returning history
        task = await db_service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if isinstance(task, dict):
            _check_task_ownership(task, token)

        # Get status history directly from database service which is more reliable
        # than the enhanced service dependency injection
        from services.tasks_db import TasksDatabase

        task_db = TasksDatabase(db_service.pool)
        history = await task_db.get_status_history(task_id, limit)

        return {
            "task_id": task_id,
            "history_count": len(history) if history else 0,
            "history": history if history else [],
        }

    except Exception as e:
        logger.error(f"Error fetching status history for {task_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch status history") from e


@status_router.get(
    "/{task_id}/status-history/failures",
    response_model=Dict[str, Any],
    summary="Get validation failures for a task",
    tags=["Task Status Management"],
)
async def get_task_validation_failures(
    task_id: str,
    limit: int = Query(50, ge=1, le=200, description="Maximum number of failure records"),
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    status_service: EnhancedStatusChangeService = Depends(
        lambda: (
            __import__(
                "utils.route_utils", fromlist=["get_enhanced_status_change_service"]
            ).get_enhanced_status_change_service()
        )
    ),
):
    """
    **Get all validation failures and errors for a task.**

    Retrieves all times a task transitioned to a validation error state,
    useful for debugging and understanding validation issues.

    **Parameters:**
    - task_id: Task UUID
    - limit: Maximum failure records (default 50, max 200)

    **Returns:**
    - List of validation failure records with error details

    **Response:**
    ```json
    {
      "task_id": "550e8400-e29b-41d4-a716-446655440000",
      "failure_count": 1,
      "failures": [
        {
          "timestamp": "2025-12-22T10:30:00",
          "reason": "Content validation failed",
          "errors": [
            "Content length below minimum (800 words)",
            "SEO keywords not met"
          ],
          "context": {"stage": "validation", "model": "claude-3"}
        }
      ]
    }
    ```
    """
    try:
        # Ownership check: verify user owns this task
        task = await db_service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if isinstance(task, dict):
            _check_task_ownership(task, token)

        # Get validation failures
        failures = await status_service.get_validation_failures(task_id, limit=limit)

        if not failures.get("failures"):
            logger.info(f"ℹ️  No validation failures found for task {task_id}")

        return failures

    except Exception as e:
        logger.error(f"Error fetching validation failures for {task_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch validation failures") from e


@status_router.patch(
    "/{task_id}",
    response_model=UnifiedTaskResponse,
    summary="Update task status and results (legacy endpoint)",
)
async def update_task(
    task_id: str,
    update_data: TaskStatusUpdateRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Update task status and results.

    **Parameters:**
    - task_id: Task UUID
    - status: New status (queued, pending, running, completed, failed)
    - result: Task result/output if completed (optional)
    - metadata: Additional metadata (optional)

    **Returns:**
    - Updated task with new status and timestamps

    **Example cURL:**
    ```bash
    curl -X PATCH http://localhost:8000/api/tasks/550e8400-e29b-41d4-a716-446655440000 \
      -H "Authorization: Bearer YOUR_JWT_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{
        "status": "running"
      }'
    ```
    """
    try:
        # Validate UUID format
        try:
            UUID(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid task ID format") from exc

        # Fetch task
        task = await db_service.get_task(task_id)

        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        # Ownership check
        if isinstance(task, dict):
            _check_task_ownership(task, token)

        # Prepare update data
        update_dict = {
            "status": update_data.status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Set timestamps based on status
        if update_data.status == "running" and not task.get("started_at"):
            update_dict["started_at"] = datetime.now(timezone.utc).isoformat()
        elif update_data.status in ["completed", "failed"] and not task.get("completed_at"):
            update_dict["completed_at"] = datetime.now(timezone.utc).isoformat()

        # Add result if provided
        if update_data.result:
            update_dict["result"] = update_data.result  # type: ignore[assignment]

        # Merge metadata if provided
        if update_data.metadata:
            task["metadata"] = {**(task.get("metadata") or {}), **update_data.metadata}
            update_dict["metadata"] = task["metadata"]  # type: ignore[assignment]

        # Update task status - pass result dict (asyncpg handles JSONB conversion)
        await db_service.update_task_status(
            task_id,
            update_data.status,
            result=json.dumps(update_data.result) if update_data.result else None,
        )

        # Fetch updated task
        updated_task = await db_service.get_task(task_id)

        # Convert to response schema with proper type conversions
        return UnifiedTaskResponse(
            **ModelConverter.task_response_to_unified(ModelConverter.to_task_response(updated_task))
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to update task") from e


@status_router.patch(
    "/{task_id}/content",
    response_model=UnifiedTaskResponse,
    summary="Edit task content fields (title, content, metadata)",
)
async def update_task_content(
    task_id: str,
    updates: Dict[str, Any],
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Edit task content without requiring a status change.
    Used by the content editor in the task detail modal.

    Allowed fields: topic, content, title, excerpt, featured_image_url,
    seo_title, seo_description, seo_keywords, task_metadata.
    """
    try:
        task = await db_service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if isinstance(task, dict):
            _check_task_ownership(task, token)

        # Filter to allowed content fields only
        allowed = {
            "topic",
            "content",
            "title",
            "excerpt",
            "featured_image_url",
            "seo_title",
            "seo_description",
            "seo_keywords",
            "task_metadata",
            "style",
            "tone",
            "target_length",
            "primary_keyword",
            "target_audience",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            raise HTTPException(status_code=400, detail="No valid content fields to update")

        await db_service.update_task(task_id, filtered)
        updated_task = await db_service.get_task(task_id)
        if not updated_task:
            raise HTTPException(status_code=404, detail="Task not found after update")
        return UnifiedTaskResponse(**_normalize_seo_keywords_in_task(updated_task))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update task content: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update task content") from e
