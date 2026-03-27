"""
Unit tests for utils.circuit_breaker module.

All tests are pure — zero DB, LLM, or network calls.
Covers CircuitBreaker state transitions, CachedResponse, and
the get_circuit_breaker / with_circuit_breaker helpers.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import pytest

from utils.circuit_breaker import (
    CachedResponse,
    CircuitBreaker,
    CircuitState,
    circuit_breakers,
    get_all_circuit_breaker_status,
    get_circuit_breaker,
    with_circuit_breaker,
)

# ---------------------------------------------------------------------------
# CircuitBreaker — initial state
# ---------------------------------------------------------------------------


class TestCircuitBreakerInit:
    def test_default_state_is_closed(self):
        cb = CircuitBreaker("test_service")
        assert cb.state == CircuitState.CLOSED

    def test_default_failure_threshold(self):
        cb = CircuitBreaker("svc")
        assert cb.failure_threshold == 5

    def test_custom_thresholds(self):
        cb = CircuitBreaker("svc", failure_threshold=3, recovery_timeout=30, success_threshold=1)
        assert cb.failure_threshold == 3
        assert cb.recovery_timeout == 30
        assert cb.success_threshold == 1

    def test_initial_counts_are_zero(self):
        cb = CircuitBreaker("svc")
        assert cb.failure_count == 0
        assert cb.success_count == 0

    def test_initial_timestamps_are_none(self):
        cb = CircuitBreaker("svc")
        assert cb.last_failure_time is None
        assert cb.opened_time is None


# ---------------------------------------------------------------------------
# CircuitBreaker — record_failure / opening circuit
# ---------------------------------------------------------------------------


class TestCircuitBreakerFailures:
    def test_failure_increments_count(self):
        cb = CircuitBreaker("svc")
        cb.record_failure()
        assert cb.failure_count == 1

    def test_failure_records_timestamp(self):
        cb = CircuitBreaker("svc")
        before = datetime.now(timezone.utc)
        cb.record_failure()
        assert cb.last_failure_time is not None
        assert cb.last_failure_time >= before

    def test_circuit_opens_after_threshold(self):
        cb = CircuitBreaker("svc", failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_circuit_stays_closed_below_threshold(self):
        cb = CircuitBreaker("svc", failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    def test_opened_time_set_when_circuit_opens(self):
        cb = CircuitBreaker("svc", failure_threshold=1)
        cb.record_failure()
        assert cb.opened_time is not None

    def test_failure_in_half_open_reopens_circuit(self):
        cb = CircuitBreaker("svc", failure_threshold=1)
        cb.record_failure()  # Opens circuit
        cb._half_open_circuit()
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_failure()  # Should reopen
        assert cb.state == CircuitState.OPEN


# ---------------------------------------------------------------------------
# CircuitBreaker — record_success / closing circuit
# ---------------------------------------------------------------------------


class TestCircuitBreakerSuccesses:
    def test_success_resets_failure_count_in_closed_state(self):
        cb = CircuitBreaker("svc", failure_threshold=5)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0

    def test_success_in_half_open_increments_success_count(self):
        cb = CircuitBreaker("svc", failure_threshold=1, success_threshold=2)
        cb.record_failure()  # Open
        cb._half_open_circuit()
        cb.record_success()
        assert cb.success_count == 1

    def test_circuit_closes_after_success_threshold(self):
        cb = CircuitBreaker("svc", failure_threshold=1, success_threshold=2)
        cb.record_failure()  # Open
        cb._half_open_circuit()
        cb.record_success()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_success_not_in_half_open_does_not_change_state(self):
        cb = CircuitBreaker("svc")
        cb.record_success()
        assert cb.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# CircuitBreaker — is_available
# ---------------------------------------------------------------------------


class TestCircuitBreakerAvailability:
    def test_closed_circuit_is_available(self):
        cb = CircuitBreaker("svc")
        assert cb.is_available() is True

    def test_open_circuit_is_not_available(self):
        cb = CircuitBreaker("svc", failure_threshold=1)
        cb.record_failure()
        assert cb.is_available() is False

    def test_half_open_circuit_is_available(self):
        cb = CircuitBreaker("svc", failure_threshold=1)
        cb.record_failure()
        cb._half_open_circuit()
        assert cb.is_available() is True

    def test_open_circuit_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker("svc", failure_threshold=1, recovery_timeout=0)
        cb.record_failure()
        # Backdate opened_time beyond recovery_timeout
        cb.opened_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        available = cb.is_available()
        assert available is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_open_circuit_stays_closed_before_timeout(self):
        cb = CircuitBreaker("svc", failure_threshold=1, recovery_timeout=9999)
        cb.record_failure()
        assert cb.is_available() is False


# ---------------------------------------------------------------------------
# CircuitBreaker — get_status
# ---------------------------------------------------------------------------


class TestCircuitBreakerStatus:
    def test_status_contains_required_keys(self):
        cb = CircuitBreaker("my_service")
        status = cb.get_status()
        assert "service" in status
        assert "state" in status
        assert "failure_count" in status
        assert "success_count" in status

    def test_status_service_name(self):
        cb = CircuitBreaker("my_service")
        assert cb.get_status()["service"] == "my_service"

    def test_status_state_is_string(self):
        cb = CircuitBreaker("svc")
        assert isinstance(cb.get_status()["state"], str)


# ---------------------------------------------------------------------------
# get_circuit_breaker — singleton registry
# ---------------------------------------------------------------------------


class TestGetCircuitBreaker:
    def test_returns_circuit_breaker(self):
        cb = get_circuit_breaker("unique_service_xyz")
        assert isinstance(cb, CircuitBreaker)

    def test_same_name_returns_same_instance(self):
        cb1 = get_circuit_breaker("singleton_service")
        cb2 = get_circuit_breaker("singleton_service")
        assert cb1 is cb2

    def test_different_names_return_different_instances(self):
        cb1 = get_circuit_breaker("service_alpha_test")
        cb2 = get_circuit_breaker("service_beta_test")
        assert cb1 is not cb2


# ---------------------------------------------------------------------------
# get_all_circuit_breaker_status
# ---------------------------------------------------------------------------


class TestGetAllCircuitBreakerStatus:
    def test_returns_dict(self):
        get_circuit_breaker("status_test_svc")
        result = get_all_circuit_breaker_status()
        assert isinstance(result, dict)

    def test_contains_registered_services(self):
        get_circuit_breaker("status_check_svc")
        result = get_all_circuit_breaker_status()
        assert "status_check_svc" in result


# ---------------------------------------------------------------------------
# with_circuit_breaker — async helper
# ---------------------------------------------------------------------------


class TestWithCircuitBreaker:
    @pytest.mark.asyncio
    async def test_returns_operation_result_on_success(self):
        async def op():
            return "ok"

        result = await with_circuit_breaker(op, "cb_test_success")
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_returns_fallback_when_circuit_open(self):
        cb = get_circuit_breaker("cb_test_open_svc")
        # Force circuit open
        cb.state = CircuitState.OPEN
        cb.opened_time = datetime.now(timezone.utc)  # recent, no recovery yet
        cb.failure_count = 99

        async def op():
            return "should_not_reach"

        result = await with_circuit_breaker(op, "cb_test_open_svc", fallback_value="fallback")
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_returns_fallback_on_timeout(self):
        async def timeout_op():
            raise asyncio.TimeoutError()

        result = await with_circuit_breaker(
            timeout_op, "cb_test_timeout_svc", fallback_value="timeout_fallback"
        )
        assert result == "timeout_fallback"

    @pytest.mark.asyncio
    async def test_returns_fallback_on_connection_error(self):
        async def conn_op():
            raise ConnectionError("refused")

        result = await with_circuit_breaker(
            conn_op, "cb_test_conn_svc", fallback_value="conn_fallback"
        )
        assert result == "conn_fallback"

    @pytest.mark.asyncio
    async def test_returns_fallback_on_generic_exception(self):
        async def bad_op():
            raise RuntimeError("boom")

        result = await with_circuit_breaker(
            bad_op, "cb_test_generic_svc", fallback_value="generic_fallback"
        )
        assert result == "generic_fallback"

    @pytest.mark.asyncio
    async def test_records_success_after_successful_call(self):
        svc_name = "cb_record_success_svc"
        # Start fresh
        circuit_breakers.pop(svc_name, None)
        cb = get_circuit_breaker(svc_name)
        cb._half_open_circuit()

        async def op():
            return "done"

        await with_circuit_breaker(op, svc_name)
        assert cb.success_count >= 1


# ---------------------------------------------------------------------------
# CachedResponse
# ---------------------------------------------------------------------------


class TestCachedResponse:
    def test_get_returns_none_for_missing_key(self):
        cache = CachedResponse()
        assert cache.get("missing") is None

    def test_set_and_get_roundtrip(self):
        cache = CachedResponse()
        cache.set("k", {"data": 1})
        assert cache.get("k") == {"data": 1}

    def test_expired_entry_returns_none(self):
        cache = CachedResponse(max_age=0)
        cache.set("k", "value")
        # Back-date the cache entry's timestamp so age > 0 without real sleep.
        # cache stores (value, timestamp) tuples; move the timestamp 1 second into
        # the past so the expiry condition (age > max_age=0) is deterministically met.
        value, _ts = cache.cache["k"]
        cache.cache["k"] = (value, datetime.now(timezone.utc) - timedelta(seconds=1))
        assert cache.get("k") is None

    def test_clear_removes_all_entries(self):
        cache = CachedResponse()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_get_stats_returns_expected_keys(self):
        cache = CachedResponse()
        stats = cache.get_stats()
        assert "cached_items" in stats
        assert "max_age_seconds" in stats

    def test_get_stats_item_count(self):
        cache = CachedResponse()
        cache.set("x", "hello")
        assert cache.get_stats()["cached_items"] == 1
