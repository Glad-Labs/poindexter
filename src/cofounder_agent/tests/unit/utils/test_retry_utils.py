"""
Unit tests for utils.retry_utils module.

All tests are pure — zero DB or network calls.
Covers RetryConfig, RetryableException, async_retry, should_retry_exception,
and RetryStats.
"""

import asyncio
from unittest.mock import AsyncMock, call

import pytest

from utils.retry_utils import (
    API_RETRY_CONFIG,
    DB_RETRY_CONFIG,
    RetryConfig,
    RetryStats,
    RetryableException,
    async_retry,
    should_retry_exception,
)


# ---------------------------------------------------------------------------
# RetryConfig
# ---------------------------------------------------------------------------


class TestRetryConfig:
    def test_default_values(self):
        config = RetryConfig()
        assert config.max_attempts == 3
        assert config.initial_delay == 0.5
        assert config.max_delay == 10.0
        assert config.exponential_base == 2.0
        assert config.jitter is True

    def test_custom_values(self):
        config = RetryConfig(max_attempts=5, initial_delay=0.1, max_delay=5.0, jitter=False)
        assert config.max_attempts == 5
        assert config.initial_delay == 0.1
        assert config.max_delay == 5.0
        assert config.jitter is False

    def test_get_delay_attempt_zero_is_zero(self):
        config = RetryConfig(jitter=False)
        assert config.get_delay(0) == 0

    def test_get_delay_attempt_one(self):
        config = RetryConfig(initial_delay=1.0, exponential_base=2.0, jitter=False)
        # attempt=1: delay = 1.0 * (2^0) = 1.0
        assert config.get_delay(1) == 1.0

    def test_get_delay_attempt_two(self):
        config = RetryConfig(initial_delay=1.0, exponential_base=2.0, jitter=False)
        # attempt=2: delay = 1.0 * (2^1) = 2.0
        assert config.get_delay(2) == 2.0

    def test_get_delay_capped_at_max_delay(self):
        config = RetryConfig(initial_delay=10.0, exponential_base=10.0, max_delay=5.0, jitter=False)
        delay = config.get_delay(3)
        assert delay == 5.0

    def test_get_delay_with_jitter_adds_some_amount(self):
        config = RetryConfig(initial_delay=1.0, exponential_base=2.0, jitter=True)
        delay = config.get_delay(1)
        # With jitter, delay >= 1.0 and <= 1.25
        assert 1.0 <= delay <= 1.25 + 0.001  # float tolerance

    def test_db_retry_config_exists(self):
        assert isinstance(DB_RETRY_CONFIG, RetryConfig)
        assert DB_RETRY_CONFIG.max_attempts == 3

    def test_api_retry_config_exists(self):
        assert isinstance(API_RETRY_CONFIG, RetryConfig)
        assert API_RETRY_CONFIG.max_attempts == 3


# ---------------------------------------------------------------------------
# RetryableException
# ---------------------------------------------------------------------------


class TestRetryableException:
    def test_stores_original_exception(self):
        original = ValueError("original")
        exc = RetryableException(original, attempt=1, config=RetryConfig())
        assert exc.original_exception is original

    def test_stores_attempt_number(self):
        exc = RetryableException(RuntimeError("err"), attempt=2, config=RetryConfig())
        assert exc.attempt == 2

    def test_message_contains_attempt_info(self):
        config = RetryConfig(max_attempts=3)
        exc = RetryableException(ValueError("boom"), attempt=1, config=config)
        assert "1/3" in str(exc)

    def test_final_attempt_message_has_no_retry_info(self):
        config = RetryConfig(max_attempts=3)
        exc = RetryableException(ValueError("boom"), attempt=3, config=config)
        # No "Retrying in..." for final attempt
        assert "Retrying" not in str(exc)


# ---------------------------------------------------------------------------
# async_retry
# ---------------------------------------------------------------------------


class TestAsyncRetry:
    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        mock_op = AsyncMock(return_value="result")
        config = RetryConfig(max_attempts=3, jitter=False)
        result = await async_retry(mock_op, config=config)
        assert result == "result"
        assert mock_op.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_failure_then_succeeds(self):
        # Fails twice, then succeeds
        mock_op = AsyncMock(side_effect=[ValueError("fail1"), ValueError("fail2"), "ok"])
        config = RetryConfig(max_attempts=3, initial_delay=0, jitter=False)
        result = await async_retry(mock_op, config=config)
        assert result == "ok"
        assert mock_op.call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_attempts(self):
        mock_op = AsyncMock(side_effect=ValueError("always fails"))
        config = RetryConfig(max_attempts=2, initial_delay=0, jitter=False)
        with pytest.raises(ValueError, match="always fails"):
            await async_retry(mock_op, config=config)
        assert mock_op.call_count == 2

    @pytest.mark.asyncio
    async def test_non_retryable_exception_propagates_immediately(self):
        call_count = 0

        async def op():
            nonlocal call_count
            call_count += 1
            raise KeyboardInterrupt()

        config = RetryConfig(max_attempts=3, initial_delay=0, jitter=False)
        with pytest.raises(KeyboardInterrupt):
            await async_retry(
                op,
                config=config,
                retryable_exceptions=(ValueError,),  # KeyboardInterrupt is NOT retryable
            )
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_calls_on_retry_callback(self):
        retry_calls = []

        async def on_retry(exc, attempt, delay):
            retry_calls.append((attempt, delay))

        mock_op = AsyncMock(side_effect=[ValueError("fail"), "ok"])
        config = RetryConfig(max_attempts=2, initial_delay=0, jitter=False)
        await async_retry(mock_op, config=config, on_retry=on_retry)
        assert len(retry_calls) == 1
        assert retry_calls[0][0] == 1  # attempt 1

    @pytest.mark.asyncio
    async def test_passes_args_to_operation(self):
        async def op(a, b):
            return a + b

        config = RetryConfig(max_attempts=1, jitter=False)
        result = await async_retry(op, 3, 4, config=config)
        assert result == 7

    @pytest.mark.asyncio
    async def test_passes_kwargs_to_operation(self):
        async def op(x=0, y=0):
            return x * y

        config = RetryConfig(max_attempts=1, jitter=False)
        result = await async_retry(op, config=config, x=3, y=4)
        assert result == 12

    @pytest.mark.asyncio
    async def test_respects_retryable_exceptions_filter(self):
        call_count = 0

        async def op():
            nonlocal call_count
            call_count += 1
            raise TypeError("type error")

        config = RetryConfig(max_attempts=3, initial_delay=0, jitter=False)
        # Only retry on ValueError — TypeError should propagate on first attempt
        with pytest.raises(TypeError):
            await async_retry(op, config=config, retryable_exceptions=(ValueError,))
        assert call_count == 1


# ---------------------------------------------------------------------------
# should_retry_exception
# ---------------------------------------------------------------------------


class TestShouldRetryException:
    def test_timeout_in_message_is_retryable(self):
        assert should_retry_exception(Exception("connection timeout")) is True

    def test_temporarily_unavailable_is_retryable(self):
        assert should_retry_exception(Exception("temporarily unavailable")) is True

    def test_connection_reset_is_retryable(self):
        assert should_retry_exception(Exception("connection reset by peer")) is True

    def test_pool_exhausted_is_retryable(self):
        assert should_retry_exception(Exception("pool exhausted")) is True

    def test_too_many_connections_is_retryable(self):
        assert should_retry_exception(Exception("too many connections")) is True

    def test_generic_value_error_is_not_retryable(self):
        assert should_retry_exception(ValueError("bad value")) is False

    def test_type_error_is_not_retryable(self):
        assert should_retry_exception(TypeError("wrong type")) is False

    def test_timeout_error_type_is_retryable(self):
        assert should_retry_exception(TimeoutError()) is True

    def test_connection_error_type_is_retryable(self):
        assert should_retry_exception(ConnectionError()) is True


# ---------------------------------------------------------------------------
# RetryStats
# ---------------------------------------------------------------------------


class TestRetryStats:
    def test_initial_state_is_zero(self):
        stats = RetryStats()
        result = stats.get_stats()
        assert result["total_operations"] == 0
        assert result["successful_first_try"] == 0
        assert result["failed_permanently"] == 0

    def test_record_success_first_try(self):
        stats = RetryStats()
        stats.record_success(on_first_try=True)
        result = stats.get_stats()
        assert result["total_operations"] == 1
        assert result["successful_first_try"] == 1

    def test_record_success_after_retry(self):
        stats = RetryStats()
        stats.record_success(on_first_try=False)
        result = stats.get_stats()
        assert result["successful_after_retry"] == 1

    def test_record_failure(self):
        stats = RetryStats()
        stats.record_failure("Connection refused")
        result = stats.get_stats()
        assert result["failed_permanently"] == 1
        assert result["last_failure_reason"] == "Connection refused"
        assert result["last_failure_time"] is not None

    def test_record_retry_increments_count(self):
        stats = RetryStats()
        stats.record_retry()
        stats.record_retry()
        assert stats.get_stats()["total_retries"] == 2

    def test_success_rate_all_success(self):
        stats = RetryStats()
        stats.record_success(on_first_try=True)
        stats.record_success(on_first_try=True)
        assert stats.get_stats()["success_rate"] == 1.0

    def test_success_rate_mixed(self):
        stats = RetryStats()
        stats.record_success(on_first_try=True)
        stats.record_failure("err")
        result = stats.get_stats()
        assert result["success_rate"] == 0.5

    def test_success_rate_zero_operations(self):
        stats = RetryStats()
        assert stats.get_stats()["success_rate"] == 0.0

    def test_get_stats_returns_all_expected_keys(self):
        stats = RetryStats()
        result = stats.get_stats()
        expected_keys = {
            "total_operations",
            "successful_first_try",
            "successful_after_retry",
            "failed_permanently",
            "total_retries",
            "success_rate",
            "last_failure_reason",
            "last_failure_time",
        }
        assert expected_keys.issubset(result.keys())
