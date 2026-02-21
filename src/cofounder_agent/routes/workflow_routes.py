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

from fastapi import APIRouter, Body, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/workflows",
    tags=["workflows"],
    responses={404: {"description": "Workflow not found"}},
)


@router.post("/templates", name="List Workflow Templates")
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
async def get_workflow_status(workflow_id: str):
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
        # TODO: Implement workflow status retrieval from storage/engine
        # This is a placeholder that would integrate with WorkflowEngine
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving workflow status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve workflow status: {str(e)}")


@router.post("/pause/{workflow_id}", name="Pause Workflow")
async def pause_workflow(workflow_id: str):
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
        # TODO: Implement pause functionality via WorkflowEngine
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error pausing workflow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to pause workflow: {str(e)}")


@router.post("/resume/{workflow_id}", name="Resume Workflow")
async def resume_workflow(workflow_id: str):
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
        # TODO: Implement resume functionality via WorkflowEngine
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resuming workflow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to resume workflow: {str(e)}")


@router.post("/cancel/{workflow_id}", name="Cancel Workflow")
async def cancel_workflow(workflow_id: str):
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
        # TODO: Implement cancel functionality via WorkflowEngine
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling workflow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to cancel workflow: {str(e)}")


@router.post("/execute/{template_name}", name="Execute Workflow Template", status_code=202)
async def execute_workflow_template(
    request: Request,
    template_name: str,
    task_input: Dict[str, Any] = Body(..., description="Input data for the workflow"),
    skip_phases: Optional[List[str]] = Query(None, description="Phases to skip"),
    quality_threshold: float = Query(
        0.7, ge=0.0, le=1.0, description="Quality threshold for assessment"
    ),
    tags: Optional[List[str]] = Query(None, description="Tags for workflow"),
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
        # Get template execution service from app state
        template_service = getattr(request.app.state, "template_execution_service", None)
        if not template_service:
            logger.error("Template execution service not available in app state")
            raise HTTPException(
                status_code=500,
                detail="Template execution service not initialized",
            )

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


@router.get("/status/{execution_id}", name="Get Workflow Execution Status")
async def get_workflow_status(
    request: Request,
    execution_id: str,
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
        template_service = getattr(request.app.state, "template_execution_service", None)
        if not template_service:
            logger.error("Template execution service not available")
            raise HTTPException(
                status_code=500,
                detail="Template execution service not initialized",
            )

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
    request: Request,
    limit: int = Query(50, ge=1, le=1000, description="Maximum number of executions to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    template_name: Optional[str] = Query(None, description="Filter by template name (optional)"),
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
        template_service = getattr(request.app.state, "template_execution_service", None)
        if not template_service:
            logger.error("Template execution service not available")
            raise HTTPException(
                status_code=500,
                detail="Template execution service not initialized",
            )

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
