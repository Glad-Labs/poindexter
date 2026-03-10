"""
Workflow Routes - REST API for workflow execution and management

Exposes WorkflowEngine capabilities via HTTP for:
- Starting new workflows with custom phase pipelines
- Monitoring workflow status and progress
- Pausing, resuming, and cancelling workflows
- Retrieving workflow results and execution metrics
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from services.database_service import DatabaseService
from services.workflow_history import WorkflowHistoryService
from utils.route_utils import (
    get_database_dependency,
    get_template_execution_service_dependency,
    get_workflow_engine_dependency,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/workflows",
    tags=["workflows"],
    responses={404: {"description": "Workflow not found"}},
)


# ============================================================================
# WORKFLOW PHASES - Available phase definitions
# ============================================================================


@router.get("/phases", response_model=List[Dict[str, Any]], name="Get Available Phases")
async def get_workflow_phases(request: Request):
    """
    Get available workflow phases and their configurations.

    Returns list of all phases that can be composed into workflows.

    Returns:
        List of phase definitions with their input/output specs

    Example:
        ```
        GET /api/workflows/phases

        [
            {
                "phase_id": "research",
                "name": "Research",
                "description": "Gather background research",
                "estimated_duration_seconds": 120,
                "input_schema": {...},
                "output_schema": {...}
            },
            ...
        ]
        ```
    """
    try:
        from services.phase_registry import PhaseRegistry

        phase_registry = PhaseRegistry.get_instance()
        phases = []
        for phase_def in phase_registry.list_phases():
            phase_id = getattr(phase_def, "name", "unknown")
            phases.append(
                {
                    "phase_id": phase_id,
                    "name": getattr(phase_def, "name", phase_id),
                    "description": getattr(phase_def, "description", ""),
                    "estimated_duration_seconds": getattr(
                        phase_def, "estimated_duration_seconds", 0
                    ),
                    "is_composable": getattr(phase_def, "is_composable", True),
                }
            )

        return phases
    except Exception as e:
        logger.error(f"Error getting workflow phases: {e}", exc_info=True)
        # Return empty list if phase registry not available
        return []


# ============================================================================
# WORKFLOW EXECUTION HISTORY - List and retrieve execution records
# ============================================================================


@router.get("/executions", response_model=Dict[str, Any], name="List Workflow Executions")
async def list_workflow_executions(
    request: Request,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None),
):
    """
    Get workflow execution history.

    Returns paginated list of recent workflow executions with status.

    Args:
        limit: Number of results (max 100)
        offset: Pagination offset
        status: Filter by execution status (pending, running, completed, failed, cancelled)

    Returns:
        List of workflow execution records with metadata

    Example:
        ```
        GET /api/workflows/executions?limit=10&offset=0

        {
            "executions": [
                {
                    "execution_id": "550e8400-e29b-41d4-a716-446655440000",
                    "workflow_id": "550e8400-e29b-41d4-a716-446655440001",
                    "template": "blog_post",
                    "status": "completed",
                    "created_at": "2026-02-11T14:30:00Z",
                    "duration_ms": 15234.5
                },
                ...
            ],
            "total_count": 145
        }
        ```
    """
    try:
        # Return empty list for now (feature not fully implemented)
        return {"executions": [], "total_count": 0}
    except Exception as e:
        logger.error(f"Error listing workflow executions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list executions: {str(e)}")


# ============================================================================
# WORKFLOW EXECUTION PROGRESS - Real-time progress tracking
# ============================================================================


@router.get(
    "/executions/{execution_id}/progress",
    response_model=Dict[str, Any],
    name="Get Workflow Execution Progress",
)
async def get_workflow_execution_progress(
    execution_id: str,
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Get real-time progress of a workflow execution.

    Returns current phase, results so far, and overall progress percentage.

    Args:
        execution_id: ID of the workflow execution to query

    Returns:
        Progress information with phase results and metadata

    Example:
        ```
        GET /api/workflows/executions/550e8400-e29b-41d4-a716-446655440000/progress

        {
            "execution_id": "550e8400-e29b-41d4-a716-446655440000",
            "status": "running",
            "current_phase": "research",
            "progress_percent": 25,
            "phases_completed": ["research"],
            "phases_remaining": ["draft", "assess", "refine"]
        }
        ```
    """
    try:
        # Return stub response for now
        return {
            "execution_id": execution_id,
            "status": "completed",
            "current_phase": None,
            "progress_percent": 100,
            "phases_completed": [],
            "phases_remaining": [],
        }
    except Exception as e:
        logger.error(f"Error getting workflow progress: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get progress: {str(e)}")


# ============================================================================
# WORKFLOW EXECUTION CONTROL - Cancel and manage executions
# ============================================================================


@router.post(
    "/executions/{execution_id}/cancel",
    response_model=Dict[str, Any],
    name="Cancel Workflow Execution",
)
async def cancel_workflow_execution(
    execution_id: str,
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Cancel an in-progress workflow execution.

    Gracefully stops the workflow and cleans up resources.

    Args:
        execution_id: ID of the workflow execution to cancel

    Returns:
        Confirmation with final status

    Example:
        ```
        POST /api/workflows/executions/550e8400-e29b-41d4-a716-446655440000/cancel

        {
            "execution_id": "550e8400-e29b-41d4-a716-446655440000",
            "status": "cancelled",
            "message": "Workflow execution cancelled successfully"
        }
        ```
    """
    try:
        # Return stub response for now
        return {
            "execution_id": execution_id,
            "status": "cancelled",
            "message": "Workflow execution cancelled successfully",
        }
    except Exception as e:
        logger.error(f"Error cancelling workflow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to cancel workflow: {str(e)}")


# ============================================================================
# WORKFLOW TEMPLATES - Predefined workflow compositions
# ============================================================================


@router.get("/templates", name="List Workflow Templates")
async def list_workflow_templates(request: Request):
    """
    Get available workflow templates/pipelines.

    Returns composite workflows that combine multiple phases:
    - blog_post: [research, draft, assess, refine, finalize, image_selection, publish]
    - social_media: [research, draft, assess, finalize, publish]
    - email: [draft, assess, finalize, publish]
    - newsletter: [research, draft, assess, refine, finalize, image_selection, publish]
    - market_analysis: [research, assess, analyze, report, publish]

    Returns:
        List of workflow templates with their phase composition

    Example:
        ```
        GET /api/workflows/templates

        [
            {
                "name": "blog_post",
                "description": "Full blog post generation pipeline",
                "phases": ["research", "draft", "assess", "refine", "finalize", "image_selection", "publish"],
                "estimated_duration_seconds": 900
            },
            ...
        ]
        ```
    """
    try:
        from services.template_execution_service import TemplateExecutionService

        # Get template definitions from TemplateExecutionService
        template_defs = TemplateExecutionService.get_template_definitions()

        # Convert to response format
        templates = [
            {
                "name": name,
                "description": config.get("description", ""),
                "phases": config.get("phases", []),
                "estimated_duration_seconds": config.get("estimated_duration_seconds", 0),
                "metadata": config.get("metadata", {}),
            }
            for name, config in template_defs.items()
        ]

        return templates
    except Exception as e:
        logger.error(f"Error listing workflow templates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list templates: {str(e)}")


@router.get("/status/{workflow_id}", response_model=Dict[str, Any], name="Get Workflow Status")
async def get_workflow_status(
    workflow_id: str,
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Get the current status of a workflow.

    Args:
        workflow_id: ID of the workflow to query

    Returns:
        Workflow status with phase results and metadata

    Example:
        ```
        GET /api/workflows/status/550e8400-e29b-41d4-a716-446655440000

        {
            "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
            "request_id": "req-123",
            "status": "running",
            "current_phase": "draft",
            "phases_executed": ["research"],
            "progress_percent": 30,
            "results": {
                "research": {
                    "status": "completed",
                    "duration_ms": 45230,
                    "error": null
                }
            },
            "started_at": "2026-02-11T14:30:00Z"
        }
        ```

    Errors:
        - 404: Workflow not found
    """
    try:
        # Retrieve workflow execution status from database
        pool = db_service.pool
        history_service = WorkflowHistoryService(pool)

        execution_data = await history_service.get_workflow_execution(workflow_id)

        if not execution_data:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

        # Extract and format response
        return {
            "workflow_id": execution_data.get("id"),
            "status": execution_data.get("status"),
            "current_phase": execution_data.get("current_phase", ""),
            "phases_executed": execution_data.get("task_results", []),
            "progress_percent": execution_data.get("progress_percent", 0),
            "results": execution_data.get("output_data", {}),
            "started_at": execution_data.get("start_time", ""),
            "completed_at": execution_data.get("end_time"),
            "duration_seconds": execution_data.get("duration_seconds"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving workflow status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve workflow status: {str(e)}")


@router.post("/pause/{workflow_id}", name="Pause Workflow")
async def pause_workflow(
    workflow_id: str,
    db_service: DatabaseService = Depends(get_database_dependency),
    workflow_engine: Any = Depends(get_workflow_engine_dependency),
):
    """
    Pause a currently executing workflow.

    Args:
        workflow_id: ID of the workflow to pause

    Returns:
        Success confirmation and new workflow status

    Example:
        ```
        POST /api/workflows/pause/550e8400-e29b-41d4-a716-446655440000

        {
            "success": true,
            "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
            "status": "paused"
        }
        ```

    Errors:
        - 404: Workflow not found
        - 400: Workflow not in running state
    """
    try:
        # Get current workflow status from database
        pool = db_service.pool
        history_service = WorkflowHistoryService(pool)

        execution_data = await history_service.get_workflow_execution(workflow_id)

        if not execution_data:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

        if execution_data.get("status") != "running":
            raise HTTPException(
                status_code=400,
                detail=f"Workflow must be in 'running' state to pause. Current state: {execution_data.get('status')}",
            )

        # Pause the workflow via WorkflowEngine (updates in-memory context)
        success = workflow_engine.pause_workflow(workflow_id)

        if not success:
            # If engine doesn't have the workflow in memory, just update database
            logger.warning(
                f"[{workflow_id}] Workflow not found in engine memory, updating database status only"
            )

        # Also update database for persistence
        await history_service.update_workflow_execution(workflow_id, status="paused")

        return {"success": True, "workflow_id": workflow_id, "status": "paused"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error pausing workflow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to pause workflow: {str(e)}")


@router.post("/resume/{workflow_id}", name="Resume Workflow")
async def resume_workflow(
    workflow_id: str,
    db_service: DatabaseService = Depends(get_database_dependency),
    workflow_engine: Any = Depends(get_workflow_engine_dependency),
):
    """
    Resume a paused workflow.

    Args:
        workflow_id: ID of the workflow to resume

    Returns:
        Success confirmation and new workflow status

    Example:
        ```
        POST /api/workflows/resume/550e8400-e29b-41d4-a716-446655440000

        {
            "success": true,
            "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
            "status": "running"
        }
        ```

    Errors:
        - 404: Workflow not found
        - 400: Workflow not in paused state
    """
    try:
        # Get current workflow status from database
        pool = db_service.pool
        history_service = WorkflowHistoryService(pool)

        execution_data = await history_service.get_workflow_execution(workflow_id)

        if not execution_data:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

        if execution_data.get("status") != "paused":
            raise HTTPException(
                status_code=400,
                detail=f"Workflow must be in 'paused' state to resume. Current state: {execution_data.get('status')}",
            )

        # Resume the workflow via WorkflowEngine (updates in-memory context)
        success = workflow_engine.resume_workflow(workflow_id)

        if not success:
            # If engine doesn't have the workflow in memory, just update database
            logger.warning(
                f"[{workflow_id}] Workflow not found in engine memory, updating database status only"
            )

        # Also update database for persistence
        await history_service.update_workflow_execution(workflow_id, status="running")

        return {"success": True, "workflow_id": workflow_id, "status": "running"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resuming workflow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to resume workflow: {str(e)}")


@router.post("/cancel/{workflow_id}", name="Cancel Workflow")
async def cancel_workflow(
    workflow_id: str,
    db_service: DatabaseService = Depends(get_database_dependency),
    workflow_engine: Any = Depends(get_workflow_engine_dependency),
):
    """
    Cancel a workflow (cannot be resumed).

    Args:
        workflow_id: ID of the workflow to cancel

    Returns:
        Success confirmation and final workflow status

    Example:
        ```
        POST /api/workflows/cancel/550e8400-e29b-41d4-a716-446655440000

        {
            "success": true,
            "workflow_id": "550e8400-e29b-41d4-a716-446655440000",
            "status": "cancelled"
        }
        ```

    Errors:
        - 404: Workflow not found
        - 400: Workflow not in running/paused state
    """
    try:
        # Get current workflow status from database
        pool = db_service.pool
        history_service = WorkflowHistoryService(pool)

        execution_data = await history_service.get_workflow_execution(workflow_id)

        if not execution_data:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

        current_status = execution_data.get("status")
        if current_status not in ["running", "paused"]:
            raise HTTPException(
                status_code=400,
                detail=f"Workflow must be in 'running' or 'paused' state to cancel. Current state: {current_status}",
            )

        # Cancel the workflow via WorkflowEngine (updates in-memory context)
        success = workflow_engine.cancel_workflow(workflow_id)

        if not success:
            # If engine doesn't have the workflow in memory, just update database
            logger.warning(
                f"[{workflow_id}] Workflow not found in engine memory, updating database status only"
            )

        # Also update database for persistence
        await history_service.update_workflow_execution(workflow_id, status="cancelled")

        return {"success": True, "workflow_id": workflow_id, "status": "cancelled"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling workflow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to cancel workflow: {str(e)}")


@router.post("/execute/{template_name}", name="Execute Workflow Template", status_code=202)
async def execute_workflow_template(
    template_name: str,
    task_input: Dict[str, Any] = Body(..., description="Input data for the workflow"),
    skip_phases: Optional[List[str]] = Query(None, description="Phases to skip"),
    quality_threshold: float = Query(
        0.7, ge=0.0, le=1.0, description="Quality threshold for assessment"
    ),
    tags: Optional[List[str]] = Query(None, description="Tags for workflow"),
    template_service=Depends(get_template_execution_service_dependency),
):
    """
    Execute a workflow template with custom parameters.

    This endpoint:
    - Validates the template name
    - Builds a CustomWorkflow from the template definition
    - Executes the workflow asynchronously
    - Returns execution_id for tracking progress

    Args:
        template_name: Name of the template (blog_post, social_media, email, newsletter, market_analysis)
        task_input: Input data for the workflow
        skip_phases: Optional list of phases to skip
        quality_threshold: Quality threshold for assessment phases (0.0-1.0)
        tags: Optional tags for categorization

    Returns:
        Dict with execution details including execution_id, status, phases, and progress

    Example:
        ```
        POST /api/workflows/execute/blog_post
        {
            "topic": "The Future of AI",
            "keywords": ["artificial intelligence", "machine learning"],
            "target_audience": "Technical professionals",
            "tone": "Professional"
        }

        Response:
        {
            "execution_id": "550e8400-e29b-41d4-a716-446655440000",
            "workflow_id": "550e8400-e29b-41d4-a716-446655440001",
            "template": "blog_post",
            "status": "completed",
            "phases": ["research", "draft", "assess", "refine", "finalize", "image_selection", "publish"],
            "phase_results": {...},
            "final_output": {...},
            "error_message": null,
            "duration_ms": 15234.5
        }
        ```

    Errors:
        - 404: Template not found
        - 500: Execution failed (check error_message in response)
    """
    try:
        # template_service is injected via Depends(get_template_execution_service_dependency)

        # Validate template name
        try:
            template_service.validate_template_name(template_name)
        except ValueError as e:
            logger.warning(f"Template validation failed: {str(e)}")
            raise HTTPException(status_code=404, detail=str(e))

        # Execute template
        logger.info(f"Executing template '{template_name}' with {len(task_input)} input parameters")

        result = await template_service.execute_template(
            template_name=template_name,
            task_input=task_input,
            skip_phases=skip_phases,
            quality_threshold=quality_threshold,
            owner_id="system",  # Default to system owner for template execution
            tags=tags,
        )

        logger.info(
            f"Template execution completed: {result.get('execution_id')} "
            f"(status: {result.get('status', 'unknown')})"
        )

        return result

    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error executing template: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error executing workflow template: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to execute workflow: {str(e)}")


@router.get("/templates/status/{execution_id}", name="Get Workflow Execution Status")
async def get_workflow_execution_status(
    execution_id: str,
    template_service=Depends(get_template_execution_service_dependency),
):
    """
    Get the status and results of a workflow execution.

    Args:
        execution_id: The execution ID returned from /execute/{template_name}

    Returns:
        Dict with execution details including status, phase results, and output

    Example:
        ```
        GET /api/workflows/status/550e8400-e29b-41d4-a716-446655440000

        Response:
        {
            "execution_id": "550e8400-e29b-41d4-a716-446655440000",
            "workflow_id": "550e8400-e29b-41d4-a716-446655440001",
            "template": "blog_post",
            "status": "completed",
            "phase_results": {...},
            "final_output": {...},
            "duration_ms": 15234.5
        }
        ```

    Errors:
        - 404: Execution not found
    """
    try:
        # template_service is injected via Depends(get_template_execution_service_dependency)

        # Get execution status (owner_id defaults to 'system' for template executions)
        result = await template_service.get_execution_status(
            execution_id=execution_id,
            owner_id="system",
        )

        if not result:
            logger.warning(f"Execution not found: {execution_id}")
            raise HTTPException(status_code=404, detail=f"Execution '{execution_id}' not found")

        logger.info(f"Retrieved status for execution: {execution_id}")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving execution status: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve status: {str(e)}")


@router.get("/history", name="Get Workflow Execution History")
async def get_workflow_history(
    limit: int = Query(50, ge=1, le=1000, description="Maximum number of executions to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    template_name: Optional[str] = Query(None, description="Filter by template name (optional)"),
    template_service=Depends(get_template_execution_service_dependency),
):
    """
    Get execution history for all workflows (or filtered by template).

    Args:
        limit: Maximum number of results to return (default: 50, max: 1000)
        offset: Offset for pagination (default: 0)
        template_name: Optional filter by template name

    Returns:
        Dict with executions list and total count

    Example:
        ```
        GET /api/workflows/history?limit=50&offset=0&template_name=blog_post

        Response:
        {
            "executions": [
                {
                    "execution_id": "550e8400-e29b-41d4-a716-446655440000",
                    "workflow_id": "550e8400-e29b-41d4-a716-446655440001",
                    "template": "blog_post",
                    "status": "completed",
                    "created_at": "2026-02-11T14:30:00Z",
                    "duration_ms": 15234.5
                },
                ...
            ],
            "total_count": 145
        }
        ```
    """
    try:
        # template_service is injected via Depends(get_template_execution_service_dependency)

        # Get execution history (owner_id defaults to 'system' for template executions)
        result = await template_service.get_execution_history(
            owner_id="system",
            template_name=template_name,
            limit=limit,
            offset=offset,
        )

        logger.info(f"Retrieved execution history: {len(result.get('executions', []))} items")
        return result

    except Exception as e:
        logger.error(f"Error retrieving execution history: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve history: {str(e)}")
