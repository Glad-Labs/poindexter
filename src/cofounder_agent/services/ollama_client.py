"""
Ollama Client Service

Provides local AI model inference using Ollama.
NOT zero-cost — GPU inference uses electricity.

Cost is calculated as:
  (gpu_power_watts / 1000) * (duration_seconds / 3600) * electricity_rate_per_kwh

Defaults: 300W typical inference draw, $0.12/kWh.
The electricity rate is configurable via app_settings key "electricity_rate_kwh".

Install Ollama: https://ollama.ai/download
Pull models: ollama pull qwen3:8b
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from aiolimiter import AsyncLimiter
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from services.logger_config import get_logger
from services.site_config import SiteConfig

logger = get_logger(__name__)


# ============================================================================
# CONSTANTS
# ============================================================================

# Lifespan-bound SiteConfig; main.py wires this via set_site_config().
# Falls back to a fresh env-fallback instance when unset.
_site_config: SiteConfig | None = None


def set_site_config(sc: SiteConfig) -> None:
    """Wire the lifespan-bound SiteConfig instance for this module."""
    global _site_config
    _site_config = sc


def _sc() -> SiteConfig:
    """Return the wired SiteConfig, or a fresh env-fallback instance."""
    return _site_config if _site_config is not None else SiteConfig()


def _sc_get(key: str, default: str = "") -> str:
    """Get from site_config (falls back to env automatically)."""
    return _sc().get(key, default)

# All config below is resolved lazily via _default_*() helpers because
# site_config is empty at module-import time (loaded later in the lifespan).
# A cached module-level read would freeze env-fallback defaults and silently
# ignore any app_settings overrides set after first import.


def _default_model() -> str:
    return _sc_get("default_ollama_model", "auto")


def _default_base_url() -> str:
    return (
        _sc_get("ollama_base_url")
        or _sc_get("ollama_host")
        or "http://host.docker.internal:11434"
    )


def _default_gpu_power_watts() -> float:
    """GPU electricity cost default (RTX 5090: 575W TDP, ~300W typical inference)."""
    try:
        return float(_sc_get("gpu_inference_watts", "300"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"Invalid app_settings value for gpu_inference_watts: {exc}"
        ) from exc


def _default_electricity_rate_kwh() -> float:
    try:
        return float(_sc_get("electricity_rate_kwh", "0.12"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"Invalid app_settings value for electricity_rate_kwh: {exc}"
        ) from exc


def _default_num_ctx() -> int:
    """Context window limit — prevents models from allocating massive KV caches.
    Default 8192 is plenty for article generation and saves ~15GB VRAM vs 65K context."""
    try:
        return int(_sc_get("ollama_num_ctx", "8192"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"Invalid app_settings value for ollama_num_ctx: {exc}"
        ) from exc


def _get_int_setting(key: str, default: int) -> int:
    """Read a positive int from app_settings, falling back to ``default`` on
    missing / invalid values. Wraps bad config in a warning log instead of
    raising so a typo in app_settings can't silently take down the worker.
    """
    raw = _sc_get(key, "")
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except (ValueError, TypeError):
        logger.warning(
            "Invalid app_settings value for %s=%r — using default %d",
            key, raw, default,
        )
        return default


# ============================================================================
# CONCURRENCY LIMITER
# ============================================================================
# An aiolimiter.AsyncLimiter caps the number of concurrent in-flight requests
# this client process makes against the local Ollama server. Without it, a
# burst of pipeline tasks (or a forked stage that fans out) can pile work on
# the GPU faster than it can drain — leading to VRAM thrash, OOM kills, and
# the silent-fallback failure mode where the writer model gets evicted and
# the next call lands on a smaller model.
#
# The limit is read from app_settings (`ollama_concurrency_limit`) on first
# call. Tenacity's stop/wait config is decorator-level and frozen at module
# import; the concurrency limiter is rebuildable at runtime via
# ``rebuild_concurrency_limiter()`` for operators who want to tune live
# without restarting the worker.

_CONCURRENCY_DEFAULT = 10
_concurrency_limiter: AsyncLimiter | None = None


def _get_concurrency_limiter() -> AsyncLimiter:
    """Lazy-init the module-level concurrency limiter.

    Resolved on first use because site_config is empty at module-import
    time — reading it here lets the operator change
    ``ollama_concurrency_limit`` in app_settings before the worker has
    made its first Ollama call without needing a restart.
    """
    global _concurrency_limiter
    if _concurrency_limiter is None:
        limit = _get_int_setting("ollama_concurrency_limit", _CONCURRENCY_DEFAULT)
        # AsyncLimiter is a leaky-bucket rate limiter. With time_period=0
        # (or any small interval) and max_rate=N, it caps in-flight
        # entries to N. We want a true semaphore-style cap, so use a
        # 1-second window — sustainably allows N parallel calls.
        _concurrency_limiter = AsyncLimiter(max_rate=limit, time_period=1.0)
        logger.info(
            "Ollama concurrency limiter initialised",
            ollama_concurrency_limit=limit,
        )
    return _concurrency_limiter


def rebuild_concurrency_limiter() -> AsyncLimiter:
    """Force a re-read of ``ollama_concurrency_limit`` and rebuild the
    limiter. Call from an admin endpoint / settings-changed hook to apply
    a new concurrency cap without restarting the worker. In-flight
    requests against the old limiter complete normally; new requests go
    through the new one.
    """
    global _concurrency_limiter
    _concurrency_limiter = None
    return _get_concurrency_limiter()


def _log_retry_attempt(retry_state: Any) -> None:
    """Tenacity ``before_sleep`` hook — log every retry attempt as a
    structured warning. Uses our structlog wrapper so the event lands
    in the same audit pipeline as the rest of the worker logs.
    """
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    sleep_for = getattr(retry_state.next_action, "sleep", 0.0)
    logger.warning(
        "[generate_with_retry] Attempt %d failed, retrying in %.2fs",
        retry_state.attempt_number,
        sleep_for,
        error=str(exc) if exc else "unknown",
    )


# ============================================================================
# EXCEPTIONS
# ============================================================================


def calculate_electricity_cost(
    duration_seconds: float,
    gpu_power_watts: float | None = None,
    electricity_rate_kwh: float | None = None,
) -> float:
    """Calculate electricity cost for a GPU inference call.

    Formula: (watts / 1000) * (seconds / 3600) * rate_per_kwh

    Args:
        duration_seconds: Wall-clock time of the inference call.
        gpu_power_watts: GPU power draw in watts (default 300W typical inference).
        electricity_rate_kwh: Electricity price in USD/kWh (default $0.12).

    Returns:
        Cost in USD (typically fractions of a cent).
    """
    if duration_seconds <= 0:
        return 0.0
    watts = gpu_power_watts if gpu_power_watts is not None else _default_gpu_power_watts()
    rate = electricity_rate_kwh if electricity_rate_kwh is not None else _default_electricity_rate_kwh()
    kwh = (watts / 1000.0) * (duration_seconds / 3600.0)
    return round(kwh * rate, 8)


class OllamaError(Exception):
    """Base exception for Ollama errors."""


class OllamaConnectionError(OllamaError):
    """Raised when cannot connect to Ollama server."""


class OllamaModelNotFoundError(OllamaError):
    """Raised when requested model is not available."""


class OllamaClient:
    """
    Client for Ollama local LLM inference.

    Zero-cost alternative to OpenAI/Claude for local environments.
    Model profiles are discovered dynamically from the Ollama server.
    """

    def __init__(self, base_url: str | None = None, model: str | None = None, timeout: int | None = None):
        self.base_url = (base_url or _default_base_url()).rstrip("/")
        self.model = model or _default_model()
        # Default timeout is high (600s) because a 70B+ writer model can
        # easily take 3-10 minutes to generate a long blog post on a
        # single GPU. The old default of 120s silently dropped big-model
        # calls to httpx.ReadTimeout and the ai_content_generator
        # fallback chain would switch to a smaller model — users thought
        # they were writing with qwen2.5:72b but were actually on
        # gemma3:27b the whole time. Callers can still override for
        # short-timeout use cases (health checks, quick list calls).
        if timeout is None:
            timeout = int(_sc_get("ollama_client_timeout_seconds", "600") or 600)
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)
        self._model_cache: dict[str, dict[str, Any]] = {}
        self._cache_ts: float = 0
        self._resolved_default: str | None = None  # Lazily resolved from installed models

        # Electricity cost parameters — updated at runtime via configure_electricity()
        self._gpu_power_watts: float = _default_gpu_power_watts()
        self._electricity_rate_kwh: float = _default_electricity_rate_kwh()

        logger.info("Ollama client initialized", base_url=self.base_url, model=self.model)

    def configure_electricity(
        self,
        gpu_power_watts: float | None = None,
        electricity_rate_kwh: float | None = None,
    ) -> None:
        """Update electricity cost parameters (call after reading app_settings)."""
        if gpu_power_watts is not None:
            self._gpu_power_watts = gpu_power_watts
        if electricity_rate_kwh is not None:
            self._electricity_rate_kwh = electricity_rate_kwh
        logger.info(
            "Ollama electricity config updated",
            gpu_power_watts=self._gpu_power_watts,
            electricity_rate_kwh=self._electricity_rate_kwh,
        )

    async def resolve_model(self, model: str | None = None) -> str:
        """Resolve a model name, handling 'auto' by discovering the best installed model.

        Resolution order:
        1. Explicit model name (not 'auto') — use as-is
        2. Cached resolved default — skip discovery on repeat calls
        3. PREFERRED_OLLAMA_MODEL env var — user-declared best model
        4. First installed match from a quality-ranked preference list
        5. Largest non-embedding model by file size

        Set PREFERRED_OLLAMA_MODEL in the environment (or via app_settings) to pin your best model.
        """
        model = model or self.model
        if model != "auto":
            return model

        if self._resolved_default:
            return self._resolved_default

        try:
            models = await self.list_models()
            installed_names = {m.get("name", "") for m in models}

            # Check config first — user knows which model is best for their hardware
            preferred = _sc_get("preferred_ollama_model", "")
            if preferred and preferred in installed_names:
                self._resolved_default = preferred
                logger.info("Auto-resolved model from PREFERRED_OLLAMA_MODEL: %s", preferred)
                return self._resolved_default

            # Filter out embedding models
            gen_models = [m for m in models if "embed" not in m.get("name", "").lower()]

            if gen_models:
                # Pick largest by file size as a reasonable default
                best = sorted(gen_models, key=lambda x: x.get("size", 0), reverse=True)[0]
                self._resolved_default = best["name"]
                logger.info("Auto-resolved default model (largest): %s", self._resolved_default)
                return self._resolved_default
        except Exception as e:
            logger.warning("Could not auto-resolve model: %s", e)

        # Absolute last resort — caller should handle this model not existing
        return "llama3:latest"

    async def close(self):
        """Close the HTTP client connection."""
        if self.client:
            await self.client.aclose()

    # ========================================================================
    # Health & Discovery
    # ========================================================================

    async def check_health(self) -> bool:
        """Check if Ollama server is running.

        Returns False on any failure. Callers handle False by falling back
        to other providers (HF, cloud), so an unreachable Ollama is a normal
        state — not an error. Logs at DEBUG instead of ERROR to avoid
        spamming production logs when Ollama is intentionally off.
        """
        try:
            response = await self.client.get(f"{self.base_url}/api/tags", timeout=5.0)
            return response.status_code == 200
        except Exception as e:
            logger.debug("[check_health] Ollama unreachable at %s: %s", self.base_url, e)
            return False

    async def list_models(self) -> list[dict[str, Any]]:
        """List available Ollama models with metadata."""
        try:
            response = await self.client.get(f"{self.base_url}/api/tags", timeout=10.0)
            response.raise_for_status()
            data = response.json()
            models = data.get("models", [])
            logger.info("Found %d Ollama models", len(models))
            return models
        except Exception as e:
            logger.error("[list_models] Failed to list models", error=str(e), exc_info=True)
            return []

    async def get_model_profiles(self, force_refresh: bool = False) -> dict[str, dict[str, Any]]:
        """
        Build model profiles dynamically from the running Ollama instance.
        Cached for 5 minutes to avoid hammering the API.

        Returns:
            Dict mapping model name to profile info (size, family, quantization, etc.)
        """
        import time

        if not force_refresh and self._model_cache and (time.time() - self._cache_ts < 300):
            return self._model_cache

        models = await self.list_models()
        profiles: dict[str, dict[str, Any]] = {}

        for m in models:
            name = m.get("name", "")
            details = m.get("details", {})
            size_bytes = m.get("size", 0)
            size_gb = round(size_bytes / (1024**3), 1) if size_bytes else 0

            profiles[name] = {
                "name": name,
                "family": details.get("family", "unknown"),
                "parameter_size": details.get("parameter_size", "unknown"),
                "quantization": details.get("quantization_level", "unknown"),
                "size_gb": size_gb,
                "format": details.get("format", "unknown"),
                "modified_at": m.get("modified_at", ""),
                "cost": 0.0,
            }

        self._model_cache = profiles
        self._cache_ts = time.time()

        logger.info("Built %d model profiles from Ollama", len(profiles))
        return profiles

    def get_model_profile(self, model: str) -> dict[str, Any] | None:
        """Get cached model profile. Call get_model_profiles() first to populate."""
        return self._model_cache.get(model)

    async def recommend_model(self, task_type: str) -> str:
        """
        Recommend best available Ollama model for a task type.
        Uses the actually-installed models, not a hardcoded list.
        """
        profiles = await self.get_model_profiles()
        if not profiles:
            return self.model

        available = list(profiles.keys())
        task_lower = task_type.lower()

        # Prefer larger models for complex tasks
        def param_size_key(name: str) -> float:
            p = profiles[name].get("parameter_size", "0B")
            try:
                return float(p.replace("B", "").replace("b", ""))
            except (ValueError, AttributeError):
                return 0

        sorted_by_size = sorted(available, key=param_size_key, reverse=True)

        # Code tasks: prefer models with "code" or "coder" in the name
        if any(kw in task_lower for kw in ["code", "program", "debug", "implement"]):
            code_models = [m for m in sorted_by_size if "code" in m.lower()]
            if code_models:
                return code_models[0]

        # Complex reasoning: use the largest model
        if any(kw in task_lower for kw in ["complex", "reason", "analyze", "critical"]):
            return sorted_by_size[0] if sorted_by_size else self.model

        # Default: use the configured default model if available, else largest
        if self.model in profiles:
            return self.model
        return sorted_by_size[0] if sorted_by_size else self.model

    # ========================================================================
    # Generation
    # ========================================================================

    async def generate(
        self,
        prompt: str,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        stream: bool = False,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Generate completion from Ollama model.

        Internally uses /api/chat (the modern Ollama endpoint) by converting
        the prompt/system into chat messages. The legacy /api/generate endpoint
        was removed in newer Ollama versions. Return shape is preserved for
        backwards compatibility with callers that read 'text' and 'response'.

        ``timeout`` (seconds) overrides ``self.timeout`` for this single
        call only — lets callers (QA reviewers, writer self-review) set
        aggressive timeouts without constructing a whole new client.
        """
        model = await self.resolve_model(model)
        call_timeout = timeout if timeout is not None else self.timeout

        # Build chat messages from prompt + optional system
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "options": {"temperature": temperature, "num_ctx": _default_num_ctx()},
        }

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        try:
            async with _get_concurrency_limiter():
                response = await self.client.post(
                    f"{self.base_url}/api/chat", json=payload, timeout=call_timeout
                )
            response.raise_for_status()
            result = response.json()

            # Extract text from chat response format
            msg = result.get("message") or {}
            # Ollama occasionally returns "content": null (not missing)
            # on thinking-model empty-response failures. The .get default
            # only triggers when the key is absent, so we coalesce None
            # to "" here so every downstream caller can assume text is a
            # plain string and not have to defensively (result.get("text")
            # or "").
            text = msg.get("content") or ""
            # Thinking models (qwen3, qwen3.5, glm-4.7) split output into
            # `message.content` (final answer) and `message.thinking`
            # (reasoning trace). When num_predict is too small, the thinking
            # phase eats the entire budget and content comes back empty.
            _thinking = msg.get("thinking") or ""
            if not text and _thinking:
                logger.warning(
                    "Ollama thinking-model returned empty content with %d-char thinking trace — extracting last paragraph from thinking as fallback",
                    len(_thinking),
                )
                # Try to salvage the answer from the thinking trace.
                # Thinking models often reach their conclusion in the final
                # lines before the budget runs out.
                lines = [ln.strip() for ln in _thinking.strip().splitlines() if ln.strip()]
                if lines:
                    # Take the last non-empty line as the likely answer
                    text = lines[-1]
                    logger.info("Salvaged %d-char answer from thinking trace", len(text))

            duration_s = result.get("total_duration", 0) / 1e9
            electricity_cost = calculate_electricity_cost(
                duration_s,
                gpu_power_watts=self._gpu_power_watts,
                electricity_rate_kwh=self._electricity_rate_kwh,
            )

            logger.info(
                "Ollama generation complete",
                model=model,
                tokens=result.get("eval_count", 0),
                duration=duration_s,
                electricity_cost_usd=electricity_cost,
            )

            return {
                "text": text,
                "response": text,  # Legacy key for callers using response.get("response")
                "model": model,
                "tokens": result.get("eval_count", 0),
                "prompt_tokens": result.get("prompt_eval_count", 0),
                "total_tokens": result.get("eval_count", 0) + result.get("prompt_eval_count", 0),
                "duration_seconds": duration_s,
                "cost": electricity_cost,
                "done": result.get("done", False),
            }

        except httpx.HTTPError as e:
            logger.error("[generate] Ollama generation failed: %s", e, exc_info=True, model=model)
            raise

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """
        Chat completion using Ollama's native /api/chat endpoint.
        Supports full message history with roles.
        """
        model = await self.resolve_model(model)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": _default_num_ctx()},
        }

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        try:
            async with _get_concurrency_limiter():
                response = await self.client.post(
                    f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
                )
            response.raise_for_status()
            result = response.json()

            msg = result.get("message", {})

            duration_s = result.get("total_duration", 0) / 1e9
            electricity_cost = calculate_electricity_cost(
                duration_s,
                gpu_power_watts=self._gpu_power_watts,
                electricity_rate_kwh=self._electricity_rate_kwh,
            )

            logger.info(
                "Ollama chat complete",
                model=model,
                tokens=result.get("eval_count", 0),
                electricity_cost_usd=electricity_cost,
            )

            return {
                "role": msg.get("role", "assistant"),
                "content": msg.get("content", ""),
                "model": model,
                "tokens": result.get("eval_count", 0),
                "prompt_tokens": result.get("prompt_eval_count", 0),
                "total_tokens": result.get("eval_count", 0) + result.get("prompt_eval_count", 0),
                "duration_seconds": duration_s,
                "cost": electricity_cost,
                "done": result.get("done", False),
            }

        except httpx.HTTPError as e:
            logger.error("[chat] Ollama chat failed: %s", e, exc_info=True, model=model)
            raise

    # ========================================================================
    # Model Management
    # ========================================================================

    async def pull_model(self, model: str) -> bool:
        """Pull a model from the Ollama library."""
        try:
            logger.info("Pulling Ollama model: %s", model)
            response = await self.client.post(
                f"{self.base_url}/api/pull",
                json={"name": model},
                timeout=3600.0,
            )
            response.raise_for_status()
            logger.info("Successfully pulled model: %s", model)
            return True
        except Exception as e:
            logger.error("[pull_model] Failed to pull model %s", model, error=str(e), exc_info=True)
            return False

    # ========================================================================
    # Retry & Streaming
    # ========================================================================

    async def generate_with_retry(
        self,
        prompt: str,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        max_retries: int | None = None,
        base_delay: float | None = None,
    ) -> dict[str, Any]:
        """Generate completion with exponential-backoff retry on transient errors.

        Retry policy is owned by ``tenacity`` — only ``httpx.HTTPError``
        (network timeouts, connect errors, transient 5xx) and our own
        ``OllamaConnectionError`` are retried. Non-retryable failures
        (4xx, ValueError, programming bugs) propagate immediately.

        ``max_retries`` and ``base_delay`` accept legacy explicit values
        for backwards compatibility with existing callers (e.g.
        ``script_for_video.py``). When omitted, they fall back to
        ``app_settings`` keys ``ollama_max_retries`` (default 3) /
        ``ollama_retry_initial_seconds`` (default 1) so operators can
        tune the policy at runtime without redeploying.

        Trade-off: tenacity's stop/wait config is read here once per
        call (cheap), so app_settings changes apply on the next call —
        no worker restart needed for these values. The module-level
        concurrency limiter is rebuildable separately via
        ``rebuild_concurrency_limiter()``.
        """
        model = model or self.model
        attempts = (
            max_retries if max_retries is not None
            else _get_int_setting("ollama_max_retries", 3)
        )
        initial = (
            base_delay if base_delay is not None
            else float(_sc_get("ollama_retry_initial_seconds", "1") or 1)
        )
        max_wait = float(_sc_get("ollama_retry_max_seconds", "30") or 30)

        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type((httpx.HTTPError, OllamaConnectionError)),
                stop=stop_after_attempt(attempts),
                wait=wait_exponential_jitter(initial=initial, max=max_wait, jitter=2.0),
                before_sleep=_log_retry_attempt,
                reraise=True,
            ):
                with attempt:
                    result = await self.generate(
                        prompt=prompt,
                        model=model,
                        system=system,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        stream=False,
                    )
                    return result
        except RetryError as exc:
            # Defensive — reraise=True should surface the original exception,
            # but a RetryError wrapping a non-retryable check still surfaces
            # here. Unwrap so callers see the real cause.
            logger.error(
                "[generate_with_retry] All attempts exhausted",
                error=str(exc),
                max_retries=attempts,
                model=model,
            )
            raise exc.last_attempt.exception() from exc

        # AsyncRetrying with reraise=True always either returns or raises
        # — this fallback only fires if the loop never iterated (attempts<1).
        raise OllamaError("Generation failed: no attempts were made")

    async def stream_generate(
        self,
        prompt: str,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Stream generation from Ollama model using /api/chat."""
        model = model or self.model

        # Build chat messages from prompt + optional system
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature, "num_ctx": _default_num_ctx()},
        }

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        try:
            async with self.client.stream(
                "POST", f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            msg = data.get("message", {})
                            if msg.get("content"):
                                yield msg["content"]
                        except json.JSONDecodeError:
                            continue

        except httpx.HTTPError as e:
            logger.error(
                "[stream_generate] Ollama streaming failed: %s", e, exc_info=True, model=model
            )
            raise


    # ========================================================================
    # Embeddings
    # ========================================================================

    async def embed(self, text: str, model: str = "nomic-embed-text") -> list[float]:
        """Generate embedding vector for a single text.

        Args:
            text: The text to embed.
            model: Embedding model name (default: nomic-embed-text).

        Returns:
            Embedding vector as list of floats.
        """
        try:
            response = await self.client.post(
                f"{self.base_url}/api/embed",
                json={"model": model, "input": text},
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()

            embeddings = result.get("embeddings", [])
            if not embeddings:
                raise OllamaError("No embeddings returned from Ollama")

            logger.info(
                "Ollama embedding complete",
                model=model,
                dimensions=len(embeddings[0]),
            )
            return embeddings[0]

        except httpx.HTTPError as e:
            logger.error("[embed] Ollama embedding failed: %s", e, exc_info=True, model=model)
            raise

    async def embed_batch(
        self, texts: list[str], model: str = "nomic-embed-text"
    ) -> list[list[float]]:
        """Generate embedding vectors for multiple texts.

        Args:
            texts: List of texts to embed.
            model: Embedding model name (default: nomic-embed-text).

        Returns:
            List of embedding vectors.
        """
        try:
            response = await self.client.post(
                f"{self.base_url}/api/embed",
                json={"model": model, "input": texts},
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()

            embeddings = result.get("embeddings", [])
            if len(embeddings) != len(texts):
                raise OllamaError(
                    f"Expected {len(texts)} embeddings, got {len(embeddings)}"
                )

            logger.info(
                "Ollama batch embedding complete",
                model=model,
                count=len(embeddings),
                dimensions=len(embeddings[0]) if embeddings else 0,
            )
            return embeddings

        except httpx.HTTPError as e:
            logger.error(
                "[embed_batch] Ollama batch embedding failed: %s",
                e,
                exc_info=True,
                model=model,
            )
            raise


# Initialize function for easy integration
async def initialize_ollama_client(
    base_url: str | None = None, model: str | None = None
) -> OllamaClient:
    return OllamaClient(base_url=base_url, model=model)
