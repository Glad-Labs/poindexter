"""Resilience tests for services/ollama_client.py — Glad-Labs/poindexter#200.

Covers the tenacity retry decorator + aiolimiter concurrency cap that
replaced the hand-rolled exponential-backoff loop in
``generate_with_retry``. The pre-existing
``TestOllamaGenerateWithRetry`` class in ``test_ollama_client.py``
covers the retry-then-success and exhaustion-raises happy paths; this
file fills in the additional coverage requested in #200:

- Non-retryable errors (4xx, ValueError, OllamaModelNotFoundError)
  fail fast on the first attempt without re-trying.
- The module-level concurrency limiter caps the in-flight Ollama
  request count.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from services import ollama_client as ollama_client_module
from services.ollama_client import (
    OllamaClient,
    OllamaError,
    OllamaModelNotFoundError,
    rebuild_concurrency_limiter,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> OllamaClient:
    """Standard OllamaClient with a mocked httpx session."""
    c = OllamaClient()
    c.client = AsyncMock(spec=httpx.AsyncClient)
    return c


@pytest.fixture(autouse=True)
def reset_concurrency_limiter():
    """Reset the module-level limiter between tests so each test gets a
    fresh state and any per-test ``ollama_concurrency_limit`` override
    actually takes effect."""
    ollama_client_module._concurrency_limiter = None
    yield
    ollama_client_module._concurrency_limiter = None


# ---------------------------------------------------------------------------
# Non-retryable errors fail fast
# ---------------------------------------------------------------------------


class TestNonRetryableFailsImmediately:
    """ValueError / programming bugs / 4xx errors must NOT trigger
    tenacity's retry loop — they're not transient and retrying just
    burns time and GPU cycles before raising the same error.
    """

    @pytest.mark.asyncio
    async def test_value_error_does_not_retry(self, client):
        call_count = 0

        async def boom(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise ValueError("programmer bug — not transient")

        with patch.object(client, "generate", side_effect=boom):
            with pytest.raises(ValueError, match="programmer bug"):
                await client.generate_with_retry(
                    "Test", max_retries=5, base_delay=0.0
                )

        # Single attempt — ValueError is not in retry_if_exception_type
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_model_not_found_does_not_retry(self, client):
        """OllamaModelNotFoundError represents a permanent config issue,
        not a transient outage — pulling the same nonexistent model
        again won't help."""
        call_count = 0

        async def boom(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise OllamaModelNotFoundError("no such model: phantom:99b")

        with patch.object(client, "generate", side_effect=boom):
            with pytest.raises(OllamaModelNotFoundError):
                await client.generate_with_retry(
                    "Test", max_retries=5, base_delay=0.0
                )

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_http_status_4xx_is_retried_via_httpx_error(self, client):
        """httpx.HTTPStatusError IS an httpx.HTTPError subclass, so the
        retry policy DOES retry it. This test pins that behavior so a
        future tightening (e.g. excluding 4xx) is an explicit decision.
        """
        call_count = 0
        resp = httpx.Response(status_code=400)

        async def boom(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.HTTPStatusError(
                "bad request", request=httpx.Request("POST", "http://x"), response=resp
            )

        with patch.object(client, "generate", side_effect=boom):
            with pytest.raises(httpx.HTTPStatusError):
                await client.generate_with_retry(
                    "Test", max_retries=2, base_delay=0.0
                )

        # Currently retried because it's an httpx.HTTPError. If we ever
        # narrow the retry policy this assertion will flip to == 1.
        assert call_count == 2


# ---------------------------------------------------------------------------
# OllamaError base class is not retried
# ---------------------------------------------------------------------------


class TestOllamaErrorNotRetried:
    @pytest.mark.asyncio
    async def test_plain_ollama_error_does_not_retry(self, client):
        """Generic OllamaError (not the OllamaConnectionError subclass)
        is not in the retry policy — it represents a logic-level failure
        from inside our own code, not a transient network blip.
        """
        call_count = 0

        async def boom(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise OllamaError("downstream rejected the response")

        with patch.object(client, "generate", side_effect=boom):
            with pytest.raises(OllamaError):
                await client.generate_with_retry(
                    "Test", max_retries=4, base_delay=0.0
                )

        assert call_count == 1


# ---------------------------------------------------------------------------
# Concurrency limiter
# ---------------------------------------------------------------------------


class TestConcurrencyLimiter:
    """The aiolimiter.AsyncLimiter caps the number of concurrent
    in-flight Ollama requests this worker process makes. Without the
    cap, a fan-out stage could spawn 50+ parallel requests and
    VRAM-thrash the GPU into evicting the writer model."""

    @pytest.mark.asyncio
    async def test_rebuild_limiter_picks_up_new_setting(self):
        """rebuild_concurrency_limiter() must re-read app_settings so
        operators can change the cap live."""
        with patch.object(
            ollama_client_module, "_sc_get",
            side_effect=lambda key, default="": {
                "ollama_concurrency_limit": "5",
            }.get(key, default),
        ):
            limiter = rebuild_concurrency_limiter()
        assert limiter.max_rate == 5

        with patch.object(
            ollama_client_module, "_sc_get",
            side_effect=lambda key, default="": {
                "ollama_concurrency_limit": "20",
            }.get(key, default),
        ):
            limiter2 = rebuild_concurrency_limiter()
        assert limiter2.max_rate == 20
        assert limiter is not limiter2  # truly rebuilt

    @pytest.mark.asyncio
    async def test_limiter_caps_concurrent_calls(self):
        """Fire 20 calls; the limiter's max_rate=5 means at most 5
        permits are released per second. We measure the high-water mark
        of in-flight calls to confirm the cap is enforced.
        """
        # Force a 5-permit limiter
        with patch.object(
            ollama_client_module, "_sc_get",
            side_effect=lambda key, default="": {
                "ollama_concurrency_limit": "5",
            }.get(key, default),
        ):
            limiter = rebuild_concurrency_limiter()

        in_flight = 0
        max_observed = 0
        lock = asyncio.Lock()
        gate = asyncio.Event()

        async def worker():
            nonlocal in_flight, max_observed
            async with limiter:
                async with lock:
                    in_flight += 1
                    if in_flight > max_observed:
                        max_observed = in_flight
                # Hold the slot until the gate fires so the high-water
                # mark reflects parallel admissions, not serial completions.
                await gate.wait()
                async with lock:
                    in_flight -= 1

        tasks = [asyncio.create_task(worker()) for _ in range(20)]
        # Give the limiter a tick to admit the first batch
        await asyncio.sleep(0.05)
        # max_rate=5 with time_period=1.0 — first 5 should be admitted
        # immediately; the rest queue behind the leaky bucket.
        assert max_observed <= 5, (
            f"Concurrency cap breached — observed {max_observed} in flight, expected <= 5"
        )
        assert max_observed >= 1, "limiter never admitted any callers"

        # Release everyone
        gate.set()
        await asyncio.gather(*tasks)
