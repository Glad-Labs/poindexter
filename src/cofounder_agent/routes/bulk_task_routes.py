"""
Bulk Task Operations Routes

Provides endpoints for performing bulk operations on multiple tasks such as:
- Pausing/resuming multiple tasks
- Cancelling batches of tasks
- Deleting/archiving multiple tasks
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from routes.auth_unified import get_current_user
from schemas.bulk_task_schemas import (
    BulkCreateTasksRequest,
    BulkCreateTasksResponse,
    BulkTaskRequest,
    BulkTaskResponse,
)
from services.database_service import DatabaseService
from utils.error_responses import ErrorResponseBuilder
from utils.route_utils import get_database_dependency

logger = logging.getLogger(__name__)

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
    - `delete`: Mark tasks as deleted (set to deleted)

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

    if request.action not in ["pause", "resume", "cancel", "delete"]:
        error_response = (
            ErrorResponseBuilder()
            .error_code("VALIDATION_ERROR")
            .message("Invalid action specified")
            .with_field_error(
                "action",
                f"Must be one of: pause, resume, cancel, or delete. Got: {request.action}",
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
        "delete": "deleted",
    }
    new_status = status_map[request.action]

    updated_count = 0
    failed_count = 0
    errors = []

    for task_id in request.task_ids:
        try:
            # Validate UUID format
            try:
                UUID(task_id)
            except ValueError:
                errors.append({"task_id": task_id, "error": "Invalid UUID format"})
                failed_count += 1
                continue

            # Check if task exists
            task = await db_service.get_task(task_id)
            if not task:
                errors.append({"task_id": task_id, "error": "Task not found"})
                failed_count += 1
                continue

            # Update task status
            await db_service.update_task_status(task_id, new_status)
            updated_count += 1
            logger.info(f"Updated task {task_id} status to {new_status}")

        except HTTPException as e:
            errors.append({"task_id": task_id, "error": e.detail})
            failed_count += 1
        except Exception as e:
            errors.append({"task_id": task_id, "error": str(e)})
            failed_count += 1
            logger.error(f"Failed to update task {task_id}: {str(e)}")

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
        created_tasks = []
        errors = []

        for i, task in enumerate(request.tasks):
            try:
                # Create task in database
                result = await db_service.create_task(
                    title=task.task_name,
                    description=task.description or task.topic,
                    status="pending",
                    priority=task.priority,
                    metadata={
                        "topic": task.topic,
                        "primary_keyword": task.primary_keyword,
                        "target_audience": task.target_audience,
                        "category": task.category,
                    },
                    created_by=current_user.get("user_id") if current_user else "system",
                )

                created_tasks.append(
                    {
                        "id": str(result.get("id")) if isinstance(result, dict) else str(result),
                        "name": task.task_name,
                        "status": "pending",
                    }
                )
            except Exception as e:
                logger.error(f"Error creating task {i+1}: {str(e)}")
                errors.append({"index": i, "task_name": task.task_name, "error": str(e)})

        return BulkCreateTasksResponse(
            created=len(created_tasks),
            failed=len(errors),
            total=len(request.tasks),
            tasks=created_tasks if created_tasks else None,
            errors=errors if errors else None,
        )
    except Exception as e:
        logger.error(f"Bulk create error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Bulk create failed: {str(e)}")
