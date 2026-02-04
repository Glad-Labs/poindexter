"""
Service Layer Foundation for LLM Integration

Enables natural language workflows by providing:
1. Standardized service interfaces (ServiceBase)
2. Action registry for LLM discovery
3. Service composition/orchestration
4. Consistent error handling
5. Schema-driven parameters

This layer allows LLMs to:
- Query available services and actions (/api/services/registry)
- Execute workflows by calling service actions
- Chain multiple services together
- Handle errors gracefully
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Type

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ============================================================================
# SERVICE ACTION DEFINITIONS
# ============================================================================


class ActionStatus(str, Enum):
    """Status of an action execution"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class JsonSchema:
    """JSON Schema definition for parameter validation"""

    type: str
    properties: Dict[str, Any] = field(default_factory=dict)
    required: List[str] = field(default_factory=list)
    description: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        result = {"type": self.type, "properties": self.properties}
        if self.required:
            result["required"] = self.required
        if self.description:
            result["description"] = self.description
        return result


@dataclass
class ServiceAction:
    """Represents a single service action/tool"""

    name: str
    description: str
    input_schema: JsonSchema
    output_schema: JsonSchema
    error_codes: List[str] = field(default_factory=list)
    requires_auth: bool = True
    is_async: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert action to dictionary for LLM consumption"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema.to_dict(),
            "output_schema": self.output_schema.to_dict(),
            "error_codes": self.error_codes,
            "requires_auth": self.requires_auth,
            "is_async": self.is_async,
        }


class ActionResult(BaseModel):
    """Standard result format for all service actions"""

    action: str
    status: ActionStatus
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    execution_time_ms: Optional[float] = None
    timestamp: datetime = Field(default_factory=datetime.now)

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class ServiceError(Exception):
    """Base exception for service errors"""

    def __init__(self, error_code: str, message: str, details: Optional[Dict] = None):
        self.error_code = error_code
        self.message = message
        self.details = details or {}
        super().__init__(message)


# ============================================================================
# SERVICE BASE CLASS
# ============================================================================


class ServiceBase(ABC):
    """
    Base class for all services in the LLM-driven system.

    Each service:
    - Exposes standardized actions with defined inputs/outputs
    - Can be discovered by the ServiceRegistry
    - Can call other services (composition)
    - Has consistent error handling
    - Supports dependency injection

    Example:
        class ContentService(ServiceBase):
            name = "content"
            version = "1.0.0"

            def get_actions(self):
                return [
                    ServiceAction(
                        name="generate_blog_post",
                        description="Generate a blog post from a topic",
                        input_schema=JsonSchema(...),
                        output_schema=JsonSchema(...)
                    )
                ]

            async def generate_blog_post(self, topic: str, keywords: List[str]):
                # Implementation
                pass
    """

    # Service metadata (override in subclasses)
    name: str = "unknown"
    version: str = "0.1.0"
    description: str = ""

    def __init__(self, service_registry: "ServiceRegistry" = None):
        """
        Initialize service with optional registry reference.

        Args:
            service_registry: Reference to global ServiceRegistry for calling other services
        """
        self.service_registry = service_registry
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
        self._actions: Dict[str, ServiceAction] = {}
        self._load_actions()

    @abstractmethod
    def get_actions(self) -> List[ServiceAction]:
        """
        Define all actions this service provides.

        Returns:
            List of ServiceAction objects
        """
        pass

    def _load_actions(self):
        """Load and register actions from get_actions()"""
        for action in self.get_actions():
            self._actions[action.name] = action

    async def execute_action(
        self, action_name: str, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None
    ) -> ActionResult:
        """
        Execute a named action with parameters.

        Args:
            action_name: Name of the action to execute
            params: Input parameters for the action
            context: Execution context (user_id, request_id, etc.)

        Returns:
            ActionResult with status, data, and error information
        """
        start_time = datetime.now()

        try:
            # Validate action exists
            if action_name not in self._actions:
                raise ServiceError(
                    error_code="ACTION_NOT_FOUND",
                    message=f"Action '{action_name}' not found in service '{self.name}'",
                )

            action = self._actions[action_name]

            # Validate parameters against schema
            self._validate_params(params, action.input_schema)

            # Call the action method
            method_name = f"action_{action_name}"
            if not hasattr(self, method_name):
                raise ServiceError(
                    error_code="ACTION_IMPLEMENTATION_MISSING",
                    message=f"Action method '{method_name}' not implemented",
                )

            method = getattr(self, method_name)

            # Execute with timeout
            result_data = await asyncio.wait_for(method(**params), timeout=300)  # 5 minute timeout

            execution_time = (datetime.now() - start_time).total_seconds() * 1000

            return ActionResult(
                action=action_name,
                status=ActionStatus.COMPLETED,
                data=result_data if isinstance(result_data, dict) else {"result": result_data},
                metadata={"service": self.name, "version": self.version},
                execution_time_ms=execution_time,
            )

        except ServiceError as e:
            execution_time = (datetime.now() - start_time).total_seconds() * 1000
            return ActionResult(
                action=action_name,
                status=ActionStatus.FAILED,
                error=e.message,
                error_code=e.error_code,
                metadata={"details": e.details, "service": self.name},
                execution_time_ms=execution_time,
            )
        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds() * 1000
            self.logger.error(
                f"Unexpected error in action '{action_name}': {str(e)}", exc_info=True
            )
            return ActionResult(
                action=action_name,
                status=ActionStatus.FAILED,
                error=str(e),
                error_code="UNEXPECTED_ERROR",
                metadata={"service": self.name},
                execution_time_ms=execution_time,
            )

    def _validate_params(self, params: Dict[str, Any], schema: JsonSchema):
        """Validate parameters against JSON schema"""
        # Simple validation - in production use jsonschema library
        if schema.required:
            missing = set(schema.required) - set(params.keys())
            if missing:
                raise ServiceError(
                    error_code="VALIDATION_ERROR",
                    message=f"Missing required parameters: {missing}",
                    details={"missing_fields": list(missing)},
                )

    async def call_service(
        self, service_name: str, action_name: str, params: Dict[str, Any]
    ) -> ActionResult:
        """
        Call another service from within an action.
        Enables service composition.

        Args:
            service_name: Name of the service to call
            action_name: Name of the action in that service
            params: Parameters for the action

        Returns:
            ActionResult from the called service
        """
        if not self.service_registry:
            raise ServiceError(
                error_code="REGISTRY_NOT_AVAILABLE",
                message="Cannot call services - registry not available",
            )

        return await self.service_registry.execute_action(
            service_name=service_name, action_name=action_name, params=params
        )

    def get_action(self, action_name: str) -> Optional[ServiceAction]:
        """Get action definition by name"""
        return self._actions.get(action_name)

    def get_all_actions(self) -> List[ServiceAction]:
        """Get all actions provided by this service"""
        return list(self._actions.values())


# ============================================================================
# SERVICE REGISTRY
# ============================================================================


class ServiceRegistry:
    """
    Central registry for all services.

    Enables:
    - Service discovery for LLMs
    - Action execution across services
    - Service composition
    - Dependency resolution
    """

    def __init__(self):
        self.services: Dict[str, ServiceBase] = {}
        self.logger = logging.getLogger(__name__)

    def register(self, service: ServiceBase) -> None:
        """Register a service"""
        self.services[service.name] = service
        # Inject registry reference so service can call other services
        service.service_registry = self
        self.logger.info(f"✅ Registered service: {service.name} (v{service.version})")

    def get_service(self, service_name: str) -> Optional[ServiceBase]:
        """Get a service by name"""
        return self.services.get(service_name)

    async def execute_action(
        self,
        service_name: str,
        action_name: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ActionResult:
        """Execute an action in a service"""
        service = self.get_service(service_name)
        if not service:
            return ActionResult(
                action=action_name,
                status=ActionStatus.FAILED,
                error=f"Service '{service_name}' not found",
                error_code="SERVICE_NOT_FOUND",
            )

        return await service.execute_action(action_name, params, context)

    def get_registry_schema(self) -> Dict[str, Any]:
        """
        Get complete registry schema for LLM consumption.
        This is what the LLM uses to understand available tools.
        """
        return {
            "services": [
                {
                    "name": service.name,
                    "version": service.version,
                    "description": service.description,
                    "actions": [action.to_dict() for action in service.get_all_actions()],
                }
                for service in self.services.values()
            ],
            "total_services": len(self.services),
            "total_actions": sum(
                len(service.get_all_actions()) for service in self.services.values()
            ),
        }

    def list_services(self) -> List[Dict[str, Any]]:
        """List all registered services"""
        return [
            {
                "name": service.name,
                "version": service.version,
                "description": service.description,
                "actions_count": len(service.get_all_actions()),
            }
            for service in self.services.values()
        ]

    def list_actions(self, service_name: str) -> List[Dict[str, Any]]:
        """List all actions for a service"""
        service = self.get_service(service_name)
        if not service:
            return []
        return [action.to_dict() for action in service.get_all_actions()]


# Global registry instance
_service_registry: Optional[ServiceRegistry] = None


def get_service_registry() -> ServiceRegistry:
    """Get or create the global service registry"""
    global _service_registry
    if _service_registry is None:
        _service_registry = ServiceRegistry()
    return _service_registry


def set_service_registry(registry: ServiceRegistry) -> None:
    """Set the global service registry (for testing or custom setup)"""
    global _service_registry
    _service_registry = registry
