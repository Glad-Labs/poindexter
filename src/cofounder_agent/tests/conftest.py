"""
Pytest configuration and shared fixtures for Glad Labs unit tests.

This module provides:
- Fixture definitions for common test dependencies
- Mock factories for services and databases
- Async test support configuration
- Test environment setup
"""

import asyncio
import os
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel


# ============================================================================
# Pytest Configuration
# ============================================================================

def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests for individual components")
    config.addinivalue_line("markers", "integration: Integration tests with multiple components")
    config.addinivalue_line("markers", "e2e: End-to-end tests")
    config.addinivalue_line("markers", "slow: Tests that take more than 5 seconds")
    config.addinivalue_line("markers", "smoke: Fast smoke tests for CI pipelines")
    config.addinivalue_line("markers", "websocket: Tests involving WebSocket connections")
    config.addinivalue_line("markers", "performance: Performance benchmarking tests")


# ============================================================================
# Event Loop Management (for async tests)
# ============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def async_context():
    """Async context for tests that need async setup/teardown."""
    yield None


# ============================================================================
# Mock Model Router Fixture
# ============================================================================

class MockModelResponse(BaseModel):
    """Mock LLM response."""
    content: str
    model: str
    usage: Dict[str, int] = {}


@pytest.fixture
def mock_model_router():
    """Mock ModelRouter for testing."""
    router = MagicMock()
    
    async def mock_route(*args, **kwargs):
        return MockModelResponse(
            content="Mock response content",
            model="mock-model",
            usage={"input_tokens": 10, "output_tokens": 20}
        )
    
    router.route = AsyncMock(side_effect=mock_route)
    router.get_available_models = MagicMock(return_value=["mock-model"])
    router.select_model_for_tier = MagicMock(return_value="mock-model")
    
    return router


# ============================================================================
# Mock Database Service Fixture
# ============================================================================

class MockDatabase:
    """In-memory mock database for testing."""
    
    def __init__(self):
        self.tasks: Dict[str, Any] = {}
        self.workflows: Dict[str, Any] = {}
        self.users: Dict[str, Any] = {}
        self.content: Dict[str, Any] = {}
        self.audit_logs: list = []
        self._counter = 0
    
    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.tasks.get(task_id)
    
    async def create_task(self, task_data: Dict[str, Any]) -> str:
        self._counter += 1
        task_id = f"task_{self._counter}"
        self.tasks[task_id] = {**task_data, "id": task_id}
        return task_id
    
    async def update_task(self, task_id: str, updates: Dict[str, Any]) -> None:
        if task_id in self.tasks:
            self.tasks[task_id].update(updates)
    
    async def get_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        return self.workflows.get(workflow_id)
    
    async def create_workflow(self, workflow_data: Dict[str, Any]) -> str:
        self._counter += 1
        workflow_id = f"workflow_{self._counter}"
        self.workflows[workflow_id] = {**workflow_data, "id": workflow_id}
        return workflow_id
    
    async def log_audit(self, event: Dict[str, Any]) -> None:
        self.audit_logs.append(event)
    
    async def close(self) -> None:
        pass


@pytest.fixture
async def mock_database():
    """Fixture providing in-memory mock database."""
    db = MockDatabase()
    yield db
    await db.close()


@pytest.fixture
def mock_database_service(mock_database):
    """Mock DatabaseService."""
    service = MagicMock()
    service.get_task = AsyncMock(side_effect=mock_database.get_task)
    service.create_task = AsyncMock(side_effect=mock_database.create_task)
    service.update_task = AsyncMock(side_effect=mock_database.update_task)
    service.get_workflow = AsyncMock(side_effect=mock_database.get_workflow)
    service.create_workflow = AsyncMock(side_effect=mock_database.create_workflow)
    service.log_audit = AsyncMock(side_effect=mock_database.log_audit)
    
    return service


# ============================================================================
# Mock Workflow Executor Fixture
# ============================================================================

@pytest.fixture
def mock_workflow_executor():
    """Mock WorkflowExecutor for testing."""
    executor = MagicMock()
    
    async def mock_execute(workflow_id: str, **kwargs):
        return {
            "workflow_id": workflow_id,
            "status": "completed",
            "result": "Mock workflow result",
            "phases_completed": 3
        }
    
    executor.execute = AsyncMock(side_effect=mock_execute)
    executor.pause = AsyncMock(return_value={"status": "paused"})
    executor.resume = AsyncMock(return_value={"status": "running"})
    executor.cancel = AsyncMock(return_value={"status": "cancelled"})
    
    return executor


# ============================================================================
# Mock Task Executor Fixture
# ============================================================================

@pytest.fixture
def mock_task_executor():
    """Mock TaskExecutor for testing."""
    executor = MagicMock()
    
    async def mock_execute(task_id: str, **kwargs):
        return {
            "task_id": task_id,
            "status": "completed",
            "result": "Mock task result"
        }
    
    executor.execute = AsyncMock(side_effect=mock_execute)
    executor.get_status = AsyncMock(return_value={"status": "running"})
    executor.cancel = AsyncMock(return_value={"status": "cancelled"})
    
    return executor


# ============================================================================
# Mock Unified Orchestrator Fixture
# ============================================================================

@pytest.fixture
def mock_unified_orchestrator(mock_model_router, mock_database_service, mock_workflow_executor):
    """Mock UnifiedOrchestrator for testing."""
    orchestrator = MagicMock()
    orchestrator.model_router = mock_model_router
    orchestrator.database_service = mock_database_service
    orchestrator.workflow_executor = mock_workflow_executor
    
    async def mock_route_task(intent: str, **kwargs):
        return {
            "selected_agents": ["research_agent", "creative_agent"],
            "execution_plan": "mock_plan",
            "estimated_cost": 0.05
        }
    
    orchestrator.route_task = AsyncMock(side_effect=mock_route_task)
    
    return orchestrator


# ============================================================================
# Test Data Fixtures
# ============================================================================

@pytest.fixture
def sample_task_data():
    """Sample task data for testing."""
    return {
        "type": "content_generation",
        "title": "Test Task",
        "description": "A test task for unit testing",
        "status": "pending",
        "priority": "high",
        "assigned_agent": "content_agent",
        "metadata": {"custom_field": "value"}
    }


@pytest.fixture
def sample_workflow_data():
    """Sample workflow data for testing."""
    return {
        "name": "blog_post",
        "template_name": "blog_post",
        "status": "pending",
        "phases": [
            {"name": "research", "status": "pending"},
            {"name": "creative", "status": "pending"},
            {"name": "qa", "status": "pending"}
        ],
        "metadata": {"blog_topic": "AI agents"}
    }


@pytest.fixture
def sample_user_data():
    """Sample user data for testing."""
    return {
        "username": "test_user",
        "email": "test@example.com",
        "password_hash": "hashed_password",
        "is_active": True,
        "role": "user"
    }


@pytest.fixture
def sample_content_data():
    """Sample content data for testing."""
    return {
        "title": "Test Article",
        "body": "This is test content",
        "status": "draft",
        "quality_score": 0.85,
        "seo_keywords": ["test", "content"]
    }


# ============================================================================
# Environment Fixtures
# ============================================================================

@pytest.fixture
def test_env(monkeypatch):
    """Set up test environment variables."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test_db")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("ENABLE_WORKFLOW_EXECUTION", "true")
    monkeypatch.setenv("ENABLE_CAPABILITY_SYSTEM", "true")
    
    return {
        "DATABASE_URL": "postgresql://test:test@localhost/test_db",
        "LOG_LEVEL": "DEBUG",
        "OLLAMA_BASE_URL": "http://localhost:11434"
    }


# ============================================================================
# Context Manager Fixtures
# ============================================================================

class AsyncContextManager:
    """Helper for async context manager testing."""
    
    def __init__(self) -> None:
        self.entered = False
        self.exited = False
    
    async def __aenter__(self):
        self.entered = True
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exited = True
        return False


@pytest.fixture
def async_context_manager():
    """Fixture for testing async context managers."""
    return AsyncContextManager()


# ============================================================================
# Cleanup Fixtures
# ============================================================================

@pytest.fixture
def cleanup_resources():
    """Fixture for resource cleanup after tests."""
    resources = []
    
    def register_resource(resource):
        resources.append(resource)
        return resource
    
    yield register_resource
    
    # Cleanup
    for resource in resources:
        if hasattr(resource, "close"):
            if asyncio.iscoroutinefunction(resource.close):
                asyncio.run(resource.close())
            else:
                resource.close()


# ============================================================================
# Markers for Test Organization
# ============================================================================

def pytest_collection_modifyitems(config, items):
    """Auto-mark tests based on their location."""
    for item in items:
        if "unit" in str(item.fspath):
            item.add_marker(pytest.mark.unit)
        elif "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        elif "e2e" in str(item.fspath) or "playwright" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)
