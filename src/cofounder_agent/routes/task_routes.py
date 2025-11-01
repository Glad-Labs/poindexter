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

# Import async database service
from services.database_service import DatabaseService
from routes.auth_routes import get_current_user

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
        # Create task data
        task_data = {
            "id": str(uuid_lib.uuid4()),
            "task_name": request.task_name,
            "topic": request.topic,
            "primary_keyword": request.primary_keyword or "",
            "target_audience": request.target_audience or "",
            "category": request.category,
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
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create task: {str(e)}")


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
        
        # Convert to response schema
        task_responses = [
            TaskResponse(**task)
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
        
        return TaskResponse(**task)
        
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
        
        # Update task status
        await db_service.update_task_status(task_id, update_data.status)
        
        # Fetch updated task
        updated_task = await db_service.get_task(task_id)
        
        return TaskResponse(**updated_task)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update task: {str(e)}")


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
