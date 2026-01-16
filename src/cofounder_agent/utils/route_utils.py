"""
Route Utilities - Centralized dependency injection for routes

This module provides a centralized way to inject and access services in route handlers,
eliminating the need for global variables and set_db_service() patterns across multiple routes.

Provides:
- Centralized service management
- FastAPI dependency injection integration
- Request-scoped service access
- Clean separation of concerns

This eliminates the duplicate pattern of:
    db_service = None
    def set_db_service(service):
        global db_service
        db_service = service

Found in: content_routes, task_routes, subtask_routes, bulk_task_routes, settings_routes
"""

import logging
from typing import Optional, Any
from fastapi import FastAPI, Request, Depends
from functools import lru_cache

logger = logging.getLogger(__name__)


# ============================================================================
# SERVICE CONTAINER
# ============================================================================


class ServiceContainer:
    """
    Centralized container for managing application services.

    This replaces the scattered global variables and set_db_service() functions
    across multiple route files with a single, organized container.

    Usage:
        # In main.py during startup
        services = ServiceContainer()
        services.set_database(db_service)
        services.set_orchestrator(orchestrator)

        # In route files
        @app.get("/tasks")
        async def list_tasks(request: Request):
            db = request.state.services.get_database()
            tasks = await db.pool.fetch("SELECT * FROM tasks")
            return tasks
    """

    def __init__(self):
        """Initialize service container with None values"""
        self._database_service = None
        self._orchestrator = None
        self._task_executor = None
        self._intelligent_orchestrator = None
        self._workflow_history = None
        self._additional_services = {}

    def set_database(self, service: Any) -> None:
        """Set the database service"""
        self._database_service = service
        logger.info("Database service registered with ServiceContainer")

    def set_orchestrator(self, service: Any) -> None:
        """Set the orchestrator service"""
        self._orchestrator = service
        logger.info("Orchestrator service registered with ServiceContainer")

    def set_task_executor(self, service: Any) -> None:
        """Set the task executor service"""
        self._task_executor = service
        logger.info("Task executor service registered with ServiceContainer")

    def set_intelligent_orchestrator(self, service: Any) -> None:
        """Set the intelligent orchestrator service"""
        self._intelligent_orchestrator = service
        logger.info("Intelligent orchestrator service registered with ServiceContainer")

    def set_workflow_history(self, service: Any) -> None:
        """Set the workflow history service"""
        self._workflow_history = service
        logger.info("Workflow history service registered with ServiceContainer")

    def set_service(self, name: str, service: Any) -> None:
        """Set an arbitrary service by name"""
        self._additional_services[name] = service
        logger.info(f"Service '{name}' registered with ServiceContainer")

    def get_database(self) -> Optional[Any]:
        """Get the database service"""
        return self._database_service

    def get_orchestrator(self) -> Optional[Any]:
        """Get the orchestrator service"""
        return self._orchestrator

    def get_task_executor(self) -> Optional[Any]:
        """Get the task executor service"""
        return self._task_executor

    def get_intelligent_orchestrator(self) -> Optional[Any]:
        """Get the intelligent orchestrator service"""
        return self._intelligent_orchestrator

    def get_workflow_history(self) -> Optional[Any]:
        """Get the workflow history service"""
        return self._workflow_history

    def get_service(self, name: str) -> Optional[Any]:
        """Get an arbitrary service by name"""
        return self._additional_services.get(name)

    def get_all_services(self) -> dict:
        """Get all registered services"""
        return {
            "database": self._database_service,
            "orchestrator": self._orchestrator,
            "task_executor": self._task_executor,
            "intelligent_orchestrator": self._intelligent_orchestrator,
            "workflow_history": self._workflow_history,
            **self._additional_services,
        }


# ============================================================================
# GLOBAL SERVICE CONTAINER INSTANCE
# ============================================================================

# Single global instance
_services = ServiceContainer()


def get_services() -> ServiceContainer:
    """
    Get the global service container instance.

    This function provides lazy access to the service container.
    Use this in route files instead of global variables.

    Returns:
        ServiceContainer: The global service container

    Example:
        from utils.route_utils import get_services

        async def list_tasks():
            services = get_services()
            db = services.get_database()
            tasks = await db.pool.fetch("SELECT * FROM tasks")
            return tasks
    """
    return _services


# ============================================================================
# FASTAPI DEPENDENCY INJECTION FUNCTIONS
# ============================================================================


def get_database_dependency() -> Any:
    """
    FastAPI dependency for database service.

    Usage:
        @app.get("/tasks")
        async def list_tasks(db = Depends(get_database_dependency)):
            tasks = await db.pool.fetch("SELECT * FROM tasks")
            return tasks
    """
    db = _services.get_database()
    if db is None:
        raise RuntimeError("Database service not initialized")
    return db


def get_orchestrator_dependency() -> Any:
    """FastAPI dependency for orchestrator service"""
    orchestrator = _services.get_orchestrator()
    if orchestrator is None:
        raise RuntimeError("Orchestrator not initialized")
    return orchestrator


def get_task_executor_dependency() -> Any:
    """FastAPI dependency for task executor service"""
    executor = _services.get_task_executor()
    if executor is None:
        raise RuntimeError("Task executor not initialized")
    return executor


def get_enhanced_status_change_service() -> Any:
    """FastAPI dependency for enhanced status change service."""
    from services.enhanced_status_change_service import EnhancedStatusChangeService
    from services.tasks_db import TaskDatabaseService

    # Get the database pool from the generic database service
    db = _services.get_database()
    if db is None:
        raise RuntimeError("Database service not initialized")

    # Create TaskDatabaseService with the pool
    task_db = TaskDatabaseService(db.pool)

    # Create and return EnhancedStatusChangeService
    return EnhancedStatusChangeService(task_db)


def get_intelligent_orchestrator_dependency() -> Any:
    """FastAPI dependency for intelligent orchestrator service"""
    io = _services.get_intelligent_orchestrator()
    if io is None:
        raise RuntimeError("Intelligent orchestrator not initialized")
    return io


def get_workflow_history_dependency() -> Any:
    """FastAPI dependency for workflow history service"""
    wh = _services.get_workflow_history()
    if wh is None:
        raise RuntimeError("Workflow history service not initialized")
    return wh


def get_service_dependency(service_name: str) -> Any:
    """FastAPI dependency for arbitrary service by name"""
    service = _services.get_service(service_name)
    if service is None:
        raise RuntimeError(f"Service '{service_name}' not initialized")
    return service


# ============================================================================
# LEGACY COMPATIBILITY FUNCTIONS
# ============================================================================


def register_legacy_db_service(service: Any) -> None:
    """
    Wrapper for legacy set_db_service() calls.

    This allows old route files to continue working with:
        from utils.route_utils import register_legacy_db_service
        register_legacy_db_service(db_service)

    Instead of the old pattern of defining set_db_service() in each route file.

    Args:
        service: Database service instance
    """
    _services.set_database(service)
    logger.info("Database service registered via legacy register_legacy_db_service()")


# ============================================================================
# INITIALIZATION FUNCTION FOR MAIN.PY
# ============================================================================


def initialize_services(
    app: FastAPI,
    database_service: Any = None,
    orchestrator: Any = None,
    task_executor: Any = None,
    intelligent_orchestrator: Any = None,
    workflow_history: Any = None,
    **additional_services,
) -> ServiceContainer:
    """
    Initialize the global service container with all services.

    This should be called in main.py after all services are initialized.

    Args:
        app: FastAPI application instance
        database_service: Database service instance
        orchestrator: Orchestrator instance
        task_executor: Task executor instance
        intelligent_orchestrator: Intelligent orchestrator instance
        workflow_history: Workflow history service instance
        **additional_services: Any additional services to register

    Returns:
        ServiceContainer: The initialized global service container

    Example:
        from utils.route_utils import initialize_services

        # In lifespan or startup event
        services = await startup_manager.initialize_all_services()
        initialize_services(
            app,
            database_service=services['database'],
            orchestrator=services['orchestrator'],
            task_executor=services['task_executor'],
            intelligent_orchestrator=services['intelligent_orchestrator'],
            workflow_history=services['workflow_history']
        )
    """
    if database_service:
        _services.set_database(database_service)

    if orchestrator:
        _services.set_orchestrator(orchestrator)

    if task_executor:
        _services.set_task_executor(task_executor)

    if intelligent_orchestrator:
        _services.set_intelligent_orchestrator(intelligent_orchestrator)

    if workflow_history:
        _services.set_workflow_history(workflow_history)

    # Register additional services
    for name, service in additional_services.items():
        if service:
            _services.set_service(name, service)

    # Store services in app state for request-scoped access
    app.state.services = _services

    logger.info(f"✅ Services initialized: {list(_services.get_all_services().keys())}")

    return _services


# ============================================================================
# REQUEST-SCOPED SERVICE ACCESS
# ============================================================================


def get_db_from_request(request: Request) -> Any:
    """
    Get database service from request state.

    Usage:
        @app.get("/tasks")
        async def list_tasks(request: Request):
            db = get_db_from_request(request)
            tasks = await db.pool.fetch("SELECT * FROM tasks")
            return tasks

    Args:
        request: FastAPI Request object

    Returns:
        Database service instance

    Raises:
        RuntimeError: If services not initialized in request.state
    """
    if not hasattr(request.app.state, "services"):
        raise RuntimeError("Services not initialized in app.state")
    return request.app.state.services.get_database()


def get_orchestrator_from_request(request: Request) -> Any:
    """Get orchestrator from request state"""
    if not hasattr(request.app.state, "services"):
        raise RuntimeError("Services not initialized in app.state")
    return request.app.state.services.get_orchestrator()


def get_task_executor_from_request(request: Request) -> Any:
    """Get task executor from request state"""
    if not hasattr(request.app.state, "services"):
        raise RuntimeError("Services not initialized in app.state")
    return request.app.state.services.get_task_executor()


def get_service_from_request(request: Request, service_name: str) -> Any:
    """Get arbitrary service from request state"""
    if not hasattr(request.app.state, "services"):
        raise RuntimeError("Services not initialized in app.state")
    return request.app.state.services.get_service(service_name)


# ============================================================================
# SUMMARY OF PATTERNS
# ============================================================================

"""
OLD PATTERN (still works but deprecated):
===========================================

In main.py:
    from routes.task_routes import set_db_service
    set_db_service(database_service)

In task_routes.py:
    db_service = None
    
    def set_db_service(service: DatabaseService):
        global db_service
        db_service = service
    
    @app.get("/tasks")
    async def list_tasks():
        tasks = await db_service.pool.fetch("SELECT * FROM tasks")
        return tasks

PROBLEMS:
  - Scattered across multiple files
  - Global variables
  - Unclear dependencies
  - Hard to test
  - Multiple set_db_service() definitions


NEW PATTERN 1 (Using global service function):
================================================

In main.py:
    from utils.route_utils import initialize_services
    
    services = await startup_manager.initialize_all_services()
    initialize_services(
        app,
        database_service=services['database'],
        task_executor=services['task_executor']
    )

In task_routes.py:
    from utils.route_utils import get_services
    
    @app.get("/tasks")
    async def list_tasks():
        db = get_services().get_database()
        tasks = await db.pool.fetch("SELECT * FROM tasks")
        return tasks

BENEFITS:
  ✅ Single source for service registration
  ✅ Cleaner route files
  ✅ Type-safe with proper error handling
  ✅ Centralized initialization


NEW PATTERN 2 (Using FastAPI Depends):
=========================================

In main.py:
    from utils.route_utils import initialize_services
    
    services = await startup_manager.initialize_all_services()
    initialize_services(app, database_service=services['database'])

In task_routes.py:
    from utils.route_utils import get_database_dependency
    
    @app.get("/tasks")
    async def list_tasks(db = Depends(get_database_dependency)):
        tasks = await db.pool.fetch("SELECT * FROM tasks")
        return tasks

BENEFITS:
  ✅ Standard FastAPI pattern
  ✅ IDE support and type hints
  ✅ Automatic dependency resolution
  ✅ Easy to mock in tests
  ✅ Clear parameter dependencies


NEW PATTERN 3 (Using Request state):
======================================

In main.py:
    # initialize_services() automatically sets app.state.services

In task_routes.py:
    from utils.route_utils import get_db_from_request
    
    @app.get("/tasks")
    async def list_tasks(request: Request):
        db = get_db_from_request(request)
        tasks = await db.pool.fetch("SELECT * FROM tasks")
        return tasks

BENEFITS:
  ✅ Request-scoped access
  ✅ Works with middleware
  ✅ Can access other request data
  ✅ Flexible for complex scenarios
"""
