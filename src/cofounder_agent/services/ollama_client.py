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

import asyncio
import json
import os
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from services.logger_config import get_logger

logger = get_logger(__name__)


# ============================================================================
# CONSTANTS
# ============================================================================

def _sc_get(key: str, default: str = "") -> str:
    """Get from site_config with env fallback (lazy import to avoid circular deps)."""
    try:
        from services.site_config import site_config
        val = site_config.get(key)
        if val:
            return val
    except Exception:
        pass
    return os.getenv(key.upper(), default)

DEFAULT_MODEL = _sc_get("default_ollama_model", os.getenv("DEFAULT_OLLAMA_MODEL", "auto"))
DEFAULT_BASE_URL = (
    os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST") or "http://host.docker.internal:11434"
)

# GPU electricity cost defaults (RTX 5090: 575W TDP, ~300W typical inference)
DEFAULT_GPU_POWER_WATTS = float(_sc_get("gpu_inference_watts", os.getenv("GPU_POWER_WATTS", "300")))
DEFAULT_ELECTRICITY_RATE_KWH = float(_sc_get("electricity_rate_kwh", os.getenv("ELECTRICITY_RATE_KWH", "0.12")))

# Context window limit — prevents models from allocating massive KV caches.
# Default 8192 is plenty for article generation and saves ~15GB VRAM vs 65K context.
DEFAULT_NUM_CTX = int(_sc_get("ollama_num_ctx", os.getenv("OLLAMA_NUM_CTX", "8192")))


# ============================================================================
# EXCEPTIONS
# ============================================================================


def calculate_electricity_cost(
    duration_seconds: float,
    gpu_power_watts: float = DEFAULT_GPU_POWER_WATTS,
    electricity_rate_kwh: float = DEFAULT_ELECTRICITY_RATE_KWH,
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
    kwh = (gpu_power_watts / 1000.0) * (duration_seconds / 3600.0)
    return round(kwh * electricity_rate_kwh, 8)


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

    def __init__(self, base_url: str | None = None, model: str | None = None, timeout: int = 300):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)
        self._model_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ts: float = 0
        self._resolved_default: Optional[str] = None  # Lazily resolved from installed models

        # Electricity cost parameters — updated at runtime via configure_electricity()
        self._gpu_power_watts: float = DEFAULT_GPU_POWER_WATTS
        self._electricity_rate_kwh: float = DEFAULT_ELECTRICITY_RATE_KWH

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

    async def resolve_model(self, model: Optional[str] = None) -> str:
        """Resolve a model name, handling 'auto' by discovering the best installed model.

        Resolution order:
        1. Explicit model name (not 'auto') — use as-is
        2. Cached resolved default — skip discovery on repeat calls
        3. PREFERRED_OLLAMA_MODEL env var — user-declared best model
        4. First installed match from a quality-ranked preference list
        5. Largest non-embedding model by file size

        Set PREFERRED_OLLAMA_MODEL in .env.local to pin your best model.
        """
        model = model or self.model
        if model != "auto":
            return model

        if self._resolved_default:
            return self._resolved_default

        try:
            models = await self.list_models()
            installed_names = {m.get("name", "") for m in models}

            # Check env var first — user knows which model is best for their hardware
            preferred = os.getenv("PREFERRED_OLLAMA_MODEL", "")
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
        """Check if Ollama server is running."""
        try:
            response = await self.client.get(f"{self.base_url}/api/tags", timeout=5.0)
            return response.status_code == 200
        except Exception as e:
            logger.error("[check_health] Ollama health check failed: %s", e, exc_info=True)
            return False

    async def list_models(self) -> List[Dict[str, Any]]:
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

    async def get_model_profiles(self, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
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
        profiles: Dict[str, Dict[str, Any]] = {}

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

    def get_model_profile(self, model: str) -> Optional[Dict[str, Any]]:
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
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """Generate completion from Ollama model.

        Internally uses /api/chat (the modern Ollama endpoint) by converting
        the prompt/system into chat messages. The legacy /api/generate endpoint
        was removed in newer Ollama versions. Return shape is preserved for
        backwards compatibility with callers that read 'text' and 'response'.
        """
        model = await self.resolve_model(model)

        # Build chat messages from prompt + optional system
        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "options": {"temperature": temperature, "num_ctx": DEFAULT_NUM_CTX},
        }

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        try:
            response = await self.client.post(
                f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
            )
            response.raise_for_status()
            result = response.json()

            # Extract text from chat response format
            msg = result.get("message", {})
            text = msg.get("content", "")

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
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Chat completion using Ollama's native /api/chat endpoint.
        Supports full message history with roles.
        """
        model = await self.resolve_model(model)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": DEFAULT_NUM_CTX},
        }

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        try:
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
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> Dict[str, Any]:
        """Generate completion with exponential backoff retry."""
        model = model or self.model
        last_error = None

        for attempt in range(max_retries):
            try:
                result = await self.generate(
                    prompt=prompt,
                    model=model,
                    system=system,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=False,
                )
                if result and result.get("text"):
                    return result

            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                last_error = e
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        "[generate_with_retry] Attempt %d failed, retrying in %ss",
                        attempt + 1,
                        delay,
                        error=str(e),
                        model=model,
                    )
                    await asyncio.sleep(delay)

            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        "[generate_with_retry] Attempt %d failed, retrying in %ss",
                        attempt + 1,
                        delay,
                        error=str(e),
                        model=model,
                    )
                    await asyncio.sleep(delay)

        logger.error(
            "[generate_with_retry] All attempts exhausted",
            error=str(last_error),
            max_retries=max_retries,
            model=model,
            exc_info=True,
        )
        raise last_error if last_error else OllamaError("Generation failed after all retries")

    async def stream_generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """Stream generation from Ollama model using /api/chat."""
        model = model or self.model

        # Build chat messages from prompt + optional system
        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature, "num_ctx": DEFAULT_NUM_CTX},
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
