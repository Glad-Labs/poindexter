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
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services import ollama_client as ollama_client_module
from services.ollama_client import (
    OllamaClient,
    OllamaError,
    OllamaModelNotFoundError,
    _get_int_setting,
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


# ---------------------------------------------------------------------------
# Thinking-model salvage path
# ---------------------------------------------------------------------------


def _make_resp(data: dict) -> MagicMock:
    """Mock httpx.Response with .json() preloaded and .raise_for_status() no-op."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


class TestThinkingModelSalvage:
    """qwen3 / glm-4.7 / other thinking models split output into
    ``message.content`` (final answer) and ``message.thinking`` (reasoning
    trace). If ``num_predict`` is too small, the thinking phase consumes
    the budget and content comes back empty. ollama_client.generate()
    salvages the last non-empty thinking line as a best-effort fallback —
    this codepath had no coverage before."""

    @pytest.mark.asyncio
    async def test_empty_content_salvages_last_thinking_line(self, client):
        """Empty content + non-empty thinking → last non-empty thinking
        line is promoted into ``text``/``response``."""
        thinking = (
            "Step 1: consider the question.\n"
            "Step 2: weigh options.\n"
            "\n"
            "Therefore the answer is 42."
        )
        client.client.post = AsyncMock(return_value=_make_resp({
            "message": {"role": "assistant", "content": "", "thinking": thinking},
            "eval_count": 100,
            "prompt_eval_count": 10,
            "total_duration": 1_000_000_000,
            "done": True,
        }))
        # Skip the auto-resolve roundtrip
        with patch.object(client, "resolve_model", AsyncMock(return_value="qwen3:8b")):
            result = await client.generate("test prompt", max_tokens=50)

        assert result["text"] == "Therefore the answer is 42."
        assert result["response"] == "Therefore the answer is 42."

    @pytest.mark.asyncio
    async def test_empty_content_and_empty_thinking_returns_empty_text(self, client):
        """No content AND no thinking → ``text`` stays empty (no salvage
        path triggered). Pins the negative branch of the salvage logic so
        a future refactor can't accidentally salvage from nowhere."""
        client.client.post = AsyncMock(return_value=_make_resp({
            "message": {"role": "assistant", "content": "", "thinking": ""},
            "eval_count": 0,
            "prompt_eval_count": 5,
            "total_duration": 500_000_000,
            "done": True,
        }))
        with patch.object(client, "resolve_model", AsyncMock(return_value="qwen3:8b")):
            result = await client.generate("test prompt")

        assert result["text"] == ""
        assert result["response"] == ""

    @pytest.mark.asyncio
    async def test_null_content_coalesces_to_empty_string(self, client):
        """Ollama occasionally returns ``"content": null`` (not missing)
        on thinking-model empty-response failures. The ``or ""``
        coalescing in generate() must convert that to an empty string so
        downstream callers can treat ``text`` as always-a-string."""
        client.client.post = AsyncMock(return_value=_make_resp({
            # Note: content is JSON null → Python None after .json()
            "message": {"role": "assistant", "content": None},
            "eval_count": 0,
            "prompt_eval_count": 8,
            "total_duration": 200_000_000,
            "done": True,
        }))
        with patch.object(client, "resolve_model", AsyncMock(return_value="qwen3:8b")):
            result = await client.generate("test prompt")

        assert result["text"] == ""
        assert isinstance(result["text"], str)


# ---------------------------------------------------------------------------
# stream_generate — entirely uncovered surface
# ---------------------------------------------------------------------------


class _FakeStreamResponse:
    """Mimics the async-context-manager + aiter_lines protocol that
    ``httpx.AsyncClient.stream(...)`` returns."""

    def __init__(self, lines: list[str]):
        self._lines = lines

    async def __aenter__(self) -> "_FakeStreamResponse":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class TestStreamGenerate:
    """``stream_generate`` had zero coverage. It's used by the voice
    agent's incremental TTS path, so silent regressions would only
    surface in production."""

    @pytest.mark.asyncio
    async def test_yields_content_chunks_in_order(self, client):
        import json as _json
        lines = [
            _json.dumps({"message": {"content": "Hello"}}),
            _json.dumps({"message": {"content": " world"}}),
            _json.dumps({"message": {"content": "!"}}),
            _json.dumps({"message": {"content": ""}, "done": True}),
        ]
        client.client.stream = MagicMock(return_value=_FakeStreamResponse(lines))

        chunks = [chunk async for chunk in client.stream_generate("hi")]
        assert chunks == ["Hello", " world", "!"]

    @pytest.mark.asyncio
    async def test_skips_blank_lines_and_malformed_json(self, client):
        """Ollama's NDJSON stream occasionally emits blank keepalive
        lines or a partial line on disconnect; stream_generate must
        tolerate both without raising or yielding garbage."""
        lines = [
            "",  # blank
            "not-valid-json",  # JSONDecodeError → continue
            '{"message": {"content": "ok"}}',
            '{"message": {}}',  # well-formed but no content → skip
        ]
        client.client.stream = MagicMock(return_value=_FakeStreamResponse(lines))

        chunks = [chunk async for chunk in client.stream_generate("hi")]
        assert chunks == ["ok"]


# ---------------------------------------------------------------------------
# _get_int_setting — config robustness helper
# ---------------------------------------------------------------------------


class TestGetIntSetting:
    """Wraps invalid app_settings values in a warning log instead of
    raising, so a typo in the config can't silently take down the worker.
    Indirectly exercised via the concurrency limiter, but the helper has
    its own branches worth pinning."""

    def test_invalid_value_falls_back_to_default(self):
        with patch.object(
            ollama_client_module, "_sc_get",
            side_effect=lambda key, default="": (
                "not-a-number" if key == "fake_key" else default
            ),
        ):
            assert _get_int_setting("fake_key", 42) == 42

    def test_zero_or_negative_falls_back_to_default(self):
        """``value > 0 else default`` — operator-set 0 or negative
        values are treated as misconfiguration, not "disable feature"."""
        with patch.object(
            ollama_client_module, "_sc_get",
            side_effect=lambda key, default="": (
                "0" if key == "zero_key"
                else "-5" if key == "neg_key"
                else default
            ),
        ):
            assert _get_int_setting("zero_key", 7) == 7
            assert _get_int_setting("neg_key", 7) == 7

    def test_empty_string_returns_default(self):
        """Unset key (empty string from _sc_get) short-circuits before
        the int() call — covers the ``if not raw`` branch."""
        with patch.object(
            ollama_client_module, "_sc_get",
            side_effect=lambda key, default="": "",
        ):
            assert _get_int_setting("missing_key", 99) == 99


# ---------------------------------------------------------------------------
# recommend_model — fallback when configured model isn't installed
# ---------------------------------------------------------------------------


class TestRecommendModelConfiguredMissing:
    """If ``self.model`` is set to something that isn't actually
    installed (e.g. operator typo'd app_settings ``default_ollama_model``)
    AND the task isn't code/complex, recommend_model falls through to
    the largest-installed-model branch instead of returning the missing
    configured name. Covers the final ``sorted_by_size[0]`` return."""

    @pytest.mark.asyncio
    async def test_returns_largest_when_configured_model_not_in_profiles(self):
        c = OllamaClient(model="phantom:99b")  # not installed
        c._model_cache = {
            "small:3b": {"parameter_size": "3B"},
            "large:35b": {"parameter_size": "35B"},
            "medium:8b": {"parameter_size": "8B"},
        }
        c._cache_ts = time.time()  # Prevent cache refresh from hitting Ollama

        # "write a blog post" matches neither code nor complex keywords —
        # falls through to the default branch.
        result = await c.recommend_model("write a blog post")
        assert result == "large:35b"
