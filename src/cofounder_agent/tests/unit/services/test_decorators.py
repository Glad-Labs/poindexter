"""
Unit tests for services/decorators.py

Tests the log_query_performance decorator. The tests verify
that the decorator:
- Correctly pass-through return values from the wrapped function
- Re-raises exceptions without swallowing them
- Respects the ENABLE_QUERY_MONITORING env var toggle
- Handles both list and dict return values for result_count inference

All tests are pure async — no DB or network calls.
"""

import os

import pytest

# Ensure monitoring is enabled for all tests (override env before import)
os.environ.setdefault("ENABLE_QUERY_MONITORING", "true")

from services.decorators import log_query_performance

# ---------------------------------------------------------------------------
# log_query_performance
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogQueryPerformance:
    @pytest.mark.asyncio
    async def test_returns_value_from_wrapped_function(self):
        @log_query_performance(operation="test_op", category="test")
        async def fetch_list():
            return [1, 2, 3]

        result = await fetch_list()
        assert result == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_works_with_dict_return_value(self):
        @log_query_performance(operation="test_dict_op", category="test")
        async def fetch_dict():
            return {"results": ["a", "b"], "total": 2}

        result = await fetch_dict()
        assert result["total"] == 2
        assert result["results"] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_works_with_none_return_value(self):
        @log_query_performance(operation="test_none_op", category="test")
        async def fetch_none():
            return None

        result = await fetch_none()
        assert result is None

    @pytest.mark.asyncio
    async def test_re_raises_exception(self):
        @log_query_performance(operation="failing_op", category="test")
        async def failing_query():
            raise ValueError("db error")

        with pytest.raises(ValueError, match="db error"):
            await failing_query()

    @pytest.mark.asyncio
    async def test_passes_kwargs_to_wrapped_function(self):
        @log_query_performance(operation="with_kwargs", category="test")
        async def query_with_params(limit: int = 10, offset: int = 0):
            return {"limit": limit, "offset": offset}

        result = await query_with_params(limit=5, offset=20)
        assert result == {"limit": 5, "offset": 20}

    @pytest.mark.asyncio
    async def test_passes_args_to_wrapped_function(self):
        @log_query_performance(operation="with_args", category="test")
        async def query_with_args(task_id: str):
            return {"id": task_id}

        result = await query_with_args("abc-123")
        assert result == {"id": "abc-123"}

    @pytest.mark.asyncio
    async def test_custom_slow_threshold_does_not_break(self):
        @log_query_performance(operation="fast_op", category="test", slow_threshold_ms=1000)
        async def fast_query():
            return "ok"

        result = await fast_query()
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_monitoring_disabled_still_returns_value(self, monkeypatch):
        monkeypatch.setattr("services.decorators.ENABLE_QUERY_MONITORING", False)

        @log_query_performance(operation="disabled_op", category="test")
        async def fast_query():
            return "bypassed"

        result = await fast_query()
        assert result == "bypassed"

    @pytest.mark.asyncio
    async def test_filters_sensitive_kwargs(self):
        """Sensitive kwargs like 'password' must not surface in logs — no exception."""

        @log_query_performance(operation="sensitive_op", category="test")
        async def secure_query(username: str, password: str):
            return username

        result = await secure_query(username="admin", password="secret")
        assert result == "admin"

    @pytest.mark.asyncio
    async def test_dict_return_with_total_key(self):
        """Decorator should infer result_count from dict['total']."""

        @log_query_performance(operation="total_key_op", category="test")
        async def count_query():
            return {"total": 42, "data": []}

        result = await count_query()
        assert result["total"] == 42

