"""
Capability Introspection - Auto-discover and register capabilities from agents.

Scans agent packages for public methods and automatically registers them
as capabilities with derived input/output schemas.
"""

import inspect
from typing import Any, Callable, List, Optional, get_type_hints
import re

from .capability_registry import (
    CapabilityRegistry,
    InputSchema,
    OutputSchema,
    ParameterSchema,
    ParameterType,
    CapabilityMetadata,
    Capability,
)


class CapabilityIntrospector:
    """Auto-discovers and registers capabilities from agents and services."""
    
    # Mapping of Python type hints to ParameterType
    TYPE_MAPPING = {
        str: ParameterType.STRING,
        int: ParameterType.INTEGER,
        float: ParameterType.FLOAT,
        bool: ParameterType.BOOLEAN,
        dict: ParameterType.OBJECT,
        list: ParameterType.ARRAY,
    }
    
    def __init__(self, registry: CapabilityRegistry):
        """Initialize introspector."""
        self.registry = registry
    
    def _get_type_from_hint(self, hint: Any) -> ParameterType:
        """Convert Python type hint to ParameterType."""
        if hint in self.TYPE_MAPPING:
            return self.TYPE_MAPPING[hint]
        
        # Handle Optional types
        if hasattr(hint, '__origin__'):
            if hint.__origin__ is type(Optional):
                return ParameterType.ANY
        
        return ParameterType.ANY
    
    def _extract_schema_from_docstring(self, docstring: Optional[str]) -> tuple[InputSchema, OutputSchema]:
        """
        Extract input/output schema from docstring.
        
        Expected format:
        ```
        Function description.
        
        Args:
            param_name: Parameter description (type: string/int/etc)
        
        Returns:
            Return type and description
        ```
        """
        input_params = []
        output_description = ""
        output_type = ParameterType.ANY
        
        if not docstring:
            return InputSchema(), OutputSchema()
        
        lines = docstring.split('\n')
        in_args = False
        in_returns = False
        
        for line in lines:
            line = line.strip()
            
            if line.startswith('Args:'):
                in_args = True
                in_returns = False
                continue
            elif line.startswith('Returns:'):
                in_returns = True
                in_args = False
                continue
            
            if in_args and line and not line.startswith('Returns:'):
                # Parse "param_name: description (type: type_name)"
                match = re.match(r'(\w+):\s*(.+?)(?:\s*\(type:\s*(\w+)\))?$', line)
                if match:
                    param_name = match.group(1)
                    description = match.group(2)
                    param_type_str = match.group(3) or "string"
                    
                    # Convert string type to ParameterType
                    param_type = ParameterType.ANY
                    for ptype in ParameterType:
                        if ptype.value == param_type_str.lower():
                            param_type = ptype
                            break
                    
                    input_params.append(ParameterSchema(
                        name=param_name,
                        type=param_type,
                        description=description,
                        required=True,
                    ))
            
            elif in_returns:
                if not line.startswith('Args:'):
                    output_description = line
        
        return (
            InputSchema(parameters=input_params),
            OutputSchema(description=output_description, return_type=output_type),
        )
    
    def _extract_schema_from_signature(
        self,
        func: Callable,
        type_hints: dict
    ) -> InputSchema:
        """
        Extract input schema from function signature and type hints.
        
        Args:
            func: Function to introspect
            type_hints: Result of get_type_hints(func)
            
        Returns:
            InputSchema derived from signature
        """
        sig = inspect.signature(func)
        input_params = []
        
        for param_name, param in sig.parameters.items():
            if param_name in ('self', 'cls'):
                continue
            
            # Get type hint if available
            param_type = ParameterType.ANY
            if param_name in type_hints:
                param_type = self._get_type_from_hint(type_hints[param_name])
            
            # Default value indicates optional
            required = param.default == inspect.Parameter.empty
            default = param.default if param.default != inspect.Parameter.empty else None
            
            input_params.append(ParameterSchema(
                name=param_name,
                type=param_type,
                description=f"Parameter {param_name}",
                required=required,
                default=default,
            ))
        
        return InputSchema(parameters=input_params)
    
    def register_function_as_capability(
        self,
        func: Callable,
        name: str,
        description: str = "",
        tags: Optional[List[str]] = None,
        cost_tier: str = "balanced",
    ) -> bool:
        """
        Register a single function as a capability.
        
        Args:
            func: Function to register
            name: Capability name
            description: Capability description (from docstring if empty)
            tags: Tags for categorization
            cost_tier: Cost tier (ultra_cheap, cheap, balanced, premium)
            
        Returns:
            True if registered successfully
        """
        try:
            # Get type hints
            try:
                type_hints = get_type_hints(func)
            except:
                type_hints = {}
            
            # Use provided description or extract from docstring
            doc = func.__doc__ or description or name
            if not description:
                description = doc.split('\n')[0] if doc else name
            
            # Extract schemas
            try:
                input_schema, output_schema = self._extract_schema_from_docstring(doc)
                # Enhance with signature info if docstring parsing didn't work
                if not input_schema.parameters:
                    input_schema = self._extract_schema_from_signature(func, type_hints)
            except:
                input_schema = self._extract_schema_from_signature(func, type_hints)
                output_schema = OutputSchema()
            
            # Register
            self.registry.register_function(
                func=func,
                name=name,
                description=description,
                input_schema=input_schema,
                output_schema=output_schema,
                tags=tags,
                cost_tier=cost_tier,
            )
            
            return True
        
        except Exception as e:
            print(f"Failed to register capability '{name}': {e}")
            return False
    
    def register_class_methods_as_capabilities(
        self,
        cls: type,
        instance: Optional[Any] = None,
        method_patterns: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        cost_tier: str = "balanced",
    ) -> int:
        """
        Register public methods of a class as capabilities.
        
        Args:
            cls: Class to introspect
            instance: Instance of class (if None, will try to instantiate)
            method_patterns: List of method name patterns to include (regex)
                             If None, includes all public methods
            tags: Tags for all capabilities
            cost_tier: Cost tier for all capabilities
            
        Returns:
            Number of successfully registered capabilities
        """
        count = 0
        
        # Get all public methods
        for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
            if name.startswith('_'):
                continue  # Skip private methods
            
            # Apply method patterns if specified
            if method_patterns:
                if not any(re.match(pattern, name) for pattern in method_patterns):
                    continue
            
            # Get actual function (handle descriptors)
            try:
                if instance and hasattr(instance, name):
                    func = getattr(instance, name)
                else:
                    func = method
                
                # Derive capability name from class and method
                cap_name = f"{cls.__name__}.{name}".lower()
                
                if self.register_function_as_capability(
                    func,
                    name=cap_name,
                    description=method.__doc__ or f"{cls.__name__} {name}",
                    tags=tags or [cls.__name__],
                    cost_tier=cost_tier,
                ):
                    count += 1
            
            except Exception as e:
                print(f"Failed to register {cls.__name__}.{name}: {e}")
        
        return count
    
    def register_module_functions(
        self,
        module: Any,
        function_patterns: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        cost_tier: str = "balanced",
    ) -> int:
        """
        Register functions from a module as capabilities.
        
        Args:
            module: Module to scan
            function_patterns: List of function name patterns to include (regex)
            tags: Tags for all capabilities
            cost_tier: Cost tier for all capabilities
            
        Returns:
            Number of successfully registered capabilities
        """
        count = 0
        
        for name, obj in inspect.getmembers(module, predicate=inspect.isfunction):
            if name.startswith('_'):
                continue
            
            # Apply patterns if specified
            if function_patterns:
                if not any(re.match(pattern, name) for pattern in function_patterns):
                    continue
            
            if self.register_function_as_capability(
                obj,
                name=name,
                description=obj.__doc__ or name,
                tags=tags or [module.__name__],
                cost_tier=cost_tier,
            ):
                count += 1
        
        return count


def auto_register_agent_capabilities(
    registry: CapabilityRegistry,
    agent_modules: List[Any],
) -> int:
    """
    Auto-register capabilities from agent modules.
    
    This is the main entry point for capability auto-discovery.
    
    Args:
        registry: CapabilityRegistry to populate
        agent_modules: List of agent modules to scan
        
    Returns:
        Total number of registered capabilities
    """
    introspector = CapabilityIntrospector(registry)
    total = 0
    
    for module in agent_modules:
        # Register module functions
        count = introspector.register_module_functions(
            module,
            tags=[module.__name__],
            cost_tier="balanced",
        )
        total += count
    
    return total
