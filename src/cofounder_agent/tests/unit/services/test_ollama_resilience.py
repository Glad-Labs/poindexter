"""Unit tests for services/ollama_resilience.py — Glad-Labs/poindexter#153.

Covers the retry helper, in-process semaphore, and circuit breaker
that wrap every Ollama call. The resilience layer is what keeps the
content pipeline from publishing stub content when GPU contention or
VRAM pressure pushes Ollama into degraded mode.
"""
from __future__ import annotations

import asyncio
import random
import time
from unittest.mock import patch

import httpx
import pytest

from services.ollama_resilience import (
    CircuitBreaker,
    OllamaCircuitOpenError,
    OllamaEmptyResponseError,
    OllamaResilienceManager,
    compute_backoff,
    is_retryable,
)
from services.site_config import SiteConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _fast_site_config(**overrides: str) -> SiteConfig:
    """SiteConfig pre-seeded with low timeouts so tests run quickly."""
    cfg = {
        "ollama_retry_max_attempts": "3",
        "ollama_retry_base_seconds": "0.0",  # No real sleeping
        "ollama_retry_max_seconds": "0.0",
        "ollama_retry_jitter_pct": "0.0",
        "ollama_max_concurrent_calls": "2",
        "ollama_circuit_breaker_failures": "3",
        "ollama_circuit_breaker_window_s": "60",
        "ollama_circuit_breaker_cooldown_s": "1",
    }
    cfg.update(overrides)
    return SiteConfig(initial_config=cfg)


@pytest.fixture
def site_config() -> SiteConfig:
    return _fast_site_config()


@pytest.fixture
def manager(site_config: SiteConfig) -> OllamaResilienceManager:
    return OllamaResilienceManager(site_config=site_config)


# ---------------------------------------------------------------------------
# is_retryable classification
# ---------------------------------------------------------------------------


class TestIsRetryable:
    def test_connect_error_is_retryable(self):
        assert is_retryable(httpx.ConnectError("refused")) is True

    def test_read_timeout_is_retryable(self):
        assert is_retryable(httpx.ReadTimeout("slow")) is True

    def test_pool_timeout_is_retryable(self):
        assert is_retryable(httpx.PoolTimeout("pool exhausted")) is True

    def test_empty_response_is_retryable(self):
        assert is_retryable(OllamaEmptyResponseError("empty")) is True

    def test_503_is_retryable(self):
        resp = httpx.Response(status_code=503)
        exc = httpx.HTTPStatusError("service unavailable", request=None, response=resp)
        assert is_retryable(exc) is True

    def test_429_is_retryable(self):
        resp = httpx.Response(status_code=429)
        exc = httpx.HTTPStatusError("rate limited", request=None, response=resp)
        assert is_retryable(exc) is True

    def test_400_not_retryable(self):
        resp = httpx.Response(status_code=400)
        exc = httpx.HTTPStatusError("bad request", request=None, response=resp)
        assert is_retryable(exc) is False

    def test_401_auth_not_retryable(self):
        resp = httpx.Response(status_code=401)
        exc = httpx.HTTPStatusError("unauthorized", request=None, response=resp)
        assert is_retryable(exc) is False

    def test_404_schema_not_retryable(self):
        resp = httpx.Response(status_code=404)
        exc = httpx.HTTPStatusError("not found", request=None, response=resp)
        assert is_retryable(exc) is False

    def test_value_error_not_retryable(self):
        assert is_retryable(ValueError("bug")) is False

    def test_cancelled_error_not_retryable(self):
        assert is_retryable(asyncio.CancelledError()) is False

    def test_circuit_open_not_retryable(self):
        exc = OllamaCircuitOpenError(
            "open", opened_at=0.0, cooldown_seconds=300.0, consecutive_failures=5
        )
        assert is_retryable(exc) is False


# ---------------------------------------------------------------------------
# compute_backoff
# ---------------------------------------------------------------------------


class TestComputeBackoff:
    def test_exponential_growth_no_jitter(self):
        assert compute_backoff(1, base_seconds=1.0, max_seconds=100.0, jitter_pct=0.0) == 1.0
        assert compute_backoff(2, base_seconds=1.0, max_seconds=100.0, jitter_pct=0.0) == 2.0
        assert compute_backoff(3, base_seconds=1.0, max_seconds=100.0, jitter_pct=0.0) == 4.0
        assert compute_backoff(4, base_seconds=1.0, max_seconds=100.0, jitter_pct=0.0) == 8.0

    def test_capped_at_max_seconds(self):
        # base=1.0 * 2^9 = 512, but cap is 30 → returns 30
        result = compute_backoff(10, base_seconds=1.0, max_seconds=30.0, jitter_pct=0.0)
        assert result == 30.0

    def test_jitter_within_bounds(self):
        rng = random.Random(42)
        # base=2.0, attempt=2 → raw=4.0, jitter=±25% → [3.0, 5.0]
        for _ in range(50):
            d = compute_backoff(
                2, base_seconds=2.0, max_seconds=100.0, jitter_pct=0.25, rng=rng
            )
            assert 3.0 <= d <= 5.0

    def test_zero_attempt_returns_zero(self):
        assert compute_backoff(0, base_seconds=1.0, max_seconds=10.0, jitter_pct=0.0) == 0.0

    def test_never_negative(self):
        # Even with extreme jitter (200%) the result clamps to >= 0
        rng = random.Random(42)
        for _ in range(50):
            d = compute_backoff(
                1, base_seconds=1.0, max_seconds=10.0, jitter_pct=2.0, rng=rng
            )
            assert d >= 0.0


# ---------------------------------------------------------------------------
# Retry — happy path on attempt 2 (timeout, then success)
# ---------------------------------------------------------------------------


class TestRetrySuccess:
    @pytest.mark.asyncio
    async def test_retries_on_timeout_succeeds_on_attempt_2(self, manager):
        attempts: list[int] = []

        async def op():
            attempts.append(len(attempts) + 1)
            if attempts[-1] == 1:
                raise httpx.ReadTimeout("transient")
            return {"text": "hello"}

        result = await manager.run(op, op_name="generate")
        assert result == {"text": "hello"}
        assert len(attempts) == 2  # one failure + one success
        # Breaker stays closed after recovery
        assert manager.circuit.state == "closed"

    @pytest.mark.asyncio
    async def test_retries_on_empty_content_succeeds_on_attempt_3(self, manager):
        """Thinking-trace overflow returns 200 OK with empty content."""
        attempts: list[int] = []

        async def op():
            attempts.append(len(attempts) + 1)
            if attempts[-1] < 3:
                # Empty content — simulate thinking-trace overflow
                return {"message": {"role": "assistant", "content": ""}}
            return {"message": {"role": "assistant", "content": "real answer"}}

        def validate(r):
            msg = r.get("message") or {}
            return bool(msg.get("content") or msg.get("thinking"))

        result = await manager.run(op, op_name="generate", validate_result=validate)
        assert result["message"]["content"] == "real answer"
        assert len(attempts) == 3


# ---------------------------------------------------------------------------
# Retry — exhaustion
# ---------------------------------------------------------------------------


class TestRetryExhaustion:
    @pytest.mark.asyncio
    async def test_exhausts_max_attempts_then_raises(self, manager):
        calls = 0

        async def op():
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("refused")

        with pytest.raises(httpx.ConnectError):
            await manager.run(op, op_name="generate")
        # Default max_attempts=3 in the test fixture
        assert calls == 3

    @pytest.mark.asyncio
    async def test_non_retryable_4xx_does_not_retry(self, manager):
        calls = 0
        resp = httpx.Response(status_code=401)

        async def op():
            nonlocal calls
            calls += 1
            raise httpx.HTTPStatusError("unauthorized", request=None, response=resp)

        with pytest.raises(httpx.HTTPStatusError):
            await manager.run(op, op_name="generate")
        assert calls == 1  # No retry on 4xx

    @pytest.mark.asyncio
    async def test_value_error_does_not_retry(self, manager):
        calls = 0

        async def op():
            nonlocal calls
            calls += 1
            raise ValueError("programmer bug")

        with pytest.raises(ValueError):
            await manager.run(op, op_name="generate")
        assert calls == 1


# ---------------------------------------------------------------------------
# Semaphore — concurrency cap
# ---------------------------------------------------------------------------


class TestSemaphoreCap:
    @pytest.mark.asyncio
    async def test_semaphore_caps_concurrent_calls(self, site_config):
        """Start 5 calls concurrently — observe max 2 in flight at once."""
        site_config._config["ollama_max_concurrent_calls"] = "2"
        mgr = OllamaResilienceManager(site_config=site_config)

        in_flight = 0
        max_observed = 0
        lock = asyncio.Lock()
        gate = asyncio.Event()

        async def op():
            nonlocal in_flight, max_observed
            async with lock:
                in_flight += 1
                if in_flight > max_observed:
                    max_observed = in_flight
            await gate.wait()  # hold until released
            async with lock:
                in_flight -= 1
            return {"text": "ok"}

        # Launch 5 calls concurrently
        tasks = [
            asyncio.create_task(mgr.run(op, op_name="generate")) for _ in range(5)
        ]
        # Let the first two acquire permits and pile up against the gate
        await asyncio.sleep(0.05)
        assert max_observed <= 2

        # Release everyone
        gate.set()
        results = await asyncio.gather(*tasks)
        assert all(r["text"] == "ok" for r in results)
        assert max_observed == 2  # exactly the cap was reached, never above


# ---------------------------------------------------------------------------
# Circuit breaker — open + recover
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_trips_after_n_failures(self, site_config):
        site_config._config["ollama_circuit_breaker_failures"] = "3"
        site_config._config["ollama_circuit_breaker_window_s"] = "60"
        site_config._config["ollama_circuit_breaker_cooldown_s"] = "10"
        site_config._config["ollama_retry_max_attempts"] = "1"  # No retry layer noise

        mgr = OllamaResilienceManager(site_config=site_config)

        async def fail():
            raise httpx.ConnectError("down")

        # Three failures should drive the breaker open
        for _ in range(3):
            with pytest.raises(httpx.ConnectError):
                await mgr.run(fail, op_name="generate")
        assert mgr.circuit.state == "open"

        # Subsequent call fails fast with OllamaCircuitOpenError
        with pytest.raises(OllamaCircuitOpenError) as exc_info:
            await mgr.run(fail, op_name="generate")
        assert exc_info.value.consecutive_failures >= 3
        assert exc_info.value.cooldown_seconds == 10.0

    @pytest.mark.asyncio
    async def test_recovers_after_cooldown(self, site_config):
        site_config._config["ollama_circuit_breaker_failures"] = "2"
        site_config._config["ollama_circuit_breaker_cooldown_s"] = "0.1"
        site_config._config["ollama_retry_max_attempts"] = "1"

        mgr = OllamaResilienceManager(site_config=site_config)

        async def fail():
            raise httpx.ConnectError("down")

        # Trip the breaker
        for _ in range(2):
            with pytest.raises(httpx.ConnectError):
                await mgr.run(fail, op_name="generate")
        assert mgr.circuit.state == "open"

        # Wait past cooldown
        await asyncio.sleep(0.15)
        assert mgr.circuit.state == "half_open"

        # First call after cooldown is admitted as a probe
        async def succeed():
            return {"text": "recovered"}

        result = await mgr.run(succeed, op_name="generate")
        assert result == {"text": "recovered"}
        assert mgr.circuit.state == "closed"

    @pytest.mark.asyncio
    async def test_half_open_failure_re_opens(self, site_config):
        site_config._config["ollama_circuit_breaker_failures"] = "2"
        site_config._config["ollama_circuit_breaker_cooldown_s"] = "0.1"
        site_config._config["ollama_retry_max_attempts"] = "1"

        mgr = OllamaResilienceManager(site_config=site_config)

        async def fail():
            raise httpx.ConnectError("down")

        for _ in range(2):
            with pytest.raises(httpx.ConnectError):
                await mgr.run(fail, op_name="generate")

        # Wait for cooldown then fail the probe — should re-open
        await asyncio.sleep(0.15)
        with pytest.raises(httpx.ConnectError):
            await mgr.run(fail, op_name="generate")
        assert mgr.circuit.state == "open"

    @pytest.mark.asyncio
    async def test_window_drops_stale_failures(self, site_config):
        """Failures outside the rolling window should not contribute."""
        site_config._config["ollama_circuit_breaker_failures"] = "3"
        site_config._config["ollama_circuit_breaker_window_s"] = "0.05"
        site_config._config["ollama_retry_max_attempts"] = "1"

        mgr = OllamaResilienceManager(site_config=site_config)

        async def fail():
            raise httpx.ConnectError("down")

        # 2 failures, then wait past window, then 2 more — should NOT trip
        for _ in range(2):
            with pytest.raises(httpx.ConnectError):
                await mgr.run(fail, op_name="generate")
        await asyncio.sleep(0.1)  # past window
        for _ in range(2):
            with pytest.raises(httpx.ConnectError):
                await mgr.run(fail, op_name="generate")
        # Stale failures dropped → only 2 in window → still closed
        assert mgr.circuit.state == "closed"


# ---------------------------------------------------------------------------
# Health snapshot
# ---------------------------------------------------------------------------


class TestHealthSnapshot:
    def test_snapshot_reports_closed_state(self, manager):
        snap = manager.health_snapshot()
        assert snap["state"] == "closed"
        assert snap["consecutive_failures"] == 0
        assert "max_concurrent_calls" in snap
        assert "in_flight_calls" in snap
        assert snap["in_flight_calls"] == 0

    def test_snapshot_after_force_open(self, manager):
        manager.circuit._force_open()
        snap = manager.health_snapshot()
        assert snap["state"] == "open"
        assert snap["seconds_until_recheck"] > 0


# ---------------------------------------------------------------------------
# Audit logging — every retry/trip should fire-and-forget log
# ---------------------------------------------------------------------------


class TestAuditLog:
    @pytest.mark.asyncio
    async def test_retry_fires_audit_log(self, manager):
        calls = 0

        async def op():
            nonlocal calls
            calls += 1
            if calls < 2:
                raise httpx.ReadTimeout("slow")
            return {"text": "ok"}

        with patch("services.ollama_resilience.audit_log_bg") as mock_audit:
            await manager.run(op, op_name="generate")

        # Should have logged the failure + the backoff + the retry success
        event_types = {call.args[0] for call in mock_audit.call_args_list}
        assert "ollama_call_failed" in event_types
        assert "ollama_retry_backoff" in event_types
        assert "ollama_retry_success" in event_types

    @pytest.mark.asyncio
    async def test_circuit_open_fires_audit_log(self, site_config):
        site_config._config["ollama_circuit_breaker_failures"] = "2"
        site_config._config["ollama_retry_max_attempts"] = "1"
        mgr = OllamaResilienceManager(site_config=site_config)

        async def fail():
            raise httpx.ConnectError("down")

        with patch("services.ollama_resilience.audit_log_bg") as mock_audit:
            for _ in range(2):
                with pytest.raises(httpx.ConnectError):
                    await mgr.run(fail, op_name="generate")

        event_types = [call.args[0] for call in mock_audit.call_args_list]
        assert "ollama_circuit_opened" in event_types


# ---------------------------------------------------------------------------
# CircuitBreaker direct unit tests
# ---------------------------------------------------------------------------


class TestCircuitBreakerDirect:
    @pytest.mark.asyncio
    async def test_default_state_closed(self):
        cb = CircuitBreaker(site_config=_fast_site_config())
        assert cb.state == "closed"
        await cb.allow()  # does not raise

    @pytest.mark.asyncio
    async def test_record_success_resets_consecutive_count(self):
        cb = CircuitBreaker(site_config=_fast_site_config())
        await cb.record_failure(exc=RuntimeError("blip"))
        assert cb._consecutive_failures == 1
        await cb.record_success()
        assert cb._consecutive_failures == 0
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_seconds_until_recheck_decreases(self):
        cfg = _fast_site_config(ollama_circuit_breaker_cooldown_s="10")
        cb = CircuitBreaker(site_config=cfg)
        cb._force_open()
        snap1 = cb.snapshot()
        # Allow a short tick of monotonic time
        await asyncio.sleep(0.01)
        snap2 = cb.snapshot()
        assert snap2["seconds_until_recheck"] <= snap1["seconds_until_recheck"]
        assert snap1["seconds_until_recheck"] > 0
