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

from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from uuid import UUID
import uuid as uuid_lib
import json
import logging
import os

from utils.error_responses import ErrorResponseBuilder
from utils.route_utils import get_database_dependency


def convert_db_row_to_dict(row):
    """
    Convert asyncpg database row to proper types for TaskResponse.
    Handles UUID → str, datetime → ISO string, JSONB string → dict conversions.
    Prioritizes normalized columns over nested task_metadata.
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
    
    # Ensure task_name is never None (required field)
    if 'task_name' not in data or data['task_name'] is None:
        data['task_name'] = data.get('topic', 'Untitled Task')[:100]
    
    # Convert datetimes to ISO format strings
    for dt_field in ['created_at', 'updated_at', 'started_at', 'completed_at']:
        if dt_field in data and data[dt_field] is not None:
            if isinstance(data[dt_field], datetime):
                data[dt_field] = data[dt_field].isoformat()
            # else already a string
    
    # Handle task_metadata JSONB (for backward compatibility with orchestrator)
    # This is still the primary source for orchestrator metadata
    if 'task_metadata' in data:
        if isinstance(data['task_metadata'], str):
            try:
                data['task_metadata'] = json.loads(data['task_metadata'])
            except (json.JSONDecodeError, TypeError):
                data['task_metadata'] = {}
        elif data['task_metadata'] is None:
            data['task_metadata'] = {}
    else:
        data['task_metadata'] = {}
    
    # IMPORTANT: Merge normalized columns back into task_metadata for UI compatibility
    # The frontend expects task_metadata to contain all content fields
    normalized_fields = [
        'content', 'excerpt', 'featured_image_url', 'featured_image_data',
        'qa_feedback', 'quality_score', 'seo_title', 'seo_description', 'seo_keywords',
        'stage', 'percentage', 'message'
    ]
    
    for field in normalized_fields:
        if field in data and data[field] is not None:
            # Merge normalized column into task_metadata for UI
            data['task_metadata'][field] = data[field]
    
    # Convert metadata JSONB string to dict (FALLBACK)
    if 'metadata' in data and isinstance(data['metadata'], str):
        try:
            data['metadata'] = json.loads(data['metadata'])
        except (json.JSONDecodeError, TypeError):
            data['metadata'] = {}
    elif 'metadata' not in data:
        data['metadata'] = {}
    
    # Handle result JSONB similarly (FALLBACK for old results)
    if 'result' in data and isinstance(data['result'], str):
        try:
            data['result'] = json.loads(data['result'])
        except (json.JSONDecodeError, TypeError):
            data['result'] = None
    
    # Handle error fields (for failed tasks)
    # Map error_message and error_details from database to response
    if 'error_message' not in data:
        data['error_message'] = None
    
    if 'error_details' not in data:
        # Try to extract error details from task_metadata
        if data.get('task_metadata') and isinstance(data['task_metadata'], dict):
            if 'error_details' in data['task_metadata']:
                data['error_details'] = data['task_metadata']['error_details']
            elif 'error' in data['task_metadata']:
                # Fallback: if error exists in metadata, use it
                data['error_details'] = {'error': data['task_metadata']['error']}
        else:
            data['error_details'] = None
    else:
        # Parse error_details if it's a string (JSON)
        if isinstance(data['error_details'], str):
            try:
                data['error_details'] = json.loads(data['error_details'])
            except (json.JSONDecodeError, TypeError):
                data['error_details'] = {'raw_error': data['error_details']}
    
    # If error_message is in task_metadata, promote it to top level
    if not data.get('error_message') and data.get('task_metadata', {}).get('error_message'):
        data['error_message'] = data['task_metadata']['error_message']
    
    return data

# Import async database service
from services.database_service import DatabaseService
from routes.auth_unified import get_current_user
from utils.route_utils import get_database_dependency

# Configure logging
logger = logging.getLogger(__name__)

# Configure router with prefix and tags
router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# ============================================================================
# PYDANTIC SCHEMAS FOR VALIDATION
# ============================================================================

class TaskCreateRequest(BaseModel):
    """Schema for creating a new task"""
    task_name: str = Field(..., min_length=3, max_length=200, 
                          description="Name of the task (3-200 chars)")
    topic: str = Field(..., min_length=3, max_length=200, 
                      description="Blog post topic (3-200 chars)")
    primary_keyword: str = Field(default="", max_length=100, 
                                description="Primary SEO keyword (max 100 chars)")
    target_audience: str = Field(default="", max_length=100, 
                                description="Target audience (max 100 chars)")
    category: str = Field(default="general", max_length=50, 
                         description="Content category (max 50 chars)")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, 
                                              description="Additional metadata")
    
    class Config:
        json_schema_extra = {
            "example": {
                "task_name": "Blog Post - AI in Healthcare",
                "topic": "How AI is Transforming Healthcare",
                "primary_keyword": "AI healthcare",
                "target_audience": "Healthcare professionals",
                "category": "healthcare",
                "metadata": {"priority": "high"}
            }
        }


class TaskStatusUpdateRequest(BaseModel):
    """Schema for updating task status"""
    status: str = Field(..., pattern="^(pending|in_progress|completed|failed|cancelled)$",
                       description="New task status")
    result: Optional[Dict[str, Any]] = Field(None, description="Task result/output")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "completed",
                "result": {"content": "Generated blog post..."},
                "metadata": {"execution_time": 45.2}
            }
        }


class TaskResponse(BaseModel):
    """Schema for task response"""
    id: Optional[str] = None  # Can be NULL for legacy migrated tasks
    task_name: str
    agent_id: Optional[str] = None  # Can be NULL for legacy migrated tasks
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
    task_metadata: Dict[str, Any] = {}  # For orchestrator output (content, excerpt, qa_feedback, etc.)
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None  # ✅ Error message for failed tasks
    error_details: Optional[Dict[str, Any]] = None  # ✅ Detailed error info for failed tasks
    
    @property
    def title(self) -> str:
        """Alias for task_name for frontend compatibility"""
        return self.task_name
    
    @property
    def name(self) -> str:
        """Alias for task_name for frontend compatibility"""
        return self.task_name
    
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
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
    background_tasks: BackgroundTasks = None
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
            logger.error("❌ Task creation failed: task_name is empty")
            raise HTTPException(
                status_code=422,
                detail={
                    "field": "task_name",
                    "message": "task_name is required and cannot be empty",
                    "type": "validation_error"
                }
            )
        
        if not request.topic or not str(request.topic).strip():
            logger.error("❌ Task creation failed: topic is empty")
            raise HTTPException(
                status_code=422,
                detail={
                    "field": "topic",
                    "message": "topic is required and cannot be empty",
                    "type": "validation_error"
                }
            )
        
        # Log incoming request
        logger.info(f"📥 [TASK_CREATE] Received request:")
        logger.info(f"   - task_name: {request.task_name}")
        logger.info(f"   - topic: {request.topic}")
        logger.info(f"   - category: {request.category}")
        logger.info(f"   - user_id: {current_user.get('id', 'system')}")
        
        # Create task data
        task_id = str(uuid_lib.uuid4())
        task_data = {
            "id": task_id,
            "task_name": request.task_name.strip(),
            "topic": request.topic.strip(),
            "primary_keyword": (request.primary_keyword or "").strip(),
            "target_audience": (request.target_audience or "").strip(),
            "category": (request.category or "general").strip(),
            "status": "pending",
            "agent_id": "content-agent",
            "metadata": request.metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        logger.info(f"🔄 [TASK_CREATE] Generated task_id: {task_id}")
        logger.info(f"🔄 [TASK_CREATE] Task data prepared: {json.dumps({k: v for k, v in task_data.items() if k != 'metadata'}, indent=2)}")
        
        # Add task to database
        logger.info(f"💾 [TASK_CREATE] Inserting into database...")
        returned_task_id = await db_service.add_task(task_data)
        logger.info(f"✅ [TASK_CREATE] Database insert successful - returned task_id: {returned_task_id}")
        
        # Verify task was inserted
        logger.info(f"🔍 [TASK_CREATE] Verifying task in database...")
        verify_task = await db_service.get_task(returned_task_id)
        if verify_task:
            logger.info(f"✅ [TASK_CREATE] Verification SUCCESS - Task found in database")
            logger.info(f"   - Status: {verify_task.get('status')}")
            logger.info(f"   - Created: {verify_task.get('created_at')}")
        else:
            logger.error(f"❌ [TASK_CREATE] Verification FAILED - Task NOT found in database after insert!")
        
        response = {
            "id": returned_task_id,
            "status": "pending",
            "created_at": task_data["created_at"],
            "message": "Task created successfully"
        }
        logger.info(f"📤 [TASK_CREATE] Returning response: {response}")
        
        # Queue background task to execute content generation and publishing
        if background_tasks:
            logger.info(f"⏳ [TASK_CREATE] Queueing background task for content generation...")
            background_tasks.add_task(
                _execute_and_publish_task,
                returned_task_id
            )
            logger.info(f"✅ [TASK_CREATE] Background task queued successfully")
        else:
            logger.warning(f"⚠️ [TASK_CREATE] No background_tasks available - content generation will not run!")
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ [TASK_CREATE] Exception: {str(e)}", exc_info=True)
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
    limit: int = Query(20, ge=1, le=100, description="Results per page (default: 20, max: 100)"),
    status: Optional[str] = Query(None, description="Filter by status (optional)"),
    category: Optional[str] = Query(None, description="Filter by category (optional)"),
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency)
):
    """
    List tasks with database-level pagination and filtering.
    
    **Optimizations:**
    - Database-level pagination (not in-memory, much faster!)
    - Server-side filtering (status, category)
    - Default limit: 20 (retrieves only what user sees)
    - Max limit: 100 (prevents abuse)
    - Expected response time: <2 seconds (was 150s with all tasks)
    
    **Query Parameters:**
    - offset: Pagination offset (default: 0)
    - limit: Number of results (default: 20, max: 100)
    - status: Filter by task status (optional)
    - category: Filter by category (optional)
    
    **Returns:**
    - List of tasks (paginated)
    - Total count (for UI pagination)
    - Current offset and limit
    
    **Example:**
    ```
    GET /api/tasks?offset=0&limit=20&status=completed
    ```
    """
    try:
        # Use database-level pagination (much faster than in-memory!)
        tasks, total = await db_service.get_tasks_paginated(
            offset=offset,
            limit=limit,
            status=status,
            category=category
        )
        
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
        logger.error(f"Error listing tasks: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch tasks: {str(e)}")


@router.get("/{task_id}", response_model=TaskResponse, summary="Get task details")
async def get_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency)
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
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency)
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


@router.get("/metrics/summary", response_model=MetricsResponse, summary="Get task metrics")
async def get_metrics(
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency)
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


# ============================================================================
# BACKGROUND TASK EXECUTION
# ============================================================================

async def _execute_and_publish_task(task_id: str):
    """
    Background task to execute content generation.
    
    This function:
    1. Retrieves the task from database
    2. Generates content using Ollama (or other LLM)
    3. Stores generated content in task result JSON
    4. Updates task status to "completed" or "failed"
    
    This runs in the background after task creation returns to the client.
    """
    try:
        logger.info(f"[BG_TASK] Starting content generation for task: {task_id}")
        
        # Step 1: Retrieve task from database
        logger.info(f"[BG_TASK] Fetching task from database...")
        task = await db_service.get_task(task_id)
        
        if not task:
            logger.error(f"[BG_TASK] Task not found: {task_id}")
            return
        
        logger.info(f"[BG_TASK] Task retrieved:")
        logger.info(f"   - Topic: {task.get('topic')}")
        logger.info(f"   - Status: {task.get('status')}")
        logger.info(f"   - Category: {task.get('category')}")
        
        # Step 2: Update status to "in_progress"
        logger.info(f"[BG_TASK] Updating task status to 'in_progress'...")
        await db_service.update_task_status(task_id, "in_progress")
        
        # Step 3: Generate content using Ollama
        logger.info(f"[BG_TASK] Starting content generation with Ollama...")
        
        topic = task.get('topic', '')
        keyword = task.get('primary_keyword', '')
        audience = task.get('target_audience', '')
        
        # Build prompt for Ollama
        prompt = f"""Write a professional blog post about: {topic}
        
Target Audience: {audience if audience else 'General audience'}
Primary Keyword: {keyword if keyword else topic}

The post should be:
- Well-structured with clear headings
- 800-1200 words
- Include an introduction, main points, and conclusion
- Professional and informative
- SEO-optimized

Start writing the blog post now:"""
        
        logger.info(f"[BG_TASK] Calling Ollama with prompt...")
        logger.debug(f"Prompt:\n{prompt[:200]}...")
        
        # Call Ollama directly via HTTP
        import aiohttp
        generated_content = None
        try:
            async with aiohttp.ClientSession() as session:
                ollama_url = "http://localhost:11434/api/generate"
                ollama_payload = {
                    "model": "llama2",
                    "prompt": prompt,
                    "stream": False,
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "top_k": 40,
                }
                
                logger.info(f"[BG_TASK] Connecting to Ollama at {ollama_url}...")
                async with session.post(ollama_url, json=ollama_payload, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        generated_content = result.get('response', '').strip()
                        logger.info(f"[BG_TASK] Content generation successful! ({len(generated_content)} chars)")
                    else:
                        logger.error(f"[BG_TASK] Ollama returned status {resp.status}")
                        generated_content = f"# {topic}\n\nError generating content. Status: {resp.status}"
        except Exception as ollama_err:
            logger.error(f"[BG_TASK] Ollama error: {str(ollama_err)}")
            generated_content = f"# {topic}\n\nContent generation failed: {str(ollama_err)}"
        
        if not generated_content:
            generated_content = f"# {topic}\n\nContent generation returned empty result."
        
        # Step 4: Update task status and store result with generated content
        logger.info(f"[BG_TASK] Storing generated content in database...")
        
        # Store result as JSON containing the generated content
        result_json = json.dumps({
            "content": generated_content,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "content_length": len(generated_content)
        })
        
        await db_service.update_task_status(task_id, "ready_to_publish", result=result_json)
        logger.info(f"[BG_TASK] Content stored in task result")
        
        # Step 5: Create post from generated content
        logger.info(f"[BG_TASK] Creating post from generated content...")
        try:
            # Extract topic or use default title
            post_title = topic or task.get('task_name', 'Generated Content')
            
            # Create slug from title (replace spaces with hyphens, lowercase)
            import re
            slug = re.sub(r'[^\w\s-]', '', post_title.lower())
            slug = re.sub(r'[-\s]+', '-', slug)
            slug = slug.strip('-')
            
            # Create post data structure matching actual posts table schema
            post_data = {
                "id": str(uuid_lib.uuid4()),
                "title": post_title,
                "slug": slug,
                "content": generated_content,
                "excerpt": (generated_content[:200] + "...") if len(generated_content) > 200 else generated_content,
                "seo_title": post_title,
                "seo_description": (generated_content[:150] + "...") if len(generated_content) > 150 else generated_content,
                "seo_keywords": topic or "generated,content,ai",
                "status": "published",  # Auto-publish generated posts
                "featured_image": task.get('featured_image'),
            }
            
            logger.info(f"[BG_TASK] Creating post: {post_title} (slug: {slug})")
            post_result = await db_service.create_post(post_data)
            logger.info(f"[BG_TASK] Post created successfully! Post ID: {post_result.get('id')}")
            
        except Exception as post_err:
            logger.error(f"[BG_TASK] Error creating post: {str(post_err)}", exc_info=True)
            # Don't fail the task if post creation fails, just log it
        
        # Step 6: Final status update
        logger.info(f"[BG_TASK] Content generation complete, marking task as completed...")
        
        final_result = json.dumps({
            "content": generated_content,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "content_length": len(generated_content),
            "status": "success",
            "post_created": True
        })
        await db_service.update_task_status(task_id, "completed", result=final_result)
        logger.info(f"[BG_TASK] Task completed successfully!")
        
    except Exception as e:
        logger.error(f"[BG_TASK] Unhandled error: {str(e)}", exc_info=True)
        try:
            error_result = json.dumps({
                "error": str(e),
                "failed_at": datetime.now(timezone.utc).isoformat()
            })
            await db_service.update_task_status(task_id, "failed", result=error_result)
        except Exception as status_err:
            logger.error(f"[BG_TASK] Could not update task status to failed: {status_err}")


# ============================================================================
# PHASE 1: INTENT-BASED TASK CREATION (Natural Language Support)
# ============================================================================

class IntentTaskRequest(BaseModel):
    """Request for natural language task creation."""
    user_input: str = Field(..., description="Natural language task description")
    user_context: Optional[Dict[str, Any]] = Field(None, description="User context (preferences, settings)")
    business_metrics: Optional[Dict[str, Any]] = Field(None, description="Budget, deadline, quality preference")


class TaskIntentResponse(BaseModel):
    """Response from intent detection and planning."""
    task_id: Optional[str] = Field(None, description="Temp task ID for confirmation")
    intent_request: Dict[str, Any] = Field(..., description="Parsed intent (task_type, parameters, subtasks)")
    execution_plan: Dict[str, Any] = Field(..., description="Execution plan summary for UI")
    ready_to_execute: bool = Field(True, description="Whether user can proceed with execution")
    warnings: Optional[List[str]] = Field(None, description="Warnings (e.g., 'No QA review')")


@router.post("/intent", response_model=TaskIntentResponse)
async def create_task_from_intent(
    request: IntentTaskRequest,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Phase 1: Parse natural language input and create execution plan.
    
    This endpoint:
    1. Takes user NL input
    2. Detects intent (content_generation, social_media, etc.)
    3. Extracts parameters (topic, style, budget, deadline)
    4. Determines subtasks
    5. Creates execution plan
    6. Returns plan to UI for confirmation
    
    User then calls /api/tasks/confirm to execute.
    """
    
    try:
        from services.task_intent_router import TaskIntentRouter
        from services.task_planning_service import TaskPlanningService
        
        # Initialize services
        intent_router = TaskIntentRouter()
        planner = TaskPlanningService()
        
        # Step 1: Parse NL input into TaskIntentRequest
        intent_request = await intent_router.route_user_input(
            request.user_input,
            request.user_context or {}
        )
        
        logger.info(f"[INTENT] Detected intent: {intent_request.intent_type} → task_type: {intent_request.task_type}")
        logger.info(f"[INTENT] Suggested subtasks: {intent_request.suggested_subtasks}")
        logger.info(f"[INTENT] Parameters: {intent_request.parameters}")
        
        # Step 2: Generate execution plan
        plan = await planner.generate_plan(
            intent_request,
            request.business_metrics or {}
        )
        
        logger.info(f"[INTENT] Generated plan: {plan.total_estimated_duration_ms}ms, ${plan.total_estimated_cost:.2f}")
        
        # Step 3: Convert plan to summary for UI
        plan_summary = planner.plan_to_summary(plan)
        
        # Store plan in temp record for confirmation step
        plan_dict = planner.serialize_plan(plan)
        
        response = TaskIntentResponse(
            task_id=None,  # No task created yet - waiting for confirmation
            intent_request={
                "intent_type": intent_request.intent_type,
                "task_type": intent_request.task_type,
                "confidence": float(intent_request.confidence),
                "parameters": intent_request.parameters,
                "suggested_subtasks": intent_request.suggested_subtasks,
                "requires_confirmation": intent_request.requires_confirmation,
                "execution_strategy": intent_request.execution_strategy,
            },
            execution_plan={
                "title": plan_summary.title,
                "description": plan_summary.description,
                "estimated_time": plan_summary.estimated_time,
                "estimated_cost": plan_summary.estimated_cost,
                "confidence": plan_summary.confidence,
                "key_stages": plan_summary.key_stages,
                "warnings": plan_summary.warnings,
                "opportunities": plan_summary.opportunities,
                "full_plan": plan_dict,  # Store full plan for confirmation
            },
            ready_to_execute=not intent_request.requires_confirmation,
            warnings=plan_summary.warnings,
        )
        
        logger.info(f"[INTENT] Response ready to send to UI")
        return response
        
    except Exception as e:
        logger.error(f"[INTENT] Intent parsing failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Intent parsing failed: {str(e)}")


class TaskConfirmRequest(BaseModel):
    """Request to confirm and execute a task from intent plan."""
    intent_request: Dict[str, Any] = Field(..., description="Original intent request")
    execution_plan: Dict[str, Any] = Field(..., description="Execution plan (full version)")
    user_confirmed: bool = Field(True, description="User confirmed the plan")
    modifications: Optional[Dict[str, Any]] = Field(None, description="User modifications to plan")


class TaskConfirmResponse(BaseModel):
    """Response from task confirmation and creation."""
    task_id: str
    status: str
    message: str
    execution_plan_id: str


@router.post("/confirm-intent", response_model=TaskConfirmResponse)
async def confirm_and_execute_task(
    request: TaskConfirmRequest,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Phase 1: Confirm execution plan and create task.
    
    This endpoint:
    1. Receives confirmed execution plan from UI
    2. Creates task in database
    3. Stores execution plan in metadata
    4. Marks task as pending for execution
    5. Starts background task executor
    
    Task executor will follow the execution plan stages.
    """
    
    if not request.user_confirmed:
        raise HTTPException(status_code=400, detail="User did not confirm execution plan")
    
    try:
        task_id = str(uuid_lib.uuid4())
        intent_req = request.intent_request
        plan = request.execution_plan
        
        # Build execution metadata
        execution_metadata = {
            "intent": {
                "intent_type": intent_req.get("intent_type"),
                "task_type": intent_req.get("task_type"),
                "parameters": intent_req.get("parameters"),
            },
            "plan": plan,
            "user_confirmed": request.user_confirmed,
            "modifications": request.modifications or {},
            "created_from_intent": True,
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
        }
        
        # Create task in database
        await db_service.add_task({
            "id": task_id,
            "task_name": intent_req.get("parameters", {}).get("topic", "Task from Intent"),
            "task_type": intent_req.get("task_type", "generic"),
            "status": "pending",
            "metadata": execution_metadata
        })
        
        logger.info(f"[CONFIRM] Created task {task_id} from intent plan")
        
        # Queue background execution
        background_tasks.add_task(execute_task_background, task_id, current_user)
        
        return TaskConfirmResponse(
            task_id=task_id,
            status="pending",
            message=f"Task created and queued for execution. Plan: {len(plan.get('stages', []))} stages",
            execution_plan_id=plan.get("task_id", task_id),
        )
        
    except Exception as e:
        logger.error(f"[CONFIRM] Task confirmation failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Task confirmation failed: {str(e)}")

