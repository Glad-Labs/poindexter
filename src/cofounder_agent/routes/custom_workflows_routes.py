"""
Custom Workflows Routes - REST API for workflow building and execution

Endpoints:
- POST /api/workflows/custom - Create new workflow
- GET /api/workflows/custom - List user's workflows
- GET /api/workflows/custom/{workflow_id} - Get workflow details
- PUT /api/workflows/custom/{workflow_id} - Update workflow
- DELETE /api/workflows/custom/{workflow_id} - Delete workflow
- POST /api/workflows/custom/{workflow_id}/execute - Execute custom workflow
- GET /api/workflows/available-phases - List available phases for building
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from routes.auth_unified import get_current_user
from schemas.custom_workflow_schemas import (
    AvailablePhasesResponse,
    CustomWorkflow,
    WorkflowExecutionResponse,
    WorkflowListPageResponse,
    WorkflowListResponse,
)
from services.custom_workflows_service import CustomWorkflowsService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/workflows",
    tags=["custom-workflows"],
    dependencies=[Depends(get_current_user)],
    responses={404: {"description": "Workflow not found"}},
)


def get_workflows_service(request: Request) -> CustomWorkflowsService:
    """Dependency injection for custom workflows service"""
    service = getattr(request.app.state, "custom_workflows_service", None)
    if not service:
        raise HTTPException(status_code=503, detail="Workflows service not initialized")
    return service


def get_owner_id(current_user: Dict[str, Any] = Depends(get_current_user)) -> str:
    """Extract and validate owner_id from the authenticated user."""
    user_id = current_user.get("id") if isinstance(current_user, dict) else current_user
    if not user_id:
        raise HTTPException(status_code=401, detail="User identity could not be determined")
    return str(user_id)


@router.post("/custom", response_model=CustomWorkflow, name="Create Custom Workflow")
async def create_custom_workflow(
    workflow: CustomWorkflow,
    request: Request,
    service: CustomWorkflowsService = Depends(get_workflows_service),
    owner_id: str = Depends(get_owner_id),
) -> CustomWorkflow:
    """
    Create and save a new custom workflow.

    Args:
        workflow: Workflow definition with phases

    Returns:
        Created workflow with ID and timestamps

    Raises:
        400: Invalid workflow definition
        500: Database error
    """
    try:

        created = await service.create_workflow(workflow, owner_id)
        logger.info(f"Created workflow: {created.id} for user {owner_id}")
        return created
    except ValueError as e:
        logger.warning(f"Invalid workflow: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail="Invalid workflow definition") from e
    except Exception as e:
        logger.error(f"Error creating workflow: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create workflow") from e


@router.get("/custom", response_model=WorkflowListPageResponse, name="List Custom Workflows")
async def list_custom_workflows(
    request: Request,
    service: CustomWorkflowsService = Depends(get_workflows_service),
    owner_id: str = Depends(get_owner_id),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Results per page"),
    include_templates: bool = Query(True, description="Include shared templates"),
) -> WorkflowListPageResponse:
    """
    List workflows for the current user.

    Includes:
    - User's own workflows
    - Shared template workflows (if include_templates=true)

    Returns:
        Paginated list of workflows
    """
    try:

        result = await service.list_workflows(
            owner_id=owner_id, include_templates=include_templates, page=page, page_size=page_size
        )

        workflows = [
            WorkflowListResponse(
                id=str(w.id),
                name=w.name,
                description=w.description,
                phase_count=len(w.phases),
                created_at=w.created_at,
                updated_at=w.updated_at,
                tags=w.tags,
                is_template=w.is_template,
            )
            for w in result["workflows"]
        ]

        return WorkflowListPageResponse(
            workflows=workflows,
            total_count=result["total_count"],
            page=page,
            page_size=page_size,
            has_next=result["has_next"],
        )
    except Exception as e:
        logger.error(f"Error listing workflows: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list workflows") from e


@router.get("/custom/{workflow_id}", response_model=CustomWorkflow, name="Get Custom Workflow")
async def get_custom_workflow(
    workflow_id: str,
    request: Request,
    service: CustomWorkflowsService = Depends(get_workflows_service),
    owner_id: str = Depends(get_owner_id),
) -> CustomWorkflow:
    """
    Retrieve a custom workflow by ID.

    User must own the workflow or it must be a shared template.

    Args:
        workflow_id: Workflow UUID

    Returns:
        CustomWorkflow details

    Raises:
        404: Workflow not found or access denied
    """
    try:

        workflow = await service.get_workflow(workflow_id, owner_id)

        if not workflow:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

        return workflow
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving workflow: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve workflow") from e


@router.put("/custom/{workflow_id}", response_model=CustomWorkflow, name="Update Custom Workflow")
async def update_custom_workflow(
    workflow_id: str,
    workflow: CustomWorkflow,
    request: Request,
    service: CustomWorkflowsService = Depends(get_workflows_service),
    owner_id: str = Depends(get_owner_id),
) -> CustomWorkflow:
    """
    Update an existing custom workflow.

    Only the workflow owner can update it.

    Args:
        workflow_id: Workflow UUID to update
        workflow: Updated workflow definition

    Returns:
        Updated workflow

    Raises:
        404: Workflow not found or access denied
        400: Invalid workflow definition
    """
    try:

        updated = await service.update_workflow(workflow_id, workflow, owner_id)
        logger.info(f"Updated workflow: {workflow_id}")
        return updated
    except ValueError as e:
        logger.warning(f"Invalid workflow or access denied: {str(e)}", exc_info=True)
        error_msg = str(e)
        if "not found" in error_msg.lower() or "access denied" in error_msg.lower():
            raise HTTPException(status_code=404, detail=error_msg) from e
        raise HTTPException(status_code=400, detail=f"Invalid workflow: {error_msg}") from e
    except Exception as e:
        logger.error(f"Error updating workflow: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update workflow") from e


@router.delete("/custom/{workflow_id}", name="Delete Custom Workflow")
async def delete_custom_workflow(
    workflow_id: str,
    request: Request,
    service: CustomWorkflowsService = Depends(get_workflows_service),
    owner_id: str = Depends(get_owner_id),
) -> Dict[str, str]:
    """
    Delete a custom workflow.

    Only the workflow owner can delete it.

    Args:
        workflow_id: Workflow UUID to delete

    Returns:
        Success message

    Raises:
        404: Workflow not found or access denied
    """
    try:

        success = await service.delete_workflow(workflow_id, owner_id)

        if success:
            logger.info(f"Deleted workflow: {workflow_id}")
            return {"message": f"Workflow '{workflow_id}' deleted successfully"}

        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Access denied or not found: {str(e)}", exc_info=True)
        raise HTTPException(status_code=404, detail="Workflow not found or access denied") from e
    except Exception as e:
        logger.error(f"Error deleting workflow: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete workflow") from e


@router.post(
    "/custom/{workflow_id}/execute",
    response_model=WorkflowExecutionResponse,
    name="Execute Custom Workflow",
)
async def execute_custom_workflow(
    workflow_id: str,
    request_body: Dict[str, Any],
    request: Request,
    service: CustomWorkflowsService = Depends(get_workflows_service),
    owner_id: str = Depends(get_owner_id),
    skip_phases: Optional[List[str]] = Query(None, description="Phases to skip"),
    quality_threshold: Optional[float] = Query(
        None, ge=0.0, le=1.0, description="Override quality threshold"
    ),
) -> WorkflowExecutionResponse:
    """
    Execute a saved custom workflow.

    Loads the workflow definition and starts background execution.

    Args:
        workflow_id: Workflow UUID to execute
        request_body: Input data for workflow execution
        skip_phases: Optional phases to skip
        quality_threshold: Optional quality threshold override

    Returns:
        Execution response with workflow_id and tracking info

    Raises:
        404: Workflow not found
        400: Invalid input
    """
    try:

        # Load workflow
        workflow = await service.get_workflow(workflow_id, owner_id)
        if not workflow:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

        # Execute workflow using the adapter
        from services.workflow_execution_adapter import execute_custom_workflow

        # Accept both payload styles:
        # - {"input_data": {...}} (current frontend client)
        # - {...} (raw execution input)
        if isinstance(request_body, dict):
            input_data = request_body.get("input_data", request_body)
            if input_data is None:
                input_data = {}
        else:
            input_data = {}

        # Get database service from app state
        database_service = getattr(request.app.state, "database", None) or getattr(
            request.app.state, "database_service", None
        )
        if not database_service:
            raise HTTPException(status_code=503, detail="Database service not initialized")

        # Execute workflow asynchronously (returns execution ID immediately)
        result = await execute_custom_workflow(
            custom_workflow=workflow,
            input_data=input_data,
            database_service=database_service,
            execution_owner_id=owner_id,
            queue_async=True,  # Execute in background
        )

        existing_execution = await service.get_workflow_execution(result["execution_id"], owner_id)
        if not existing_execution:
            persisted = await service.persist_workflow_execution(
                execution_id=result["execution_id"],
                workflow_id=str(workflow.id),
                owner_id=owner_id,
                execution_status=result.get("status", "pending"),
                phase_results={},
                duration_ms=0,
                initial_input=input_data,
                final_output=None,
                error_message=None,
                completed_phases=0,
                total_phases=len(workflow.phases or []),
                progress_percent=result.get("progress_percent", 0),
                tags=workflow.tags,
                metadata={
                    "execution_id": result["execution_id"],
                    "workflow_name": workflow.name,
                    "queued_from": "custom_workflows_routes",
                },
            )
            if not persisted:
                logger.warning(
                    "Failed to persist initial pending execution record for %s",
                    result["execution_id"],
                )

        logger.info(f"Workflow execution started: {result['execution_id']}")

        return WorkflowExecutionResponse(
            workflow_id=str(result["workflow_id"]),
            execution_id=result["execution_id"],
            status=result["status"],
            started_at=result["started_at"],
            phases=result["phases"],
            progress_percent=result.get("progress_percent", 0),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error executing workflow: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to execute workflow") from e


@router.get("/executions/{execution_id}", name="Get Workflow Execution Status")
async def get_workflow_execution_status(
    execution_id: str,
    request: Request,
    service: CustomWorkflowsService = Depends(get_workflows_service),
    owner_id: str = Depends(get_owner_id),
) -> Dict[str, Any]:
    """
    Get status/details for a workflow execution.

    Used by frontend polling after execution starts.

    Args:
        execution_id: Execution UUID

    Returns:
        Execution status payload with progress and results when available

    Raises:
        404: Execution not found
    """
    try:

        execution = await service.get_workflow_execution(execution_id, owner_id)

        if not execution:
            raise HTTPException(
                status_code=404,
                detail=f"Workflow execution '{execution_id}' not found",
            )

        phase_results = execution.get("phase_results") or {}
        metadata = execution.get("metadata") or {}
        phase_order = list(phase_results.keys())
        fallback_error = next(
            (
                phase_result.get("error")
                for phase_result in phase_results.values()
                if str(phase_result.get("status", "")).lower() == "failed"
                and phase_result.get("error")
            ),
            None,
        )

        return {
            "execution_id": execution.get("id"),
            "workflow_id": execution.get("workflow_id"),
            "status": execution.get("execution_status", "pending"),
            "started_at": execution.get("started_at"),
            "completed_at": execution.get("completed_at"),
            "duration_ms": execution.get("duration_ms") or 0,
            "progress_percent": execution.get("progress_percent") or 0,
            "completed_phases": execution.get("completed_phases") or 0,
            "total_phases": execution.get("total_phases") or 0,
            "current_phase": metadata.get("current_phase"),
            "phase_order": phase_order,
            "last_updated_at": metadata.get("last_updated_at"),
            "phase_results": phase_results,
            "final_output": execution.get("final_output"),
            "error_message": execution.get("error_message") or fallback_error,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error fetching workflow execution status for {execution_id}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to get execution status",
        ) from e


@router.get("/custom-executions", name="List Custom Workflow Executions")
async def list_custom_workflow_executions(
    workflow_id: str = Query(..., description="Custom workflow ID to list executions for"),
    service: CustomWorkflowsService = Depends(get_workflows_service),
    owner_id: str = Depends(get_owner_id),
    limit: int = Query(50, ge=1, le=200, description="Maximum executions to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    status: Optional[str] = Query(None, description="Optional execution status filter"),
) -> Dict[str, Any]:
    """
    List executions for a custom workflow owned by current user.

    Useful for recovering in-progress executions after UI refresh.
    """
    try:

        result = await service.get_workflow_executions(
            workflow_id=workflow_id,
            owner_id=owner_id,
            limit=limit,
            offset=offset,
            status=status,
        )

        return {
            "workflow_id": workflow_id,
            "total": result.get("total", 0),
            "limit": result.get("limit", limit),
            "offset": result.get("offset", offset),
            "executions": result.get("executions", []),
        }
    except Exception as e:
        logger.error(
            f"Error listing workflow executions for {workflow_id}: {str(e)}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to list workflow executions") from e


@router.get(
    "/available-phases", response_model=AvailablePhasesResponse, name="Get Available Phases"
)
async def get_available_phases(
    service: CustomWorkflowsService = Depends(get_workflows_service),
) -> AvailablePhasesResponse:
    """
    Get list of available phases that can be used when building workflows.

    Returns:
        List of phase metadata (name, description, compatible agents, etc)
    """
    try:
        phases = await service.get_available_phases()

        return AvailablePhasesResponse(phases=phases, total_count=len(phases))
    except Exception as e:
        logger.error(f"Error getting available phases: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get available phases") from e
