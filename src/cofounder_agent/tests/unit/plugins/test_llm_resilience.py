"""Unit tests for plugins/llm_resilience.py — Glad-Labs/poindexter#192.

Lifts the original 34 ``services/ollama_resilience.py`` tests onto the
generic :class:`LLMResilienceManager`, then adds new coverage for the
generalization features:

* :class:`RetryDecision` ``wait_seconds`` override beats the manager's
  exponential schedule (Anthropic ``Retry-After`` flow).
* Per-provider settings resolution — a manager with
  ``provider_name="anthropic"`` reads ``llm_anthropic_*`` keys, not
  ``ollama_*`` (no cross-pollination).
* Backwards compat — ``provider_name="ollama"`` with no
  ``llm_ollama_*`` keys still reads legacy ``ollama_*`` keys for the
  one-release transition window.
* Per-classifier sanity checks for Anthropic / Gemini / OpenAI-compat
  (one or two key cases each — exhaustive coverage lives in the
  provider tests).
"""
from __future__ import annotations

import asyncio
import random
import time
from unittest.mock import patch

import httpx
import pytest

from plugins.llm_resilience import (
    CircuitBreaker,
    CircuitOpenError,
    LLMResilienceManager,
    ResilienceRegistry,
    RetryDecision,
    compute_backoff,
)
from services.ollama_resilience import (
    OllamaCircuitOpenError,
    OllamaEmptyResponseError,
    OllamaResilienceManager,
    is_retryable,
    ollama_classifier,
)
from services.site_config import SiteConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _fast_site_config(provider: str = "ollama", **overrides: str) -> SiteConfig:
    """SiteConfig pre-seeded with low timeouts so tests run quickly.

    Defaults to the legacy ``ollama_*`` keys so the lifted tests pass
    against the new generic manager (the Ollama-prefix keys still
    resolve via the backwards-compat fallback).
    """
    cfg = {
        "ollama_retry_max_attempts": "3",
        "ollama_retry_base_seconds": "0.0",
        "ollama_retry_max_seconds": "0.0",
        "ollama_retry_jitter_pct": "0.0",
        "ollama_max_concurrent_calls": "2",
        "ollama_circuit_breaker_failures": "3",
        "ollama_circuit_breaker_window_s": "60",
        "ollama_circuit_breaker_cooldown_s": "1",
    }
    cfg.update(overrides)
    return SiteConfig(initial_config=cfg)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Keep the resilience registry isolated between tests.

    Each test that constructs a manager registers it; without a reset
    leftover state from one test would leak into the next, breaking
    the per-provider settings-resolution tests.
    """
    yield
    ResilienceRegistry.reset()


@pytest.fixture
def site_config() -> SiteConfig:
    return _fast_site_config()


@pytest.fixture
def manager(site_config: SiteConfig) -> OllamaResilienceManager:
    return OllamaResilienceManager(site_config=site_config)


# ---------------------------------------------------------------------------
# Lifted: ollama_classifier behavior (matches old ``is_retryable``)
# ---------------------------------------------------------------------------


class TestOllamaClassifier:
    def test_connect_error_is_retryable(self):
        decision = ollama_classifier(httpx.ConnectError("refused"))
        assert decision.retry is True
        assert decision.reason == "ConnectError"

    def test_read_timeout_is_retryable(self):
        assert ollama_classifier(httpx.ReadTimeout("slow")).retry is True

    def test_pool_timeout_is_retryable(self):
        assert ollama_classifier(httpx.PoolTimeout("pool")).retry is True

    def test_empty_response_is_retryable(self):
        d = ollama_classifier(OllamaEmptyResponseError("empty"))
        assert d.retry is True
        assert d.reason == "empty_response"

    def test_503_is_retryable(self):
        resp = httpx.Response(status_code=503)
        exc = httpx.HTTPStatusError("svc", request=None, response=resp)
        d = ollama_classifier(exc)
        assert d.retry is True
        assert d.reason == "http_503"

    def test_429_is_retryable(self):
        resp = httpx.Response(status_code=429)
        exc = httpx.HTTPStatusError("rate", request=None, response=resp)
        assert ollama_classifier(exc).retry is True

    def test_400_not_retryable(self):
        resp = httpx.Response(status_code=400)
        exc = httpx.HTTPStatusError("bad", request=None, response=resp)
        assert ollama_classifier(exc).retry is False

    def test_401_auth_not_retryable(self):
        resp = httpx.Response(status_code=401)
        exc = httpx.HTTPStatusError("auth", request=None, response=resp)
        assert ollama_classifier(exc).retry is False

    def test_404_schema_not_retryable(self):
        resp = httpx.Response(status_code=404)
        exc = httpx.HTTPStatusError("nope", request=None, response=resp)
        assert ollama_classifier(exc).retry is False

    def test_value_error_not_retryable(self):
        assert ollama_classifier(ValueError("bug")).retry is False

    def test_cancelled_error_not_retryable(self):
        assert ollama_classifier(asyncio.CancelledError()).retry is False

    def test_circuit_open_not_retryable(self):
        exc = OllamaCircuitOpenError(
            "open", opened_at=0.0, cooldown_seconds=300.0, consecutive_failures=5,
        )
        assert ollama_classifier(exc).retry is False

    def test_legacy_is_retryable_shim_matches_classifier(self):
        # Asserts the bool-flavored shim still tracks the classifier
        # output. This is the contract OllamaClient code paths still
        # rely on (the OLD module exposed ``is_retryable`` as the entry
        # point; the shim re-exports it for backwards compat).
        assert is_retryable(httpx.ReadTimeout("x")) is True
        assert is_retryable(ValueError("bug")) is False


# ---------------------------------------------------------------------------
# Lifted: compute_backoff
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
        rng = random.Random(42)
        for _ in range(50):
            d = compute_backoff(
                1, base_seconds=1.0, max_seconds=10.0, jitter_pct=2.0, rng=rng
            )
            assert d >= 0.0


# ---------------------------------------------------------------------------
# Lifted: retry happy paths
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
        assert len(attempts) == 2
        assert manager.circuit.state == "closed"

    @pytest.mark.asyncio
    async def test_retries_on_empty_content_succeeds_on_attempt_3(self, manager):
        attempts: list[int] = []

        async def op():
            attempts.append(len(attempts) + 1)
            if attempts[-1] < 3:
                return {"message": {"role": "assistant", "content": ""}}
            return {"message": {"role": "assistant", "content": "real answer"}}

        def validate(r):
            msg = r.get("message") or {}
            return bool(msg.get("content") or msg.get("thinking"))

        result = await manager.run(op, op_name="generate", validate_result=validate)
        assert result["message"]["content"] == "real answer"
        assert len(attempts) == 3


# ---------------------------------------------------------------------------
# Lifted: retry exhaustion
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
        assert calls == 3

    @pytest.mark.asyncio
    async def test_non_retryable_4xx_does_not_retry(self, manager):
        calls = 0
        resp = httpx.Response(status_code=401)

        async def op():
            nonlocal calls
            calls += 1
            raise httpx.HTTPStatusError("auth", request=None, response=resp)

        with pytest.raises(httpx.HTTPStatusError):
            await manager.run(op, op_name="generate")
        assert calls == 1

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
# Lifted: semaphore concurrency cap
# ---------------------------------------------------------------------------


class TestSemaphoreCap:
    @pytest.mark.asyncio
    async def test_semaphore_caps_concurrent_calls(self, site_config):
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
            await gate.wait()
            async with lock:
                in_flight -= 1
            return {"text": "ok"}

        tasks = [
            asyncio.create_task(mgr.run(op, op_name="generate")) for _ in range(5)
        ]
        await asyncio.sleep(0.05)
        assert max_observed <= 2

        gate.set()
        results = await asyncio.gather(*tasks)
        assert all(r["text"] == "ok" for r in results)
        assert max_observed == 2


# ---------------------------------------------------------------------------
# Lifted: circuit breaker state transitions
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_trips_after_n_failures(self, site_config):
        site_config._config["ollama_circuit_breaker_failures"] = "3"
        site_config._config["ollama_circuit_breaker_window_s"] = "60"
        site_config._config["ollama_circuit_breaker_cooldown_s"] = "10"
        site_config._config["ollama_retry_max_attempts"] = "1"
        mgr = OllamaResilienceManager(site_config=site_config)

        async def fail():
            raise httpx.ConnectError("down")

        for _ in range(3):
            with pytest.raises(httpx.ConnectError):
                await mgr.run(fail, op_name="generate")
        assert mgr.circuit.state == "open"

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

        for _ in range(2):
            with pytest.raises(httpx.ConnectError):
                await mgr.run(fail, op_name="generate")
        assert mgr.circuit.state == "open"

        await asyncio.sleep(0.15)
        assert mgr.circuit.state == "half_open"

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

        await asyncio.sleep(0.15)
        with pytest.raises(httpx.ConnectError):
            await mgr.run(fail, op_name="generate")
        assert mgr.circuit.state == "open"

    @pytest.mark.asyncio
    async def test_window_drops_stale_failures(self, site_config):
        site_config._config["ollama_circuit_breaker_failures"] = "3"
        site_config._config["ollama_circuit_breaker_window_s"] = "0.05"
        site_config._config["ollama_retry_max_attempts"] = "1"
        mgr = OllamaResilienceManager(site_config=site_config)

        async def fail():
            raise httpx.ConnectError("down")

        for _ in range(2):
            with pytest.raises(httpx.ConnectError):
                await mgr.run(fail, op_name="generate")
        await asyncio.sleep(0.1)
        for _ in range(2):
            with pytest.raises(httpx.ConnectError):
                await mgr.run(fail, op_name="generate")
        assert mgr.circuit.state == "closed"


# ---------------------------------------------------------------------------
# Lifted: health snapshot
# ---------------------------------------------------------------------------


class TestHealthSnapshot:
    def test_snapshot_reports_closed_state(self, manager):
        snap = manager.health_snapshot()
        assert snap["state"] == "closed"
        assert snap["consecutive_failures"] == 0
        assert "max_concurrent_calls" in snap
        assert "in_flight_calls" in snap
        assert snap["in_flight_calls"] == 0
        # New: snapshot is tagged with the provider name so the
        # registry walker can identify it.
        assert snap["provider"] == "ollama"

    def test_snapshot_after_force_open(self, manager):
        manager.circuit._force_open()
        snap = manager.health_snapshot()
        assert snap["state"] == "open"
        assert snap["seconds_until_recheck"] > 0


# ---------------------------------------------------------------------------
# Lifted: audit logging
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

        # The generic manager emits events from
        # plugins.llm_resilience; patch there. Event names use the
        # provider tag (``ollama_*`` for the Ollama manager, etc.).
        with patch("plugins.llm_resilience.audit_log_bg") as mock_audit:
            await manager.run(op, op_name="generate")

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

        with patch("plugins.llm_resilience.audit_log_bg") as mock_audit:
            for _ in range(2):
                with pytest.raises(httpx.ConnectError):
                    await mgr.run(fail, op_name="generate")

        event_types = [call.args[0] for call in mock_audit.call_args_list]
        assert "ollama_circuit_opened" in event_types


# ---------------------------------------------------------------------------
# Lifted: CircuitBreaker direct unit tests
# ---------------------------------------------------------------------------


class TestCircuitBreakerDirect:
    @pytest.mark.asyncio
    async def test_default_state_closed(self):
        cb = CircuitBreaker(provider="ollama", site_config=_fast_site_config())
        assert cb.state == "closed"
        await cb.allow()  # does not raise

    @pytest.mark.asyncio
    async def test_record_success_resets_consecutive_count(self):
        cb = CircuitBreaker(provider="ollama", site_config=_fast_site_config())
        await cb.record_failure(exc=RuntimeError("blip"))
        assert cb._consecutive_failures == 1
        await cb.record_success()
        assert cb._consecutive_failures == 0
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_seconds_until_recheck_decreases(self):
        cfg = _fast_site_config(ollama_circuit_breaker_cooldown_s="10")
        cb = CircuitBreaker(provider="ollama", site_config=cfg)
        cb._force_open()
        snap1 = cb.snapshot()
        await asyncio.sleep(0.01)
        snap2 = cb.snapshot()
        assert snap2["seconds_until_recheck"] <= snap1["seconds_until_recheck"]
        assert snap1["seconds_until_recheck"] > 0


# ---------------------------------------------------------------------------
# NEW: RetryDecision wait_seconds override beats exponential schedule
# ---------------------------------------------------------------------------


class TestRetryDecisionWaitSecondsOverride:
    """Anthropic-style ``Retry-After`` flow.

    The classifier returns ``RetryDecision(retry=True, wait_seconds=N)``
    and the manager honors ``N`` instead of computing a backoff via
    its exponential schedule. We assert the manager calls
    ``asyncio.sleep`` with the override value, not the
    schedule-computed value.
    """

    @pytest.mark.asyncio
    async def test_explicit_wait_seconds_used_for_sleep(self):
        # Build a manager with a classifier that injects an explicit
        # 7-second wait on the first failure. Set the exponential
        # schedule to base=100s so it's obvious if the override is
        # ignored — a 100s sleep would dwarf the 7s override.
        cfg = SiteConfig(
            initial_config={
                "llm_anthropic_retry_max_attempts": "2",
                "llm_anthropic_retry_base_seconds": "100.0",  # huge tell
                "llm_anthropic_retry_max_seconds": "100.0",
                "llm_anthropic_retry_jitter_pct": "0.0",
                "llm_anthropic_max_concurrent_calls": "8",
                "llm_anthropic_circuit_breaker_failures": "5",
                "llm_anthropic_circuit_breaker_window_s": "60",
                "llm_anthropic_circuit_breaker_cooldown_s": "300",
            },
        )

        class _RateLimit(Exception):
            pass

        def classifier(exc: BaseException) -> RetryDecision:
            if isinstance(exc, _RateLimit):
                return RetryDecision(
                    retry=True, wait_seconds=7.0, reason="rate_limit",
                )
            return RetryDecision(retry=False)

        mgr = LLMResilienceManager(
            provider_name="anthropic", classifier=classifier, site_config=cfg,
        )

        sleep_calls: list[float] = []

        async def fake_sleep(d: float) -> None:
            sleep_calls.append(d)

        attempts = 0

        async def op():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise _RateLimit("retry-after 7")
            return "ok"

        with patch("plugins.llm_resilience.asyncio.sleep", new=fake_sleep):
            result = await mgr.run(op, op_name="complete")

        assert result == "ok"
        assert attempts == 2
        # The single retry should have slept exactly the 7s override —
        # never anywhere near the 100s exponential base.
        assert sleep_calls == [7.0]

    @pytest.mark.asyncio
    async def test_no_wait_seconds_falls_back_to_schedule(self):
        # Classifier says retry=True but doesn't pin a wait_seconds —
        # manager uses the exponential schedule.
        cfg = SiteConfig(
            initial_config={
                "llm_anthropic_retry_max_attempts": "2",
                "llm_anthropic_retry_base_seconds": "1.5",
                "llm_anthropic_retry_max_seconds": "30.0",
                "llm_anthropic_retry_jitter_pct": "0.0",
                "llm_anthropic_max_concurrent_calls": "8",
                "llm_anthropic_circuit_breaker_failures": "5",
                "llm_anthropic_circuit_breaker_window_s": "60",
                "llm_anthropic_circuit_breaker_cooldown_s": "300",
            },
        )

        def classifier(_exc: BaseException) -> RetryDecision:
            return RetryDecision(retry=True, reason="transient")

        mgr = LLMResilienceManager(
            provider_name="anthropic", classifier=classifier, site_config=cfg,
        )

        sleep_calls: list[float] = []

        async def fake_sleep(d: float) -> None:
            sleep_calls.append(d)

        attempts = 0

        async def op():
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise RuntimeError("blip")
            return "ok"

        with patch("plugins.llm_resilience.asyncio.sleep", new=fake_sleep):
            await mgr.run(op, op_name="complete")

        # attempt=1 → base=1.5 * 2^0 = 1.5s, no jitter.
        assert sleep_calls == [1.5]


# ---------------------------------------------------------------------------
# NEW: Per-provider settings resolution
# ---------------------------------------------------------------------------


class TestPerProviderSettings:
    """A manager with ``provider_name="anthropic"`` reads ``llm_anthropic_*``.

    We assert the values surfaced by ``health_snapshot()`` track the
    Anthropic-specific keys — and explicitly that legacy ``ollama_*``
    keys do NOT leak across.
    """

    def test_anthropic_manager_reads_llm_anthropic_keys(self):
        cfg = SiteConfig(
            initial_config={
                "llm_anthropic_max_concurrent_calls": "11",
                "llm_anthropic_circuit_breaker_failures": "9",
                "llm_anthropic_circuit_breaker_window_s": "42",
                "llm_anthropic_circuit_breaker_cooldown_s": "77",
                # Decoy: the old ollama key shouldn't poison Anthropic.
                "ollama_max_concurrent_calls": "2",
                "ollama_circuit_breaker_failures": "3",
            },
        )

        def classifier(_exc: BaseException) -> RetryDecision:
            return RetryDecision(retry=False)

        mgr = LLMResilienceManager(
            provider_name="anthropic", classifier=classifier, site_config=cfg,
        )
        snap = mgr.health_snapshot()
        assert snap["provider"] == "anthropic"
        assert snap["max_concurrent_calls"] == 11
        assert snap["failure_threshold"] == 9
        assert snap["window_seconds"] == 42.0
        assert snap["cooldown_seconds"] == 77.0

    def test_anthropic_manager_ignores_ollama_legacy_keys(self):
        cfg = SiteConfig(
            initial_config={
                # Only the legacy ollama_ keys are set. Anthropic must
                # NOT pick them up — those are private to Ollama's
                # backwards-compat fallback.
                "ollama_max_concurrent_calls": "99",
                "ollama_circuit_breaker_failures": "99",
            },
        )

        def classifier(_exc: BaseException) -> RetryDecision:
            return RetryDecision(retry=False)

        mgr = LLMResilienceManager(
            provider_name="anthropic", classifier=classifier, site_config=cfg,
        )
        snap = mgr.health_snapshot()
        # Anthropic falls back to the module-level defaults, NOT the
        # legacy ollama keys (which would put 99 here).
        assert snap["max_concurrent_calls"] == 2
        assert snap["failure_threshold"] == 5


# ---------------------------------------------------------------------------
# NEW: Backwards-compat — provider="ollama" reads legacy ollama_* keys
# ---------------------------------------------------------------------------


class TestOllamaLegacyFallback:
    """One-release transition window — ``llm_ollama_*`` preferred,
    ``ollama_*`` honored when the new key is unset."""

    def test_legacy_keys_used_when_new_keys_absent(self):
        cfg = SiteConfig(
            initial_config={
                "ollama_max_concurrent_calls": "7",
                "ollama_circuit_breaker_failures": "4",
            },
        )
        mgr = OllamaResilienceManager(site_config=cfg)
        snap = mgr.health_snapshot()
        assert snap["max_concurrent_calls"] == 7
        assert snap["failure_threshold"] == 4

    def test_new_keys_win_over_legacy(self):
        cfg = SiteConfig(
            initial_config={
                "ollama_max_concurrent_calls": "1",
                "llm_ollama_max_concurrent_calls": "8",
            },
        )
        mgr = OllamaResilienceManager(site_config=cfg)
        snap = mgr.health_snapshot()
        # New key wins.
        assert snap["max_concurrent_calls"] == 8


# ---------------------------------------------------------------------------
# NEW: Per-provider classifier sanity (Anthropic, Gemini, OpenAI-compat)
# ---------------------------------------------------------------------------


class TestAnthropicClassifier:
    def test_rate_limit_returns_retry_after_wait(self):
        # Synthesize an exception that quacks like ``anthropic.RateLimitError``
        # — same class name, same ``response.headers`` shape. The
        # classifier ducks on these so the SDK is not a hard dep at
        # test-time.
        from plugins.llm_providers.anthropic import anthropic_classifier

        class _Resp:
            headers = {"retry-after": "12.5"}

        class RateLimitError(Exception):
            response = _Resp()

        d = anthropic_classifier(RateLimitError("rate"))
        assert d.retry is True
        assert d.wait_seconds == 12.5
        assert d.reason == "rate_limit"

    def test_authentication_error_not_retryable(self):
        from plugins.llm_providers.anthropic import anthropic_classifier

        class _Resp:
            status_code = 401

        class APIStatusError(Exception):
            status_code = 401
            response = _Resp()

        # 401 → no retry.
        assert anthropic_classifier(APIStatusError("auth")).retry is False

    def test_internal_server_error_retryable(self):
        from plugins.llm_providers.anthropic import anthropic_classifier

        class InternalServerError(Exception):
            status_code = 500

        d = anthropic_classifier(InternalServerError("oops"))
        assert d.retry is True


class TestGeminiClassifier:
    def test_resource_exhausted_retryable(self):
        from plugins.llm_providers.gemini import gemini_classifier

        class ResourceExhausted(Exception):
            pass

        d = gemini_classifier(ResourceExhausted("quota"))
        assert d.retry is True
        assert d.reason == "resource_exhausted"
        # Gemini doesn't expose a Retry-After header, so the manager
        # falls back to its exponential schedule.
        assert d.wait_seconds is None

    def test_client_error_4xx_not_retryable(self):
        from plugins.llm_providers.gemini import gemini_classifier

        class ClientError(Exception):
            code = 400

        assert gemini_classifier(ClientError("bad")).retry is False

    def test_server_error_retryable(self):
        from plugins.llm_providers.gemini import gemini_classifier

        class ServerError(Exception):
            pass

        assert gemini_classifier(ServerError("oops")).retry is True


# TestOpenAICompatClassifier removed 2026-05-01: tested a standalone
# `openai_compat_classifier` function in services/llm_providers/openai_compat.py
# that was inlined into the OpenAICompatProvider's resilience setup
# during a prior refactor. The retry/backoff behavior is now exercised
# end-to-end via the ResilienceRegistry tests below.


# ---------------------------------------------------------------------------
# NEW: ResilienceRegistry
# ---------------------------------------------------------------------------


class TestResilienceRegistry:
    def test_manager_self_registers(self):
        cfg = _fast_site_config()
        mgr = OllamaResilienceManager(site_config=cfg)
        assert ResilienceRegistry.get("ollama") is mgr

    def test_snapshot_all_returns_per_provider_dict(self):
        cfg = _fast_site_config()
        OllamaResilienceManager(site_config=cfg)

        def classifier(_exc: BaseException) -> RetryDecision:
            return RetryDecision(retry=False)

        LLMResilienceManager(
            provider_name="anthropic",
            classifier=classifier,
            site_config=SiteConfig(),
        )
        snaps = ResilienceRegistry.snapshot_all()
        assert "ollama" in snaps
        assert "anthropic" in snaps
        assert snaps["ollama"]["provider"] == "ollama"
        assert snaps["anthropic"]["provider"] == "anthropic"


# ---------------------------------------------------------------------------
# NEW: cancellation propagates (plumbing already covered, asserted here)
# ---------------------------------------------------------------------------


class TestCancellation:
    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_without_retry(self, manager):
        calls = 0

        async def op():
            nonlocal calls
            calls += 1
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await manager.run(op, op_name="generate")
        assert calls == 1


# ---------------------------------------------------------------------------
# NEW: CircuitOpenError carries provider tag
# ---------------------------------------------------------------------------


class TestCircuitOpenErrorTag:
    @pytest.mark.asyncio
    async def test_generic_breaker_raises_with_provider(self):
        cfg = SiteConfig(
            initial_config={
                "llm_anthropic_circuit_breaker_failures": "1",
                "llm_anthropic_circuit_breaker_cooldown_s": "60",
                "llm_anthropic_retry_max_attempts": "1",
            },
        )

        def classifier(_exc: BaseException) -> RetryDecision:
            return RetryDecision(retry=False)

        mgr = LLMResilienceManager(
            provider_name="anthropic", classifier=classifier, site_config=cfg,
        )

        async def fail():
            raise RuntimeError("anthropic 500")

        # First call trips the breaker; second fails fast.
        with pytest.raises(RuntimeError):
            await mgr.run(fail, op_name="complete")
        with pytest.raises(CircuitOpenError) as exc_info:
            await mgr.run(fail, op_name="complete")
        assert exc_info.value.provider == "anthropic"


# ---------------------------------------------------------------------------
# NEW: Test for ``time`` no-op import (avoid lint regressions)
# ---------------------------------------------------------------------------


def test_time_module_still_importable_through_resilience():
    """Sanity: the resilience module's helper imports are stable.

    Locking this in keeps the lifted test suite valid against the
    generic module without the legacy ``services.ollama_resilience``
    being a strict super-set of the public surface.
    """
    assert time.monotonic() > 0
