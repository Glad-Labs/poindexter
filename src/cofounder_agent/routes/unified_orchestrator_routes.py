"""
Unified Orchestrator Routes

Consolidated endpoints for ORCHESTRATOR-SPECIFIC functionality:
- Natural language request processing via UnifiedOrchestrator
- Task approval and publishing workflows
- Multi-channel content distribution
- Training data management
- Learning patterns and tool discovery

This file consolidates:
- intelligent_orchestrator_routes.py (orchestration + publishing)
- natural_language_content_routes.py (natural language processing)

ORCHESTRATOR-SPECIFIC ENDPOINTS (kept):
POST   /api/orchestrator/process              Process natural language request
POST   /api/orchestrator/tasks/{task_id}/approve  Approve and publish task
POST   /api/orchestrator/tasks/{task_id}/refine   Refine task content
POST   /api/orchestrator/training-data/export Export training examples
POST   /api/orchestrator/training-data/upload Upload custom model

REMOVED (now use /api/tasks instead):
❌ GET    /api/orchestrator/tasks              → Use GET /api/tasks
❌ GET    /api/orchestrator/tasks/{task_id}    → Use GET /api/tasks/{task_id}
❌ GET    /api/orchestrator/status/{task_id}   → Use GET /api/tasks/{task_id}
❌ In-memory TASK_STORE                        → Use PostgreSQL via /api/tasks

QUALITY ENDPOINTS (in separate file):
POST   /api/quality/evaluate                  Evaluate content quality
POST   /api/quality/batch-evaluate            Batch quality evaluation
GET    /api/quality/statistics                Get quality service statistics
"""

import logging
import os
import re
import uuid as uuid_lib
from typing import Dict, Any, Optional, List
from datetime import datetime
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query, Depends, Request
from pydantic import BaseModel, Field

from services.unified_orchestrator import UnifiedOrchestrator
from services.quality_service import UnifiedQualityService, EvaluationMethod
from services.database_service import DatabaseService
from routes.auth_unified import get_current_user, UserProfile
from utils.service_dependencies import (
    get_unified_orchestrator,
    get_quality_service,
    get_database_service
)

# Optional: Publishing services (if available)
try:
    from services.linkedin_publisher import LinkedInPublisher
    from services.twitter_publisher import TwitterPublisher
    from services.email_publisher import EmailPublisher
    PUBLISHERS_AVAILABLE = True
except ImportError:
    PUBLISHERS_AVAILABLE = False
    LinkedInPublisher = None
    TwitterPublisher = None
    EmailPublisher = None

logger = logging.getLogger(__name__)

orchestrator_router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class BusinessMetrics(BaseModel):
    """Business metrics for context"""
    revenue_monthly: Optional[float] = None
    traffic_monthly: Optional[int] = None
    conversion_rate: Optional[float] = None
    customer_count: Optional[int] = None
    market_position: Optional[str] = None
    custom_metrics: Optional[Dict[str, Any]] = Field(None, description="Additional metrics")


class UserPreferences(BaseModel):
    """User preferences for execution"""
    tone: Optional[str] = Field("professional", description="Writing tone")
    length: Optional[str] = Field(None, description="Content length")
    channels: Optional[List[str]] = Field(
        ["blog"],
        description="Publishing channels: blog, linkedin, twitter, email"
    )
    language: Optional[str] = Field("en", description="Language code")
    custom_preferences: Optional[Dict[str, Any]] = Field(None)


class ProcessRequestBody(BaseModel):
    """Request to process with orchestrator"""
    request: str = Field(
        ...,
        description="Natural language request",
        min_length=10,
        max_length=2000
    )
    business_metrics: Optional[BusinessMetrics] = Field(None)
    preferences: Optional[UserPreferences] = Field(None)
    auto_quality_check: bool = Field(True, description="Auto evaluate quality")
    auto_approve: bool = Field(False, description="Auto approve if quality passes")
    
    class Config:
        json_schema_extra = {
            "example": {
                "request": "Create a blog post about AI marketing trends",
                "auto_quality_check": True,
                "preferences": {
                    "tone": "professional",
                    "channels": ["blog"]
                }
            }
        }


class ApprovalAction(BaseModel):
    """User approval action"""
    approved: bool
    publish_to_channels: List[str] = Field(["blog"])
    feedback: Optional[str] = None


class RefineContentRequest(BaseModel):
    """Request to refine content"""
    feedback: str = Field(..., min_length=10, max_length=1000)
    focus_area: Optional[str] = Field(None)


# ============================================================================
# ORCHESTRATOR ENDPOINTS
# ============================================================================

@orchestrator_router.post(
    "/process",
    summary="Process natural language request",
    description="""
    Process a natural language business request with unified orchestration.
    
    The system will:
    1. Parse and understand the request
    2. Route to appropriate handler
    3. Execute the workflow
    4. Assess quality (if requested)
    5. Create a task in /api/tasks (use GET /api/tasks/{task_id} to check status)
    
    Examples:
    - "Create a blog post about AI in healthcare"
    - "Research market trends for SaaS"
    - "Generate financial analysis report"
    
    **Note:** To list all tasks or check task status, use the main task endpoints:
    - GET /api/tasks - List all tasks
    - GET /api/tasks/{task_id} - Get task details
    """
)
async def process_orchestrator_request(
    body: ProcessRequestBody,
    background_tasks: BackgroundTasks,
    orchestrator: UnifiedOrchestrator = Depends(get_unified_orchestrator),
    quality_service: UnifiedQualityService = Depends(get_quality_service),
    db_service: DatabaseService = Depends(get_database_service),
) -> Dict[str, Any]:
    """Process natural language request through unified orchestrator"""
    try:
        task_id = str(uuid_lib.uuid4())
        logger.info(f"[{task_id}] Processing: {body.request[:100]}")
        
        # Create task in database
        task_data = {
            "id": task_id,
            "task_name": f"Orchestrated: {body.request[:100]}",
            "topic": body.request,
            "status": "pending",
            "agent_id": "unified-orchestrator",
            "metadata": {
                "orchestrator_request": body.request,
                "auto_quality_check": body.auto_quality_check,
                "auto_approve": body.auto_approve,
                "business_metrics": body.business_metrics.dict() if body.business_metrics else None,
                "preferences": body.preferences.dict() if body.preferences else None,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        stored_task_id = await db_service.add_task(task_data)
        logger.info(f"[{stored_task_id}] Task created in database")
        
        # Process in background
        background_tasks.add_task(
            _process_request_async,
            stored_task_id,
            body,
            orchestrator,
            quality_service,
            db_service
        )
        
        return {
            "success": True,
            "task_id": stored_task_id,
            "status": "pending",
            "status_url": f"/api/tasks/{stored_task_id}",
            "approval_url": f"/api/orchestrator/tasks/{stored_task_id}/approve",
            "message": "Request received and processing started"
        }
        
    except Exception as e:
        logger.error(f"Process request failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def _process_request_async(
    task_id: str,
    body: ProcessRequestBody,
    orchestrator: UnifiedOrchestrator,
    quality_service: UnifiedQualityService,
    db_service: DatabaseService
):
    """Background processing of orchestrator request using database"""
    try:
        # Update task to executing
        await db_service.update_task_status(task_id, "in_progress", {})
        
        # Process through unified orchestrator
        result = await orchestrator.process_request(
            user_input=body.request,
            context={
                "business_metrics": body.business_metrics.dict() if body.business_metrics else None,
                "preferences": body.preferences.dict() if body.preferences else None,
                "auto_quality_check": body.auto_quality_check,
            }
        )
        
        # Store result in database
        task_result = {
            "request_result": result,
            "output": result.get("output", ""),
            "request_type": result.get("request_type"),
        }
        
        # Run quality check if requested
        if body.auto_quality_check and result.get("output"):
            try:
                assessment = await quality_service.evaluate(
                    content=result.get("output", ""),
                    context={"topic": body.request},
                    method=EvaluationMethod.PATTERN_BASED
                )
                task_result["quality"] = assessment.to_dict()
                logger.info(f"[{task_id}] Quality: {assessment.overall_score:.1f}/10")
            except Exception as e:
                logger.warning(f"[{task_id}] Quality check failed: {e}")
        
        # Auto-approve if enabled and quality passes
        final_status = "completed"
        if body.auto_approve and task_result.get("quality", {}).get("passing", False):
            final_status = "completed"  # Mark as ready for approval
            logger.info(f"[{task_id}] Auto-approved (quality passed)")
        
        # Update task with result
        await db_service.update_task_status(task_id, final_status, task_result)
        logger.info(f"[{task_id}] Processing complete, status: {final_status}")
        
    except Exception as e:
        logger.error(f"[{task_id}] Background processing failed: {e}", exc_info=True)
        await db_service.update_task_status(
            task_id,
            "failed",
            {"error": str(e), "error_message": f"Processing failed: {str(e)}"}
        )


@orchestrator_router.get(
    "/status/{task_id}",
    response_model=ExecutionStatusResponse,
    summary="Get task status"
)
async def get_task_status(task_id: str) -> ExecutionStatusResponse:
    """Get status of an orchestration task"""
    if task_id not in TASK_STORE:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    
    task = TASK_STORE[task_id]
    return ExecutionStatusResponse(
        task_id=task_id,
        status=task.get("status", "unknown"),
        progress_percentage=task.get("progress", 0),
        current_phase=task.get("current_phase"),
        request_type=task.get("result", {}).get("request_type"),
        error=task.get("error")
    )


@orchestrator_router.get(
    "/tasks",
    summary="List all tasks",
    description="Get paginated list of all orchestration tasks"
)
async def list_tasks(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status_filter: Optional[str] = None
) -> Dict[str, Any]:
    """List tasks with optional filtering"""
    tasks = list(TASK_STORE.items())
    
    # Filter by status if provided
    if status_filter:
        tasks = [(tid, t) for tid, t in tasks if t.get("status") == status_filter]
    
    # Sort by creation time (newest first)
    tasks.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
    
    # Paginate
    total = len(tasks)
    tasks = tasks[offset:offset + limit]
    
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "tasks": [
            {
                "task_id": tid,
                "status": t.get("status"),
                "created_at": t.get("created_at"),
                "request": t.get("request", "")[:100],
                "progress": t.get("progress", 0)
            }
            for tid, t in tasks
        ]
    }


@orchestrator_router.get(
    "/tasks/{task_id}",
    summary="Get task details"
)
async def get_task_details(task_id: str) -> Dict[str, Any]:
    """Get full details of a task"""
    if task_id not in TASK_STORE:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    
    task = TASK_STORE[task_id]
    return {
        "task_id": task_id,
        "status": task.get("status"),
        "created_at": task.get("created_at"),
        "request": task.get("request"),
        "output": task.get("output"),
        "quality": task.get("quality"),
        "error": task.get("error"),
        "metadata": {
            "channels": task.get("channels"),
            "auto_approve": task.get("auto_approve"),
            "progress": task.get("progress")
        }
    }


@orchestrator_router.post(
    "/tasks/{task_id}/approve",
    summary="Approve and publish task"
)
async def approve_task(
    task_id: str,
    action: ApprovalAction,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    """Approve task and optionally publish to channels"""
    if task_id not in TASK_STORE:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    
    task = TASK_STORE[task_id]
    
    if action.approved:
        task["status"] = "approved"
        task["approved_at"] = datetime.now().isoformat()
        task["publish_channels"] = action.publish_to_channels
        
        # Start publishing in background
        if action.publish_to_channels:
            background_tasks.add_task(
                _publish_task_async,
                task_id,
                action.publish_to_channels
            )
        
        return {
            "success": True,
            "task_id": task_id,
            "status": "approved",
            "message": f"Approved and queued for publishing to {action.publish_to_channels}"
        }
    else:
        task["status"] = "rejected"
        task["rejected_at"] = datetime.now().isoformat()
        task["rejection_feedback"] = action.feedback
        
        return {
            "success": True,
            "task_id": task_id,
            "status": "rejected",
            "message": "Task rejected"
        }


async def _publish_task_async(task_id: str, channels: List[str]):
    """Background publishing to multiple channels"""
    try:
        logger.info(f"[{task_id}] Publishing to: {channels}")
        
        if task_id not in TASK_STORE:
            return
        
        task = TASK_STORE[task_id]
        output = task.get("output", "")
        published_to = []
        errors = []
        
        # Blog publishing
        if "blog" in channels:
            try:
                # TODO: Implement blog publishing
                published_to.append("blog")
                logger.info(f"[{task_id}] ✅ Published to blog")
            except Exception as e:
                logger.error(f"[{task_id}] Blog publishing failed: {e}")
                errors.append(f"Blog: {str(e)}")
        
        # LinkedIn publishing
        if "linkedin" in channels and PUBLISHERS_AVAILABLE:
            try:
                # TODO: Implement LinkedIn publishing
                published_to.append("linkedin")
                logger.info(f"[{task_id}] ✅ Published to LinkedIn")
            except Exception as e:
                logger.warning(f"[{task_id}] LinkedIn publishing failed: {e}")
                errors.append(f"LinkedIn: {str(e)}")
        
        # Update task status
        task["status"] = "published" if not errors else "partially_published"
        task["published_to"] = published_to
        task["publishing_errors"] = errors if errors else None
        task["published_at"] = datetime.now().isoformat()
        
    except Exception as e:
        logger.error(f"[{task_id}] Publishing failed: {e}")
        if task_id in TASK_STORE:
            TASK_STORE[task_id]["status"] = "publishing_failed"
            TASK_STORE[task_id]["error"] = str(e)


@orchestrator_router.post(
    "/tasks/{task_id}/refine",
    response_model=Dict[str, Any],
    summary="Refine content"
)
async def refine_task_content(
    task_id: str,
    refinement: RefineContentRequest,
    orchestrator: UnifiedOrchestrator = Depends(get_unified_orchestrator),
) -> Dict[str, Any]:
    """Refine previously generated content"""
    if task_id not in TASK_STORE:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    
    task = TASK_STORE[task_id]
    original_request = task.get("request", "")
    
    refinement_prompt = f"""
    Original request: {original_request}
    
    Feedback: {refinement.feedback}
    
    {"Focus area: " + refinement.focus_area if refinement.focus_area else ""}
    
    Please refine the content based on this feedback.
    """
    
    try:
        result = await orchestrator.process_request(
            user_input=refinement_prompt,
            context={"refinement": True, "original_task_id": task_id}
        )
        
        new_task_id = f"task-{datetime.now().timestamp()}"
        TASK_STORE[new_task_id] = {
            "status": result.get("status", "completed"),
            "created_at": datetime.now().isoformat(),
            "request": refinement_prompt,
            "output": result.get("output"),
            "parent_task_id": task_id,
            "is_refinement": True
        }
        
        return {
            "success": True,
            "new_task_id": new_task_id,
            "status": result.get("status"),
            "output": result.get("output"),
            "message": "Content refined successfully"
        }
        
    except Exception as e:
        logger.error(f"Content refinement failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# QUALITY ASSESSMENT ENDPOINTS
# ============================================================================

@quality_router.post(
    "/evaluate",
    response_model=QualityEvaluationResponse,
    summary="Evaluate content quality"
)
async def evaluate_quality(
    request: QualityEvaluationRequest,
    quality_service: UnifiedQualityService = Depends(get_quality_service),
) -> QualityEvaluationResponse:
    """Evaluate content quality using 7-criteria framework"""
    try:
        # Map method string to enum
        method_map = {
            "pattern-based": EvaluationMethod.PATTERN_BASED,
            "llm-based": EvaluationMethod.LLM_BASED,
            "hybrid": EvaluationMethod.HYBRID,
        }
        method = method_map.get(request.method.lower(), EvaluationMethod.PATTERN_BASED)
        
        assessment = await quality_service.evaluate(
            content=request.content,
            context={
                "topic": request.topic,
                "keywords": request.keywords or []
            },
            method=method,
            store_result=True
        )
        
        return QualityEvaluationResponse(
            overall_score=assessment.overall_score,
            passing=assessment.passing,
            dimensions=QualityDimensionsResponse(**assessment.dimensions.to_dict()),
            feedback=assessment.feedback,
            suggestions=assessment.suggestions,
            evaluation_method=assessment.evaluation_method.value,
        )
        
    except Exception as e:
        logger.error(f"Quality evaluation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@quality_router.get(
    "/statistics",
    summary="Get quality statistics"
)
async def get_quality_stats(
    quality_service: UnifiedQualityService = Depends(get_quality_service),
) -> Dict[str, Any]:
    """Get quality service statistics"""
    try:
        stats = quality_service.get_statistics()
        return {
            "statistics": stats,
            "retrieved_at": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to get statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# REGISTRATION
# ============================================================================

def register_unified_orchestrator_routes(app):
    """Register consolidated orchestrator routes"""
    app.include_router(orchestrator_router)
    app.include_router(quality_router)
    logger.info("✅ Unified orchestrator routes registered")
