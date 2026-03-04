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

import jwt
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
from services.token_validator import JWTTokenValidator
from utils.route_utils import get_custom_workflows_service_dependency

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/workflows",
    tags=["custom-workflows"],
    responses={404: {"description": "Workflow not found"}},
)


# Helper function is no longer needed - use get_custom_workflows_service_dependency from route_utils instead


def get_user_id(request: Request) -> str:
    """
    Get user ID from JWT token in Authorization header or request context.

    Flow:
    1. Try to extract from Authorization: Bearer {token} header
    2. Fall back to request.state.user_id if set by middleware
    3. Use test user ID in development mode

    Returns:
        User ID string

    Raises:
        HTTPException: 401 if token is invalid
    """
    # Check if user_id already in request context (from auth middleware)
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return str(user_id)

    # Try to extract from Authorization header
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            token = auth_header[7:]  # Remove "Bearer " prefix

            # DEVELOPMENT MODE: Allow dev tokens without JWT validation
            if token.lower().startswith("dev-") or token == "dev-token":
                logger.info(f"[get_user_id] Development token accepted: {token[:20]}...")
                return "dev-user-123"

            claims = JWTTokenValidator.verify_token(token)
            if claims and "user_id" in claims:
                return str(claims["user_id"])
        except jwt.ExpiredSignatureError:
            logger.warning("JWT token expired in get_user_id()")
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid JWT token in get_user_id(): {e}")
            raise HTTPException(status_code=401, detail="Invalid token")
        except Exception as e:
            logger.warning(f"Error extracting user ID from JWT: {e}")
            raise HTTPException(status_code=401, detail="Authentication failed")

    # Development fallback (no token provided)
    if not auth_header:
        logger.debug("No authorization header provided, using test user for development")
        return "test-user-123"

    # Authorization header present but invalid format
    logger.warning(f"Invalid authorization header format: {auth_header[:20]}...")
    raise HTTPException(status_code=401, detail="Invalid authorization header format")


@router.post("/custom", response_model=CustomWorkflow, name="Create Custom Workflow")
async def create_custom_workflow(
    workflow: CustomWorkflow,
    request: Request,
    service: CustomWorkflowsService = Depends(get_custom_workflows_service_dependency),
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
    service: CustomWorkflowsService = Depends(get_custom_workflows_service_dependency),
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
        raise HTTPException(status_code=500, detail=f"Failed to list workflows: {str(e)}")


@router.get("/custom/{workflow_id}", response_model=CustomWorkflow, name="Get Custom Workflow")
async def get_custom_workflow(
    workflow_id: str,
    request: Request,
    service: CustomWorkflowsService = Depends(get_custom_workflows_service_dependency),
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
    service: CustomWorkflowsService = Depends(get_custom_workflows_service_dependency),
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
    service: CustomWorkflowsService = Depends(get_custom_workflows_service_dependency),
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


@router.post("/custom/{workflow_id}/execute", name="Execute Custom Workflow", status_code=202)
async def execute_custom_workflow(
    workflow_id: str,
    request_body: Dict[str, Any],
    request: Request,
    service: CustomWorkflowsService = Depends(get_custom_workflows_service_dependency),
) -> Dict[str, Any]:
    """
    Execute a saved custom workflow.

    Loads the workflow definition and begins execution.

    Args:
        workflow_id: Workflow UUID to execute
        request_body: Input data for workflow execution
                      May include 'model' field to specify LLM provider

    Returns:
        Execution response with execution_id and phase results

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

        # Extract model parameter if present
        selected_model = None
        if isinstance(request_body, dict):
            selected_model = request_body.get("model") or request_body.get("selected_model")
            # Remove model from input_data to avoid passing it as phase input
            request_body.pop("model", None)
            request_body.pop("selected_model", None)

        # Extract input data from request
        # Accept both payload styles:
        # - {"input_data": {...}} (frontend client)
        # - {...} (raw workflow input)
        if isinstance(request_body, dict):
            input_data = request_body.get("input_data", request_body)
            if input_data is None:
                input_data = {}
        else:
            input_data = {}

        # Execute workflow with optional model specification
        result = await service.execute_workflow(
            workflow=workflow,
            initial_inputs=input_data,
            selected_model=selected_model
        )

        logger.info(f"Workflow execution completed: {result['execution_id']}")

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error executing workflow: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to execute workflow: {str(e)}")


@router.get("/available-phases", name="Get Available Phases")
async def get_available_phases(
    service: CustomWorkflowsService = Depends(get_custom_workflows_service_dependency),
) -> Dict[str, Any]:
    """
    Get list of available phases that can be used when building workflows.

    Returns:
        List of phase metadata (name, description, input/output fields, etc)
    """
    try:
        phases = await service.get_available_phases()

        return {"phases": phases, "total_count": len(phases)}
    except Exception as e:
        logger.error(f"Error getting available phases: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get available phases: {str(e)}")


@router.get("/executions/{execution_id}", name="Get Workflow Execution")
async def get_workflow_execution(
    execution_id: str,
    request: Request,
    service: CustomWorkflowsService = Depends(get_custom_workflows_service_dependency),
) -> Dict[str, Any]:
    """Get execution status and results for a workflow execution."""
    try:
        owner_id = get_user_id(request)
        execution = await service.get_workflow_execution(execution_id, owner_id)
        if not execution:
            raise HTTPException(status_code=404, detail=f"Execution '{execution_id}' not found")
        return execution
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting workflow execution {execution_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get workflow execution: {str(e)}")


@router.get("/custom/{workflow_id}/executions", name="List Workflow Executions")
async def list_workflow_executions(
    workflow_id: str,
    request: Request,
    service: CustomWorkflowsService = Depends(get_custom_workflows_service_dependency),
    limit: int = Query(20, ge=1, le=100, description="Max executions to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    status: Optional[str] = Query(None, description="Optional status filter"),
) -> Dict[str, Any]:
    """List execution history for a specific workflow."""
    try:
        owner_id = get_user_id(request)
        execution_page = await service.get_workflow_executions(
            workflow_id=workflow_id,
            owner_id=owner_id,
            limit=limit,
            offset=offset,
            status=status,
        )
        executions = execution_page.get("executions", [])
        total_count = execution_page.get("total", 0)
        return {
            "workflow_id": workflow_id,
            "executions": executions,
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
            "has_next": (offset + limit) < total_count,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error listing workflow executions for {workflow_id}: {str(e)}", exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Failed to list workflow executions: {str(e)}")


@router.get("/history", name="Get Workflow Execution History")
async def get_workflow_history(
    request: Request,
    service: CustomWorkflowsService = Depends(get_custom_workflows_service_dependency),
    limit: int = Query(50, ge=1, le=500, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    status: Optional[str] = Query(None, description="Filter by status"),
) -> Dict[str, Any]:
    """Get workflow execution history for the user."""
    try:
        owner_id = get_user_id(request)
        # Get all executions for user's workflows
        all_executions = await service.get_all_executions(owner_id=owner_id)

        # Filter by status if provided
        if status:
            all_executions = [
                e for e in all_executions if e.get("status", "").lower() == status.lower()
            ]

        # Sort by creation time (newest first)
        all_executions.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        # Apply pagination
        total = len(all_executions)
        paginated = all_executions[offset : offset + limit]

        return {
            "executions": paginated,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_next": (offset + limit) < total,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching workflow history: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch workflow history: {str(e)}")


@router.get("/statistics", name="Get Workflow Statistics")
async def get_workflow_statistics(
    request: Request,
    service: CustomWorkflowsService = Depends(get_custom_workflows_service_dependency),
) -> Dict[str, Any]:
    """Get aggregate statistics for user's workflows."""
    try:
        owner_id = get_user_id(request)
        stats = await service.get_workflow_statistics(owner_id=owner_id)
        return stats
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching workflow statistics: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch workflow statistics: {str(e)}"
        )


@router.get("/performance-metrics", name="Get Performance Metrics")
async def get_performance_metrics(
    request: Request,
    service: CustomWorkflowsService = Depends(get_custom_workflows_service_dependency),
    range: str = Query("30d", description="Time range: 7d, 30d, 90d, all"),
) -> Dict[str, Any]:
    """Get workflow performance metrics."""
    try:
        owner_id = get_user_id(request)
        metrics = await service.get_performance_metrics(owner_id=owner_id, time_range=range)
        return metrics
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching performance metrics: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch performance metrics: {str(e)}"
        )


@router.get("/workflow/{execution_id}/details", name="Get Execution Details")
async def get_execution_details(
    execution_id: str,
    request: Request,
    service: CustomWorkflowsService = Depends(get_custom_workflows_service_dependency),
) -> Dict[str, Any]:
    """Get detailed information about a workflow execution."""
    try:
        owner_id = get_user_id(request)
        details = await service.get_execution_details(execution_id=execution_id, owner_id=owner_id)
        return details
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error fetching execution details for {execution_id}: {str(e)}", exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Failed to fetch execution details: {str(e)}")
