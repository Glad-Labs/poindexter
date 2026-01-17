"""
Service Registry API Routes

Exposes service discovery and execution endpoints for LLM integration.

Endpoints:
- GET /api/services - List all registered services
- GET /api/services/{service_name} - Get service details
- GET /api/services/{service_name}/actions - List service actions
- GET /api/services/registry - Complete registry schema for LLM consumption
- POST /api/services/{service_name}/actions/{action_name} - Execute action
- GET /api/services/registry/schema - OpenAPI schema of all services
"""

from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import logging

from services.service_base import get_service_registry, ActionResult
from routes.auth_unified import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/services", tags=["services"])


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================


class ServiceExecutionRequest(BaseModel):
    """Request to execute a service action"""

    params: Dict[str, Any] = Field(default_factory=dict, description="Action parameters")
    context: Optional[Dict[str, Any]] = Field(
        None, description="Execution context (user_id, request_id, etc.)"
    )


class ServiceInfo(BaseModel):
    """Information about a registered service"""

    name: str
    version: str
    description: str
    actions_count: int


class ActionInfo(BaseModel):
    """Information about a service action"""

    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    error_codes: List[str]
    requires_auth: bool
    is_async: bool


class RegistrySchema(BaseModel):
    """Complete registry schema for LLM consumption"""

    services: List[Dict[str, Any]]
    total_services: int
    total_actions: int


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get(
    "",
    response_model=List[ServiceInfo],
    summary="List all registered services",
    description="Get list of all available services that can be called",
)
async def list_services(current_user: dict = Depends(get_current_user)):
    """
    List all registered services.
    
    **Returns:**
    - List of services with metadata
    
    **Example:**
    ```bash
    curl http://localhost:8000/api/services \
      -H "Authorization: Bearer YOUR_JWT_TOKEN"
    ```
    
    **Response:**
    ```json
    [
        {
            "name": "tasks",
            "version": "1.0.0",
            "description": "Manage content generation tasks",
            "actions_count": 4
        },
        {
            "name": "content",
            "version": "1.0.0",
            "description": "Generate and manage content",
            "actions_count": 5
        }
    ]
    ```
    """
    try:
        registry = get_service_registry()
        services = registry.list_services()
        return services
    except Exception as e:
        logger.error(f"Error listing services: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list services: {str(e)}")


@router.get(
    "/registry",
    response_model=RegistrySchema,
    summary="Get complete service registry",
    description="Get complete registry schema with all services and actions for LLM consumption",
)
async def get_registry(current_user: dict = Depends(get_current_user)):
    """
    Get complete service registry.

    This endpoint provides the full schema that LLMs use to discover and understand
    all available services and actions.

    **Returns:**
    - Complete registry with service and action definitions

    **Usage by LLM:**
    1. Query this endpoint to get available tools
    2. Parse action input_schema to understand parameters
    3. Call actions via POST /api/services/{service}/actions/{action}
    4. Interpret results based on output_schema

    **Example Response:**
    ```json
    {
        "services": [
            {
                "name": "tasks",
                "version": "1.0.0",
                "description": "Manage tasks",
                "actions": [
                    {
                        "name": "create_task",
                        "description": "Create a new task",
                        "input_schema": {
                            "type": "object",
                            "properties": {...},
                            "required": ["task_name", "topic"]
                        },
                        "output_schema": {...},
                        "error_codes": ["VALIDATION_ERROR", "DATABASE_ERROR"],
                        "requires_auth": true,
                        "is_async": true
                    }
                ]
            }
        ],
        "total_services": 5,
        "total_actions": 32
    }
    ```
    """
    try:
        registry = get_service_registry()
        schema = registry.get_registry_schema()
        return schema
    except Exception as e:
        logger.error(f"Error getting registry: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get registry: {str(e)}")


@router.get(
    "/{service_name}",
    summary="Get service details",
    description="Get details about a specific service",
)
async def get_service_details(service_name: str, current_user: dict = Depends(get_current_user)):
    """
    Get details about a specific service.
    
    **Parameters:**
    - service_name: Name of the service
    
    **Returns:**
    - Service metadata and actions
    
    **Example:**
    ```bash
    curl http://localhost:8000/api/services/tasks \
      -H "Authorization: Bearer YOUR_JWT_TOKEN"
    ```
    """
    try:
        registry = get_service_registry()
        service = registry.get_service(service_name)

        if not service:
            raise HTTPException(status_code=404, detail=f"Service '{service_name}' not found")

        return {
            "name": service.name,
            "version": service.version,
            "description": service.description,
            "actions": [action.to_dict() for action in service.get_all_actions()],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting service details: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get service: {str(e)}")


@router.get(
    "/{service_name}/actions",
    response_model=List[ActionInfo],
    summary="List service actions",
    description="Get list of actions available in a service",
)
async def list_service_actions(service_name: str, current_user: dict = Depends(get_current_user)):
    """
    List all actions available in a service.
    
    **Parameters:**
    - service_name: Name of the service
    
    **Returns:**
    - List of action definitions
    
    **Example:**
    ```bash
    curl http://localhost:8000/api/services/tasks/actions \
      -H "Authorization: Bearer YOUR_JWT_TOKEN"
    ```
    """
    try:
        registry = get_service_registry()
        actions = registry.list_actions(service_name)

        if not actions:
            raise HTTPException(
                status_code=404, detail=f"Service '{service_name}' not found or has no actions"
            )

        return actions
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing service actions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list actions: {str(e)}")


@router.post(
    "/{service_name}/actions/{action_name}",
    response_model=Dict[str, Any],
    summary="Execute service action",
    description="Execute an action in a service",
)
async def execute_service_action(
    service_name: str,
    action_name: str,
    request: ServiceExecutionRequest,
    current_user: dict = Depends(get_current_user),
    background_tasks: BackgroundTasks = None,
):
    """
    Execute a service action.
    
    This is the main endpoint for LLMs to trigger workflows.
    
    **Parameters:**
    - service_name: Name of the service
    - action_name: Name of the action
    - params: Action parameters (must match input_schema)
    
    **Returns:**
    - ActionResult with execution status, data, and timing
    
    **Example:**
    ```bash
    curl -X POST http://localhost:8000/api/services/tasks/actions/create_task \
      -H "Authorization: Bearer YOUR_JWT_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{
        "params": {
          "task_name": "Blog Post - AI Ethics",
          "topic": "Ethical considerations in AI",
          "primary_keyword": "AI ethics"
        }
      }'
    ```
    
    **Response:**
    ```json
    {
        "action": "create_task",
        "status": "completed",
        "data": {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "status": "pending",
            "created_at": "2026-01-01T18:00:00Z"
        },
        "error": null,
        "error_code": null,
        "metadata": {
            "service": "tasks",
            "version": "1.0.0"
        },
        "execution_time_ms": 45.2,
        "timestamp": "2026-01-01T18:00:00Z"
    }
    ```
    """
    try:
        logger.info(f"Executing action: {service_name}.{action_name}")
        logger.debug(f"Parameters: {request.params}")

        # Add user context
        context = request.context or {}
        context["user_id"] = current_user.get("id")

        registry = get_service_registry()
        result: ActionResult = await registry.execute_action(
            service_name=service_name,
            action_name=action_name,
            params=request.params,
            context=context,
        )

        logger.info(f"Action executed: {action_name} - Status: {result.status}")

        return result.model_dump(exclude_none=True)

    except Exception as e:
        logger.error(f"Error executing action: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to execute action: {str(e)}")


# ============================================================================
# UTILITY ENDPOINTS
# ============================================================================


@router.get(
    "/health",
    summary="Check service registry health",
    description="Verify that the service registry is operational",
)
async def service_registry_health(current_user: dict = Depends(get_current_user)):
    """
    Check if the service registry is healthy and operational.
    
    **Returns:**
    - Health status and service count
    
    **Example:**
    ```bash
    curl http://localhost:8000/api/services/health \
      -H "Authorization: Bearer YOUR_JWT_TOKEN"
    ```
    """
    try:
        registry = get_service_registry()
        service_count = len(registry.services)
        action_count = sum(len(service.get_all_actions()) for service in registry.services.values())

        return {
            "status": "healthy",
            "services_registered": service_count,
            "total_actions": action_count,
            "timestamp": str(__import__("datetime").datetime.now()),
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": str(__import__("datetime").datetime.now()),
        }
