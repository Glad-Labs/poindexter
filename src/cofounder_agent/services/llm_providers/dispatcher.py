"""LLMProvider dispatcher — resolve a provider instance at call time.

Phase J follow-up (GitHub #72). Callers that want the
swap-provider-by-config capability use this dispatcher instead of
importing ``services/ollama_client.py`` directly.

## Usage

.. code:: python

    from services.llm_providers.dispatcher import get_provider

    provider = await get_provider(pool, tier="standard")
    result = await provider.complete(
        messages=[{"role": "user", "content": "hi"}],
        model="gemma3:27b",
        _provider_config=provider_config,
    )

## Config resolution

The active provider per cost tier lives in ``app_settings``:

- ``plugin.llm_provider.primary.standard`` — which provider name to
  use for "standard" tier (default: ``ollama_native``)
- ``plugin.llm_provider.primary.budget`` — etc.
- ``plugin.llm_provider.primary.free`` — etc.

Per-provider config (e.g. ``base_url`` for OpenAICompatProvider) lives
under that provider's own PluginConfig key:
``plugin.llm_provider.openai_compat``.

## Swap test

This dispatcher is the mechanism behind the Phase J exit criterion:
*"swap Ollama → vllm/llama.cpp by one app_settings row"*. Customer
flow:

1. ``UPDATE app_settings SET value='{"enabled":true,"config":{
     "base_url":"http://localhost:8080/v1"}}' WHERE
     key='plugin.llm_provider.openai_compat'`` — configure the
     OpenAI-compat provider to point at their local vllm.
2. ``UPDATE app_settings SET value='openai_compat' WHERE
     key='plugin.llm_provider.primary.standard'`` — flip the standard
     tier to use it.
3. Next pipeline run dispatches through vllm. Zero code edits.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.config import PluginConfig
from plugins.registry import get_all_llm_providers

logger = logging.getLogger(__name__)

# OpenTelemetry is optional — the dispatcher works with or without it.
# When the opentelemetry SDK isn't installed, _tracer is a no-op
# implementation that matches the real API's ``start_as_current_span``
# contract.
try:
    from opentelemetry import trace as _otel_trace  # type: ignore[import-untyped]

    _tracer = _otel_trace.get_tracer("poindexter.llm_providers")
except ImportError:  # pragma: no cover - exercised in minimal dev envs
    from contextlib import contextmanager

    @contextmanager
    def _noop_span(_name: str, **_kwargs: Any):
        class _NoopSpan:
            def set_attribute(self, *_a: Any, **_k: Any) -> None:
                pass

            def record_exception(self, *_a: Any, **_k: Any) -> None:
                pass

            def set_status(self, *_a: Any, **_k: Any) -> None:
                pass

        yield _NoopSpan()

    class _NoopTracer:
        start_as_current_span = staticmethod(_noop_span)

    _tracer = _NoopTracer()


# Default provider per tier if no app_settings override exists.
_DEFAULT_PROVIDER_PER_TIER = {
    "free": "ollama_native",
    "budget": "ollama_native",
    "standard": "ollama_native",
    "premium": "ollama_native",
    "flagship": "ollama_native",
}


async def get_provider_name(pool: Any, tier: str) -> str:
    """Return the provider name configured for this tier.

    Reads ``plugin.llm_provider.primary.<tier>`` from app_settings.
    Falls back to ``ollama_native`` if the row is missing.
    """
    try:
        async with pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = $1",
                f"plugin.llm_provider.primary.{tier}",
            )
    except Exception as e:
        logger.warning(
            "dispatcher: could not read primary provider for tier %r: %s", tier, e,
        )
        val = None

    if val and val.strip():
        return val.strip()
    return _DEFAULT_PROVIDER_PER_TIER.get(tier, "ollama_native")


async def get_provider(pool: Any, tier: str = "standard") -> Any:
    """Return the LLMProvider instance for this tier.

    Looks up the provider name from config, then finds it in the
    registry. Raises if the configured provider isn't registered —
    callers can catch that + fall back to ollama_native.
    """
    with _tracer.start_as_current_span("llm.get_provider") as span:
        span.set_attribute("llm.tier", tier)
        name = await get_provider_name(pool, tier)
        span.set_attribute("llm.provider.requested", name)
        providers = {p.name: p for p in get_all_llm_providers()}
        if name not in providers:
            span.set_attribute("llm.provider.fallback", True)
            logger.warning(
                "dispatcher: configured provider %r not found in registry "
                "(available: %s); falling back to ollama_native",
                name, sorted(providers.keys()),
            )
            if "ollama_native" in providers:
                span.set_attribute("llm.provider.resolved", "ollama_native")
                return providers["ollama_native"]
            raise RuntimeError(
                f"No LLMProvider named {name!r}, and ollama_native fallback "
                "also not registered. Check entry_points config."
            )
        span.set_attribute("llm.provider.resolved", name)
        return providers[name]


async def get_provider_config(pool: Any, provider_name: str) -> dict[str, Any]:
    """Fetch ``plugin.llm_provider.<name>.config`` from app_settings."""
    cfg = await PluginConfig.load(pool, "llm_provider", provider_name)
    return cfg.config


async def dispatch_complete(
    pool: Any,
    messages: list[dict[str, str]],
    model: str,
    tier: str = "standard",
    **kwargs: Any,
) -> Any:
    """One-shot ``complete`` call using the tier's configured provider.

    Convenience wrapper that combines ``get_provider`` +
    ``get_provider_config`` + ``provider.complete``. New call sites
    should use this instead of importing OllamaClient directly.
    """
    with _tracer.start_as_current_span("llm.dispatch_complete") as span:
        span.set_attribute("llm.tier", tier)
        span.set_attribute("llm.model", model)
        span.set_attribute("llm.messages.count", len(messages))
        try:
            provider = await get_provider(pool, tier)
            span.set_attribute("llm.provider.name", provider.name)
            provider_config = await get_provider_config(pool, provider.name)
            kwargs.setdefault("_provider_config", provider_config)
            result = await provider.complete(messages=messages, model=model, **kwargs)
            # Completion has .prompt_tokens / .completion_tokens when the
            # provider populates them; safe getattr for non-standard shapes.
            pt = getattr(result, "prompt_tokens", 0)
            ct = getattr(result, "completion_tokens", 0)
            span.set_attribute("llm.tokens.prompt", int(pt or 0))
            span.set_attribute("llm.tokens.completion", int(ct or 0))
            finish = getattr(result, "finish_reason", "")
            if finish:
                span.set_attribute("llm.finish_reason", finish)
            return result
        except Exception as exc:
            span.record_exception(exc)
            raise


async def dispatch_embed(
    pool: Any,
    text: str,
    model: str,
    tier: str = "free",
) -> list[float]:
    """One-shot ``embed`` call. Embeddings default to 'free' tier since
    nomic-embed-text is the canonical local model across the stack.
    """
    with _tracer.start_as_current_span("llm.dispatch_embed") as span:
        span.set_attribute("llm.tier", tier)
        span.set_attribute("llm.model", model)
        span.set_attribute("llm.text.chars", len(text))
        try:
            provider = await get_provider(pool, tier)
            span.set_attribute("llm.provider.name", provider.name)
            return await provider.embed(text=text, model=model)
        except Exception as exc:
            span.record_exception(exc)
            raise
