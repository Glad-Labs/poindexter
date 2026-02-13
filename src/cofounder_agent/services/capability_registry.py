"""
Capability Registry - Central registry for composable capabilities.

Capabilities are reusable units of functionality (methods, functions) that can be
chained together into tasks. Each capability has a standardized interface with
input/output schemas for data flow validation.
"""

from typing import Any, Callable, Dict, List, Optional, TypeVar, Generic
from dataclasses import dataclass, field, asdict
from enum import Enum
import inspect
from abc import ABC, abstractmethod
import json


class ParameterType(str, Enum):
    """Supported parameter types for capability I/O."""
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"
    ANY = "any"


@dataclass
class ParameterSchema:
    """Schema for a single input/output parameter."""
    name: str
    type: ParameterType
    description: str = ""
    required: bool = True
    default: Optional[Any] = None
    example: Optional[Any] = None
    enum_values: Optional[List[Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (for JSON serialization)."""
        return {
            "name": self.name,
            "type": self.type.value,
            "description": self.description,
            "required": self.required,
            "default": self.default,
            "example": self.example,
            "enum_values": self.enum_values,
        }


@dataclass
class InputSchema:
    """Input schema for a capability."""
    parameters: List[ParameterSchema] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "parameters": [p.to_dict() for p in self.parameters]
        }
    
    def validate(self, inputs: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate inputs against schema."""
        for param in self.parameters:
            if param.required and param.name not in inputs:
                return False, f"Missing required parameter: {param.name}"
        return True, None


@dataclass
class OutputSchema:
    """Output schema for a capability."""
    return_type: ParameterType = ParameterType.ANY
    description: str = ""
    output_format: str = "json"  # json, text, binary, etc.
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "return_type": self.return_type.value,
            "description": self.description,
            "output_format": self.output_format,
        }


@dataclass
class CapabilityMetadata:
    """Metadata for a capability."""
    name: str
    description: str
    tags: List[str] = field(default_factory=list)
    version: str = "1.0.0"
    author: str = ""
    cost_tier: str = "balanced"  # ultra_cheap, cheap, balanced, premium
    timeout_ms: int = 60000  # 60 seconds default
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


class Capability(ABC):
    """
    Base class for all capabilities.
    
    A capability is a composable unit of functionality with standardized I/O.
    """
    
    @property
    @abstractmethod
    def metadata(self) -> CapabilityMetadata:
        """Return capability metadata."""
        pass
    
    @property
    @abstractmethod
    def input_schema(self) -> InputSchema:
        """Return input schema."""
        pass
    
    @property
    @abstractmethod
    def output_schema(self) -> OutputSchema:
        """Return output schema."""
        pass
    
    @abstractmethod
    async def execute(self, **inputs) -> Any:
        """
        Execute the capability.
        
        Args:
            **inputs: inputs matching input_schema
            
        Returns:
            Output matching output_schema
        """
        pass


class CapabilityRegistry:
    """
    Central registry for all capabilities.
    
    Manages capability discovery, registration, and lookup.
    """
    
    def __init__(self):
        """Initialize the registry."""
        self._capabilities: Dict[str, Capability] = {}
        self._callable_capabilities: Dict[str, Callable] = {}  # Functions wrapped as capabilities
        self._metadata: Dict[str, CapabilityMetadata] = {}
    
    def register(self, capability: Capability) -> None:
        """
        Register a capability.
        
        Args:
            capability: Capability instance with metadata and schemas
        """
        name = capability.metadata.name
        if name in self._capabilities:
            raise ValueError(f"Capability '{name}' already registered")
        
        self._capabilities[name] = capability
        self._metadata[name] = capability.metadata
    
    def register_function(
        self,
        func: Callable,
        name: str,
        description: str,
        input_schema: InputSchema,
        output_schema: OutputSchema,
        tags: Optional[List[str]] = None,
        cost_tier: str = "balanced",
    ) -> None:
        """
        Register a function as a capability.
        
        Args:
            func: Async or sync function
            name: Capability name
            description: Capability description
            input_schema: Input parameter schema
            output_schema: Output schema
            tags: Optional tags for categorization
            cost_tier: Cost tier (ultra_cheap, cheap, balanced, premium)
        """
        if name in self._capabilities or name in self._callable_capabilities:
            raise ValueError(f"Capability '{name}' already registered")
        
        self._callable_capabilities[name] = func
        self._metadata[name] = CapabilityMetadata(
            name=name,
            description=description,
            tags=tags or [],
            cost_tier=cost_tier,
        )
    
    def get(self, name: str) -> Optional[Capability]:
        """Get a capability by name."""
        return self._capabilities.get(name)
    
    def get_function(self, name: str) -> Optional[Callable]:
        """Get a registered function by name."""
        return self._callable_capabilities.get(name)
    
    def get_metadata(self, name: str) -> Optional[CapabilityMetadata]:
        """Get capability metadata by name."""
        return self._metadata.get(name)
    
    def list_capabilities(self) -> Dict[str, CapabilityMetadata]:
        """List all registered capabilities with metadata."""
        return self._metadata.copy()
    
    def list_by_tag(self, tag: str) -> List[str]:
        """List capability names by tag."""
        return [
            name for name, metadata in self._metadata.items()
            if tag in metadata.tags
        ]
    
    def list_by_cost_tier(self, tier: str) -> List[str]:
        """List capability names by cost tier."""
        return [
            name for name, metadata in self._metadata.items()
            if metadata.cost_tier == tier
        ]
    
    async def execute(self, name: str, **inputs) -> Any:
        """
        Execute a capability by name.
        
        Args:
            name: Capability name
            **inputs: Input parameters
            
        Returns:
            Capability output
            
        Raises:
            ValueError: If capability not found
        """
        # Try executable capability first
        cap = self.get(name)
        if cap:
            return await cap.execute(**inputs)
        
        # Try callable capability
        func = self.get_function(name)
        if func:
            # Check if async or sync
            if inspect.iscoroutinefunction(func):
                return await func(**inputs)
            else:
                return func(**inputs)
        
        raise ValueError(f"Capability '{name}' not found in registry")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert registry to dict (for API serialization)."""
        return {
            name: {
                "metadata": metadata.to_dict(),
                "input_schema": self._get_input_schema_dict(name),
                "output_schema": self._get_output_schema_dict(name),
            }
            for name, metadata in self._metadata.items()
        }
    
    def _get_input_schema_dict(self, name: str) -> Dict[str, Any]:
        """Get input schema dict for a capability."""
        cap = self.get(name)
        if cap:
            return cap.input_schema.to_dict()
        # For function-based capabilities, we don't have schema yet
        # (would need decorator or manual registration)
        return {"parameters": []}
    
    def _get_output_schema_dict(self, name: str) -> Dict[str, Any]:
        """Get output schema dict for a capability."""
        cap = self.get(name)
        if cap:
            return cap.output_schema.to_dict()
        return {"return_type": "any", "description": "", "output_format": "json"}


# Global registry instance
_registry: Optional[CapabilityRegistry] = None


def get_registry() -> CapabilityRegistry:
    """Get the global capability registry."""
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
    return _registry


def set_registry(registry: CapabilityRegistry) -> None:
    """Set the global capability registry."""
    global _registry
    _registry = registry
