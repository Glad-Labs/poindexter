"""
Task Management Routes

Provides REST API endpoints for creating, retrieving, and managing tasks.
Integrates with SQLAlchemy ORM and PostgreSQL backend.

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
from datetime import datetime
from uuid import UUID
import uuid as uuid_lib
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

# Import database models and utilities
from models import Task, User, Base
from database import get_db
from routes.auth_routes import get_current_user

# Configure router with prefix and tags
router = APIRouter(prefix="/api/tasks", tags=["tasks"])

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


class TaskResponse(BaseModel):
    """Schema for task response"""
    id: str = Field(..., description="Task UUID")
    task_name: str
    agent_id: str
    status: str
    topic: str
    primary_keyword: Optional[str]
    target_audience: Optional[str]
    category: Optional[str]
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = {}
    result: Optional[Dict[str, Any]] = None
    
    class Config:
        from_attributes = True


class TaskListResponse(BaseModel):
    """Schema for task list response with pagination"""
    tasks: List[TaskResponse]
    total: int
    offset: int
    limit: int
    
    class Config:
        from_attributes = True


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
            "success_rate": 95.24,
            "avg_execution_time": 45.3,
            "total_cost": 23.50
        }


class TaskStatusUpdateRequest(BaseModel):
    """Schema for updating task status"""
    status: str = Field(..., description="New task status: queued, pending, running, completed, failed")
    result: Optional[Dict[str, Any]] = Field(default=None, description="Task result if completed")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional metadata")
    
    class Config:
        example = {
            "status": "completed",
            "result": {
                "content": "Generated blog post content...",
                "word_count": 1200,
                "metadata": {"seo_score": 85}
            },
            "metadata": {"execution_time": 42.5}
        }


# ============================================================================
# API ENDPOINTS
# ============================================================================

@router.post("/", response_model=TaskResponse, status_code=201, summary="Create new task")
async def create_task(
    task_data: TaskCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new task for content generation.
    
    **Parameters:**
    - task_name: Human-readable name for the task
    - topic: Blog post topic for generation
    - primary_keyword: Main SEO keyword (optional)
    - target_audience: Target audience for content (optional)
    - category: Content category (optional)
    - metadata: Additional metadata (optional)
    
    **Returns:**
    - Task ID (UUID)
    - Initial status: "queued"
    - Timestamps and metadata
    
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
        # Create new task
        new_task = Task(
            id=uuid_lib.uuid4(),
            task_name=task_data.task_name,
            agent_id="content_agent",  # Default agent - could be made dynamic
            status="queued",  # Initial status
            topic=task_data.topic,
            primary_keyword=task_data.primary_keyword,
            target_audience=task_data.target_audience,
            category=task_data.category,
            metadata=task_data.metadata or {},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        # Add to database
        db.add(new_task)
        db.commit()
        db.refresh(new_task)
        
        return TaskResponse.from_orm(new_task)
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create task: {str(e)}")


@router.get("/", response_model=TaskListResponse, summary="List tasks with pagination")
async def list_tasks(
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(10, ge=1, le=100, description="Pagination limit"),
    status: Optional[str] = Query(None, description="Filter by status"),
    category: Optional[str] = Query(None, description="Filter by category"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
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
        # Build query with filters
        query = db.query(Task)
        
        if status:
            query = query.filter(Task.status == status)
        if category:
            query = query.filter(Task.category == category)
        
        # Get total count
        total = query.count()
        
        # Apply pagination
        tasks = query.order_by(Task.created_at.desc()).offset(offset).limit(limit).all()
        
        return TaskListResponse(
            tasks=[TaskResponse.from_orm(task) for task in tasks],
            total=total,
            offset=offset,
            limit=limit
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch tasks: {str(e)}")


@router.get("/{task_id}", response_model=TaskResponse, summary="Get task details")
async def get_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
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
        # Convert string to UUID
        try:
            task_uuid = UUID(task_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid task ID format")
        
        # Fetch task
        task = db.query(Task).filter(Task.id == task_uuid).first()
        
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        
        return TaskResponse.from_orm(task)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch task: {str(e)}")


@router.patch("/{task_id}", response_model=TaskResponse, summary="Update task status")
async def update_task(
    task_id: str,
    update_data: TaskStatusUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
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
        # Convert string to UUID
        try:
            task_uuid = UUID(task_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid task ID format")
        
        # Fetch task
        task = db.query(Task).filter(Task.id == task_uuid).first()
        
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        
        # Update status
        old_status = task.status
        task.status = update_data.status
        task.updated_at = datetime.utcnow()
        
        # Update timestamps based on status
        if update_data.status == "running" and not task.started_at:
            task.started_at = datetime.utcnow()
        elif update_data.status in ["completed", "failed"] and not task.completed_at:
            task.completed_at = datetime.utcnow()
        
        # Update result if provided
        if update_data.result:
            task.result = update_data.result
        
        # Merge metadata if provided
        if update_data.metadata:
            task.metadata = {**(task.metadata or {}), **update_data.metadata}
        
        # Commit changes
        db.commit()
        db.refresh(task)
        
        return TaskResponse.from_orm(task)
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update task: {str(e)}")


@router.get("/health/status", response_model=dict, summary="DEPRECATED: Task service health check (use /api/health instead)", deprecated=True)
async def task_health(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    DEPRECATED: Use GET /api/health instead.
    
    Health check endpoint for task service (legacy).
    This endpoint is deprecated and will be removed in version 2.0.
    Use the unified /api/health endpoint for all health checks.
    
    **Returns:**
    - Service status (healthy/unhealthy)
    - Database connection status
    - Recent task count
    
    **Example cURL:**
    ```bash
    curl -X GET http://localhost:8000/api/health
    ```
    """
    try:
        # Test database connection
        db.query(Task).limit(1).all()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return {
        "status": "healthy" if db_status == "connected" else "unhealthy",
        "database": db_status,
        "timestamp": datetime.utcnow().isoformat(),
        "_deprecated": "Use GET /api/health instead"
    }


# ============================================================================
# METRICS ENDPOINT (Aggregated task statistics)
# ============================================================================

@router.get("/metrics/aggregated", response_model=MetricsResponse, summary="Get aggregated task metrics")
async def get_metrics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get aggregated metrics for all tasks.
    
    **Returns:**
    - Total tasks created
    - Completed tasks count
    - Failed tasks count
    - Pending tasks count
    - Success rate percentage
    - Average execution time
    - Estimated total cost
    
    **Calculations:**
    - Success Rate: (completed / (completed + failed)) * 100
    - Avg Execution Time: Sum of (completed_at - started_at) / completed_count
    - Total Cost: Uses estimation (e.g., $0.01 per task)
    
    **Example cURL:**
    ```bash
    curl -X GET http://localhost:8000/api/tasks/metrics/aggregated \
      -H "Authorization: Bearer YOUR_JWT_TOKEN"
    ```
    """
    try:
        # Get task statistics
        total_tasks = db.query(func.count(Task.id)).scalar() or 0
        completed_tasks = db.query(func.count(Task.id)).filter(Task.status == "completed").scalar() or 0
        failed_tasks = db.query(func.count(Task.id)).filter(Task.status == "failed").scalar() or 0
        pending_tasks = total_tasks - completed_tasks - failed_tasks
        
        # Calculate success rate
        success_rate = 0.0
        if (completed_tasks + failed_tasks) > 0:
            success_rate = (completed_tasks / (completed_tasks + failed_tasks)) * 100
        
        # Calculate average execution time
        avg_execution_time = 0.0
        completed_task_times = db.query(
            func.avg(
                func.extract('epoch', Task.completed_at - Task.started_at)
            )
        ).filter(
            and_(Task.status == "completed", Task.started_at.isnot(None))
        ).scalar()
        
        if completed_task_times:
            avg_execution_time = float(completed_task_times)
        
        # Calculate total cost (example: $0.01 per task)
        total_cost = total_tasks * 0.01
        
        return MetricsResponse(
            total_tasks=total_tasks,
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            pending_tasks=pending_tasks,
            success_rate=success_rate,
            avg_execution_time=avg_execution_time,
            total_cost=total_cost
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to calculate metrics: {str(e)}")
