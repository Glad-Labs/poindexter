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

from typing import Any

from fastapi import FastAPI, Request

from services.logger_config import get_logger
from services.site_config import SiteConfig

# Process-wide empty-SiteConfig fallback (#272 capstone). Returned by
# ``get_site_config_dependency`` only when neither ``app.state`` nor the
# registered ``AppContainer`` carries a SiteConfig (early boot, tests that
# never bootstrap). Behaves exactly like the old per-module
# ``site_config`` global did before its lifespan setter fired.
_FALLBACK_SITE_CONFIG = SiteConfig()


logger = get_logger(__name__)
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

        # In route files
        @app.get("/tasks")
        async def list_tasks(request: Request):
            db = request.state.services.get_database()
            tasks = await db.tasks.get_all_tasks(limit=100)
            return tasks
    """

    def __init__(self):
        """Initialize service container with None values"""
        self._database_service = None
        self._redis_cache = None
        self._additional_services = {}

    def set_database(self, service: Any) -> None:
        """Set the database service"""
        self._database_service = service
        logger.info("Database service registered with ServiceContainer")

    def set_redis_cache(self, service: Any) -> None:
        """Set the Redis cache service"""
        self._redis_cache = service
        logger.info("Redis cache service registered with ServiceContainer")

    def set_service(self, name: str, service: Any) -> None:
        """Set an arbitrary service by name"""
        self._additional_services[name] = service
        logger.info(f"Service '{name}' registered with ServiceContainer")

    def get_database(self) -> Any | None:
        """Get the database service"""
        return self._database_service

    def get_redis_cache(self) -> Any | None:
        """Get the Redis cache service"""
        return self._redis_cache

    def get_service(self, name: str) -> Any | None:
        """Get an arbitrary service by name"""
        return self._additional_services.get(name)

    def get_all_services(self) -> dict:
        """Get all registered services"""
        return {
            "database": self._database_service,
            "redis_cache": self._redis_cache,
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
            tasks = await db.tasks.get_all_tasks(limit=100)
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
            tasks = await db.tasks.get_all_tasks(limit=100)
            return tasks
    """
    db = _services.get_database()
    if db is None:
        raise RuntimeError("Database service not initialized")
    return db


def get_site_config_dependency(request: Request) -> Any:
    """FastAPI dependency that returns the lifespan-bound SiteConfig.

    Resolution order (#272 capstone — the per-module ``site_config``
    global + ``set_site_config`` setter were retired; the legacy
    ``app.state.site_config`` attribute was then removed once the worker
    hot-reload fix made the container hold that same instance, so the
    parallel attribute was pure redundancy):

    1. ``request.app.state.container.site_config`` — the web process
       attaches the built ``AppContainer`` to ``app.state``; this is the
       canonical SiteConfig instance.
    2. The process-wide ``AppContainer`` via ``get_container()`` — covers
       processes that built a container but didn't stash it on
       ``app.state`` (or non-FastAPI request shims).
    3. ``_FALLBACK_SITE_CONFIG`` — an empty SiteConfig so early-boot /
       test paths never crash.

    Usage::

        from fastapi import Depends
        from utils.route_utils import get_site_config_dependency

        @router.get("/foo")
        async def handler(cfg = Depends(get_site_config_dependency)):
            site_url = cfg.require("site_url")
    """
    state = getattr(request.app, "state", None)

    container = getattr(state, "container", None)
    if container is not None:
        sc = getattr(container, "site_config", None)
        if sc is not None:
            return sc

    from services.container_registry import get_container

    registered = get_container()
    if registered is not None:
        return registered.site_config

    return _FALLBACK_SITE_CONFIG


def get_container_dependency(request: Request) -> Any:
    """FastAPI dependency that returns the lifespan-bound ``AppContainer``.

    Companion to :func:`get_site_config_dependency` for the SiteConfig
    constructor-DI migration (design doc:
    ``docs/architecture/2026-05-28-site-config-di-migration.md``).

    PR 2 of the migration wires ``app.state.container`` from
    ``services.bootstrap.build_container`` in ``main.py``'s lifespan.
    During the migration the container holds no service entries; future
    PRs add a ``@cached_property`` per migrated service, and route
    handlers will reach those services via this dependency::

        from fastapi import Depends
        from utils.route_utils import get_container_dependency

        @router.get("/topics/sweep")
        async def sweep(container = Depends(get_container_dependency)):
            return await container.topic_batch_service.run_sweep(...)

    Per ``feedback_no_silent_defaults``: if the container isn't on
    ``app.state`` (lifespan never ran, or its build_container call
    crashed and was swallowed somewhere), we raise ``RuntimeError``
    rather than silently returning ``None``. A missing container is a
    bug at the entry point, not a tolerable degenerate state.
    """
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RuntimeError(
            "AppContainer not on app.state — lifespan startup did not "
            "call services.bootstrap.build_container, or the call failed. "
            "Check worker startup logs for [LIFESPAN] AppContainer errors."
        )
    return container


def get_enhanced_status_change_service() -> Any:
    """FastAPI dependency for enhanced status change service."""
    from services.enhanced_status_change_service import EnhancedStatusChangeService
    from services.tasks_db import TasksDatabase

    # Get the database pool from the generic database service
    db = _services.get_database()
    if db is None:
        raise RuntimeError("Database service not initialized")

    # Create TasksDatabase with the pool
    task_db = TasksDatabase(db.pool)

    # Create and return EnhancedStatusChangeService
    return EnhancedStatusChangeService(task_db)


def get_redis_cache_dependency() -> Any:
    """FastAPI dependency for Redis cache service"""
    cache = _services.get_redis_cache()
    if cache is None:
        raise RuntimeError("Redis cache service not initialized")
    return cache


def get_redis_cache_optional() -> Any:
    """FastAPI dependency for Redis cache service (optional, returns None if not initialized)"""
    return _services.get_redis_cache()


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
    redis_cache: Any = None,
    **additional_services,
) -> ServiceContainer:
    """
    Initialize the global service container with all services.

    This should be called in main.py after all services are initialized.

    Args:
        app: FastAPI application instance
        database_service: Database service instance
        redis_cache: Redis cache service instance
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
            redis_cache=services['redis_cache'],
        )
    """
    if database_service:
        _services.set_database(database_service)

    if redis_cache:
        _services.set_redis_cache(redis_cache)

    # Register additional services
    for name, service in additional_services.items():
        if service:
            _services.set_service(name, service)

    # Store services in app state for request-scoped access
    app.state.services = _services

    logger.info(f"[OK] Services initialized: {list(_services.get_all_services().keys())}")

    return _services


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
        tasks = await db_service.tasks.get_all_tasks(limit=100)
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
    )

In task_routes.py:
    from utils.route_utils import get_services

    @app.get("/tasks")
    async def list_tasks():
        db = get_services().get_database()
        tasks = await db.tasks.get_all_tasks(limit=100)
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
        tasks = await db.tasks.get_all_tasks(limit=100)
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
        tasks = await db.tasks.get_all_tasks(limit=100)
        return tasks

BENEFITS:
  ✅ Request-scoped access
  ✅ Works with middleware
  ✅ Can access other request data
  ✅ Flexible for complex scenarios
"""
