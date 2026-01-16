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
    UnifiedTaskRequest,
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


@router.post("", response_model=Dict[str, Any], summary="Create task - unified endpoint for all task types", status_code=201)
async def create_task(
    request: UnifiedTaskRequest,
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
    background_tasks: BackgroundTasks = None,
):
    """
    Unified task creation endpoint - routes to appropriate handler based on task_type.
    
    **Supported Task Types:**
    - blog_post: Blog content generation with self-critiquing pipeline
    - social_media: Multi-platform social content (Twitter, LinkedIn, Instagram)
    - email: Email campaign generation
    - newsletter: Newsletter content generation
    - business_analytics: Business metrics analysis and insights
    - data_retrieval: Data extraction from multiple sources
    - market_research: Competitive intelligence and market analysis
    - financial_analysis: Financial data analysis and reporting
    
    **Common Parameters (all tasks):**
    - task_type: REQUIRED - Type of task to create
    - topic: REQUIRED - Task topic/subject/query
    - models_by_phase: Optional per-phase model selection
    - quality_preference: fast|balanced|quality (default: balanced)
    - metadata: Optional additional metadata
    
    **Content Task Parameters (blog_post, social_media, email, newsletter):**
    - style: technical|narrative|listicle|educational|thought-leadership
    - tone: professional|casual|academic|inspirational
    - target_length: 200-5000 words (blog_post)
    - generate_featured_image: true|false (blog_post)
    - platforms: List of platforms (social_media)
    - tags: Content tags
    
    **Analytics Task Parameters (business_analytics):**
    - metrics: List of metrics to analyze (revenue, churn, conversion_rate)
    - time_period: Analysis period (last_month, last_quarter, ytd)
    - business_context: Industry, size, goals context
    
    **Data Task Parameters (data_retrieval):**
    - data_sources: List of source types (postgres, s3, api)
    - filters: Query filters and parameters
    
    **Returns:**
    - task_id: UUID of created task
    - status: pending (will be picked up by TaskExecutor)
    - created_at: ISO timestamp
    
    **Example Requests:**
    
    Blog Post:
    ```json
    {
      "task_type": "blog_post",
      "topic": "AI in Healthcare",
      "style": "technical",
      "tone": "professional",
      "target_length": 2000,
      "generate_featured_image": true,
      "quality_preference": "balanced"
    }
    ```
    
    Social Media:
    ```json
    {
      "task_type": "social_media",
      "topic": "New Product Launch",
      "platforms": ["twitter", "linkedin"],
      "tone": "casual",
      "quality_preference": "fast"
    }
    ```
    
    Business Analytics:
    ```json
    {
      "task_type": "business_analytics",
      "topic": "Q4 Revenue Analysis",
      "metrics": ["revenue", "churn_rate", "customer_acquisition"],
      "time_period": "last_quarter",
      "business_context": {"industry": "SaaS", "size": "mid-market"}
    }
    ```
    """
    try:
        # Validate required fields
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

        logger.info(f"📥 [UNIFIED_TASK_CREATE] Received: task_type={request.task_type}, topic={request.topic}")

        # Route based on task_type
        if request.task_type == "blog_post":
            return await _handle_blog_post_creation(request, current_user, db_service)
        
        elif request.task_type == "social_media":
            return await _handle_social_media_creation(request, current_user, db_service)
        
        elif request.task_type == "email":
            return await _handle_email_creation(request, current_user, db_service)
        
        elif request.task_type == "newsletter":
            return await _handle_newsletter_creation(request, current_user, db_service)
        
        elif request.task_type == "business_analytics":
            return await _handle_business_analytics_creation(request, current_user, db_service)
        
        elif request.task_type == "data_retrieval":
            return await _handle_data_retrieval_creation(request, current_user, db_service)
        
        elif request.task_type == "market_research":
            return await _handle_market_research_creation(request, current_user, db_service)
        
        elif request.task_type == "financial_analysis":
            return await _handle_financial_analysis_creation(request, current_user, db_service)
        
        else:
            raise HTTPException(
                status_code=400,
                detail={"message": f"Unknown task_type: {request.task_type}", "supported": ["blog_post", "social_media", "email", "newsletter", "business_analytics", "data_retrieval", "market_research", "financial_analysis"]}
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ [UNIFIED_TASK_CREATE] Exception: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"message": f"Failed to create task: {str(e)}", "type": "internal_error"},
        )


# ============================================================================
# TASK TYPE HANDLERS
# ============================================================================


async def _handle_blog_post_creation(request: UnifiedTaskRequest, current_user: dict, db_service: DatabaseService) -> Dict[str, Any]:
    """Handle blog post task creation"""
    from services.content_router_service import process_content_generation_task
    import asyncio
    
    task_id = str(uuid_lib.uuid4())
    
    task_data = {
        "id": task_id,
        "task_name": f"Blog Post: {request.topic}",
        "task_type": "blog_post",
        "topic": request.topic.strip(),
        "category": request.category or "general",
        "style": request.style,
        "tone": request.tone,
        "target_length": request.target_length or 1500,
        "model_selections": request.models_by_phase or {},
        "quality_preference": request.quality_preference or "balanced",
        "status": "pending",
        "user_id": current_user.get("id", "system"),
        "metadata": {**(request.metadata or {}), "generate_featured_image": request.generate_featured_image, "tags": request.tags},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    # Store in database
    returned_task_id = await db_service.add_task(task_data)
    logger.info(f"✅ [BLOG_TASK] Created: {returned_task_id}")
    
    # Schedule background generation
    async def _run_blog_generation():
        try:
            await process_content_generation_task(
                topic=request.topic,
                style=request.style or "narrative",
                tone=request.tone or "professional",
                target_length=request.target_length or 1500,
                tags=request.tags,
                generate_featured_image=request.generate_featured_image or True,
                database_service=db_service,
                task_id=task_id,
                models_by_phase=request.models_by_phase,
                quality_preference=request.quality_preference or "balanced",
            )
        except Exception as e:
            logger.error(f"Blog generation failed: {e}", exc_info=True)
            await db_service.update_task(task_id, {"status": "failed", "error_message": str(e)})
    
    asyncio.create_task(_run_blog_generation())
    
    return {
        "id": returned_task_id,
        "task_type": "blog_post",
        "status": "pending",
        "created_at": task_data["created_at"],
        "message": "Blog post task created and queued",
    }


async def _handle_social_media_creation(request: UnifiedTaskRequest, current_user: dict, db_service: DatabaseService) -> Dict[str, Any]:
    """Handle social media task creation"""
    task_id = str(uuid_lib.uuid4())
    
    task_data = {
        "id": task_id,
        "task_name": f"Social Media: {request.topic}",
        "task_type": "social_media",
        "topic": request.topic.strip(),
        "category": request.category or "general",
        "tone": request.tone or "professional",
        "style": request.style,
        "model_selections": request.models_by_phase or {},
        "quality_preference": request.quality_preference or "balanced",
        "status": "pending",
        "user_id": current_user.get("id", "system"),
        "metadata": {**(request.metadata or {}), "platforms": request.platforms, "tags": request.tags},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    returned_task_id = await db_service.add_task(task_data)
    logger.info(f"✅ [SOCIAL_TASK] Created: {returned_task_id} - Platforms: {request.platforms}")
    
    return {
        "id": returned_task_id,
        "task_type": "social_media",
        "status": "pending",
        "created_at": task_data["created_at"],
        "message": f"Social media task created for platforms: {', '.join(request.platforms or ['all'])}",
    }


async def _handle_email_creation(request: UnifiedTaskRequest, current_user: dict, db_service: DatabaseService) -> Dict[str, Any]:
    """Handle email task creation"""
    task_id = str(uuid_lib.uuid4())
    
    task_data = {
        "id": task_id,
        "task_name": f"Email: {request.topic}",
        "task_type": "email",
        "topic": request.topic.strip(),
        "category": request.category or "general",
        "tone": request.tone or "professional",
        "model_selections": request.models_by_phase or {},
        "quality_preference": request.quality_preference or "balanced",
        "status": "pending",
        "user_id": current_user.get("id", "system"),
        "metadata": {**(request.metadata or {}), "tags": request.tags},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    returned_task_id = await db_service.add_task(task_data)
    logger.info(f"✅ [EMAIL_TASK] Created: {returned_task_id}")
    
    return {
        "id": returned_task_id,
        "task_type": "email",
        "status": "pending",
        "created_at": task_data["created_at"],
        "message": "Email task created and queued",
    }


async def _handle_newsletter_creation(request: UnifiedTaskRequest, current_user: dict, db_service: DatabaseService) -> Dict[str, Any]:
    """Handle newsletter task creation"""
    task_id = str(uuid_lib.uuid4())
    
    task_data = {
        "id": task_id,
        "task_name": f"Newsletter: {request.topic}",
        "task_type": "newsletter",
        "topic": request.topic.strip(),
        "category": request.category or "general",
        "model_selections": request.models_by_phase or {},
        "quality_preference": request.quality_preference or "balanced",
        "status": "pending",
        "user_id": current_user.get("id", "system"),
        "metadata": {**(request.metadata or {}), "tags": request.tags},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    returned_task_id = await db_service.add_task(task_data)
    logger.info(f"✅ [NEWSLETTER_TASK] Created: {returned_task_id}")
    
    return {
        "id": returned_task_id,
        "task_type": "newsletter",
        "status": "pending",
        "created_at": task_data["created_at"],
        "message": "Newsletter task created and queued",
    }


async def _handle_business_analytics_creation(request: UnifiedTaskRequest, current_user: dict, db_service: DatabaseService) -> Dict[str, Any]:
    """Handle business analytics task creation"""
    task_id = str(uuid_lib.uuid4())
    
    task_data = {
        "id": task_id,
        "task_name": f"Analytics: {request.topic}",
        "task_type": "business_analytics",
        "topic": request.topic.strip(),
        "category": request.category or "general",
        "model_selections": request.models_by_phase or {},
        "quality_preference": request.quality_preference or "balanced",
        "status": "pending",
        "user_id": current_user.get("id", "system"),
        "metadata": {**(request.metadata or {}), "metrics": request.metrics, "time_period": request.time_period, "business_context": request.business_context},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    returned_task_id = await db_service.add_task(task_data)
    logger.info(f"✅ [ANALYTICS_TASK] Created: {returned_task_id} - Metrics: {request.metrics}")
    
    return {
        "id": returned_task_id,
        "task_type": "business_analytics",
        "status": "pending",
        "created_at": task_data["created_at"],
        "message": f"Business analytics task created - Analyzing: {', '.join(request.metrics or [])}",
    }


async def _handle_data_retrieval_creation(request: UnifiedTaskRequest, current_user: dict, db_service: DatabaseService) -> Dict[str, Any]:
    """Handle data retrieval task creation"""
    task_id = str(uuid_lib.uuid4())
    
    task_data = {
        "id": task_id,
        "task_name": f"Data Retrieval: {request.topic}",
        "task_type": "data_retrieval",
        "topic": request.topic.strip(),
        "category": request.category or "general",
        "model_selections": request.models_by_phase or {},
        "status": "pending",
        "user_id": current_user.get("id", "system"),
        "metadata": {**(request.metadata or {}), "data_sources": request.data_sources, "filters": request.filters},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    returned_task_id = await db_service.add_task(task_data)
    logger.info(f"✅ [DATA_TASK] Created: {returned_task_id} - Sources: {request.data_sources}")
    
    return {
        "id": returned_task_id,
        "task_type": "data_retrieval",
        "status": "pending",
        "created_at": task_data["created_at"],
        "message": f"Data retrieval task created from sources: {', '.join(request.data_sources or [])}",
    }


async def _handle_market_research_creation(request: UnifiedTaskRequest, current_user: dict, db_service: DatabaseService) -> Dict[str, Any]:
    """Handle market research task creation"""
    task_id = str(uuid_lib.uuid4())
    
    task_data = {
        "id": task_id,
        "task_name": f"Market Research: {request.topic}",
        "task_type": "market_research",
        "topic": request.topic.strip(),
        "category": request.category or "general",
        "model_selections": request.models_by_phase or {},
        "quality_preference": request.quality_preference or "balanced",
        "status": "pending",
        "user_id": current_user.get("id", "system"),
        "metadata": {**(request.metadata or {})},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    returned_task_id = await db_service.add_task(task_data)
    logger.info(f"✅ [MARKET_RESEARCH_TASK] Created: {returned_task_id}")
    
    return {
        "id": returned_task_id,
        "task_type": "market_research",
        "status": "pending",
        "created_at": task_data["created_at"],
        "message": "Market research task created and queued",
    }


async def _handle_financial_analysis_creation(request: UnifiedTaskRequest, current_user: dict, db_service: DatabaseService) -> Dict[str, Any]:
    """Handle financial analysis task creation"""
    task_id = str(uuid_lib.uuid4())
    
    task_data = {
        "id": task_id,
        "task_name": f"Financial Analysis: {request.topic}",
        "task_type": "financial_analysis",
        "topic": request.topic.strip(),
        "category": request.category or "general",
        "model_selections": request.models_by_phase or {},
        "quality_preference": request.quality_preference or "balanced",
        "status": "pending",
        "user_id": current_user.get("id", "system"),
        "metadata": {**(request.metadata or {})},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    
    returned_task_id = await db_service.add_task(task_data)
    logger.info(f"✅ [FINANCIAL_ANALYSIS_TASK] Created: {returned_task_id}")
    
    return {
        "id": returned_task_id,
        "task_type": "financial_analysis",
        "status": "pending",
        "created_at": task_data["created_at"],
        "message": "Financial analysis task created and queued",
    }


# ============================================================================
# RETRIEVAL ENDPOINTS
# ============================================================================


@router.get("", response_model=TaskListResponse, summary="List all tasks with pagination")
async def list_tasks(
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(20, ge=1, le=1000, description="Pagination limit"),
    status: Optional[str] = Query(None, description="Filter by status (queued, pending, running, completed, failed)"),
    category: Optional[str] = Query(None, description="Filter by category"),
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    List all tasks with pagination and optional filtering.
    
    **Parameters:**
    - offset: Pagination offset (default: 0)
    - limit: Pagination limit (default: 20, max: 1000)
    - status: Optional status filter
    - category: Optional category filter
    
    **Returns:**
    - List of tasks with total count
    
    **Example cURL:**
    ```bash
    curl -X GET "http://localhost:8000/api/tasks?offset=0&limit=20" \\
      -H "Authorization: Bearer TOKEN"
    ```
    """
    try:
        # get_tasks_paginated returns a tuple (tasks, total)
        tasks, total = await db_service.get_tasks_paginated(
            offset=offset,
            limit=limit,
            status=status,
            category=category
        )
        
        # Convert raw task dicts to UnifiedTaskResponse objects if needed
        validated_tasks = []
        for task in tasks:
            if isinstance(task, dict):
                # Fix type mismatches from database
                # Ensure id is a string
                if "id" in task and isinstance(task["id"], int):
                    task["id"] = str(task["id"])
                
                # Parse seo_keywords if it's a JSON string
                if "seo_keywords" in task and isinstance(task["seo_keywords"], str):
                    try:
                        task["seo_keywords"] = json.loads(task["seo_keywords"])
                    except (json.JSONDecodeError, TypeError):
                        task["seo_keywords"] = []
                
                validated_tasks.append(UnifiedTaskResponse(**task))
            else:
                validated_tasks.append(task)
        
        return TaskListResponse(
            tasks=validated_tasks,
            total=total,
            offset=offset,
            limit=limit,
        )
    except Exception as e:
        logger.error(f"Failed to list tasks: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to list tasks: {str(e)}")


@router.get("/{task_id}", response_model=UnifiedTaskResponse, summary="Get task details")
async def get_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Get details of a specific task.
    
    **Parameters:**
    - task_id: Task UUID
    
    **Returns:**
    - Complete task object with all details
    
    **Example cURL:**
    ```bash
    curl -X GET "http://localhost:8000/api/tasks/{task_id}" \\
      -H "Authorization: Bearer TOKEN"
    ```
    """
    try:
        task = await db_service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        return task
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch task {task_id}: {str(e)}")
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
    - task_id: Task ID (UUID or numeric ID for backwards compatibility)
    
    **Returns:**
    - Updated task with status 'approved'
    
    **Example cURL:**
    ```bash
    curl -X POST http://localhost:8000/api/tasks/550e8400-e29b-41d4-a716-446655440000/approve \
      -H "Authorization: Bearer YOUR_JWT_TOKEN"
    ```
    """
    try:
        # Accept both UUID and numeric task IDs (backwards compatibility)
        # Try to convert numeric ID to UUID if it's a string number
        try:
            UUID(task_id)
        except ValueError:
            # If not a valid UUID, check if it's a numeric ID (legacy tasks)
            if task_id.isdigit():
                # Convert numeric ID to string (it will work with get_task as-is)
                pass
            else:
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
        # Accept both UUID and numeric task IDs (backwards compatibility)
        try:
            UUID(task_id)
        except ValueError:
            # If not a valid UUID, check if it's a numeric ID (legacy tasks)
            if not task_id.isdigit():
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
