"""
Shared test utilities and fixtures for all tests.
"""
import os
import sys
from pathlib import Path
from unittest.mock import Mock, AsyncMock
from typing import Dict, Any, List
from datetime import datetime
import tempfile
import shutil
import asyncio
import json
from dataclasses import dataclass


@dataclass
class TestConfig:
    """Test configuration."""
    api_base_url: str = "http://localhost:8000"
    websocket_url: str = "ws://localhost:8000"
    timeout: int = 30
    db_url: str = "postgresql://localhost/test_db"


class MockAPIResponse:
    """Mock API response object."""
    def __init__(self, status: int = 200, data: Dict[str, Any] = None):
        self.status = status
        self.data = data or {}

    async def json(self):
        return self.data

    async def text(self):
        return json.dumps(self.data)


class TestUtils:
    """Utility functions for testing."""

    @staticmethod
    def assert_valid_response_structure(response: Dict[str, Any], required_keys: List[str]):
        """Assert that response has required keys."""
        for key in required_keys:
            assert key in response, f"Response missing required key: {key}"

    @staticmethod
    def create_mock_task(task_id: str = "test_123", **kwargs) -> Dict[str, Any]:
        """Create a mock task object."""
        return {
            "id": task_id,
            "status": "pending",
            "priority": "medium",
            "created_at": datetime.now().isoformat(),
            **kwargs
        }

    @staticmethod
    def create_mock_user(user_id: str = "user_123", **kwargs) -> Dict[str, Any]:
        """Create a mock user object."""
        return {
            "id": user_id,
            "username": f"testuser_{user_id}",
            "email": f"test_{user_id}@example.com",
            **kwargs
        }


class PerformanceMonitor:
    """Monitor performance of async operations."""

    def __init__(self):
        self.operations = {}

    async def measure_async_operation(self, name: str, operation):
        """Measure execution time of an async operation."""
        import time
        start = time.time()
        try:
            result = await operation()
            duration = time.time() - start
            self.operations[name] = {
                "duration": duration,
                "success": True,
                "result": result
            }
            return result, duration, True
        except Exception as e:
            duration = time.time() - start
            self.operations[name] = {
                "duration": duration,
                "success": False,
                "error": str(e)
            }
            return None, duration, False

    def get_performance_summary(self):
        """Get summary of all measured operations."""
        total_duration = sum(op.get("duration", 0) for op in self.operations.values())
        successful = sum(1 for op in self.operations.values() if op.get("success"))
        
        return {
            "total_operations": len(self.operations),
            "successful": successful,
            "failed": len(self.operations) - successful,
            "total_duration": total_duration,
            "average_duration": total_duration / len(self.operations) if self.operations else 0,
        }


# Global test utilities instances
test_utils = TestUtils()
performance_monitor = PerformanceMonitor()
test_config = TestConfig()
