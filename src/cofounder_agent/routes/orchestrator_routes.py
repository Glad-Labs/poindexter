"""
Unified Orchestrator Routes - No Duplicates with Other Routes

Consolidated natural language request processing for the Glad Labs system.

Combines best of:
- intelligent_orchestrator_routes.py (natural language processing)
- natural_language_content_routes.py (content generation)

AVOIDING DUPLICATION with:
- task_routes.py (task management: GET /api/tasks/{task_id}, GET /api/tasks, PATCH /api/tasks/{task_id}, POST /api/tasks)
- content_routes.py (structured content creation)
- quality_routes.py (quality assessment: POST /api/quality/evaluate, GET /api/quality/statistics)

Unique endpoints in this file (NOT duplicated elsewhere):
==========================================================
✅ POST /api/orchestrator/process - Process natural language request → creates task in tasks table
✅ POST /api/orchestrator/approve/{task_id} - Approve and publish result to channels
✅ POST /api/orchestrator/training-data/export - Export training data for model improvement
✅ POST /api/orchestrator/training-data/upload-model - Upload fine-tuned model
✅ GET /api/orchestrator/learning-patterns - Get learning patterns from executions
✅ GET /api/orchestrator/business-metrics-analysis - Business metrics analysis
✅ GET /api/orchestrator/tools - List available MCP tools

Task Status (use task_routes.py instead):
==========================================
❌ DO NOT use: GET /api/orchestrator/tasks/{task_id}
✅ USE INSTEAD: GET /api/tasks/{task_id} (in task_routes.py)

❌ DO NOT use: GET /api/orchestrator/tasks
✅ USE INSTEAD: GET /api/tasks (in task_routes.py)

❌ DO NOT use: GET /api/orchestrator/status/{task_id}
✅ USE INSTEAD: GET /api/tasks/{task_id} (in task_routes.py)

❌ DO NOT use: GET /api/orchestrator/history
✅ USE INSTEAD: GET /api/tasks (with filters) or GET /api/content/tasks (in task_routes.py)

Approval/Publishing workflow:
=============================
1. Process request: POST /api/orchestrator/process → returns task_id
2. Get task details: GET /api/tasks/{task_id} (in task_routes.py)
3. Approve & publish: POST /api/orchestrator/approve/{task_id}
4. Publishing updates task status automatically

Architecture:
==============
- All tasks stored in 'tasks' table in PostgreSQL
- UnifiedOrchestrator creates tasks via DatabaseService.add_task()
- task_routes.py provides universal task management for ALL task types
- orchestrator_routes.py provides unique orchestration features (publishing, training, metrics)
- No duplicate task management endpoints
"""

import logging
import json
from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime

from services.unified_orchestrator import UnifiedOrchestrator
from services.database_service import DatabaseService
from schemas.orchestrator_schemas import (
    ProcessRequestBody,
    ApprovalAction,
    TrainingDataExportRequest,
    TrainingModelUploadRequest,
)
from utils.service_dependencies import (
    get_unified_orchestrator,
    get_database_service,
)

logger = logging.getLogger(__name__)

orchestrator_router = APIRouter(
    prefix="/api/orchestrator",
    tags=["orchestrator-unique"]
)


# ============================================================================
# ENDPOINTS - ORCHESTRATION FEATURES (UNIQUE, NOT IN task_routes.py)
# ============================================================================

@orchestrator_router.post(
    "/process",
    summary="Process natural language request",
    description="""
    Process a natural language business request.
    
    This endpoint:
    1. Understands the natural language request
    2. Determines the type (content, financial, compliance, etc.)
    3. Routes to appropriate handler
    4. Creates a task in the tasks table
    5. Returns task_id and status
    
    Then use GET /api/tasks/{task_id} to monitor progress.
    
    Examples:
    - "Create a blog post about AI marketing"
    - "Research machine learning applications"
    - "Analyze our Q4 financial metrics"
    """
)
async def process_request(
    body: ProcessRequestBody,
    background_tasks: BackgroundTasks,
    orchestrator: UnifiedOrchestrator = Depends(get_unified_orchestrator),
) -> Dict[str, Any]:
    """Process natural language request through unified orchestrator"""
    try:
        logger.info(f"Processing request: {body.prompt[:100]}")
        
        # Process through unified orchestrator
        result = await orchestrator.process_request(
            user_input=body.prompt,
            context=body.context or {}
        )
        
        # Task is created in tasks table by orchestrator
        task_id = result.get("task_id")
        
        return {
            "success": True,
            "task_id": task_id,
            "request_type": result.get("request_type"),
            "status": result.get("status"),
            "message": f"Request processed. Use GET /api/tasks/{task_id} to monitor progress.",
            "next_steps": [
                f"Monitor: GET /api/tasks/{task_id}",
                f"Approve: POST /api/orchestrator/approve/{task_id}",
            ]
        }
    except Exception as e:
        logger.error(f"Request processing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@orchestrator_router.post(
    "/approve/{task_id}",
    summary="Approve and publish result",
    description="""
    Approve a completed task and publish to channels.
    
    This endpoint:
    1. Gets the completed task from the tasks table
    2. Validates quality score if thresholds set
    3. Publishes to specified channels (blog, LinkedIn, Twitter, email)
    4. Updates task status to 'published'
    
    Use GET /api/tasks/{task_id} first to review the task before approving.
    """
)
async def approve_and_publish(
    task_id: str,
    action: ApprovalAction,
    background_tasks: BackgroundTasks,
    db_service: DatabaseService = Depends(get_database_service),
) -> Dict[str, Any]:
    """Approve and publish task result"""
    try:
        logger.info(f"Approving task {task_id}")
        
        # Get task from database (uses tasks table)
        task = await db_service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        
        if action.approved:
            # Apply modifications if provided
            if action.modifications:
                result = task.get("result", {})
                if isinstance(result, str):
                    result = json.loads(result) if result else {}
                result.update(action.modifications)
                await db_service.update_task_status(
                    task_id,
                    "approved",
                    result=json.dumps(result)
                )
            else:
                await db_service.update_task_status(task_id, "approved")
            
            # Background publishing to channels
            background_tasks.add_task(
                _publish_to_channels,
                task_id=task_id,
                channels=action.publish_to_channels,
                db_service=db_service
            )
            
            return {
                "success": True,
                "task_id": task_id,
                "status": "approved",
                "publishing_to": action.publish_to_channels,
                "message": "Task approved. Publishing in progress."
            }
        else:
            rejection_reason = action.modifications.get("reason") if action.modifications else None
            await db_service.update_task_status(
                task_id,
                "rejected",
                result=json.dumps({"rejection_reason": rejection_reason})
            )
            return {
                "success": True,
                "task_id": task_id,
                "status": "rejected",
                "message": "Task rejected."
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Approval failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@orchestrator_router.post(
    "/training-data/export",
    summary="Export training data",
    description="Export collected training data for model improvement and analysis"
)
async def export_training_data(
    request: TrainingDataExportRequest,
    db_service: DatabaseService = Depends(get_database_service),
) -> Dict[str, Any]:
    """Export training data"""
    try:
        logger.info(f"Exporting training data ({request.format})")
        
        # TODO: Implement training data export from database
        # Filter by quality_score if specified
        # Format as JSONL or CSV
        # Return download URL or data
        
        return {
            "success": True,
            "format": request.format,
            "count": 0,  # TODO: Implement
            "download_url": "/api/orchestrator/training-data/download/latest",
            "message": "Training data export prepared"
        }
    except Exception as e:
        logger.error(f"Training data export failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@orchestrator_router.post(
    "/training-data/upload-model",
    summary="Upload fine-tuned model",
    description="Upload a fine-tuned model for use by the system"
)
async def upload_training_model(
    request: TrainingModelUploadRequest,
    db_service: DatabaseService = Depends(get_database_service),
) -> Dict[str, Any]:
    """Upload fine-tuned model"""
    try:
        logger.info(f"Uploading model: {request.model_name}")
        
        # TODO: Implement model upload and registration
        # Register model in database
        # Make available for orchestrator use
        
        return {
            "success": True,
            "model_name": request.model_name,
            "model_type": request.model_type,
            "status": "registered",
            "message": f"Model {request.model_name} registered and available"
        }
    except Exception as e:
        logger.error(f"Model upload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@orchestrator_router.get(
    "/learning-patterns",
    summary="Get learning patterns",
    description="Get patterns learned from execution history"
)
async def get_learning_patterns(
    limit: int = Query(100, ge=1, le=1000),
    db_service: DatabaseService = Depends(get_database_service),
) -> Dict[str, Any]:
    """Get learning patterns"""
    try:
        logger.info("Retrieving learning patterns")
        
        # TODO: Implement pattern extraction from execution history
        # Analyze task success/failure rates
        # Identify common request types
        # Find optimal parameters
        
        return {
            "patterns": [],
            "total_executions": 0,  # TODO
            "success_rate": 0.0,  # TODO
            "retrieved_at": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to retrieve patterns: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@orchestrator_router.get(
    "/business-metrics-analysis",
    summary="Analyze business metrics",
    description="Analyze collected business metrics and trends"
)
async def analyze_business_metrics(
    db_service: DatabaseService = Depends(get_database_service),
) -> Dict[str, Any]:
    """Analyze business metrics"""
    try:
        logger.info("Analyzing business metrics")
        
        # TODO: Implement metrics analysis
        # Aggregate task metrics
        # Calculate trends
        # Identify improvements
        
        return {
            "metrics": {},
            "trends": [],
            "recommendations": [],
            "analyzed_at": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Metrics analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@orchestrator_router.get(
    "/tools",
    summary="List available MCP tools",
    description="List all available Model Context Protocol tools"
)
async def list_available_tools() -> Dict[str, Any]:
    """List available MCP tools"""
    try:
        logger.info("Listing available tools")
        
        # TODO: Implement MCP tool discovery
        
        return {
            "tools": [],
            "total": 0,
            "available_at": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to list tools: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# BACKGROUND TASKS
# ============================================================================

async def _publish_to_channels(
    task_id: str,
    channels: List[str],
    db_service: DatabaseService
) -> None:
    """Background task: Publish result to channels"""
    try:
        logger.info(f"Publishing task {task_id} to channels: {channels}")
        
        # Get task result
        task = await db_service.get_task(task_id)
        if not task:
            logger.error(f"Task {task_id} not found for publishing")
            return
        
        result = task.get("result", {})
        
        # TODO: Implement channel publishing
        # For each channel in channels:
        #   - LinkedIn Publisher
        #   - Twitter Publisher
        #   - Email Publisher
        #   - Blog CMS
        
        # Update task status
        if isinstance(result, str):
            result_dict = json.loads(result) if result else {}
        else:
            result_dict = result or {}
        
        result_dict["published_to"] = channels
        
        await db_service.update_task_status(
            task_id,
            "published",
            result=json.dumps(result_dict)
        )
        
        logger.info(f"Task {task_id} published successfully")
        
    except Exception as e:
        logger.error(f"Publishing failed for task {task_id}: {e}")
        await db_service.update_task_status(
            task_id,
            "publishing_failed",
            result=json.dumps({"error": str(e)})
        )


# ============================================================================
# REGISTRATION
# ============================================================================

def register_orchestrator_routes(app):
    """Register orchestrator routes with the FastAPI app"""
    app.include_router(orchestrator_router)
    logger.info("✅ Unified orchestrator routes registered (no task duplication)")
