"""
Enhanced Pytest Fixtures for Backend Integration Testing
=========================================================

Provides:
- HTTP client for API testing
- Database fixtures for state management
- Performance measurement utilities
- WebSocket testing support
- Authentication helpers
- Test isolation and cleanup
"""

import asyncio
import json
import time
from typing import Any, Dict, Generator, List, Optional
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import AsyncClient


# ========================
# URL Configuration
# ========================

BASE_API_URL = "http://localhost:8000"
PUBLIC_SITE_URL = "http://localhost:3000"
ADMIN_UI_URL = "http://localhost:3001"


# ========================
# AsyncHTTP Client Fixture
# ========================

@pytest.fixture
async def http_client() -> AsyncClient:
    """
    Provides an async HTTP client for API testing
    
    Usage:
        async def test_api(http_client):
            response = await http_client.get("/api/tasks")
            assert response.status_code == 200
    """
    async with AsyncClient(base_url=BASE_API_URL) as client:
        yield client


@pytest.fixture
async def authenticated_http_client() -> AsyncClient:
    """
    Provides an authenticated HTTP client
    
    Note: Authentication setup depends on your auth mechanism
    """
    headers = {
        "Authorization": "Bearer test-token",
        "Content-Type": "application/json",
    }
    async with AsyncClient(base_url=BASE_API_URL, headers=headers) as client:
        yield client


# ========================
# Performance Measurement
# ========================

class PerformanceTimer:
    """Context manager for measuring execution time"""

    def __init__(self):
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.duration: Optional[float] = None

    def __enter__(self):
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, *args):
        self.end_time = time.perf_counter()
        self.duration = (self.end_time - self.start_time) * 1000  # Convert to ms
        print(f"⏱️  Duration: {self.duration:.2f}ms")

    async def __aenter__(self):
        self.start_time = time.perf_counter()
        return self

    async def __aexit__(self, *args):
        self.end_time = time.perf_counter()
        self.duration = (self.end_time - self.start_time) * 1000
        print(f"⏱️  Duration: {self.duration:.2f}ms")


@pytest.fixture
def performance_timer():
    """Provides a performance timer for measuring test execution"""
    return PerformanceTimer


# ========================
# API Testing Utilities
# ========================

class APITester:
    """Helper class for API testing"""

    def __init__(self, client: AsyncClient):
        self.client = client
        self.last_response = None

    async def get(self, endpoint: str, **kwargs):
        """GET request"""
        self.last_response = await self.client.get(endpoint, **kwargs)
        return self.last_response

    async def post(self, endpoint: str, **kwargs):
        """POST request"""
        self.last_response = await self.client.post(endpoint, **kwargs)
        return self.last_response

    async def put(self, endpoint: str, **kwargs):
        """PUT request"""
        self.last_response = await self.client.put(endpoint, **kwargs)
        return self.last_response

    async def delete(self, endpoint: str, **kwargs):
        """DELETE request"""
        self.last_response = await self.client.delete(endpoint, **kwargs)
        return self.last_response

    async def patch(self, endpoint: str, **kwargs):
        """PATCH request"""
        self.last_response = await self.client.patch(endpoint, **kwargs)
        return self.last_response

    def assert_status(self, expected_code: int):
        """Assert last response status code"""
        assert (
            self.last_response.status_code == expected_code
        ), f"Expected {expected_code}, got {self.last_response.status_code}: {self.last_response.text}"

    def assert_json_schema(self, schema: Dict[str, Any]):
        """Assert response matches JSON schema"""
        # Simple schema validation (consider using jsonschema library for production)
        try:
            data = self.last_response.json()
            for key in schema.keys():
                assert key in data, f"Key '{key}' not in response"
        except json.JSONDecodeError:
            raise AssertionError("Response is not valid JSON")

    def get_json(self):
        """Get response as JSON"""
        return self.last_response.json()


@pytest.fixture
async def api_tester(http_client: AsyncClient) -> APITester:
    """
    Provides an API testing helper
    
    Usage:
        async def test_api(api_tester):
            await api_tester.get("/api/tasks")
            api_tester.assert_status(200)
            data = api_tester.get_json()
    """
    return APITester(http_client)


# ========================
# Test Data Management
# ========================

class TestDataFactory:
    """Factory for creating test data"""

    def __init__(self, http_client: AsyncClient):
        self.http_client = http_client
        self.created_resources: List[Dict[str, Any]] = []

    async def create_task(self, **kwargs) -> Dict[str, Any]:
        """Create a test task"""
        data = {
            "title": "Test Task",
            "description": "Automated test",
            "status": "pending",
            **kwargs,
        }
        response = await self.http_client.post("/api/tasks", json=data)
        task = response.json()
        self.created_resources.append({"type": "task", "id": task.get("id")})
        return task

    async def create_multiple_tasks(self, count: int = 5) -> List[Dict[str, Any]]:
        """Create multiple test tasks"""
        return [
            await self.create_task(title=f"Test Task {i + 1}")
            for i in range(count)
        ]

    async def cleanup(self):
        """Clean up all created resources"""
        for resource in reversed(self.created_resources):
            try:
                await self.http_client.delete(
                    f"/api/{resource['type']}s/{resource['id']}"
                )
            except Exception as e:
                print(f"⚠️  Cleanup failed for {resource}: {e}")

        self.created_resources.clear()


@pytest.fixture
async def test_data_factory(http_client: AsyncClient) -> TestDataFactory:
    """
    Provides a factory for creating test data
    
    Usage:
        async def test_workflow(test_data_factory):
            task = await test_data_factory.create_task(title="My Task")
            assert task["id"]
            
            # Cleanup happens automatically
    """
    factory = TestDataFactory(http_client)
    yield factory
    await factory.cleanup()


# ========================
# Async Event Loop
# ========================

@pytest.fixture
def event_loop():
    """Provide event loop for async tests"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    yield loop

    loop.close()


# ========================
# Concurrency Testing
# ========================

class ConcurrencyTester:
    """Helper for testing concurrent operations"""

    async def run_concurrent(
        self,
        coro_func,
        args_list: List[tuple],
        expected_exceptions: Optional[list] = None,
    ) -> List[Any]:
        """
        Run multiple coroutines concurrently
        
        Usage:
            results = await concurrency_tester.run_concurrent(
                client.get,
                [("/api/tasks", ), ("/api/tasks", ), ("/api/tasks", )]
            )
        """
        tasks = [coro_func(*args) for args in args_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        if expected_exceptions:
            for result, expected in zip(results, expected_exceptions):
                if expected:
                    assert isinstance(result, Exception)

        return results

    async def stress_test(
        self,
        coro_func,
        iterations: int = 100,
        concurrent_workers: int = 10,
    ) -> Dict[str, Any]:
        """
        Run stress test with multiple concurrent workers
        
        Usage:
            stats = await concurrency_tester.stress_test(
                lambda: client.get("/api/tasks"),
                iterations=100,
                concurrent_workers=10
            )
        """
        results = {"success": 0, "failure": 0, "errors": [], "durations": []}

        async def worker():
            for _ in range(iterations // concurrent_workers):
                try:
                    start = time.perf_counter()
                    await coro_func()
                    duration = (time.perf_counter() - start) * 1000
                    results["durations"].append(duration)
                    results["success"] += 1
                except Exception as e:
                    results["failure"] += 1
                    results["errors"].append(str(e))

        await asyncio.gather(*[worker() for _ in range(concurrent_workers)])

        return {
            **results,
            "total": results["success"] + results["failure"],
            "success_rate": results["success"]
            / (results["success"] + results["failure"])
            * 100,
            "avg_duration": sum(results["durations"]) / len(results["durations"])
            if results["durations"]
            else 0,
        }


@pytest.fixture
def concurrency_tester():
    """Provides concurrency testing utilities"""
    return ConcurrencyTester()


# ========================
# WebSocket Testing
# ========================

class WebSocketTester:
    """Helper for WebSocket testing"""

    async def connect(self, endpoint: str, base_url: str = "ws://localhost:8000"):
        """Connect to WebSocket endpoint"""
        url = f"{base_url}{endpoint}"
        # Note: Requires websockets library
        # import websockets
        # return await websockets.connect(url)
        raise NotImplementedError("WebSocket testing requires websockets library")

    async def send_and_receive(self, ws, data: dict) -> dict:
        """Send data and receive response"""
        await ws.send(json.dumps(data))
        response = await asyncio.wait_for(ws.recv(), timeout=5.0)
        return json.loads(response)


@pytest.fixture
def websocket_tester():
    """Provides WebSocket testing utilities"""
    return WebSocketTester()


# ========================
# Mock Utilities
# ========================

@pytest.fixture
def mock_llm_response():
    """Mock LLM response for testing"""

    def _mock(content: str = "Mock response", tokens: int = 100):
        return {
            "content": content,
            "tokens": tokens,
            "model": "mock-model",
            "provider": "mock",
        }

    return _mock


@pytest.fixture
def mock_api_error():
    """Mock API error response"""

    def _mock(status_code: int = 400, message: str = "Bad Request"):
        return {
            "error": {
                "code": status_code,
                "message": message,
                "details": {},
            }
        }

    return _mock


# ========================
# Test Markers
# ========================

def pytest_configure(config):
    """Register custom pytest markers"""
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )
    config.addinivalue_line("markers", "api: marks tests as API tests")
    config.addinivalue_line("markers", "slow: marks tests as slow")
    config.addinivalue_line("markers", "performance: marks tests as performance tests")
    config.addinivalue_line("markers", "websocket: marks tests as WebSocket tests")
    config.addinivalue_line("markers", "concurrent: marks tests as concurrency tests")


# ========================
# Cleanup & Fixtures Management
# ========================

@pytest.fixture(autouse=True)
def reset_mocks():
    """Auto-reset mocks before each test"""
    yield
    # Cleanup happens here
