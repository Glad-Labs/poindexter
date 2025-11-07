"""
Task Management Routes - Async Implementation

Provides REST API endpoints for creating, retrieving, and managing tasks.
Uses asyncpg DatabaseService (no SQLAlchemy ORM).

Endpoints:
- POST /api/tasks - Create new task
- GET /api/tasks - List tasks with pagination
- GET /api/tasks/{task_id} - Get task details
- PATCH /api/tasks/{task_id} - Update task status
- GET /api/metrics - Aggregated task metrics
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from uuid import UUID
import uuid as uuid_lib
import json
import logging
import os


def convert_db_row_to_dict(row):
    """
    Convert asyncpg database row to proper types for TaskResponse.
    Handles UUID → str, datetime → ISO string, JSONB string → dict conversions.
    """
    if row is None:
        return None
    
    # Convert asyncpg Record to dict
    if hasattr(row, 'keys'):
        data = dict(row)
    else:
        data = row
    
    # Convert UUID to string
    if 'id' in data and data['id'] is not None:
        data['id'] = str(data['id'])
    
    # Convert datetimes to ISO format strings
    for dt_field in ['created_at', 'updated_at', 'started_at', 'completed_at']:
        if dt_field in data and data[dt_field] is not None:
            if isinstance(data[dt_field], datetime):
                data[dt_field] = data[dt_field].isoformat()
            # else already a string
    
    # Convert metadata JSONB string to dict
    if 'metadata' in data and isinstance(data['metadata'], str):
        try:
            data['metadata'] = json.loads(data['metadata'])
        except (json.JSONDecodeError, TypeError):
            data['metadata'] = {}
    elif 'metadata' not in data:
        data['metadata'] = {}
    
    # Handle result JSONB similarly
    if 'result' in data and isinstance(data['result'], str):
        try:
            data['result'] = json.loads(data['result'])
        except (json.JSONDecodeError, TypeError):
            data['result'] = None
    
    return data

# Import async database service
from services.database_service import DatabaseService
from routes.auth_routes import get_current_user
from services.strapi_publisher import StrapiPublisher

# Configure logging
logger = logging.getLogger(__name__)

# Configure router with prefix and tags
router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# Global database service (initialized in main.py)
db_service: Optional[DatabaseService] = None

def set_db_service(service: DatabaseService):
    """Set the database service instance"""
    global db_service
    db_service = service

# ============================================================================
# PYDANTIC SCHEMAS FOR VALIDATION
# ============================================================================

class TaskCreateRequest(BaseModel):
    """Schema for creating a new task"""
    task_name: str = Field(..., description="Name of the task")
    topic: str = Field(..., description="Blog post topic")
    primary_keyword: str = Field(default="", description="Primary SEO keyword")
    target_audience: str = Field(default="", description="Target audience")
    category: str = Field(default="general", description="Content category")
    metadata: Optional[Dict[str, Any]] = Field(default={}, description="Additional metadata")
    
    class Config:
        example = {
            "task_name": "Blog Post - AI in Healthcare",
            "topic": "How AI is Transforming Healthcare",
            "primary_keyword": "AI healthcare",
            "target_audience": "Healthcare professionals",
            "category": "healthcare",
            "metadata": {"priority": "high"}
        }


class TaskStatusUpdateRequest(BaseModel):
    """Schema for updating task status"""
    status: str = Field(..., description="New task status")
    result: Optional[Dict[str, Any]] = Field(None, description="Task result/output")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")
    
    class Config:
        example = {
            "status": "completed",
            "result": {"content": "Generated blog post..."},
            "metadata": {"execution_time": 45.2}
        }


class TaskResponse(BaseModel):
    """Schema for task response"""
    id: str
    task_name: str
    agent_id: str
    status: str
    topic: str
    primary_keyword: Optional[str]
    target_audience: Optional[str]
    category: Optional[str]
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    metadata: Dict[str, Any] = {}
    result: Optional[Dict[str, Any]] = None
    
    class Config:
        example = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "task_name": "Blog Post - AI in Healthcare",
            "agent_id": "content-agent",
            "status": "completed",
            "topic": "How AI is Transforming Healthcare",
            "primary_keyword": "AI healthcare",
            "target_audience": "Healthcare professionals",
            "category": "healthcare",
            "created_at": "2024-01-15T10:30:00Z",
            "updated_at": "2024-01-15T10:45:00Z",
            "started_at": "2024-01-15T10:32:00Z",
            "completed_at": "2024-01-15T10:45:00Z",
            "metadata": {"priority": "high"},
            "result": {"content": "Generated blog post...", "word_count": 1500}
        }


class TaskListResponse(BaseModel):
    """Schema for task list response with pagination"""
    tasks: List[TaskResponse]
    total: int
    offset: int
    limit: int
    
    class Config:
        example = {
            "tasks": [],
            "total": 0,
            "offset": 0,
            "limit": 10
        }


class MetricsResponse(BaseModel):
    """Schema for aggregated metrics"""
    total_tasks: int = Field(..., description="Total tasks created")
    completed_tasks: int = Field(..., description="Successfully completed tasks")
    failed_tasks: int = Field(..., description="Failed tasks")
    pending_tasks: int = Field(..., description="Pending/queued tasks")
    success_rate: float = Field(..., description="Success rate percentage (0-100)")
    avg_execution_time: float = Field(..., description="Average execution time in seconds")
    total_cost: float = Field(..., description="Total estimated cost in USD")
    
    class Config:
        example = {
            "total_tasks": 150,
            "completed_tasks": 120,
            "failed_tasks": 5,
            "pending_tasks": 25,
            "success_rate": 80.0,
            "avg_execution_time": 45.2,
            "total_cost": 125.50
        }


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.post("", response_model=Dict[str, Any], summary="Create new task", status_code=201)
async def create_task(
    request: TaskCreateRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new task for content generation.
    
    **Parameters:**
    - task_name: Name/title of the task
    - topic: Blog post topic
    - primary_keyword: Primary SEO keyword (optional)
    - target_audience: Target audience (optional)
    - category: Content category (default: "general")
    - metadata: Additional metadata (optional)
    
    **Returns:**
    - Task ID (UUID)
    - Status and creation timestamp
    
    **Example cURL:**
    ```bash
    curl -X POST http://localhost:8000/api/tasks \
      -H "Authorization: Bearer YOUR_JWT_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{
        "task_name": "Blog Post - AI in Healthcare",
        "topic": "How AI is Transforming Healthcare",
        "primary_keyword": "AI healthcare",
        "target_audience": "Healthcare professionals",
        "category": "healthcare"
      }'
    ```
    """
    try:
        # Validate required fields with detailed error messages
        if not request.task_name or not str(request.task_name).strip():
            raise HTTPException(
                status_code=422,
                detail={
                    "field": "task_name",
                    "message": "task_name is required and cannot be empty",
                    "type": "validation_error"
                }
            )
        
        if not request.topic or not str(request.topic).strip():
            raise HTTPException(
                status_code=422,
                detail={
                    "field": "topic",
                    "message": "topic is required and cannot be empty",
                    "type": "validation_error"
                }
            )
        
        # Create task data
        task_data = {
            "id": str(uuid_lib.uuid4()),
            "task_name": request.task_name.strip(),
            "topic": request.topic.strip(),
            "primary_keyword": (request.primary_keyword or "").strip(),
            "target_audience": (request.target_audience or "").strip(),
            "category": (request.category or "general").strip(),
            "status": "pending",
            "agent_id": "content-agent",
            "user_id": current_user.get("id", "system"),
            "metadata": request.metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        # Add task to database
        task_id = await db_service.add_task(task_data)
        
        return {
            "id": task_id,
            "status": "pending",
            "created_at": task_data["created_at"],
            "message": "Task created successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "message": f"Failed to create task: {str(e)}",
                "type": "internal_error"
            }
        )


@router.get("", response_model=TaskListResponse, summary="List tasks")
async def list_tasks(
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(10, ge=1, le=100, description="Results per page"),
    status: Optional[str] = Query(None, description="Filter by status"),
    category: Optional[str] = Query(None, description="Filter by category"),
    current_user: dict = Depends(get_current_user)
):
    """
    List tasks with optional filtering and pagination.
    
    **Query Parameters:**
    - offset: Pagination offset (default: 0)
    - limit: Number of results (default: 10, max: 100)
    - status: Filter by task status (optional)
    - category: Filter by category (optional)
    
    **Returns:**
    - List of tasks
    - Total count
    - Pagination info
    
    **Example cURL:**
    ```bash
    curl -X GET "http://localhost:8000/api/tasks?offset=0&limit=10&status=completed" \
      -H "Authorization: Bearer YOUR_JWT_TOKEN"
    ```
    """
    try:
        # Get all tasks (in production, add filtering to DatabaseService)
        all_tasks = await db_service.get_all_tasks(limit=10000)
        
        # Filter by status if provided
        if status:
            all_tasks = [t for t in all_tasks if t.get("status") == status]
        
        # Filter by category if provided
        if category:
            all_tasks = [t for t in all_tasks if t.get("category") == category]
        
        # Get total count
        total = len(all_tasks)
        
        # Apply pagination
        tasks = all_tasks[offset:offset + limit]
        
        # Convert to response schema with proper type conversions
        task_responses = [
            TaskResponse(**convert_db_row_to_dict(task))
            for task in tasks
        ]
        
        return TaskListResponse(
            tasks=task_responses,
            total=total,
            offset=offset,
            limit=limit
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch tasks: {str(e)}")


@router.get("/{task_id}", response_model=TaskResponse, summary="Get task details")
async def get_task(
    task_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get details for a specific task.
    
    **Parameters:**
    - task_id: Task UUID
    
    **Returns:**
    - Full task details including status, timestamps, and results
    
    **Example cURL:**
    ```bash
    curl -X GET http://localhost:8000/api/tasks/550e8400-e29b-41d4-a716-446655440000 \
      -H "Authorization: Bearer YOUR_JWT_TOKEN"
    ```
    """
    try:
        # Validate UUID format
        try:
            UUID(task_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid task ID format")
        
        # Fetch task
        task = await db_service.get_task(task_id)
        
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        
        # Convert to response schema with proper type conversions
        return TaskResponse(**convert_db_row_to_dict(task))
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch task: {str(e)}")


@router.patch("/{task_id}", response_model=TaskResponse, summary="Update task status")
async def update_task(
    task_id: str,
    update_data: TaskStatusUpdateRequest,
    current_user: dict = Depends(get_current_user)
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
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid task ID format")
        
        # Fetch task
        task = await db_service.get_task(task_id)
        
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        
        # Prepare update data
        update_dict = {
            "status": update_data.status,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Set timestamps based on status
        if update_data.status == "running" and not task.get("started_at"):
            update_dict["started_at"] = datetime.now(timezone.utc).isoformat()
        elif update_data.status in ["completed", "failed"] and not task.get("completed_at"):
            update_dict["completed_at"] = datetime.now(timezone.utc).isoformat()
        
        # Add result if provided
        if update_data.result:
            update_dict["result"] = update_data.result
        
        # Merge metadata if provided
        if update_data.metadata:
            task["metadata"] = {**(task.get("metadata") or {}), **update_data.metadata}
            update_dict["metadata"] = task["metadata"]
        
        # Update task status - pass result dict (asyncpg handles JSONB conversion)
        await db_service.update_task_status(task_id, update_data.status, result=json.dumps(update_data.result) if update_data.result else None)
        
        # Fetch updated task
        updated_task = await db_service.get_task(task_id)
        
        # Convert to response schema with proper type conversions
        return TaskResponse(**convert_db_row_to_dict(updated_task))
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update task: {str(e)}")


@router.post("/{task_id}/publish", response_model=Dict[str, Any], summary="Publish task content to Strapi")
async def publish_task(
    task_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Publish completed task content to Strapi CMS.
    
    **Path Parameters:**
    - task_id: UUID of the task to publish
    
    **Returns:**
    - {status: "published", message: "Task published successfully"}
    
    **Status Codes:**
    - 200: Successfully published
    - 404: Task not found
    - 409: Task not in publishable state
    - 500: Publication error
    
    **Example cURL:**
    ```bash
    curl -X POST http://localhost:8000/api/tasks/9205eab0-2491-4014-bda2-45b6c9c8489c/publish \
      -H "Authorization: Bearer YOUR_JWT_TOKEN"
    ```
    """
    try:
        # Validate UUID format
        try:
            UUID(task_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid task ID format")
        
        # Get task from database
        task_row = await db_service.get_task(task_id)
        if not task_row:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        
        task = convert_db_row_to_dict(task_row)
        if not task:
            raise HTTPException(status_code=500, detail="Failed to convert task data")
        
        # Verify task is in a publishable state
        task_status = task.get('status')
        if task_status not in ['completed', 'published']:
            raise HTTPException(
                status_code=409,
                detail=f"Task must be completed to publish (current status: {task_status})"
            )
        
        # Extract content from task result
        task_result = task.get('result')
        if not task_result:
            raise HTTPException(
                status_code=400,
                detail="Task has no result data to publish"
            )
        
        if isinstance(task_result, str):
            content = task_result
            title = task.get('title', 'Untitled')
        else:
            # task_result is a dict-like object
            if isinstance(task_result, dict):
                content = task_result.get('content') or task_result.get('generated_content') or task_result.get('body', '')
                title = task_result.get('title') or task.get('title', 'Untitled')
            else:
                # Unknown type, try to convert to string
                content = str(task_result)
                title = task.get('title', 'Untitled')
        
        if not content:
            raise HTTPException(
                status_code=400,
                detail="Task has no content to publish"
            )
        
        # Extract metadata from task (initialize variables)
        excerpt = task.get('description', '')[:200] if task.get('description') else content[:200]
        category = task.get('category')
        tags = task.get('tags')
        
        # Publish to Strapi using StrapiPublisher (PostgreSQL direct)
        strapi_success = False
        strapi_post_id = None
        strapi_error_detail = None
        
        try:
            logger.info(f"Publishing task {task_id} to PostgreSQL database (Strapi)")
            
            # Create publisher (uses DATABASE_URL from environment)
            publisher = StrapiPublisher()
            
            # Connect to database
            if not await publisher.connect():
                strapi_error_detail = "Failed to connect to Strapi database"
                logger.error(strapi_error_detail)
            else:
                logger.info(f"Publishing content - Title: {title}, Content length: {len(content)}")
                
                # Publish to PostgreSQL (Strapi database)
                strapi_result = await publisher.create_post(
                    title=title,
                    content=content,
                    excerpt=excerpt,
                    category=category,
                    tags=tags,
                )
                
                logger.info(f"Strapi database response: {strapi_result}")
                
                strapi_success = strapi_result.get('success', False)
                strapi_post_id = strapi_result.get('post_id')
                
                if not strapi_success:
                    strapi_error_detail = strapi_result.get('error', strapi_result.get('message', 'Unknown error'))
                    logger.error(f"Strapi publishing failed: {strapi_error_detail}")
                
                # Cleanup connection
                await publisher.disconnect()
        
        except Exception as e:
            logger.error(f"Exception during Strapi publish: {str(e)}", exc_info=True)
            strapi_error_detail = str(e)
            strapi_success = False
            strapi_post_id = None
        
        # Update task status to published
        await db_service.update_task_status(task_id, 'published')
        
        # Return response with Strapi information
        response_data = {
            "status": "published",
            "task_id": task_id,
            "message": "Task published successfully",
            "content": {
                "title": title,
                "length": len(content),
                "excerpt": excerpt[:100] if excerpt else "No excerpt"
            }
        }
        
        if strapi_success:
            response_data["strapi"] = {
                "success": True,
                "post_id": strapi_post_id,
                "message": "Content published to Strapi PostgreSQL database"
            }
        else:
            response_data["strapi"] = {
                "success": False,
                "message": f"Strapi publishing failed: {strapi_error_detail or 'Unknown error'}"
            }
        
        return response_data
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to publish task: {str(e)}")


@router.get("/metrics/summary", response_model=MetricsResponse, summary="Get task metrics")
async def get_metrics(
    current_user: dict = Depends(get_current_user)
):
    """
    Get aggregated metrics for all tasks.
    
    **Returns:**
    - Total tasks, completed, failed, pending
    - Success rate percentage
    - Average execution time
    - Total estimated cost
    
    **Example cURL:**
    ```bash
    curl -X GET http://localhost:8000/api/tasks/metrics/summary \
      -H "Authorization: Bearer YOUR_JWT_TOKEN"
    ```
    """
    try:
        # Get metrics from database service
        metrics = await db_service.get_metrics()
        
        return MetricsResponse(**metrics)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch metrics: {str(e)}")
