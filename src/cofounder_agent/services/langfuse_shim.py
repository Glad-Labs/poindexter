"""``langfuse_shim`` — single import surface for Langfuse tracing.

Centralises the try/except pattern that ``services/llm_text.py``
established (PR #385) so every Ollama-calling module wires through the
same shim instead of duplicating the import-and-no-op-fallback boilerplate.

## Why a shim and not a direct import?

Two reasons:

1. ``langfuse`` is declared as a runtime dep, but tests + the brain
   daemon (which has minimal deps) may run without it installed. Direct
   ``from langfuse.decorators import observe`` ImportErrors crash the
   importing module before any code runs — the wrong failure mode for an
   observability concern.

2. The SDK silently no-ops when ``LANGFUSE_HOST`` / ``LANGFUSE_PUBLIC_KEY``
   / ``LANGFUSE_SECRET_KEY`` are unset, so decorating a function with
   ``@observe`` is safe even when tracing isn't configured. But the
   import itself needs to succeed for the decorator syntax to work.

## What you get

- :data:`observe` — a function decorator. Same kwargs as
  ``langfuse.observe`` (``as_type="generation"``,
  ``name="ollama_chat_text"``, etc.) when Langfuse is installed; a
  no-op decorator otherwise.
- :data:`langfuse_context` — exposes the context API for stamping
  attributes onto the current span (``model``, ``input``, ``output``,
  ``usage``). No-op when Langfuse is unavailable.

## SDK version notes

Langfuse 4.x removed ``langfuse.decorators`` (the v3 module). The v4 API
is OTEL-native: ``observe`` lives at the top-level ``langfuse`` package and
``langfuse_context.update_current_observation()`` maps to
``langfuse.get_client().update_current_generation()``. This shim tries v4
first, falls back to the legacy v3 ``langfuse.decorators`` path, then falls
back to no-ops when langfuse is not installed at all.

## Usage

::

    from services.langfuse_shim import observe, langfuse_context

    @observe(as_type="generation", name="my_llm_call")
    async def call_model(prompt, *, model):
        langfuse_context.update_current_observation(
            model=model, input=prompt,
        )
        response = await _do_the_thing(prompt)
        langfuse_context.update_current_observation(
            output=response,
            usage={"input": prompt_tokens, "output": completion_tokens},
        )
        return response

Closes Glad-Labs/poindexter#485 follow-up: the Ollama paths
(``ollama_client.py``, ``topic_ranking.py``, ``multi_model_qa.py``,
``writer_rag_modes/*``, ``image_decision_agent``) all wrap their LLM
calls through this shim so every call reaches Langfuse, not just the
two atoms that happened to route through ``llm_text.ollama_chat_text``.
"""

from __future__ import annotations

from typing import Any

try:
    # Langfuse 4.x: observe lives at the top-level package; langfuse.decorators
    # was removed. get_client() returns the process-wide Langfuse client whose
    # update_current_generation() replaces the old update_current_observation().
    from langfuse import get_client as _lf_get_client, observe  # type: ignore[import-not-found]

    class _LangfuseContextCompat:
        """Maps Langfuse v3 langfuse_context API to the v4 get_client() surface.

        All production callers only use update_current_observation() with kwargs
        that update_current_generation() accepts directly (model, input, output,
        usage_details). Errors are swallowed so an observability failure never
        breaks content generation.
        """

        @staticmethod
        def update_current_observation(*_args: Any, **kwargs: Any) -> None:
            try:
                _lf_get_client().update_current_generation(**kwargs)
            except Exception:
                # Swallowed intentionally — observability must never break content generation.
                pass

        @staticmethod
        def update_current_trace(*_args: Any, **_kwargs: Any) -> None:
            pass

    langfuse_context = _LangfuseContextCompat()  # type: ignore[assignment]
    LANGFUSE_AVAILABLE: bool = True

except ImportError:  # pragma: no cover — try legacy v3 path
    try:
        from langfuse.decorators import langfuse_context, observe  # type: ignore[import-not-found]
        LANGFUSE_AVAILABLE = True
    except ImportError:
        # langfuse not installed at all (brain daemon, minimal-dep test runs)
        LANGFUSE_AVAILABLE = False

        def observe(*_args: Any, **_kwargs: Any):  # type: ignore[no-redef]
            """No-op decorator used when langfuse SDK isn't installed."""
            def _decorate(fn: Any) -> Any:
                return fn
            return _decorate

        class _NoopLangfuseContext:
            @staticmethod
            def update_current_observation(*_args: Any, **_kwargs: Any) -> None:
                return None

            @staticmethod
            def update_current_trace(*_args: Any, **_kwargs: Any) -> None:
                return None

        langfuse_context = _NoopLangfuseContext()  # type: ignore[assignment]


__all__ = ["LANGFUSE_AVAILABLE", "langfuse_context", "observe"]
