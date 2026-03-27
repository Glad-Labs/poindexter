"""
Bulk Task Operations Routes

Provides endpoints for performing bulk operations on multiple tasks such as:
- Pausing/resuming multiple tasks
- Cancelling batches of tasks
- Rejecting multiple tasks (for audit tracking)
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from routes.auth_unified import get_current_user
from schemas.bulk_task_schemas import (
    BulkCreateTasksRequest,
    BulkCreateTasksResponse,
    BulkTaskRequest,
    BulkTaskResponse,
)
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.error_responses import ErrorResponseBuilder
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)
router = APIRouter(prefix="/api/tasks", tags=["tasks-bulk"])


@router.post(
    "/bulk", response_model=BulkTaskResponse, summary="Perform bulk operations on multiple tasks"
)
async def bulk_task_operations(
    request: BulkTaskRequest,
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Perform bulk operations on multiple tasks.

    **Actions:**
    - `pause`: Set status to paused (pause execution)
    - `resume`: Resume paused tasks (set to in_progress)
    - `cancel`: Cancel pending/running tasks (set to cancelled)
    - `reject`: Mark tasks as rejected (set to rejected for audit tracking)

    **Authentication:** Required (JWT token)

    **Parameters:**
    - task_ids: List of task UUIDs to operate on
    - action: The action to perform on all tasks

    **Returns:**
    - message: Status message
    - updated: Number of tasks successfully updated
    - failed: Number of tasks that failed
    - total: Total tasks in request
    - errors: List of errors if any occurred

    **Example Request:**
    ```json
    {
      "task_ids": [
        "550e8400-e29b-41d4-a716-446655440000",
        "550e8400-e29b-41d4-a716-446655440001"
      ],
      "action": "cancel"
    }
    ```

    **Example Response:**
    ```json
    {
      "message": "Bulk cancel completed",
      "updated": 2,
      "failed": 0,
      "total": 2,
      "errors": null
    }
    ```
    """
    # Validate request
    if not request.task_ids:
        error_response = (
            ErrorResponseBuilder()
            .error_code("VALIDATION_ERROR")
            .message("No task IDs provided in request")
            .with_field_error("task_ids", "At least one task ID required", "REQUIRED")
            .build()
        )
        raise HTTPException(status_code=400, detail=error_response.model_dump())

    if request.action not in ["pause", "resume", "cancel", "reject"]:
        error_response = (
            ErrorResponseBuilder()
            .error_code("VALIDATION_ERROR")
            .message("Invalid action specified")
            .with_field_error(
                "action",
                f"Must be one of: pause, resume, cancel, or reject. Got: {request.action}",
                "INVALID_CHOICE",
            )
            .build()
        )
        raise HTTPException(status_code=400, detail=error_response.model_dump())

    # Map actions to statuses
    status_map = {
        "pause": "paused",
        "resume": "in_progress",
        "cancel": "cancelled",
        "reject": "rejected",
    }
    new_status = status_map[request.action]

    # Validate UUID formats before hitting the DB — collect invalid IDs up-front.
    errors = []
    valid_ids = []
    for task_id in request.task_ids:
        try:
            UUID(task_id)
            valid_ids.append(task_id)
        except ValueError:
            errors.append({"task_id": task_id, "error": "Invalid UUID format"})

    # Replace N+1 loop (2N queries) with 2 bulk queries regardless of batch size (#700).
    updated_count = 0
    failed_count = len(errors)

    if valid_ids:
        try:
            result = await db_service.bulk_update_task_statuses(valid_ids, new_status)
            updated_count = len(result["updated_ids"])
            for missing_id in result["missing_ids"]:
                errors.append({"task_id": missing_id, "error": "Task not found"})
                failed_count += 1
            logger.info(f"Bulk {request.action}: updated {updated_count} tasks to '{new_status}'")
        except Exception as e:
            logger.error(
                f"[bulk_task_action] Bulk update failed for action={request.action}: {e}",
                exc_info=True,
            )
            raise HTTPException(status_code=500, detail="Bulk update failed") from e

    return BulkTaskResponse(
        message=f"Bulk {request.action} completed: {updated_count} updated, {failed_count} failed",
        updated=updated_count,
        failed=failed_count,
        total=len(request.task_ids),
        errors=errors if errors else None,
    )


@router.post(
    "/bulk/create", response_model=BulkCreateTasksResponse, summary="Create multiple tasks in bulk"
)
async def bulk_create_tasks(
    request: BulkCreateTasksRequest,
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Create multiple tasks at once.

    **Fields:**
    - `task_name`: Name of the task
    - `topic`: Topic or subject matter
    - `primary_keyword`: Primary keyword for SEO
    - `target_audience`: Target audience for content
    - `category`: Category of task
    - `priority`: Priority level (high, medium, low)

    **Authentication:** Required (JWT token)

    **Returns:** List of created tasks with their IDs
    """
    try:
        user_id = current_user.get("user_id") if current_user else "system"

        # Build task data dicts for batch insert
        task_data_list = []
        for task in request.tasks:
            task_data_list.append(
                {
                    "task_name": task.task_name,
                    "title": task.task_name,
                    "topic": task.topic,
                    "status": "pending",
                    "primary_keyword": task.primary_keyword,
                    "target_audience": task.target_audience,
                    "category": task.category,
                    "metadata": {
                        "topic": task.topic,
                        "primary_keyword": task.primary_keyword,
                        "target_audience": task.target_audience,
                        "category": task.category,
                        "priority": task.priority,
                        "created_by": user_id,
                    },
                }
            )

        # Single batch insert instead of N individual INSERTs
        task_ids = await db_service.tasks.bulk_add_tasks(task_data_list)

        created_tasks = [
            {"id": tid, "name": task.task_name, "status": "pending"}
            for tid, task in zip(task_ids, request.tasks)
        ]

        return BulkCreateTasksResponse(
            created=len(created_tasks),
            failed=0,
            total=len(request.tasks),
            tasks=created_tasks if created_tasks else None,
            errors=None,
        )
    except Exception as e:
        logger.error(f"Bulk create error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Bulk create failed") from e
