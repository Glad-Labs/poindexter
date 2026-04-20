"""
Service Container for Glad Labs AI Co-Founder

Centralized service registry. Each ``ServiceContainer`` instance owns its
own services dict — no shared class-level state, so tests can build an
isolated container without polluting siblings.

A module-level ``service_container`` instance is kept for back-compat,
but routes should prefer ``request.app.state.service_container`` (stashed
in lifespan) and tests should build a fresh ``ServiceContainer()``.
"""

from typing import Any

from fastapi import FastAPI


class ServiceContainer:
    """Centralized service registry.

    Instance-scoped: each container instance has its own ``_services``
    dict. No singleton enforcement — callers choose between the
    module-level instance (legacy callers) and ``app.state`` (routes).
    """

    def __init__(self) -> None:
        self._services: dict[str, Any] = {}

    def register(self, name: str, service: Any) -> None:
        """Register a service in the container."""
        self._services[name] = service

    def get(self, name: str) -> Any:
        """Get a service from the container."""
        return self._services.get(name)

    def get_all(self) -> dict[str, Any]:
        """Get all registered services."""
        return self._services.copy()

    def clear(self) -> None:
        """Clear all registered services."""
        self._services.clear()


# Module-level instance — legacy callers (main.py lifespan,
# a handful of lazily-imported services) use this. New code should
# prefer ``request.app.state.service_container`` or a fresh instance.
service_container = ServiceContainer()


def get_service(name: str) -> Any:
    """Get a service from the module-level container."""
    return service_container.get(name)


def register_service(name: str, service: Any) -> None:
    """Register a service in the module-level container."""
    service_container.register(name, service)


def initialize_services(app: FastAPI, **services) -> None:
    """Register services in the module-level container and stash the
    container on ``app.state.service_container`` so route dependencies
    can access it without importing the module-level instance."""
    for name, service in services.items():
        register_service(name, service)
    app.state.service_container = service_container
