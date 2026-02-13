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

from schemas.custom_workflow_schemas import (
    AvailablePhasesResponse,
    CustomWorkflow,
    WorkflowExecutionRequest,
    WorkflowExecutionResponse,
    WorkflowListPageResponse,
    WorkflowListResponse,
)
from services.custom_workflows_service import CustomWorkflowsService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/workflows",
    tags=["custom-workflows"],
    responses={404: {"description": "Workflow not found"}},
)


def get_workflows_service(request: Request) -> CustomWorkflowsService:
    """Dependency injection for custom workflows service"""
    service = getattr(request.app.state, "custom_workflows_service", None)
    if not service:
        raise HTTPException(status_code=503, detail="Workflows service not initialized")
    return service


def get_user_id(request: Request) -> str:
    """Get user ID from request context (from auth middleware)"""
    # TODO: Extract from JWT token or session
    # For now, use a test user ID
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        # Fallback for development
        user_id = "test-user-123"
    return user_id


@router.post("/custom", response_model=CustomWorkflow, name="Create Custom Workflow")
async def create_custom_workflow(
    workflow: CustomWorkflow,
    request: Request,
    service: CustomWorkflowsService = Depends(get_workflows_service),
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
        owner_id = get_user_id(request)
        created = await service.create_workflow(workflow, owner_id)
        logger.info(f"Created workflow: {created.id} for user {owner_id}")
        return created
    except ValueError as e:
        logger.warning(f"Invalid workflow: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Invalid workflow: {str(e)}")
    except Exception as e:
        logger.error(f"Error creating workflow: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create workflow: {str(e)}")


@router.get("/custom", response_model=WorkflowListPageResponse, name="List Custom Workflows")
async def list_custom_workflows(
    request: Request,
    service: CustomWorkflowsService = Depends(get_workflows_service),
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
        owner_id = get_user_id(request)
        result = await service.list_workflows(
            owner_id=owner_id, include_templates=include_templates, page=page, page_size=page_size
        )

        workflows = [
            WorkflowListResponse(
                id=w.id,
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
        raise HTTPException(status_code=500, detail=f"Failed to list workflows: {str(e)}")


@router.get("/custom/{workflow_id}", response_model=CustomWorkflow, name="Get Custom Workflow")
async def get_custom_workflow(
    workflow_id: str,
    request: Request,
    service: CustomWorkflowsService = Depends(get_workflows_service),
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
        owner_id = get_user_id(request)
        workflow = await service.get_workflow(workflow_id, owner_id)

        if not workflow:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

        return workflow
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving workflow: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve workflow: {str(e)}")


@router.put("/custom/{workflow_id}", response_model=CustomWorkflow, name="Update Custom Workflow")
async def update_custom_workflow(
    workflow_id: str,
    workflow: CustomWorkflow,
    request: Request,
    service: CustomWorkflowsService = Depends(get_workflows_service),
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
        owner_id = get_user_id(request)
        updated = await service.update_workflow(workflow_id, workflow, owner_id)
        logger.info(f"Updated workflow: {workflow_id}")
        return updated
    except ValueError as e:
        logger.warning(f"Invalid workflow or access denied: {str(e)}")
        error_msg = str(e)
        if "not found" in error_msg.lower() or "access denied" in error_msg.lower():
            raise HTTPException(status_code=404, detail=error_msg)
        raise HTTPException(status_code=400, detail=f"Invalid workflow: {error_msg}")
    except Exception as e:
        logger.error(f"Error updating workflow: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update workflow: {str(e)}")


@router.delete("/custom/{workflow_id}", name="Delete Custom Workflow")
async def delete_custom_workflow(
    workflow_id: str,
    request: Request,
    service: CustomWorkflowsService = Depends(get_workflows_service),
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
        owner_id = get_user_id(request)
        success = await service.delete_workflow(workflow_id, owner_id)

        if success:
            logger.info(f"Deleted workflow: {workflow_id}")
            return {"message": f"Workflow '{workflow_id}' deleted successfully"}

        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    except ValueError as e:
        logger.warning(f"Access denied or not found: {str(e)}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error deleting workflow: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete workflow: {str(e)}")


@router.post("/custom/{workflow_id}/execute", response_model=WorkflowExecutionResponse, name="Execute Custom Workflow")
async def execute_custom_workflow(
    workflow_id: str,
    request_body: Dict[str, Any],
    request: Request,
    service: CustomWorkflowsService = Depends(get_workflows_service),
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
        owner_id = get_user_id(request)

        # Load workflow
        workflow = await service.get_workflow(workflow_id, owner_id)
        if not workflow:
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

        # Execute workflow using the adapter
        from services.workflow_execution_adapter import execute_custom_workflow
        
        # Get input data from request body
        input_data = request_body if request_body else {}
        
        # Get database service from app state
        database_service = getattr(request.app.state, "database_service", None)
        if not database_service:
            raise HTTPException(status_code=503, detail="Database service not initialized")
        
        # Execute workflow asynchronously (returns execution ID immediately)
        result = await execute_custom_workflow(
            custom_workflow=workflow,
            input_data=input_data,
            database_service=database_service,
            queue_async=True  # Execute in background
        )
        
        logger.info(f"Workflow execution started: {result['execution_id']}")
        
        return WorkflowExecutionResponse(
            workflow_id=result["workflow_id"],
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
        raise HTTPException(status_code=500, detail=f"Failed to execute workflow: {str(e)}")


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
        raise HTTPException(
            status_code=500, detail=f"Failed to get available phases: {str(e)}"
        )
