"""LiteLLMProvider — LiteLLM-backed LLMProvider plugin.

Per the OSS audit: hand-rolled cost-tier routing + retries + fallbacks
+ provider health + cost tracking duplicate what LiteLLM already
provides as mature OSS. This provider plugin gives the dispatcher a
LiteLLM-backed option so operators can:

- Route through LiteLLM's provider abstraction (works against Ollama,
  OpenAI, Anthropic, Gemini, vLLM, llama.cpp, OpenRouter, Bedrock,
  Vertex — anything LiteLLM speaks) with one app_settings flip.
- Get authoritative cost tracking from LiteLLM's MODEL_COSTS table
  instead of the drift-prone hand-rolled services/model_constants.py.
- Keep retries + fallbacks declarative via LiteLLM's Router config
  rather than the per-provider failure counter in services/model_router.py.

Per ``feedback_no_paid_apis``: LiteLLM speaks Ollama natively; the
default install routes to local models. Cloud providers stay opt-in
behind the existing cost_guard.

Config (``plugin.llm_provider.litellm`` in app_settings):

- ``api_base`` (default: ``http://localhost:11434`` for the Ollama
  case; LiteLLM treats this as the base for ``ollama/<model>`` calls).
- ``timeout_seconds`` (default 120) — per-call default; per-call
  overrides via the ``timeout_s`` kwarg still win.
- ``drop_params`` (default true) — strip params the target backend
  doesn't recognize so a single call signature works across backends.

Per ``feedback_design_for_llm_consumers``: this provider name flows
through the dispatcher's structured logging so future LLM operators
reading capability_outcomes see "model_used=litellm:ollama/glm-4.7-5090"
and can ground decisions on that.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from plugins.llm_provider import Completion, Token

logger = logging.getLogger(__name__)


class LiteLLMProvider:
    """LLMProvider implementation backed by LiteLLM.

    Delegates to ``litellm.acompletion`` for chat + ``litellm.aembedding``
    for embeddings. The LiteLLM library normalizes provider quirks (each
    backend's auth, base url, response shape, finish-reason vocabulary)
    into the OpenAI shape we already expect — so this provider stays
    thin.

    Model strings follow LiteLLM's namespacing convention:

    - ``ollama/glm-4.7-5090:latest`` — local Ollama
    - ``openai/gpt-4o-mini`` — OpenAI direct
    - ``anthropic/claude-haiku-4-5`` — Anthropic direct
    - ``vertex_ai/gemini-2.0-flash`` — Vertex AI
    - ``openrouter/anthropic/claude-haiku-4-5`` — via OpenRouter
    - ``http://host:port/v1`` (with custom api_base) — any
      OpenAI-compat backend.

    Callers that pass bare model names ("gemma3:27b") get the configured
    default provider prefix appended ("ollama/gemma3:27b") so existing
    code keeps working without churn.
    """

    name = "litellm"
    supports_streaming = True
    supports_embeddings = True

    def __init__(self) -> None:
        self._configured = False
        self._default_prefix = "ollama/"
        self._api_base: str | None = None
        self._timeout = 120.0
        self._drop_params = True

    def _configure_from(self, provider_config: dict[str, Any]) -> None:
        """Apply per-call provider config from PluginConfig (dispatcher
        injects this via the ``_provider_config`` kwarg).

        Mutating instance state on every call is fine — config rarely
        changes within a single process and the cost is one dict lookup.
        Idempotent.
        """
        self._api_base = provider_config.get("api_base") or self._api_base
        self._timeout = float(provider_config.get("timeout_seconds", self._timeout))
        self._drop_params = bool(
            provider_config.get("drop_params", self._drop_params)
        )
        prefix = provider_config.get("default_prefix")
        if prefix:
            self._default_prefix = prefix
        if not self._configured:
            self._apply_global_litellm_config()
            self._configured = True

    def _apply_global_litellm_config(self) -> None:
        """Wire LiteLLM's process-wide knobs once.

        ``set_verbose=False`` keeps litellm out of our logs unless the
        operator opts in. ``drop_params`` lets one call signature work
        against backends with different param vocabularies. The Ollama
        default api_base is what every other Ollama caller uses.
        """
        try:
            import litellm
            litellm.set_verbose = False  # noqa: SLF001 — public surface
            litellm.drop_params = self._drop_params
            if self._api_base:
                litellm.api_base = self._api_base
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[litellm_provider] global config apply failed: %s", exc,
            )

    def _resolve_model(self, model: str) -> str:
        """Apply the default provider prefix when the caller passed a
        bare model name. ``ollama/gemma3:27b`` stays as-is;
        ``gemma3:27b`` becomes ``ollama/gemma3:27b``.
        """
        if "/" in model and not model.startswith("http"):
            return model
        if model.startswith("http"):
            return model
        return f"{self._default_prefix.rstrip('/')}/{model}"

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> Completion:
        provider_config = kwargs.pop("_provider_config", {}) or {}
        self._configure_from(provider_config)

        import litellm

        resolved_model = self._resolve_model(model)
        timeout = float(kwargs.pop("timeout_s", self._timeout))
        completion_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "timeout": timeout,
            "stream": False,
        }
        if self._api_base and not resolved_model.startswith("http"):
            completion_kwargs["api_base"] = self._api_base
        for key in ("temperature", "max_tokens", "top_p"):
            if key in kwargs:
                completion_kwargs[key] = kwargs[key]

        logger.debug(
            "[litellm_provider] complete: model=%s timeout=%s",
            resolved_model, timeout,
        )

        try:
            response = await litellm.acompletion(**completion_kwargs)
        except Exception as exc:
            logger.exception(
                "[litellm_provider] acompletion failed for model=%s: %s",
                resolved_model, exc,
            )
            raise

        # LiteLLM normalizes responses to OpenAI shape — same fields
        # whether the backend is Ollama, OpenAI, Anthropic, etc.
        choice = response.choices[0] if response.choices else None
        text = ""
        finish_reason = ""
        if choice is not None:
            msg = getattr(choice, "message", None)
            text = (getattr(msg, "content", None) or "") if msg else ""
            finish_reason = getattr(choice, "finish_reason", "") or ""

        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        completion_tokens = (
            int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        )
        total_tokens = int(getattr(usage, "total_tokens", 0) or 0) if usage else 0

        # LiteLLM stamps response_cost on the response when its cost
        # table knows the model. Surface that in raw so cost_logs can
        # consume it without re-deriving the price.
        raw: dict[str, Any] = {}
        try:
            raw = (
                response.model_dump() if hasattr(response, "model_dump")
                else dict(response) if isinstance(response, dict) else {}
            )
        except Exception:  # noqa: BLE001
            raw = {}
        if hasattr(response, "_response_ms"):
            raw["_response_ms"] = response._response_ms  # noqa: SLF001
        if hasattr(response, "response_cost"):
            raw["response_cost"] = response.response_cost

        return Completion(
            text=text,
            model=resolved_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            raw=raw,
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> AsyncIterator[Token]:
        provider_config = kwargs.pop("_provider_config", {}) or {}
        self._configure_from(provider_config)

        import litellm

        resolved_model = self._resolve_model(model)
        timeout = float(kwargs.pop("timeout_s", self._timeout))
        completion_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "timeout": timeout,
            "stream": True,
        }
        if self._api_base and not resolved_model.startswith("http"):
            completion_kwargs["api_base"] = self._api_base
        for key in ("temperature", "max_tokens", "top_p"):
            if key in kwargs:
                completion_kwargs[key] = kwargs[key]

        response = await litellm.acompletion(**completion_kwargs)
        async for chunk in response:
            choice = chunk.choices[0] if getattr(chunk, "choices", None) else None
            if choice is None:
                continue
            delta = getattr(choice, "delta", None)
            text = (getattr(delta, "content", None) or "") if delta else ""
            finish_reason = getattr(choice, "finish_reason", None)
            yield Token(text=text, finish_reason=finish_reason)

    async def embed(self, text: str, model: str) -> list[float]:
        import litellm

        # LiteLLM's embedding API takes the same model namespace as
        # acompletion — "ollama/nomic-embed-text" routes to local Ollama.
        resolved_model = self._resolve_model(model)
        response = await litellm.aembedding(
            model=resolved_model,
            input=[text],
            timeout=self._timeout,
        )
        data = response.data if hasattr(response, "data") else response.get("data", [])
        if not data:
            return []
        embedding = data[0]
        if hasattr(embedding, "embedding"):
            return list(embedding.embedding)
        if isinstance(embedding, dict):
            return list(embedding.get("embedding", []))
        return list(embedding)


__all__ = ["LiteLLMProvider"]
