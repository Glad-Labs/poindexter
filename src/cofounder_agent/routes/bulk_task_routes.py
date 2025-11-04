"""
Bulk Task Operations Routes

Provides endpoints for performing bulk operations on multiple tasks such as:
- Pausing/resuming multiple tasks
- Cancelling batches of tasks
- Deleting/archiving multiple tasks
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timezone
import logging

from ..middleware.auth import get_current_user
from ..services.database import DatabaseService

logger = logging.getLogger(__name__)
db_service = None


def set_db_service(service: DatabaseService):
    """Set the database service (called during app startup)"""
    global db_service
    db_service = service


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class BulkTaskRequest(BaseModel):
    """Request schema for bulk task operations"""
    task_ids: List[str]
    action: str  # "pause", "resume", "cancel", "delete"
    
    class Config:
        json_schema_extra = {
            "example": {
                "task_ids": [
                    "550e8400-e29b-41d4-a716-446655440000",
                    "550e8400-e29b-41d4-a716-446655440001"
                ],
                "action": "cancel"
            }
        }


class BulkTaskResponse(BaseModel):
    """Response schema for bulk operations"""
    message: str
    updated: int
    failed: int
    total: int
    errors: Optional[List[dict]] = None


@router.post("/bulk", response_model=BulkTaskResponse, summary="Perform bulk operations on multiple tasks")
async def bulk_task_operations(
    request: BulkTaskRequest,
    current_user: dict = Depends(get_current_user)
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
    if not db_service:
        raise HTTPException(status_code=500, detail="Database service not initialized")
    
    if not request.task_ids:
        raise HTTPException(status_code=400, detail="No task IDs provided")
    
    if request.action not in ["pause", "resume", "cancel", "delete"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid action. Must be one of: pause, resume, cancel, or delete"
        )
    
    # Map actions to statuses
    status_map = {
        "pause": "paused",
        "resume": "in_progress",
        "cancel": "cancelled",
        "delete": "deleted"
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
                errors.append({
                    "task_id": task_id,
                    "error": "Invalid UUID format"
                })
                failed_count += 1
                continue
            
            # Check if task exists
            task = await db_service.get_task(task_id)
            if not task:
                errors.append({
                    "task_id": task_id,
                    "error": "Task not found"
                })
                failed_count += 1
                continue
            
            # Update task status
            await db_service.update_task_status(task_id, new_status)
            updated_count += 1
            logger.info(f"Updated task {task_id} status to {new_status}")
            
        except HTTPException as e:
            errors.append({
                "task_id": task_id,
                "error": e.detail
            })
            failed_count += 1
        except Exception as e:
            errors.append({
                "task_id": task_id,
                "error": str(e)
            })
            failed_count += 1
            logger.error(f"Failed to update task {task_id}: {str(e)}")
    
    return BulkTaskResponse(
        message=f"Bulk {request.action} completed: {updated_count} updated, {failed_count} failed",
        updated=updated_count,
        failed=failed_count,
        total=len(request.task_ids),
        errors=errors if errors else None
    )
