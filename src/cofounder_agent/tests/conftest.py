"""
Comprehensive Test Suite for AI Co-Founder System
Includes unit tests, integration tests, API tests, and end-to-end testing
"""

import pytest

# Register custom pytest markers
def pytest_configure(config):
    """Configure pytest with custom markers"""
    config.addinivalue_line("markers", "unit: Unit tests")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "api: API endpoint tests")
    config.addinivalue_line("markers", "e2e: End-to-end tests")
    config.addinivalue_line("markers", "performance: Performance benchmarks")
    config.addinivalue_line("markers", "slow: Slow running tests")
    config.addinivalue_line("markers", "voice: Voice interface tests")
    config.addinivalue_line("markers", "websocket: WebSocket functionality tests")
    config.addinivalue_line("markers", "resilience: System resilience tests")
    config.addinivalue_line("markers", "smoke: Smoke tests for basic functionality")
import asyncio
import json
import os
import sys
from unittest.mock import Mock, AsyncMock, patch
from typing import Dict, Any, List
from datetime import datetime
import tempfile
import shutil

# Add project paths
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.dirname(__file__))

# Test configuration
TEST_CONFIG = {
    "test_data_dir": os.path.join(os.path.dirname(__file__), "test_data"),
    "mock_responses": True,
    "test_timeout": 30,
    "api_base_url": "http://localhost:8000",
    "websocket_url": "ws://localhost:8000/ws/chat/test_client"
}

class TestDataManager:
    """Manages test data and fixtures"""
    
    def __init__(self):
        self.test_data_dir = TEST_CONFIG["test_data_dir"]
        self.ensure_test_data_dir()
    
    def ensure_test_data_dir(self):
        """Ensure test data directory exists"""
        if not os.path.exists(self.test_data_dir):
            os.makedirs(self.test_data_dir)
    
    def get_sample_business_data(self) -> Dict[str, Any]:
        """Get sample business data for testing"""
        return {
            "business_name": "Test Company",
            "revenue": 50000,
            "monthly_growth": 0.15,
            "task_completion_rate": 0.85,
            "content_pieces": 25,
            "engagement_rate": 0.045,
            "active_customers": 120,
            "churn_rate": 0.03
        }
    
    def get_sample_tasks(self) -> List[Dict[str, Any]]:
        """Get sample tasks for testing"""
        return [
            {
                "id": "task_001",
                "name": "Create blog post about AI",
                "description": "Write comprehensive blog post about AI automation",
                "requirements": ["blog_writing", "content_optimization"],
                "priority": "high",
                "status": "pending"
            },
            {
                "id": "task_002", 
                "name": "Market research analysis",
                "description": "Conduct market research for Q4 strategy",
                "requirements": ["market_analysis", "competitor_analysis"],
                "priority": "medium",
                "status": "in_progress"
            },
            {
                "id": "task_003",
                "name": "Business intelligence report",
                "description": "Generate monthly BI report",
                "requirements": ["business_intelligence", "data_visualization"],
                "priority": "low",
                "status": "completed"
            }
        ]
    
    def get_sample_voice_commands(self) -> List[Dict[str, Any]]:
        """Get sample voice commands for testing"""
        return [
            {
                "text": "Show me business metrics",
                "intent": "get_business_metrics",
                "confidence": 0.95
            },
            {
                "text": "Create a new blog post about machine learning",
                "intent": "create_content",
                "confidence": 0.88,
                "entities": {"content_type": "blog_post", "topic": "machine learning"}
            },
            {
                "text": "What is our revenue this month",
                "intent": "get_business_metrics", 
                "confidence": 0.92
            }
        ]

@pytest.fixture
def test_data_manager():
    """Test data manager fixture"""
    return TestDataManager()

@pytest.fixture
def mock_business_data(test_data_manager):
    """Mock business data fixture"""
    return test_data_manager.get_sample_business_data()

@pytest.fixture
def mock_tasks(test_data_manager):
    """Mock tasks fixture"""
    return test_data_manager.get_sample_tasks()

@pytest.fixture
def mock_voice_commands(test_data_manager):
    """Mock voice commands fixture"""
    return test_data_manager.get_sample_voice_commands()

@pytest.fixture
def temp_directory():
    """Temporary directory fixture"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)

@pytest.fixture
async def async_mock_manager():
    """Async mock manager for testing async operations"""
    class AsyncMockManager:
        def __init__(self):
            self.mocks = {}
        
        def create_async_mock(self, name: str, return_value=None):
            mock = AsyncMock()
            if return_value:
                mock.return_value = return_value
            self.mocks[name] = mock
            return mock
        
        def reset_all(self):
            for mock in self.mocks.values():
                mock.reset_mock()
    
    return AsyncMockManager()

# Performance testing utilities
class PerformanceMonitor:
    """Monitor performance metrics during testing"""
    
    def __init__(self):
        self.metrics = []
    
    async def measure_async_operation(self, operation_name: str, operation_func):
        """Measure performance of async operation"""
        start_time = datetime.now()
        try:
            result = await operation_func()
            success = True
            error = None
        except Exception as e:
            result = None
            success = False
            error = str(e)
        
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        self.metrics.append({
            "operation": operation_name,
            "duration": duration,
            "success": success,
            "error": error,
            "timestamp": start_time.isoformat()
        })
        
        return result, duration, success
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get performance summary"""
        if not self.metrics:
            return {"message": "No metrics collected"}
        
        successful_ops = [m for m in self.metrics if m["success"]]
        failed_ops = [m for m in self.metrics if not m["success"]]
        
        avg_duration = sum(m["duration"] for m in successful_ops) / len(successful_ops) if successful_ops else 0
        
        return {
            "total_operations": len(self.metrics),
            "successful_operations": len(successful_ops),
            "failed_operations": len(failed_ops),
            "success_rate": len(successful_ops) / len(self.metrics),
            "average_duration": avg_duration,
            "max_duration": max(m["duration"] for m in self.metrics),
            "min_duration": min(m["duration"] for m in self.metrics)
        }

@pytest.fixture
def performance_monitor():
    """Performance monitor fixture"""
    return PerformanceMonitor()

# Test utilities
class TestUtils:
    """Utility functions for testing"""
    
    @staticmethod
    def assert_valid_response_structure(response: Dict[str, Any], required_fields: List[str]):
        """Assert response has required structure"""
        assert isinstance(response, dict), "Response must be a dictionary"
        
        for field in required_fields:
            assert field in response, f"Response missing required field: {field}"
    
    @staticmethod
    def assert_business_metrics_valid(metrics: Dict[str, Any]):
        """Assert business metrics have valid structure"""
        required_sections = ["revenue", "operations", "content", "predictions"]
        
        for section in required_sections:
            assert section in metrics, f"Metrics missing section: {section}"
        
        # Validate revenue section
        revenue = metrics["revenue"]
        assert "monthly_recurring" in revenue
        assert "growth_rate" in revenue
        assert isinstance(revenue["monthly_recurring"], (int, float))
        assert isinstance(revenue["growth_rate"], (int, float))
    
    @staticmethod
    def assert_task_structure_valid(task: Dict[str, Any]):
        """Assert task has valid structure"""
        required_fields = ["id", "name", "description", "requirements", "priority", "status"]
        
        for field in required_fields:
            assert field in task, f"Task missing required field: {field}"
        
        valid_priorities = ["low", "medium", "high", "critical"]
        valid_statuses = ["pending", "assigned", "in_progress", "completed", "failed", "cancelled"]
        
        assert task["priority"] in valid_priorities, f"Invalid priority: {task['priority']}"
        assert task["status"] in valid_statuses, f"Invalid status: {task['status']}"

@pytest.fixture
def test_utils():
    """Test utilities fixture"""
    return TestUtils()

# Async test utilities
async def run_with_timeout(coro, timeout=30):
    """Run coroutine with timeout"""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        pytest.fail(f"Operation timed out after {timeout} seconds")

# Mock API responses
MOCK_API_RESPONSES = {
    "chat_response": {
        "success": True,
        "response": "I understand your request and will help you with that.",
        "command_detected": True,
        "actions_taken": ["analyzed_request", "generated_response"],
        "confidence": 0.95
    },
    "business_metrics": {
        "success": True,
        "metrics": {
            "timestamp": "2025-10-14T10:00:00Z",
            "revenue": {
                "monthly_recurring": 8450,
                "growth_rate": 0.125,
                "churn_rate": 0.035
            },
            "operations": {
                "task_completion_rate": 0.78,
                "automation_level": 0.65,
                "efficiency_score": 0.82
            },
            "content": {
                "pieces_published": 23,
                "engagement_rate": 0.045,
                "seo_performance": 0.78
            },
            "predictions": {
                "next_month_revenue": 9200,
                "growth_opportunities": [
                    "AI automation expansion",
                    "Content strategy optimization"
                ]
            }
        }
    },
    "task_delegation": {
        "success": True,
        "task_id": "task_12345",
        "message": "Task delegated successfully. Tracking ID: task_12345"
    },
    "workflow_creation": {
        "success": True,
        "workflow_id": "workflow_67890",
        "steps_created": 3,
        "message": "Strategic workflow created with 3 steps"
    },
    "orchestration_status": {
        "success": True,
        "status": {
            "timestamp": "2025-10-14T10:00:00Z",
            "agents": {
                "content-001": {
                    "name": "Content Creator",
                    "type": "content_creation",
                    "status": "idle",
                    "current_task": None,
                    "capabilities": 3,
                    "performance": {"success_rate": 0.92}
                }
            },
            "tasks": {
                "total": 15,
                "pending": 3,
                "in_progress": 2,
                "completed": 10,
                "failed": 0
            },
            "metrics": {
                "total_tasks": 15,
                "completed_tasks": 10,
                "success_rate": 0.91,
                "agent_utilization": 0.45
            }
        }
    }
}

@pytest.fixture
def mock_api_responses():
    """Mock API responses fixture"""
    return MOCK_API_RESPONSES

# Test markers
pytest_marks = {
    "unit": pytest.mark.unit,
    "integration": pytest.mark.integration,
    "api": pytest.mark.api,
    "e2e": pytest.mark.e2e,
    "performance": pytest.mark.performance,
    "slow": pytest.mark.slow,
    "voice": pytest.mark.voice,
    "websocket": pytest.mark.websocket
}

# FastAPI Test Client
@pytest.fixture(scope="function", autouse=True)
def init_task_store():
    """Autouse fixture to re-initialize task store for each test if needed"""
    try:
        from services import task_store_service
        # Use the same file-based SQLite for tests
        db_url = f"sqlite:///{_test_db_path.replace(chr(92), '/')}"
        # Just ensure it's initialized, don't reset it
        if task_store_service._persistent_task_store is None:
            task_store_service.initialize_task_store(database_url=db_url)
    except Exception as e:
        print(f"Warning: Task store check failed: {e}")
    yield

import tempfile
import os as os_module

# Create a temp directory for test database
_test_db_dir = tempfile.mkdtemp(prefix="pytest_", suffix="_cofounder")
_test_db_path = os_module.path.join(_test_db_dir, "test_tasks.db")

@pytest.fixture
def app():
    """FastAPI application fixture with task store initialization"""
    try:
        # Import task_store_service first to initialize
        from services import task_store_service
        # Use file-based SQLite for tests (more reliable than :memory:)
        db_url = f"sqlite:///{_test_db_path.replace(chr(92), '/')}"
        
        # Make sure it's initialized before importing the app
        if task_store_service._persistent_task_store is None:
            task_store_service.initialize_task_store(database_url=db_url)
        
        # Now import the app (which will use the initialized task store)
        from cofounder_agent.main import app as fastapi_app
        return fastapi_app
    except ImportError as e:
        print(f"Import error: {e}")
        # Create minimal mock app for tests that don't need the full app
        from fastapi import FastAPI
        mock_app = FastAPI()
        
        @mock_app.get("/api/health")
        async def health():
            return {"status": "healthy"}
        
        return mock_app

@pytest.fixture
def client(app):
    """FastAPI TestClient fixture"""
    from fastapi.testclient import TestClient
    return TestClient(app)

@pytest.fixture
def async_client(app):
    """Async test client fixture using httpx.AsyncClient"""
    try:
        import httpx
        return httpx.AsyncClient(base_url="http://test")
    except ImportError:
        pytest.skip("httpx not installed")

# Event loop fixture for async tests
@pytest.fixture(scope="function")
def event_loop():
    """Create event loop for async tests"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

# Mock environment variables
@pytest.fixture
def mock_env_vars(monkeypatch):
    """Mock environment variables for testing"""
    test_env_vars = {
        "ENVIRONMENT": "test",
        "DEBUG": "True",
        "LOG_LEVEL": "DEBUG",
        "DATABASE_URL": "sqlite:///:memory:",
        "REDIS_URL": "redis://localhost:6379/1",
        "OPENAI_API_KEY": "sk-test-key",
        "OLLAMA_HOST": "http://localhost:11434"
    }
    
    for key, value in test_env_vars.items():
        monkeypatch.setenv(key, value)
    
    return test_env_vars

# Mock database
@pytest.fixture
def mock_database():
    """Mock database fixture"""
    class MockDatabase:
        def __init__(self):
            self.data = {}
        
        async def get(self, key: str):
            return self.data.get(key)
        
        async def set(self, key: str, value: Any):
            self.data[key] = value
        
        async def delete(self, key: str):
            if key in self.data:
                del self.data[key]
        
        async def exists(self, key: str):
            return key in self.data
        
        def clear(self):
            self.data.clear()
    
    return MockDatabase()

# Mock cache
@pytest.fixture
def mock_cache():
    """Mock cache fixture"""
    class MockCache:
        def __init__(self):
            self.cache = {}
        
        async def get(self, key: str):
            return self.cache.get(key)
        
        async def set(self, key: str, value: Any, ttl: int | None = None):
            self.cache[key] = value
        
        async def delete(self, key: str):
            if key in self.cache:
                del self.cache[key]
        
        def clear(self):
            self.cache.clear()
    
    return MockCache()

# Mock logger
@pytest.fixture
def mock_logger():
    """Mock logger fixture"""
    logger = Mock()
    logger.info = Mock()
    logger.warning = Mock()
    logger.error = Mock()
    logger.debug = Mock()
    return logger

# Export test configuration
__all__ = [
    "TEST_CONFIG",
    "TestDataManager",
    "PerformanceMonitor",
    "TestUtils",
    "run_with_timeout",
    "MOCK_API_RESPONSES",
    "pytest_marks",
    "app",
    "client",
    "async_client",
    "event_loop",
    "mock_env_vars",
    "mock_database",
    "mock_cache",
    "mock_logger",
    "test_data_manager",
    "mock_business_data",
    "mock_tasks",
    "mock_voice_commands",
    "temp_directory",
    "async_mock_manager",
    "performance_monitor",
    "test_utils",
    "mock_api_responses"
]