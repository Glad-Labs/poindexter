# FastAPI Testing Utilities & Helpers
# Ready-to-use functions for common testing scenarios

import pytest
import asyncio
import json
from typing import Dict, Any, List, Optional, Callable
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
import logging

logger = logging.getLogger(__name__)


# ===== TEST CLIENT SETUP =====


class TestClientFactory:
    """Factory for creating configured test clients"""

    @staticmethod
    def create_app_client(app, use_async=False):
        """
        Create a test client for FastAPI app

        Args:
            app: FastAPI application instance
            use_async: Whether to use async client

        Returns:
            TestClient or AsyncClient configured for testing
        """
        if use_async:
            from httpx import AsyncClient

            return AsyncClient(app=app, base_url="http://test")
        else:
            from fastapi.testclient import TestClient

            return TestClient(app)

    @staticmethod
    def create_authenticated_client(app, token: str, token_type: str = "Bearer"):
        """
        Create a test client with authentication headers

        Args:
            app: FastAPI application
            token: Authentication token
            token_type: Token type (default: Bearer)

        Returns:
            TestClient with Authorization header
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        client.headers["Authorization"] = f"{token_type} {token}"
        return client


# ===== MOCK GENERATORS =====


class MockFactory:
    """Factory for creating common mock objects"""

    @staticmethod
    def mock_async_function(return_value=None, side_effect=None):
        """Create async mock function"""
        mock = AsyncMock(return_value=return_value)
        if side_effect:
            mock.side_effect = side_effect
        return mock

    @staticmethod
    def mock_database():
        """Create mock database service"""
        mock = AsyncMock()
        mock.query = AsyncMock()
        mock.execute = AsyncMock()
        mock.commit = AsyncMock()
        mock.rollback = AsyncMock()
        mock.close = AsyncMock()
        return mock

    @staticmethod
    def mock_cache():
        """Create mock cache service"""
        mock = AsyncMock()
        mock.get = AsyncMock()
        mock.set = AsyncMock()
        mock.delete = AsyncMock()
        mock.clear = AsyncMock()
        return mock

    @staticmethod
    def mock_http_client(status_code=200, json_data=None):
        """Create mock HTTP client"""
        mock = AsyncMock()
        response = AsyncMock()
        response.status_code = status_code
        response.json = AsyncMock(return_value=json_data or {})
        response.text = AsyncMock(return_value=json.dumps(json_data or {}))
        mock.get = AsyncMock(return_value=response)
        mock.post = AsyncMock(return_value=response)
        mock.put = AsyncMock(return_value=response)
        mock.delete = AsyncMock(return_value=response)
        return mock

    @staticmethod
    def mock_external_service(**kwargs):
        """Create mock external service with custom attributes"""
        mock = MagicMock()
        for key, value in kwargs.items():
            setattr(mock, key, value)
        return mock


# ===== TEST DATA BUILDERS =====


class TestDataBuilder:
    """Builder for creating test data with sensible defaults"""

    @staticmethod
    def user(
        user_id: str = "test_user_1",
        email: str = "test@example.com",
        name: str = "Test User",
        **kwargs,
    ) -> Dict[str, Any]:
        """Build user test data"""
        return {
            "id": user_id,
            "email": email,
            "name": name,
            "created_at": datetime.utcnow().isoformat(),
            **kwargs,
        }

    @staticmethod
    def task(
        task_id: str = "task_1", title: str = "Test Task", status: str = "pending", **kwargs
    ) -> Dict[str, Any]:
        """Build task test data"""
        return {
            "id": task_id,
            "title": title,
            "status": status,
            "created_at": datetime.utcnow().isoformat(),
            **kwargs,
        }

    @staticmethod
    def content(
        content_id: str = "content_1",
        title: str = "Test Content",
        body: str = "Test content body",
        **kwargs,
    ) -> Dict[str, Any]:
        """Build content test data"""
        return {
            "id": content_id,
            "title": title,
            "body": body,
            "created_at": datetime.utcnow().isoformat(),
            **kwargs,
        }

    @staticmethod
    def jwt_token(user_id: str = "test_user", expires_in: int = 3600, **kwargs) -> Dict[str, Any]:
        """Build JWT token data"""
        now = datetime.utcnow()
        return {
            "sub": user_id,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
            **kwargs,
        }

    @staticmethod
    def api_response(
        status: str = "success", data: Any = None, message: str = "Operation successful", **kwargs
    ) -> Dict[str, Any]:
        """Build API response test data"""
        return {
            "status": status,
            "message": message,
            "data": data or {},
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs,
        }


# ===== ASSERTION HELPERS =====


class AssertionHelpers:
    """Common assertions for testing"""

    @staticmethod
    def assert_success_response(response, expected_status: int = 200):
        """Assert response is successful"""
        assert (
            response.status_code == expected_status
        ), f"Expected {expected_status}, got {response.status_code}: {response.text}"

    @staticmethod
    def assert_error_response(response, error_status: int = 400):
        """Assert response is an error"""
        assert (
            response.status_code == error_status
        ), f"Expected error {error_status}, got {response.status_code}"

    @staticmethod
    def assert_has_keys(data: Dict, keys: List[str]):
        """Assert dict has all required keys"""
        for key in keys:
            assert key in data, f"Missing required key: {key}"

    @staticmethod
    def assert_is_valid_json(text: str):
        """Assert string is valid JSON"""
        try:
            json.loads(text)
        except json.JSONDecodeError as e:
            raise AssertionError(f"Invalid JSON: {e}")

    @staticmethod
    def assert_matches_schema(data: Dict, schema: Dict):
        """Assert data matches JSON schema"""
        # Basic schema validation (use jsonschema for production)
        for key, value_type in schema.items():
            assert key in data, f"Missing key: {key}"
            assert isinstance(
                data[key], value_type
            ), f"Key {key} should be {value_type}, got {type(data[key])}"

    @staticmethod
    def assert_timestamps_recent(timestamp_str: str, within_seconds: int = 60):
        """Assert timestamp is recent"""
        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = datetime.utcnow()
        delta = abs((now - timestamp).total_seconds())
        assert (
            delta < within_seconds
        ), f"Timestamp {timestamp_str} is not within {within_seconds}s of now"


# ===== FIXTURE UTILITIES =====


class FixtureHelpers:
    """Utilities for working with pytest fixtures"""

    @staticmethod
    @pytest.fixture
    def mock_db():
        """Mock database fixture"""
        return MockFactory.mock_database()

    @staticmethod
    @pytest.fixture
    def mock_cache():
        """Mock cache fixture"""
        return MockFactory.mock_cache()

    @staticmethod
    @pytest.fixture
    def mock_http():
        """Mock HTTP client fixture"""
        return MockFactory.mock_http_client()

    @staticmethod
    @pytest.fixture
    def test_user_data():
        """Test user data fixture"""
        return TestDataBuilder.user()

    @staticmethod
    @pytest.fixture
    def test_task_data():
        """Test task data fixture"""
        return TestDataBuilder.task()

    @staticmethod
    @pytest.fixture
    def test_content_data():
        """Test content data fixture"""
        return TestDataBuilder.content()


# ===== ASYNC HELPERS =====


class AsyncHelpers:
    """Utilities for async testing"""

    @staticmethod
    async def wait_for(condition: Callable, timeout: int = 10, check_interval: float = 0.1) -> bool:
        """
        Wait for condition to be true

        Args:
            condition: Callable that returns bool
            timeout: Timeout in seconds
            check_interval: Check interval in seconds

        Returns:
            True if condition met, False if timeout
        """
        start = datetime.utcnow()
        while (datetime.utcnow() - start).total_seconds() < timeout:
            if condition():
                return True
            await asyncio.sleep(check_interval)
        return False

    @staticmethod
    @asynccontextmanager
    async def async_timer():
        """Context manager for timing async operations"""
        start = datetime.utcnow()
        try:
            yield
        finally:
            elapsed = (datetime.utcnow() - start).total_seconds()
            logger.info(f"Operation completed in {elapsed:.2f}s")

    @staticmethod
    async def run_concurrent(*coroutines):
        """Run multiple coroutines concurrently"""
        return await asyncio.gather(*coroutines)


# ===== PARAMETRIZATION HELPERS =====


class ParametrizeHelpers:
    """Utilities for pytest parametrization"""

    @staticmethod
    def http_methods():
        """Parametrize HTTP methods"""
        return ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]

    @staticmethod
    def status_codes_success():
        """Parametrize success status codes"""
        return [200, 201, 202, 204, 206]

    @staticmethod
    def status_codes_client_error():
        """Parametrize client error status codes"""
        return [400, 401, 403, 404, 409, 422]

    @staticmethod
    def status_codes_server_error():
        """Parametrize server error status codes"""
        return [500, 501, 502, 503, 504]

    @staticmethod
    def invalid_inputs():
        """Parametrize invalid input values"""
        return [None, "", {}, [], 0, False]

    @staticmethod
    def valid_emails():
        """Parametrize valid email formats"""
        return ["user@example.com", "john.doe@example.co.uk", "test+tag@example.com"]

    @staticmethod
    def invalid_emails():
        """Parametrize invalid email formats"""
        return ["invalid", "@example.com", "user@", "user @example.com"]


# ===== DATABASE HELPERS =====


class DatabaseHelpers:
    """Utilities for database testing"""

    @staticmethod
    async def clear_tables(session, tables: List[str]):
        """Clear specific tables"""
        for table in tables:
            await session.execute(f"DELETE FROM {table}")
        await session.commit()

    @staticmethod
    async def insert_test_data(session, table: str, data: List[Dict]):
        """Insert test data into table"""
        for row in data:
            # This is a simplified example - adjust based on your ORM
            await session.execute(f"INSERT INTO {table} VALUES (...)", row)
        await session.commit()

    @staticmethod
    async def count_records(session, table: str) -> int:
        """Count records in table"""
        result = await session.execute(f"SELECT COUNT(*) FROM {table}")
        return result.scalar()


# ===== PERFORMANCE HELPERS =====


class PerformanceHelpers:
    """Utilities for performance testing"""

    @staticmethod
    def assert_response_time(response_time: float, max_seconds: float):
        """Assert response completed within time limit"""
        assert (
            response_time < max_seconds
        ), f"Response took {response_time:.2f}s, max allowed {max_seconds}s"

    @staticmethod
    async def measure_operation_time(coroutine):
        """Measure time taken by async operation"""
        start = datetime.utcnow()
        result = await coroutine
        elapsed = (datetime.utcnow() - start).total_seconds()
        return result, elapsed

    @staticmethod
    def benchmark_sync(func, *args, **kwargs):
        """Benchmark synchronous function"""
        import time

        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        return result, elapsed


# ===== LOGGING HELPERS =====


class LoggingHelpers:
    """Utilities for testing logging"""

    @staticmethod
    def capture_logs():
        """Capture log output"""
        import logging

        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        return handler

    @staticmethod
    def assert_log_contains(caplog, text: str, level: str = "INFO"):
        """Assert log contains specific text"""
        for record in caplog.records:
            if record.levelname == level and text in record.message:
                return True
        raise AssertionError(f"Log doesn't contain '{text}' at level {level}")


# ===== ERROR SIMULATION =====


class ErrorSimulator:
    """Utilities for simulating errors in tests"""

    @staticmethod
    def simulate_database_error():
        """Simulate database error"""

        async def raise_db_error(*args, **kwargs):
            raise Exception("Database connection failed")

        return raise_db_error

    @staticmethod
    def simulate_timeout():
        """Simulate timeout error"""

        async def raise_timeout(*args, **kwargs):
            await asyncio.sleep(10)
            raise TimeoutError("Operation timed out")

        return raise_timeout

    @staticmethod
    def simulate_http_error(status_code: int):
        """Simulate HTTP error response"""

        async def raise_http_error(*args, **kwargs):
            from fastapi import HTTPException

            raise HTTPException(status_code=status_code)

        return raise_http_error


# ===== SNAPSHOT TESTING =====


class SnapshotHelpers:
    """Utilities for snapshot testing"""

    @staticmethod
    def snapshot_path(test_name: str) -> str:
        """Generate snapshot file path"""
        import os

        snapshot_dir = os.path.join(os.path.dirname(__file__), "__snapshots__")
        os.makedirs(snapshot_dir, exist_ok=True)
        return os.path.join(snapshot_dir, f"{test_name}.json")

    @staticmethod
    def save_snapshot(test_name: str, data: Dict):
        """Save test snapshot"""
        import os
        import json

        path = SnapshotHelpers.snapshot_path(test_name)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    @staticmethod
    def load_snapshot(test_name: str) -> Dict:
        """Load test snapshot"""
        import json

        path = SnapshotHelpers.snapshot_path(test_name)
        with open(path, "r") as f:
            return json.load(f)

    @staticmethod
    def assert_matches_snapshot(test_name: str, data: Dict):
        """Assert data matches snapshot"""
        expected = SnapshotHelpers.load_snapshot(test_name)
        assert data == expected, "Data doesn't match snapshot"


# ===== USAGE EXAMPLES =====
"""
# Import in test files
from test_utilities import (
    TestClientFactory,
    MockFactory,
    TestDataBuilder,
    AssertionHelpers,
    AsyncHelpers,
    ParametrizeHelpers,
)

# Example usage in tests

def test_create_user(client):
    # Build test data
    user_data = TestDataBuilder.user(email="newuser@example.com")
    
    # Make request
    response = client.post("/users", json=user_data)
    
    # Assert response
    AssertionHelpers.assert_success_response(response, 201)
    AssertionHelpers.assert_has_keys(response.json(), ["id", "email"])

@pytest.mark.asyncio
async def test_database_operation():
    # Measure performance
    db = MockFactory.mock_database()
    result, elapsed = await AsyncHelpers.measure_operation_time(
        db.query("SELECT * FROM users")
    )
    
    # Assert performance
    PerformanceHelpers.assert_response_time(elapsed, 0.5)

@pytest.mark.parametrize("status_code", ParametrizeHelpers.status_codes_success())
def test_success_codes(client, status_code):
    response = client.get(f"/mock/{status_code}")
    assert response.status_code == status_code
"""
