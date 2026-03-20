"""
Shared pytest configuration and fixtures for all tests.
Central conftest.py for the entire test suite.

This module provides:
- Core pytest configuration and markers
- Fixture definitions for common test dependencies
- Mock factories for services and databases
- Async test support configuration
- Test environment setup
"""
import os
import sys
import asyncio
import pytest
from pathlib import Path
from dotenv import load_dotenv
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock
from pydantic import BaseModel

# Add project root to path
project_root = str(Path(__file__).parent.parent)
backend_path = os.path.join(project_root, 'src/cofounder_agent')

# Insert in order so backend code can be imported
sys.path.insert(0, backend_path)      # Main backend imports: agents, services, routes, etc.
sys.path.insert(0, project_root)       # Project root: src.mcp, src.mcp_server, etc.

# Load environment variables
env_local_path = os.path.join(project_root, ".env")
if os.path.exists(env_local_path):
    load_dotenv(env_local_path, override=True)
    os.environ['TESTING'] = '1'

# Try to import shared test utilities, but don't fail if not available
imported_test_config: Any
try:
    from test_utils import test_utils, performance_monitor, test_config
    imported_test_config = test_config
except ImportError as e:
    print(f"⚠️  Could not import test_utils: {e}")
    # Define a minimal test config if import fails
    class MinimalTestConfig:
        pass
    imported_test_config = MinimalTestConfig()


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def test_config_fixture():
    """Test configuration fixture."""
    return imported_test_config


@pytest.fixture(scope="session")
def project_root_path():
    """Return the project root path."""
    return Path(__file__).parent.parent


@pytest.fixture(scope="session")
def performance_monitor_fixture():
    """Performance monitor fixture."""
    return performance_monitor


@pytest.fixture(scope="session")
def test_utils_fixture():
    """Test utilities fixture."""
    return test_utils


# Expose at module level for direct imports (backward compatibility with old tests)
TEST_CONFIG = imported_test_config
mock_api_responses: dict[str, Any] = {}
performance_monitor = performance_monitor
test_utils = test_utils


# Pytest markers
def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line("markers", "unit: unit tests")
    config.addinivalue_line("markers", "integration: integration tests")
    config.addinivalue_line("markers", "e2e: end-to-end tests")
    config.addinivalue_line("markers", "api: API endpoint tests")
    config.addinivalue_line("markers", "slow: slow running tests")
    config.addinivalue_line("markers", "skip_ci: skip in CI environment")
    config.addinivalue_line("markers", "asyncio: async tests")
    config.addinivalue_line("markers", "performance: performance tests")
    config.addinivalue_line("markers", "websocket: WebSocket tests")
    config.addinivalue_line("markers", "smoke: Fast smoke tests for CI pipelines")


# ============================================================================
# PHASE 1 MOCK SERVICES & FIXTURES
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


class MockDatabase:
    """In-memory mock database for testing."""
    
    def __init__(self):
        self.tasks: Dict[str, Any] = {}
        self.workflows: Dict[str, Any] = {}
        self.users: Dict[str, Any] = {}
        self.content: Dict[str, Any] = {}
        self.audit_logs: list[Dict[str, Any]] = []
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


@pytest.fixture
def mock_workflow_executor():
    """Mock WorkflowExecutor for testing."""
    executor = MagicMock()
    
    async def mock_execute(workflow_id: str, **kwargs: Any) -> dict[str, Any]:
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


@pytest.fixture
def mock_task_executor():
    """Mock TaskExecutor for testing."""
    executor = MagicMock()
    
    async def mock_execute(task_id: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "status": "completed",
            "result": "Mock task result"
        }
    
    executor.execute = AsyncMock(side_effect=mock_execute)
    executor.get_status = AsyncMock(return_value={"status": "running"})
    executor.cancel = AsyncMock(return_value={"status": "cancelled"})
    
    return executor


@pytest.fixture
def mock_unified_orchestrator(mock_model_router, mock_database_service, mock_workflow_executor):
    """Mock UnifiedOrchestrator for testing."""
    orchestrator = MagicMock()
    orchestrator.model_router = mock_model_router
    orchestrator.database_service = mock_database_service
    orchestrator.workflow_executor = mock_workflow_executor
    
    async def mock_route_task(intent: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "selected_agents": ["research_agent", "creative_agent"],
            "execution_plan": "mock_plan",
            "estimated_cost": 0.05
        }
    
    orchestrator.route_task = AsyncMock(side_effect=mock_route_task)
    
    return orchestrator


# ============================================================================
# TEST DATA FIXTURES
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


# ============================================================================
# PHASE 2 DATABASE MODULE FIXTURES
# ============================================================================

def create_mock_db_row(**kwargs) -> Dict[str, Any]:
    """
    Create a complete mock database row with all required fields.
    
    Args:
        **kwargs: Override default values for specific fields
    
    Returns:
        Dict with all standard database fields including datetime objects
    """
    from datetime import datetime, timezone
    
    defaults = {
        "id": "test_id_1",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    # Merge defaults with provided kwargs
    return {**defaults, **kwargs}


@pytest.fixture
def mock_pool():
    """Mock asyncpg connection pool for database module testing."""
    pool = MagicMock()
    
    # Configure pool to support async context manager pattern
    mock_conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    
    return pool


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
# ENVIRONMENT FIXTURES
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
# CONTEXT MANAGER FIXTURES
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
# CLEANUP FIXTURES
# ============================================================================

@pytest.fixture
def cleanup_resources():
    """Fixture for resource cleanup after tests."""
    resources: list[Any] = []
    
    def register_resource(resource: Any) -> Any:
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
# TEST COLLECTION MARKERS
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

