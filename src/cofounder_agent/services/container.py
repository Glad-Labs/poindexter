"""
Service Container for Glad Labs AI Co-Founder

This module provides a centralized service registry and dependency injection mechanism.
"""

from typing import Dict, Any, Optional
from fastapi import FastAPI

# Import configuration
from config import get_config

# Get configuration
config = get_config()


class ServiceContainer:
    """Centralized service registry and dependency injection container."""
    
    _instance: Optional['ServiceContainer'] = None
    _services: Dict[str, Any] = {}
    
    def __new__(cls) -> 'ServiceContainer':
        """Singleton pattern for ServiceContainer."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def register(self, name: str, service: Any) -> None:
        """Register a service in the container."""
        self._services[name] = service
    
    def get(self, name: str) -> Any:
        """Get a service from the container."""
        return self._services.get(name)
    
    def get_all(self) -> Dict[str, Any]:
        """Get all registered services."""
        return self._services.copy()
    
    def clear(self) -> None:
        """Clear all registered services."""
        self._services.clear()


# Global service container instance
service_container = ServiceContainer()


def get_service(name: str) -> Any:
    """Get a service from the global container."""
    return service_container.get(name)


def register_service(name: str, service: Any) -> None:
    """Register a service in the global container."""
    service_container.register(name, service)


def initialize_services(app: FastAPI, **services) -> None:
    """
    Initialize services and register them in the container.
    
    Args:
        app: FastAPI application instance
        **services: Services to register
    """
    for name, service in services.items():
        register_service(name, service)