"""
Workflow Routes - REST API for workflow execution and management

Exposes WorkflowEngine capabilities via HTTP for:
- Starting new workflows with custom phase pipelines
- Monitoring workflow status and progress
- Pausing, resuming, and cancelling workflows
- Retrieving workflow results and execution metrics
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from middleware.api_token_auth import get_operator_identity, verify_api_token
from services.logger_config import get_logger
from services.workflow_history import WorkflowHistoryService
from utils.rate_limiter import limiter
from utils.route_utils import get_database_dependency
from utils.route_utils import (
    get_template_execution_service_dependency as get_template_service_dependency,
)
from utils.route_utils import get_workflow_engine_dependency

logger = get_logger(__name__)


def _verify_ownership(execution: Dict[str, Any], user_id: str) -> None:
    """Raise 404 if the execution does not belong to the given user."""
    if execution.get("owner_id") and execution["owner_id"] != user_id:
        raise HTTPException(status_code=404, detail="Workflow not found")


router = APIRouter(
    prefix="/api/workflows",
    tags=["workflows"],
    dependencies=[Depends(verify_api_token)],
    responses={404: {"description": "Workflow not found"}},
)


@router.post("/templates", response_model=Dict[str, Any], name="List Workflow Templates")
async def list_workflow_templates():
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
    """
    try:
        templates = [
            {
                "name": "blog_post",
                "description": "Complete blog post generation with research, drafting, quality assessment, refinement, and publishing",
                "phases": [
                    "research",
                    "draft",
                    "assess",
                    "refine",
                    "finalize",
                    "image_selection",
                    "publish",
                ],
                "estimated_duration_seconds": 900,
                "metadata": {
                    "word_count_target": 1500,
                    "quality_threshold": 0.75,
                    "requires_approval": True,
                },
            },
            {
                "name": "social_media",
                "description": "Social media content generation with quick assessment and publishing",
                "phases": ["research", "draft", "assess", "finalize", "publish"],
                "estimated_duration_seconds": 300,
                "metadata": {
                    "word_count_target": 280,
                    "quality_threshold": 0.7,
                    "requires_approval": False,
                },
            },
            {
                "name": "email",
                "description": "Email content generation with assessment and formatting",
                "phases": ["draft", "assess", "finalize", "publish"],
                "estimated_duration_seconds": 240,
                "metadata": {
                    "word_count_target": 350,
                    "quality_threshold": 0.75,
                    "requires_approval": True,
                },
            },
            {
                "name": "newsletter",
                "description": "Newsletter generation with full pipeline including research and refinement",
                "phases": [
                    "research",
                    "draft",
                    "assess",
                    "refine",
                    "finalize",
                    "image_selection",
                    "publish",
                ],
                "estimated_duration_seconds": 1200,
                "metadata": {
                    "word_count_target": 2000,
                    "quality_threshold": 0.8,
                    "requires_approval": True,
                },
            },
            {
                "name": "market_analysis",
                "description": "Market analysis workflow with research, assessment, and reporting",
                "phases": ["research", "assess", "analyze", "report", "publish"],
                "estimated_duration_seconds": 600,
                "metadata": {
                    "quality_threshold": 0.8,
                    "requires_approval": True,
                },
            },
        ]
        return {"templates": templates, "total": len(templates)}
    except Exception as e:
        logger.error(f"Error listing workflow templates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list templates") from e


@router.get("/status/{workflow_id}", response_model=Dict[str, Any], name="Get Workflow Status")
async def get_workflow_status(
    workflow_id: str,
    db_service: Any = Depends(get_database_dependency),
    token: str = Depends(verify_api_token),
):
    """
    Get the current status of a workflow.

    Args:
        workflow_id: ID of the workflow to query

    Returns:
        Workflow status with phase results and metadata

    Errors:
        - 404: Workflow not found
    """
    try:
        if db_service and db_service.pool:
            history_svc = WorkflowHistoryService(db_service.pool)
            execution = await history_svc.get_workflow_execution(workflow_id)
        else:
            execution = None

        if execution is None:
            raise HTTPException(
                status_code=404,
                detail=f"Workflow '{workflow_id}' not found",
            )

        _verify_ownership(execution, get_operator_identity()["id"])

        return {
            "workflow_id": execution.get("id", workflow_id),
            "status": execution.get("status", "unknown"),
            "current_phase": execution.get("current_phase", ""),
            "phases_executed": execution.get("task_results", []),
            "progress_percent": execution.get("progress_percent", 0),
            "results": execution.get("output_data", {}),
            "started_at": execution.get("start_time"),
            "completed_at": execution.get("end_time"),
            "duration_seconds": execution.get("duration_seconds"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving workflow status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve workflow status") from e


@router.post("/{workflow_id}/pause", name="Pause Workflow")
async def pause_workflow(
    workflow_id: str,
    db_service: Any = Depends(get_database_dependency),
    workflow_engine: Any = Depends(get_workflow_engine_dependency),
    token: str = Depends(verify_api_token),
):
    """
    Pause a currently executing workflow.

    Args:
        workflow_id: ID of the workflow to pause

    Returns:
        Success confirmation and new workflow status

    Errors:
        - 404: Workflow not found
        - 400: Workflow not in running state
    """
    try:
        history_svc = WorkflowHistoryService(db_service.pool)
        execution = await history_svc.get_workflow_execution(workflow_id)

        if execution is None:
            raise HTTPException(
                status_code=404,
                detail=f"Workflow '{workflow_id}' not found",
            )

        _verify_ownership(execution, get_operator_identity()["id"])

        current_status = execution.get("status", "")
        if current_status != "running":
            raise HTTPException(
                status_code=400,
                detail=f"Workflow '{workflow_id}' must be running to pause (current: {current_status})",
            )

        # Attempt to pause via engine (may already be evicted from memory)
        if workflow_engine:
            workflow_engine.pause_workflow(workflow_id)

        # Always persist the status change to the DB regardless of engine result
        await history_svc.update_workflow_execution(workflow_id, status="paused")

        return {
            "success": True,
            "workflow_id": workflow_id,
            "status": "paused",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error pausing workflow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to pause workflow") from e


@router.post("/{workflow_id}/resume", name="Resume Workflow")
async def resume_workflow(
    workflow_id: str,
    db_service: Any = Depends(get_database_dependency),
    workflow_engine: Any = Depends(get_workflow_engine_dependency),
    token: str = Depends(verify_api_token),
):
    """
    Resume a paused workflow.

    Args:
        workflow_id: ID of the workflow to resume

    Returns:
        Success confirmation and new workflow status

    Errors:
        - 404: Workflow not found
        - 400: Workflow not in paused state
    """
    try:
        history_svc = WorkflowHistoryService(db_service.pool)
        execution = await history_svc.get_workflow_execution(workflow_id)

        if execution is None:
            raise HTTPException(
                status_code=404,
                detail=f"Workflow '{workflow_id}' not found",
            )

        _verify_ownership(execution, get_operator_identity()["id"])

        current_status = execution.get("status", "")
        if current_status != "paused":
            raise HTTPException(
                status_code=400,
                detail=f"Workflow '{workflow_id}' must be paused to resume (current: {current_status})",
            )

        # Attempt to resume via engine
        if workflow_engine:
            workflow_engine.resume_workflow(workflow_id)

        # Always persist the status change
        await history_svc.update_workflow_execution(workflow_id, status="running")

        return {
            "success": True,
            "workflow_id": workflow_id,
            "status": "running",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resuming workflow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to resume workflow") from e


@router.post("/{workflow_id}/cancel", name="Cancel Workflow")
async def cancel_workflow(
    workflow_id: str,
    db_service: Any = Depends(get_database_dependency),
    workflow_engine: Any = Depends(get_workflow_engine_dependency),
    token: str = Depends(verify_api_token),
):
    """
    Cancel a workflow (cannot be resumed).

    Args:
        workflow_id: ID of the workflow to cancel

    Returns:
        Success confirmation and final workflow status

    Errors:
        - 404: Workflow not found
        - 400: Workflow not in running/paused state
    """
    try:
        history_svc = WorkflowHistoryService(db_service.pool)
        execution = await history_svc.get_workflow_execution(workflow_id)

        if execution is None:
            raise HTTPException(
                status_code=404,
                detail=f"Workflow '{workflow_id}' not found",
            )

        _verify_ownership(execution, get_operator_identity()["id"])

        current_status = execution.get("status", "")
        cancellable_statuses = {"running", "paused"}
        if current_status not in cancellable_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Workflow '{workflow_id}' must be running or paused to cancel (current: {current_status})",
            )

        # Attempt to cancel via engine
        if workflow_engine:
            workflow_engine.cancel_workflow(workflow_id)

        # Always persist the final status
        await history_svc.update_workflow_execution(workflow_id, status="cancelled")

        return {
            "success": True,
            "workflow_id": workflow_id,
            "status": "cancelled",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling workflow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to cancel workflow") from e


@router.get("/executions", name="List Workflow Executions")
async def list_workflow_executions(
    request: Request,
    limit: int = Query(20, ge=1, le=100, description="Maximum executions to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    status: Optional[str] = Query(None, description="Filter by status"),
):
    """
    List workflow executions with optional status filtering.

    Returns:
        Paginated list of workflow executions
    """
    try:
        # Stub: full implementation requires WorkflowHistoryService.list_executions()
        return {
            "executions": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        logger.error(f"Error listing workflow executions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list workflow executions") from e


@router.post("/execute/{template_name}", name="Execute Workflow Template")
@limiter.limit("5/minute")
async def execute_workflow_template(
    request: Request,
    template_name: str,
    task_input: Dict[str, Any],
    skip_phases: Optional[List[str]] = Query(None, description="Phases to skip"),
    quality_threshold: float = Query(
        0.7, ge=0.0, le=1.0, description="Quality threshold for assessment"
    ),
    tags: Optional[List[str]] = Query(None, description="Tags for workflow"),
    template_service: Any = Depends(get_template_service_dependency),
):
    """
    Execute a workflow template with custom parameters.

    Args:
        template_name: Name of the template (blog_post, social_media, email, etc.)
        task_input: Input data for the workflow
        skip_phases: Optional list of phases to skip
        quality_threshold: Quality threshold for assessment phases (0.0-1.0)
        tags: Optional tags for categorization
        template_service: Injected TemplateExecutionService

    Returns:
        Execution result with ID and status

    Errors:
        - 404: Template not found
        - 500: Execution failed
    """
    try:
        try:
            template_service.validate_template_name(template_name)
        except (ValueError, KeyError) as e:
            raise HTTPException(
                status_code=404,
                detail=f"Template '{template_name}' not found",
            ) from e

        result = await template_service.execute_template(
            template_name=template_name,
            task_input=task_input,
            skip_phases=skip_phases or [],
            quality_threshold=quality_threshold,
            tags=tags or [],
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error executing workflow template: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to execute workflow") from e


@router.get("/templates/history", name="Get Workflow Execution History")
async def get_workflow_history(
    limit: int = Query(20, ge=1, le=100, description="Maximum history entries to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    template_name: Optional[str] = Query(None, description="Filter by template name"),
    template_service: Any = Depends(get_template_service_dependency),
    token: str = Depends(verify_api_token),
):
    """
    Get workflow execution history.

    Args:
        limit: Maximum records to return (1-200)
        offset: Pagination offset
        template_name: Optional filter by template name
        template_service: Injected TemplateExecutionService

    Returns:
        Paginated execution history

    Errors:
        - 500: Failed to retrieve history
    """
    try:
        result = await template_service.get_execution_history(
            owner_id=get_operator_identity()["id"],
            template_name=template_name,
            limit=limit,
            offset=offset,
        )
        return result
    except Exception as e:
        logger.error(f"Error retrieving workflow history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve workflow history") from e


@router.post("/executions/{execution_id}/cancel", name="Cancel Workflow Execution")
async def cancel_workflow_execution(
    execution_id: str,
    db_service: Any = Depends(get_database_dependency),
    token: str = Depends(verify_api_token),
):
    """
    Cancel a specific workflow execution by its execution ID.

    Args:
        execution_id: ID of the workflow execution to cancel

    Returns:
        Cancellation confirmation with previous status

    Errors:
        - 404: Execution not found
        - 409: Execution already in terminal state
        - 503: Database unavailable
    """
    try:
        if not db_service or not db_service.pool:
            raise HTTPException(
                status_code=503,
                detail="Database service unavailable",
            )

        history_svc = WorkflowHistoryService(db_service.pool)
        execution = await history_svc.get_workflow_execution(execution_id)

        if execution is None:
            raise HTTPException(
                status_code=404,
                detail=f"Workflow execution '{execution_id}' not found",
            )

        _verify_ownership(execution, get_operator_identity()["id"])

        previous_status = execution.get("status", "unknown")
        terminal_statuses = {"completed", "failed", "cancelled"}
        if previous_status in terminal_statuses:
            raise HTTPException(
                status_code=409,
                detail=f"Workflow execution '{execution_id}' is already in terminal state: {previous_status}",
            )

        await history_svc.update_workflow_execution(execution_id, status="cancelled")

        return {
            "execution_id": execution_id,
            "status": "cancelled",
            "previous_status": previous_status,
            "message": "Workflow execution cancelled successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling workflow execution: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to cancel workflow execution") from e


@router.get("/executions/{execution_id}/progress", name="Get Workflow Execution Progress")
async def get_workflow_execution_progress(
    execution_id: str,
    db_service: Any = Depends(get_database_dependency),
    token: str = Depends(verify_api_token),
):
    """
    Get detailed progress for a specific workflow execution.

    Args:
        execution_id: ID of the workflow execution

    Returns:
        Detailed progress including phases completed and remaining

    Errors:
        - 404: Execution not found
        - 503: Database unavailable
    """
    try:
        if not db_service or not db_service.pool:
            raise HTTPException(
                status_code=503,
                detail="Database service unavailable",
            )

        history_svc = WorkflowHistoryService(db_service.pool)
        execution = await history_svc.get_workflow_execution(execution_id)

        if execution is None:
            raise HTTPException(
                status_code=404,
                detail=f"Workflow execution '{execution_id}' not found",
            )

        _verify_ownership(execution, get_operator_identity()["id"])

        status = execution.get("status", "unknown")
        current_phase = execution.get("current_phase") or ""
        completed_phases = execution.get("completed_phases") or []
        remaining_phases = execution.get("remaining_phases") or []

        # Calculate progress percentage
        if status == "completed":
            progress_percent = 100
        else:
            total_phases = len(completed_phases) + len(remaining_phases)
            if total_phases > 0:
                progress_percent = int((len(completed_phases) / total_phases) * 100)
            else:
                progress_percent = 0

        return {
            "execution_id": execution_id,
            "status": status,
            "current_phase": current_phase,
            "phases_completed": completed_phases,
            "phases_remaining": remaining_phases,
            "progress_percent": progress_percent,
            "error_message": execution.get("error_message"),
            "started_at": execution.get("created_at"),
            "updated_at": execution.get("updated_at"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving workflow execution progress: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve execution progress") from e
