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
import json
import logging
import os

from utils.error_responses import ErrorResponseBuilder
from utils.route_utils import get_database_dependency
from schemas.model_converter import ModelConverter


# Import async database service
from services.database_service import DatabaseService
from routes.auth_unified import get_current_user
from schemas.task_schemas import (
    TaskCreateRequest,
    TaskStatusUpdateRequest,
    TaskListResponse,
    MetricsResponse,
    IntentTaskRequest,
    TaskIntentResponse,
    TaskConfirmRequest,
    TaskConfirmResponse,
)
from schemas.unified_task_response import UnifiedTaskResponse

# Configure logging
logger = logging.getLogger(__name__)

# Configure router with prefix and tags
router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# ============================================================================
# ENDPOINTS
# ============================================================================


@router.post("", response_model=Dict[str, Any], summary="Create new task", status_code=201)
async def create_task(
    request: TaskCreateRequest,
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
    background_tasks: BackgroundTasks = None,
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
                    "type": "validation_error",
                },
            )

        if not request.topic or not str(request.topic).strip():
            logger.error("❌ Task creation failed: topic is empty")
            raise HTTPException(
                status_code=422,
                detail={
                    "field": "topic",
                    "message": "topic is required and cannot be empty",
                    "type": "validation_error",
                },
            )

        # Log incoming request
        logger.info(f"📥 [TASK_CREATE] Received request:")
        logger.info(f"   - task_name: {request.task_name}")
        logger.info(f"   - topic: {request.topic}")
        logger.info(f"   - category: {request.category}")
        logger.info(f"   - model_selections: {request.model_selections}")
        logger.info(f"   - quality_preference: {request.quality_preference}")
        logger.info(f"   - estimated_cost: {request.estimated_cost}")
        logger.info(f"   - user_id: {current_user.get('id', 'system')}")

        # Extract style, tone, and target_length from metadata if available
        metadata = request.metadata or {}
        style = metadata.get("style")
        tone = metadata.get("tone")
        # Map word_count to target_length if target_length is missing
        target_length = metadata.get("target_length") or metadata.get("word_count")

        # Create task data
        task_id = str(uuid_lib.uuid4())
        task_data = {
            "id": task_id,
            "task_name": request.task_name.strip(),
            "topic": request.topic.strip(),
            "primary_keyword": (request.primary_keyword or "").strip(),
            "target_audience": (request.target_audience or "").strip(),
            "category": (request.category or "general").strip(),
            "style": style,
            "tone": tone,
            "target_length": target_length,
            "model_selections": request.model_selections or {},
            "quality_preference": request.quality_preference or "balanced",
            "estimated_cost": request.estimated_cost or 0.0,
            "status": "pending",
            "agent_id": "content-agent",
            "user_id": current_user.get("id", "system"),  # Capture user for writing sample retrieval
            "writing_style_id": request.writing_style_id,  # UUID of writing sample for style guidance
            "metadata": request.metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(f"🔄 [TASK_CREATE] Generated task_id: {task_id}")
        logger.info(f"🔄 [TASK_CREATE] Task data prepared:")
        logger.info(
            f"   - Basic: {json.dumps({k: v for k, v in task_data.items() if k not in ['metadata', 'model_selections']}, indent=2)}"
        )
        logger.info(f"   - Model Selections: {task_data['model_selections']}")
        logger.info(
            f"   - Cost Info: quality={task_data['quality_preference']}, estimated=${task_data['estimated_cost']:.4f}"
        )

        # Add task to database
        logger.info(f"💾 [TASK_CREATE] Inserting into database...")
        returned_task_id = await db_service.add_task(task_data)
        logger.info(
            f"✅ [TASK_CREATE] Database insert successful - returned task_id: {returned_task_id}"
        )

        # Verify task was inserted
        logger.info(f"🔍 [TASK_CREATE] Verifying task in database...")
        verify_task = await db_service.get_task(returned_task_id)
        if verify_task:
            logger.info(f"✅ [TASK_CREATE] Verification SUCCESS - Task found in database")
            # verify_task is now a dict
            logger.info(f"   - Status: {verify_task.get('status', 'unknown')}")
            logger.info(f"   - Created: {verify_task.get('created_at', 'unknown')}")
        else:
            logger.error(
                f"❌ [TASK_CREATE] Verification FAILED - Task NOT found in database after insert!"
            )

        response = {
            "id": returned_task_id,
            "status": "pending",
            "created_at": task_data["created_at"],
            "message": "Task created successfully",
        }
        logger.info(f"📤 [TASK_CREATE] Returning response: {response}")

        # Content generation is now handled by the TaskExecutor background service which polls for pending tasks.
        # The TaskExecutor uses the full multi-agent UnifiedOrchestrator pipeline.

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ [TASK_CREATE] Exception: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"message": f"Failed to create task: {str(e)}", "type": "internal_error"},
        )


@router.get("", response_model=TaskListResponse, summary="List tasks")
async def list_tasks(
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(20, ge=1, le=1000, description="Results per page (default: 20, max: 1000)"),
    status: Optional[str] = Query(None, description="Filter by status (optional)"),
    category: Optional[str] = Query(None, description="Filter by category (optional)"),
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    List tasks with database-level pagination and filtering.

    **Optimizations:**
    - Database-level pagination (not in-memory, much faster!)
    - Server-side filtering (status, category)
    - Default limit: 20 (retrieves only what user sees)
    - Max limit: 1000 (prevents abuse while allowing bulk retrieval)
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
            offset=offset, limit=limit, status=status, category=category
        )

        # Convert to response schema with proper type conversions
        task_responses = [
            UnifiedTaskResponse(**ModelConverter.task_response_to_unified(ModelConverter.to_task_response(task)))
            for task in tasks
        ]

        return TaskListResponse(tasks=task_responses, total=total, offset=offset, limit=limit)

    except Exception as e:
        logger.error(f"Error listing tasks: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch tasks: {str(e)}")


@router.get("/{task_id}", response_model=UnifiedTaskResponse, summary="Get task details")
async def get_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
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
        return UnifiedTaskResponse(**ModelConverter.task_response_to_unified(ModelConverter.to_task_response(task)))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch task: {str(e)}")


@router.patch("/{task_id}", response_model=UnifiedTaskResponse, summary="Update task status")
async def update_task(
    task_id: str,
    update_data: TaskStatusUpdateRequest,
    current_user: dict = Depends(get_current_user),
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
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid task ID format")

        # Fetch task
        task = await db_service.get_task(task_id)

        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

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
            update_dict["result"] = update_data.result

        # Merge metadata if provided
        if update_data.metadata:
            task["metadata"] = {**(task.get("metadata") or {}), **update_data.metadata}
            update_dict["metadata"] = task["metadata"]

        # Update task status - pass result dict (asyncpg handles JSONB conversion)
        await db_service.update_task_status(
            task_id,
            update_data.status,
            result=json.dumps(update_data.result) if update_data.result else None,
        )

        # Fetch updated task
        updated_task = await db_service.get_task(task_id)

        # Convert to response schema with proper type conversions
        return UnifiedTaskResponse(**ModelConverter.task_response_to_unified(ModelConverter.to_task_response(updated_task)))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update task: {str(e)}")


@router.get("/metrics/summary", response_model=MetricsResponse, summary="Get task metrics")
async def get_metrics(
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
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
# CONTENT CLEANING UTILITIES
# ============================================================================


def clean_generated_content(content: str, title: str = "") -> str:
    """
    Clean up LLM-generated content by removing:
    - Leading markdown titles (# Title, ## Title)
    - "Introduction:" prefixes
    - Duplicate title text
    - Extra whitespace

    Args:
        content: Raw generated content from LLM
        title: Blog post title to remove if it appears in content

    Returns:
        Cleaned content ready for publishing
    """
    import re

    if not content:
        return content

    # Remove markdown-style titles at the start
    # Remove leading # or ## followed by space and text (with optional title match)
    content = re.sub(r"^#+\s+[^\n]*\n?", "", content.strip())

    # Remove "Title:" or "Title: " at the very beginning
    content = re.sub(r"^Title:\s*", "", content)

    # Remove common section prefixes if they appear as standalone lines
    content = re.sub(r"^\s*Introduction:\s*\n?", "", content, flags=re.MULTILINE)
    content = re.sub(r"^\s*Conclusion:\s*\n?", "", content, flags=re.MULTILINE)

    # If a title was provided, remove it if it appears as a standalone paragraph
    if title:
        # Escape special regex characters in title
        title_escaped = re.escape(title)
        # Remove the title if it appears on its own line
        content = re.sub(
            rf"^\s*{title_escaped}\s*\n+", "", content, flags=re.MULTILINE | re.IGNORECASE
        )

    # Remove extra blank lines (more than 2 consecutive newlines)
    content = re.sub(r"\n{3,}", "\n\n", content)

    # Strip leading/trailing whitespace
    content = content.strip()

    return content


# ============================================================================
# MODEL SELECTION HELPER
# ============================================================================


def get_model_for_phase(
    phase: str, model_selections: Dict[str, str], quality_preference: str
) -> str:
    """
    Get the appropriate LLM model for a given generation phase.

    Args:
        phase: Generation phase ('draft', 'assess', 'refine', 'finalize')
        model_selections: User's per-phase model selections (e.g., {"draft": "gpt-4", ...})
        quality_preference: Fallback preference if specific model not selected (fast, balanced, quality)

    Returns:
        Model identifier string (e.g., "gpt-4", "ollama/llama2")
    """
    # Phase-specific model defaults by quality preference
    defaults_by_phase = {
        # FAST (cheapest options)
        "fast": {
            "research": "ollama/phi",
            "outline": "ollama/phi",
            "draft": "ollama/mistral",
            "assess": "ollama/mistral",
            "refine": "ollama/mistral",
            "finalize": "ollama/phi",
        },
        # BALANCED (mix of cost and quality)
        "balanced": {
            "research": "ollama/mistral",
            "outline": "ollama/mistral",
            "draft": "ollama/mistral",
            "assess": "ollama/mistral",
            "refine": "ollama/mistral",
            "finalize": "ollama/mistral",
        },
        # QUALITY (best models)
        "quality": {
            "research": "gpt-3.5-turbo",
            "outline": "gpt-3.5-turbo",
            "draft": "gpt-4",
            "assess": "gpt-4",
            "refine": "gpt-4",
            "finalize": "gpt-4",
        },
    }

    # Try to get specific model selection for this phase
    if model_selections and phase in model_selections:
        selected = model_selections[phase]
        # If user selected a specific model (not "auto"), use it
        if selected and selected != "auto":
            logger.info(f"[BG_TASK] Using selected model for {phase}: {selected}")
            return selected

    # Fall back to quality preference default
    quality = quality_preference or "balanced"
    if quality not in defaults_by_phase:
        quality = "balanced"

    model = defaults_by_phase[quality].get(phase, "ollama/mistral")
    logger.info(f"[BG_TASK] Using {quality} quality model for {phase}: {model}")
    return model


# ============================================================================
# PHASE 1: INTENT-BASED TASK CREATION (Natural Language Support)
# ============================================================================


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
            request.user_input, request.user_context or {}
        )

        logger.info(
            f"[INTENT] Detected intent: {intent_request.intent_type} → task_type: {intent_request.task_type}"
        )
        logger.info(f"[INTENT] Suggested subtasks: {intent_request.suggested_subtasks}")
        logger.info(f"[INTENT] Parameters: {intent_request.parameters}")

        # Step 2: Generate execution plan
        plan = await planner.generate_plan(intent_request, request.business_metrics or {})

        logger.info(
            f"[INTENT] Generated plan: {plan.total_estimated_duration_ms}ms, ${plan.total_estimated_cost:.2f}"
        )

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
        await db_service.add_task(
            {
                "id": task_id,
                "task_name": intent_req.get("parameters", {}).get("topic", "Task from Intent"),
                "task_type": intent_req.get("task_type", "generic"),
                "status": "pending",
                "metadata": execution_metadata,
            }
        )

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


# ============================================================================
# TASK APPROVAL & PUBLISHING ENDPOINTS
# ============================================================================


@router.post("/{task_id}/approve", response_model=UnifiedTaskResponse, summary="Approve task for publishing")
async def approve_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Approve a task for publishing.
    
    Changes task status from 'completed' to 'approved'.
    
    **Parameters:**
    - task_id: Task UUID
    
    **Returns:**
    - Updated task with status 'approved'
    
    **Example cURL:**
    ```bash
    curl -X POST http://localhost:8000/api/tasks/550e8400-e29b-41d4-a716-446655440000/approve \
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
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Check if task is in a state that can be approved
        current_status = task.get("status", "unknown")
        if current_status not in ["completed", "pending", "approved", "awaiting_approval"]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve task with status '{current_status}'. Must be 'completed', 'pending', 'awaiting_approval', or 'approved'."
            )

        # Update task status to approved
        logger.info(f"Approving task {task_id} (current status: {current_status})")
        approval_metadata = {
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "approved_by": current_user.get("id")
        }
        await db_service.update_task_status(
            task_id,
            "approved",
            result=json.dumps({"metadata": approval_metadata})
        )

        # Fetch updated task
        updated_task = await db_service.get_task(task_id)

        # Convert to response schema
        return UnifiedTaskResponse(**ModelConverter.task_response_to_unified(ModelConverter.to_task_response(updated_task)))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to approve task {task_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to approve task: {str(e)}")


@router.post("/{task_id}/publish", response_model=UnifiedTaskResponse, summary="Publish approved task")
async def publish_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
    background_tasks: BackgroundTasks = None,
):
    """
    Publish an approved task to specified channels.
    
    Changes task status from 'approved' to 'published'.
    Handles distribution to CMS, social media, email, etc.
    
    **Parameters:**
    - task_id: Task UUID
    
    **Returns:**
    - Updated task with status 'published'
    
    **Example cURL:**
    ```bash
    curl -X POST http://localhost:8000/api/tasks/550e8400-e29b-41d4-a716-446655440000/publish \
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
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Check if task is approved
        current_status = task.get("status", "unknown")
        if current_status != "approved":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot publish task with status '{current_status}'. Must be 'approved'."
            )

        # Update task status to published
        logger.info(f"Publishing task {task_id}")
        publish_metadata = {
            "published_at": datetime.now(timezone.utc).isoformat(),
            "published_by": current_user.get("id")
        }
        await db_service.update_task_status(
            task_id,
            "published",
            result=json.dumps({"metadata": publish_metadata})
        )

        # Fetch updated task
        updated_task = await db_service.get_task(task_id)

        # Convert to response schema
        return UnifiedTaskResponse(**ModelConverter.task_response_to_unified(ModelConverter.to_task_response(updated_task)))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to publish task {task_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to publish task: {str(e)}")


@router.post("/{task_id}/reject", response_model=UnifiedTaskResponse, summary="Reject task for revision")
async def reject_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Reject a task and send it back for revision.
    
    Changes task status to 'rejected' with optional feedback.
    Task can be revised and resubmitted.
    
    **Parameters:**
    - task_id: Task UUID
    
    **Returns:**
    - Updated task with status 'rejected'
    
    **Example cURL:**
    ```bash
    curl -X POST http://localhost:8000/api/tasks/550e8400-e29b-41d4-a716-446655440000/reject \
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
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Check if task is in a state that can be rejected
        current_status = task.get("status", "unknown")
        if current_status not in ["completed", "approved"]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reject task with status '{current_status}'. Must be 'completed' or 'approved'."
            )

        # Update task status to rejected
        logger.info(f"Rejecting task {task_id} (current status: {current_status})")
        reject_metadata = {
            "rejected_at": datetime.now(timezone.utc).isoformat(),
            "rejected_by": current_user.get("id")
        }
        await db_service.update_task_status(
            task_id,
            "rejected",
            result=json.dumps({"metadata": reject_metadata})
        )

        # Fetch updated task
        updated_task = await db_service.get_task(task_id)

        # Convert to response schema
        return UnifiedTaskResponse(**ModelConverter.task_response_to_unified(ModelConverter.to_task_response(updated_task)))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to reject task {task_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to reject task: {str(e)}")
