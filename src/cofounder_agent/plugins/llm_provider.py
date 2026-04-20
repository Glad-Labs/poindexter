"""LLMProvider — the inference-backend Protocol.

The OSS inference ecosystem converged on the **OpenAI-compatible
chat/completions API**. Almost every major local backend speaks it:
Ollama (``/v1``), llama.cpp server, vllm, SGLang, HuggingFace TGI,
LM Studio, LocalAI, LiteLLM gateway. One generic provider covers them
all; per-backend plugins only appear when they need backend-specific
features.

After Phase J lands, Poindexter core ships two LLMProvider plugins:

- :class:`OpenAICompatProvider` — generic HTTP client; covers 8+
  backends by config row.
- :class:`OllamaNativeProvider` — keeps Ollama-specific features
  (electricity cost tracking, ``/api/embed``, model pull). Default
  out-of-box provider.

Community plugins can wrap paid vendors (Anthropic, OpenAI, Groq,
OpenRouter). Core install stays free.

Register a LLMProvider via ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.llm_providers"]
    ollama_native = "poindexter.llm_providers.ollama:OllamaNativeProvider"
    openai_compat = "poindexter.llm_providers.openai_compat:OpenAICompatProvider"

Exit criterion for Phase J: swapping Ollama → vllm/llama.cpp/TGI/LocalAI
is a single ``app_settings`` row edit, no code change.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Completion:
    """Result of a synchronous LLM call.

    ``raw`` preserves the provider's original response so observability
    tooling can extract backend-specific fields (reasoning tokens,
    safety labels, etc.) without widening the Protocol.
    """

    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = ""  # "stop", "length", "error", etc.
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Token:
    """A single token chunk streamed from ``LLMProvider.stream()``."""

    text: str
    finish_reason: str | None = None  # set only on the final token
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    """Inference backend. Implementations wrap one or more model servers.

    Attributes:
        name: Unique provider name (matches the entry_point key).
        supports_streaming: Whether ``stream()`` is supported. If
            ``False``, callers fall back to ``complete()``.
        supports_embeddings: Whether ``embed()`` is supported. Providers
            that don't offer embedding should set this and delegate to
            the default OllamaNativeProvider.
    """

    name: str
    supports_streaming: bool
    supports_embeddings: bool

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> Completion:
        """Synchronous completion.

        Args:
            messages: OpenAI chat-format message list (``role``, ``content``).
            model: Model identifier as the backend expects it (e.g.
                ``"gemma3:27b"`` for Ollama, ``"Qwen/Qwen2.5-32B-Instruct"``
                for vllm).
            **kwargs: Per-call overrides. Recognized standard kwargs:

                - ``temperature`` (float, default 0.7)
                - ``max_tokens`` (int, default None)
                - ``top_p`` (float, default None)
                - ``timeout_s`` (int, per-call override of the provider's
                  configured timeout — use when a specific call needs a
                  tighter window than the default)
                - ``_provider_config`` (dict, dispatcher-injected; don't
                  set this manually — ``dispatch_complete`` populates it
                  from ``plugin.llm_provider.<name>`` in app_settings)

                Provider-specific kwargs (Ollama ``num_ctx`` etc.) are
                accepted via ``**kwargs`` but ignored silently by
                providers that don't understand them.
        """
        ...

    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> AsyncIterator[Token]:
        """Streamed completion. Yields ``Token`` chunks as they arrive."""
        ...

    async def embed(
        self,
        text: str,
        model: str,
    ) -> list[float]:
        """Return an embedding vector for ``text``.

        Embedding model dimensions depend on the backend. Providers that
        don't support embedding raise ``NotImplementedError``.
        """
        ...
