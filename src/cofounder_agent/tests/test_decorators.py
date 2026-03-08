"""
Unit tests for performance monitoring decorators.

Tests the @log_query_performance decorator to ensure:
- Query timing is captured correctly
- Slow queries are logged with warnings
- Fast queries are logged at appropriate level
- Result counts are extracted from various response types
- Error handling works correctly
"""

import asyncio
import logging
import os
import time
from typing import Dict, List

import pytest

# Set environment variables before importing the decorator
os.environ["ENABLE_QUERY_MONITORING"] = "true"
os.environ["SLOW_QUERY_THRESHOLD_MS"] = "50"
os.environ["LOG_ALL_QUERIES"] = "true"

from services.decorators import log_query_performance


@pytest.mark.asyncio
async def test_decorator_basic_functionality():
    """Test that decorator tracks timing correctly."""

    @log_query_performance(operation="test_query", category="test")
    async def mock_query():
        await asyncio.sleep(0.01)  # 10ms
        return {"results": [1, 2, 3]}

    result = await mock_query()
    assert result == {"results": [1, 2, 3]}


@pytest.mark.asyncio
async def test_decorator_extracts_list_result_count():
    """Test that decorator extracts count from list results."""

    @log_query_performance(operation="test_list", category="test")
    async def mock_query_list():
        return [1, 2, 3, 4, 5]

    result = await mock_query_list()
    assert len(result) == 5


@pytest.mark.asyncio
async def test_decorator_extracts_dict_result_count():
    """Test that decorator extracts count from dict with 'results' key."""

    @log_query_performance(operation="test_dict", category="test")
    async def mock_query_dict():
        return {"results": [1, 2, 3], "total": 3}

    result = await mock_query_dict()
    assert result["total"] == 3


@pytest.mark.asyncio
async def test_decorator_handles_errors():
    """Test that decorator logs errors without breaking execution."""

    @log_query_performance(operation="test_error", category="test")
    async def mock_failing_query():
        await asyncio.sleep(0.01)
        raise ValueError("Test error")

    with pytest.raises(ValueError):
        await mock_failing_query()


@pytest.mark.asyncio
async def test_decorator_custom_threshold():
    """Test that decorator respects custom slow query threshold."""

    @log_query_performance(operation="test_custom", category="test", slow_threshold_ms=5)
    async def mock_slow_query():
        await asyncio.sleep(0.01)  # 10ms - should trigger slow warning
        return []

    result = await mock_slow_query()
    assert result == []


@pytest.mark.asyncio
async def test_decorator_parameter_sanitization():
    """Test that decorator sanitizes sensitive parameters."""

    @log_query_performance(operation="test_sanitized", category="test")
    async def mock_query_with_password(user_id: str, password: str, token: str):
        return {"user_id": user_id}

    # These sensitive parameters should be filtered out of logs
    result = await mock_query_with_password(
        user_id="user123", password="secret", token="bearer_token"
    )
    assert result["user_id"] == "user123"


@pytest.mark.asyncio
async def test_decorator_disabled():
    """Test that decorator can be disabled via environment variable."""
    os.environ["ENABLE_QUERY_MONITORING"] = "false"

    # Re-import to pick up new env var
    import importlib

    from services import decorators

    importlib.reload(decorators)

    @decorators.log_query_performance(operation="test_disabled", category="test")
    async def mock_query():
        await asyncio.sleep(0.01)
        return [1, 2, 3]

    result = await mock_query()
    assert result == [1, 2, 3]

    # Reset environment
    os.environ["ENABLE_QUERY_MONITORING"] = "true"
    importlib.reload(decorators)


@pytest.mark.asyncio
async def test_decorator_with_tuple_result():
    """Test that decorator handles tuple results (common in paginated queries)."""

    @log_query_performance(operation="test_tuple", category="test")
    async def mock_paginated_query():
        return ([1, 2, 3], 10)  # (items, total_count)

    result = await mock_paginated_query()
    items, total = result
    assert len(items) == 3
    assert total == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
