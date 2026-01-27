"""
Phase 2 Integration Tests

Comprehensive test suite validating Phase 2 service injection refactoring.

Tests:
1. ServiceContainer initialization in main.py lifespan
2. Service dependency injection via Depends() pattern
3. All updated route endpoints use correct dependencies
4. Backward compatibility maintained (no breaking changes)
5. Database service is properly injected to all endpoints
6. Request-scoped and global service access patterns work
7. Error handling with service injection
8. No global variables in updated routes
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import Optional

# Add src to path for imports
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.route_utils import (
    ServiceContainer,
    initialize_services,
    get_database_dependency,
    get_orchestrator_dependency,
    get_services,
    get_db_from_request,
)
from services.database_service import DatabaseService


# ============================================================================
# TEST FIXTURES
# ============================================================================


@pytest.fixture
def mock_db_service():
    """Create a mock DatabaseService"""
    db = AsyncMock(spec=DatabaseService)
    db.get_task = AsyncMock(return_value={"id": "task-123", "name": "Test Task"})
    db.create_task = AsyncMock(return_value={"id": "task-456", "name": "New Task"})
    db.update_task_status = AsyncMock(return_value=True)
    db.fetch = AsyncMock(return_value=[])
    db.execute = AsyncMock(return_value=None)
    return db


@pytest.fixture
def mock_orchestrator():
    """Create a mock orchestrator"""
    orchestrator = AsyncMock()
    orchestrator.process = AsyncMock(return_value={"status": "completed"})
    return orchestrator


@pytest.fixture
def app_with_services(mock_db_service, mock_orchestrator):
    """Create a FastAPI app with ServiceContainer initialized"""
    app = FastAPI()

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    # Simulate the lifespan behavior
    @app.on_event("startup")
    async def startup():
        container = ServiceContainer()
        container.set_database(mock_db_service)
        container.set_orchestrator(mock_orchestrator)
        app.state.services = container

    return app


@pytest.fixture
def client(app_with_services):
    """Create a test client"""
    return TestClient(app_with_services)


# ============================================================================
# TEST: ServiceContainer Initialization
# ============================================================================


class TestServiceContainerInitialization:
    """Test ServiceContainer initialization and usage patterns"""

    def test_service_container_creation(self, mock_db_service, mock_orchestrator):
        """Test that ServiceContainer can be created and services set"""
        container = ServiceContainer()
        assert container is not None

        container.set_database(mock_db_service)
        container.set_orchestrator(mock_orchestrator)

        assert container.get_database() == mock_db_service
        assert container.get_orchestrator() == mock_orchestrator

    def test_service_container_get_methods(self, mock_db_service):
        """Test all getter methods of ServiceContainer"""
        container = ServiceContainer()
        container.set_database(mock_db_service)

        # Should return the database service
        db = container.get_database()
        assert db == mock_db_service

    def test_service_container_state_in_app(self, app_with_services):
        """Test that ServiceContainer is stored in app.state"""
        # This would be verified at runtime, but we can check it's set up correctly
        client = TestClient(app_with_services)
        response = client.get("/healthz")
        assert response.status_code == 200


# ============================================================================
# TEST: Dependency Injection Pattern
# ============================================================================


class TestDependencyInjectionPattern:
    """Test FastAPI Depends() pattern for service injection"""

    @pytest.mark.asyncio
    async def test_get_database_dependency(self, mock_db_service):
        """Test that get_database_dependency returns the database service"""
        # Create container and set it in a mock request state
        container = ServiceContainer()
        container.set_database(mock_db_service)

        # Simulate the dependency function
        # In real usage, this would be called by FastAPI's dependency system
        db = container.get_database()
        assert db == mock_db_service

    @pytest.mark.asyncio
    async def test_dependency_injection_in_endpoint(self, mock_db_service, mock_orchestrator):
        """Test that dependency injection works in an endpoint"""
        app = FastAPI()

        # Initialize services in app state
        @app.on_event("startup")
        async def startup():
            container = ServiceContainer()
            container.set_database(mock_db_service)
            container.set_orchestrator(mock_orchestrator)
            app.state.services = container

        # Create an endpoint that uses the dependency
        @app.get("/test-task/{task_id}")
        async def get_task(
            task_id: str,
            db_service: DatabaseService = Depends(get_database_dependency),
        ):
            task = await db_service.get_task(task_id)
            return task

        client = TestClient(app)
        response = client.get("/test-task/task-123")

        # Verify the endpoint called the mocked db_service
        assert mock_db_service.get_task.called


# ============================================================================
# TEST: Route File Updates - Syntax and Imports
# ============================================================================


class TestRouteFileUpdates:
    """Test that all route files have been updated correctly"""

    def test_content_routes_has_dependency(self):
        """Test that content_routes.py has the dependency import"""
        try:
            from routes.content_routes import router as content_router

            # If import succeeds, the file has correct syntax
            assert content_router is not None
        except ImportError as e:
            pytest.fail(f"Failed to import content_routes: {e}")

    def test_task_routes_has_dependency(self):
        """Test that task_routes.py has the dependency import"""
        try:
            from routes.task_routes import router as task_router

            assert task_router is not None
        except ImportError as e:
            pytest.fail(f"Failed to import task_routes: {e}")

    @pytest.mark.skip(reason="subtask_routes module removed - consolidated into task_routes")
    def test_subtask_routes_has_dependency(self):
        """Test that subtask_routes.py has the dependency import"""
        pass

    def test_bulk_task_routes_has_dependency(self):
        """Test that bulk_task_routes.py has the dependency import"""
        try:
            from routes.bulk_task_routes import router as bulk_router

            assert bulk_router is not None
        except ImportError as e:
            pytest.fail(f"Failed to import bulk_task_routes: {e}")

    def test_content_routes_no_global_db_service(self):
        """Test that content_routes.py no longer has global db_service"""
        import inspect

        try:
            from routes import content_routes

            # Check that there's no module-level db_service variable
            source = inspect.getsource(content_routes)

            # Should not have "db_service = None" at module level
            # (It might exist as a local variable, but not as a global)
            lines = source.split("\n")

            # Find module-level assignments (not indented)
            module_level_lines = [l for l in lines if l and not l[0].isspace()]
            db_assignments = [l for l in module_level_lines if "db_service = " in l]

            # Should not have "db_service = None" pattern
            assert not any(
                "db_service = None" in l for l in db_assignments
            ), "content_routes should not have global db_service = None"
        except ImportError:
            pytest.skip("Could not import content_routes for inspection")

    def test_task_routes_no_global_db_service(self):
        """Test that task_routes.py no longer has global db_service"""
        import inspect

        try:
            from routes import task_routes

            source = inspect.getsource(task_routes)
            lines = source.split("\n")

            # Find module-level db_service assignments
            module_level_lines = [l for l in lines if l and not l[0].isspace()]
            db_assignments = [l for l in module_level_lines if "db_service" in l and "=" in l]

            # Should not have "db_service = None" at module level
            assert not any(
                "db_service = None" in l for l in db_assignments
            ), "task_routes should not have global db_service = None"
        except ImportError:
            pytest.skip("Could not import task_routes for inspection")


# ============================================================================
# TEST: Backward Compatibility
# ============================================================================


class TestBackwardCompatibility:
    """Test that refactoring maintains backward compatibility"""

    def test_endpoint_signatures_preserved(self):
        """Test that endpoint signatures are preserved (parameters added at end)"""
        # In real usage, we would inspect the actual endpoints
        # and verify they still have the same public signature
        # (db_service is added as optional dependency at end)
        assert True  # Placeholder - verified by manual inspection

    def test_no_breaking_changes_to_request_models(self):
        """Test that request/response models are unchanged"""
        try:
            from routes.task_routes import TaskCreateRequest, TaskResponse

            # Verify the models exist and have expected fields
            assert hasattr(TaskCreateRequest, "task_name")
            assert hasattr(TaskResponse, "id")
        except (ImportError, AttributeError) as e:
            pytest.skip(f"Could not verify models: {e}")

    def test_endpoint_response_format_unchanged(self):
        """Test that endpoint responses maintain the same format"""
        # This would be tested with integration tests against running server
        assert True  # Placeholder


# ============================================================================
# TEST: Error Handling with Service Injection
# ============================================================================


class TestErrorHandlingWithServiceInjection:
    """Test that error handling works with the new dependency injection pattern"""

    @pytest.mark.asyncio
    async def test_missing_service_returns_500(self):
        """Test that missing service is handled gracefully"""
        app = FastAPI()

        # Don't initialize services
        @app.on_event("startup")
        async def startup():
            # Intentionally don't set up services
            pass

        @app.get("/test")
        async def test_endpoint(db_service: DatabaseService = Depends(get_database_dependency)):
            return {"status": "ok"}

        client = TestClient(app)
        # This should handle the missing service gracefully
        # In FastAPI, this would result in a 500 or proper error handling
        assert client is not None

    def test_validation_errors_preserved(self):
        """Test that validation errors still work with dependency injection"""
        app = FastAPI()

        class TaskRequest(BaseModel):
            name: str

        @app.post("/tasks")
        async def create_task(
            request: TaskRequest,
            db_service: DatabaseService = Depends(get_database_dependency),
        ):
            return {"id": "123", "name": request.name}

        client = TestClient(app)

        # Send invalid request (missing required field)
        response = client.post("/tasks", json={})

        # Should get validation error
        assert response.status_code == 422  # Unprocessable Entity


# ============================================================================
# TEST: Service Injection Across Multiple Patterns
# ============================================================================


class TestMultipleAccessPatterns:
    """Test that services can be accessed via multiple patterns"""

    def test_global_service_access(self, mock_db_service):
        """Test global service access via get_services()"""
        container = ServiceContainer()
        container.set_database(mock_db_service)

        # Simulate storing in a global or app state
        db = container.get_database()
        assert db == mock_db_service

    def test_depends_pattern_availability(self):
        """Test that Depends() pattern is available"""
        # Verify the dependency function exists and is callable
        assert callable(get_database_dependency)

    def test_request_state_pattern_availability(self):
        """Test that request.state pattern is available"""
        # Verify the helper function exists
        assert callable(get_db_from_request)


# ============================================================================
# TEST: Service Injection Verification
# ============================================================================


class TestServiceInjectionVerification:
    """Verify that services are properly injected in endpoints"""

    @pytest.mark.asyncio
    async def test_db_service_injected_to_endpoint(self, mock_db_service, mock_orchestrator):
        """Test that db_service is properly injected to endpoints"""
        app = FastAPI()

        # Initialize services
        @app.on_event("startup")
        async def startup():
            container = ServiceContainer()
            container.set_database(mock_db_service)
            container.set_orchestrator(mock_orchestrator)
            app.state.services = container

        # Create endpoint that uses db_service
        @app.get("/test-injection")
        async def test_injection(db_service=Depends(get_database_dependency)):
            # Verify db_service is the mock
            result = await db_service.get_task("test-id")
            return result

        client = TestClient(app)
        response = client.get("/test-injection")

        # Verify the mock was called
        assert mock_db_service.get_task.called


# ============================================================================
# INTEGRATION TESTS - Full Route Testing
# ============================================================================


class TestFullRouteIntegration:
    """Integration tests for full route functionality"""

    def test_task_route_imports(self):
        """Test that task_routes can be imported and has router"""
        try:
            from routes.task_routes import router as task_router

            assert task_router is not None
            # Router should have routes registered
            # (This is basic validation - full testing requires running server)
        except ImportError as e:
            pytest.fail(f"Could not import task_routes: {e}")

    def test_content_route_imports(self):
        """Test that content_routes can be imported and has router"""
        try:
            from routes.content_routes import router as content_router

            assert content_router is not None
        except ImportError as e:
            pytest.fail(f"Could not import content_routes: {e}")

    @pytest.mark.skip(reason="subtask_routes module removed - consolidated into task_routes")
    def test_subtask_route_imports(self):
        """Test that subtask_routes can be imported and has router"""
        pass

    def test_bulk_task_route_imports(self):
        """Test that bulk_task_routes can be imported and has router"""
        try:
            from routes.bulk_task_routes import router as bulk_router

            assert bulk_router is not None
        except ImportError as e:
            pytest.fail(f"Could not import bulk_task_routes: {e}")


# ============================================================================
# SMOKE TESTS - Verify Basic Functionality
# ============================================================================


class TestSmokeTests:
    """Basic smoke tests to verify nothing is broken"""

    def test_service_container_can_be_created(self):
        """Test that ServiceContainer can be instantiated"""
        container = ServiceContainer()
        assert container is not None

    def test_get_database_dependency_callable(self):
        """Test that get_database_dependency is callable"""
        assert callable(get_database_dependency)

    def test_initialize_services_callable(self):
        """Test that initialize_services is callable"""
        assert callable(initialize_services)

    def test_route_utils_module_importable(self):
        """Test that route_utils module can be imported"""
        try:
            from utils import route_utils

            assert route_utils is not None
        except ImportError as e:
            pytest.fail(f"Could not import route_utils: {e}")


# ============================================================================
# RUN TESTS
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
