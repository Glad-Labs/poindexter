"""
Service Registry REST Routes

Exposes the ServiceRegistry via HTTP for:
- LLM/Agent discovery of available services and actions
- Service introspection
- Runtime service querying
"""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from services.service_base import get_service_registry
from utils.route_utils import get_database_dependency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/services", tags=["services"])


@router.get(
    "/registry",
    response_model=Dict[str, Any],
    summary="Get complete service registry schema",
    description="Returns all registered services with their actions and schemas for LLM/agent discovery",
)
async def get_registry_schema():
    """
    Get the complete service registry schema.

    This endpoint returns the full registry in a format suitable for LLM systems
    to discover and understand available services and actions.

    **Returns:**
    - services: Dictionary of service_name -> service metadata
      - Each service includes:
        - name: Service identifier
        - description: Service purpose
        - actions: List of callable actions
        - Each action includes:
          - name: Action name
          - description: What the action does
          - parameters: JSON schema for input parameters
          - response: Response format

    **Example Response:**
    ```json
    {
      "services": {
        "content_service": {
          "name": "content_service",
          "description": "Generates and manages content",
          "actions": [
            {
              "name": "generate_blog_post",
              "description": "Generate a blog post",
              "parameters": {
                "type": "object",
                "properties": {
                  "topic": {"type": "string"},
                  "length": {"type": "integer"}
                }
              }
            }
          ]
        }
      }
    }
    ```

    **Use Cases:**
    - LLM/Agent systems discovering available capabilities
    - Frontend UI building service action forms
    - API documentation generation
    """
    try:
        registry = get_service_registry()
        schema = registry.get_registry_schema()
        return {"services": schema}
    except Exception as e:
        logger.error(f"Failed to get registry schema: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"message": str(e), "type": "registry_error"}
        )


@router.get(
    "/list",
    response_model=List[str],
    summary="List all services",
    description="Returns names of all registered services",
)
async def list_services():
    """
    List all registered services by name.

    **Returns:**
    - List of service names (e.g., ["content_service", "quality_service", "image_service"])

    **Use Cases:**
    - Discovering available services
    - Building service selection UIs
    """
    try:
        registry = get_service_registry()
        services = registry.list_services()
        return services
    except Exception as e:
        logger.error(f"Failed to list services: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"message": str(e), "type": "registry_error"}
        )


@router.get(
    "/{service_name}",
    response_model=Dict[str, Any],
    summary="Get service metadata",
    description="Returns metadata and actions for a specific service",
)
async def get_service_metadata(service_name: str):
    """
    Get metadata for a specific service.

    **Path Parameters:**
    - service_name: Name of the service

    **Returns:**
    - Service metadata including:
      - name: Service identifier
      - description: Service purpose
      - actions: List of available actions
      - Each action includes parameters schema and response format

    **Example:** `/api/services/content_service`

    **Errors:**
    - 404: Service not found
    """
    try:
        registry = get_service_registry()
        services = registry.list_services()

        if service_name not in services:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"Service not found: {service_name}",
                    "available_services": services,
                },
            )

        service = registry.get_service(service_name)
        if not service:
            raise HTTPException(status_code=404, detail=f"Service not found: {service_name}")

        schema = registry.get_registry_schema()
        return schema.get(service_name, {})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get service metadata for {service_name}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"message": str(e), "type": "registry_error"}
        )


@router.get(
    "/{service_name}/actions",
    response_model=List[Dict[str, Any]],
    summary="List actions for a service",
    description="Returns available actions in a service",
)
async def get_service_actions(service_name: str):
    """
    Get available actions for a specific service.

    **Path Parameters:**
    - service_name: Name of the service

    **Returns:**
    - List of action objects, each including:
      - name: Action name
      - description: What the action does
      - parameters: JSON schema for input
      - response: Response format

    **Example:** `/api/services/content_service/actions`

    **Errors:**
    - 404: Service not found
    """
    try:
        registry = get_service_registry()
        actions = registry.list_actions(service_name)

        if actions is None:
            raise HTTPException(
                status_code=404, detail=f"Service not found: {service_name}"
            )

        return actions

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get actions for {service_name}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"message": str(e), "type": "registry_error"}
        )


@router.get(
    "/{service_name}/actions/{action_name}",
    response_model=Dict[str, Any],
    summary="Get action details",
    description="Returns detailed schema for a specific action",
)
async def get_action_details(service_name: str, action_name: str):
    """
    Get detailed schema for a specific action.

    **Path Parameters:**
    - service_name: Name of the service
    - action_name: Name of the action

    **Returns:**
    - Action metadata including:
      - name: Action name
      - description: Purpose
      - parameters: JSON schema with type, properties, required fields
      - response: Response schema

    **Example:** `/api/services/content_service/actions/generate_blog_post`

    **Errors:**
    - 404: Service or action not found
    """
    try:
        registry = get_service_registry()
        actions = registry.list_actions(service_name)

        if actions is None:
            raise HTTPException(
                status_code=404, detail=f"Service not found: {service_name}"
            )

        action = next((a for a in actions if a.get("name") == action_name), None)

        if not action:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"Action not found: {action_name}",
                    "available_actions": [a.get("name") for a in actions],
                },
            )

        return action

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to get action details for {service_name}.{action_name}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"message": str(e), "type": "registry_error"}
        )


@router.post(
    "/{service_name}/actions/{action_name}",
    response_model=Dict[str, Any],
    summary="Execute a service action",
    description="Execute an action in a service",
)
async def execute_service_action(
    service_name: str,
    action_name: str,
    params: Dict[str, Any],
    db = Depends(get_database_dependency),
):
    """
    Execute an action in a service.

    **Path Parameters:**
    - service_name: Name of the service
    - action_name: Name of the action

    **Request Body:**
    - JSON object with action parameters

    **Returns:**
    - Action result: `{status: "success", result: <action_output>}`

    **Example:**
    ```json
    POST /api/services/content_service/actions/generate_blog_post
    {
      "topic": "AI in Healthcare",
      "length": 1500
    }
    ```

    **Errors:**
    - 404: Service or action not found
    - 400: Invalid parameters
    - 500: Action execution failed
    """
    try:
        registry = get_service_registry()

        # Validate service exists
        if service_name not in registry.list_services():
            raise HTTPException(
                status_code=404, detail=f"Service not found: {service_name}"
            )

        # Validate action exists
        actions = registry.list_actions(service_name)
        if not any(a.get("name") == action_name for a in actions):
            raise HTTPException(
                status_code=404,
                detail=f"Action not found: {service_name}.{action_name}",
            )

        # Execute action
        logger.info(f"Executing {service_name}.{action_name} with params: {params}")
        result = await registry.execute_action(service_name, action_name, params)

        return {"status": "success", "result": result}

    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid parameters for {service_name}.{action_name}: {e}")
        raise HTTPException(status_code=400, detail={"message": str(e), "type": "validation_error"})
    except Exception as e:
        logger.error(
            f"Failed to execute {service_name}.{action_name}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"message": str(e), "type": "execution_error"},
        )
