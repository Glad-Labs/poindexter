"""OllamaNativeProvider — thin LLMProvider wrapper around OllamaClient.

Preserves every Ollama-specific behavior already implemented in
``services/ollama_client.py``: electricity-cost tracking, retry/backoff,
``/api/embed`` for embeddings, model resolution via the router. This
wrapper just exposes that logic through the standard Protocol so the
rest of the refactor (model_router, future Phase J follow-ups) can
talk to it uniformly with OpenAICompatProvider.

Config (``plugin.llm_provider.ollama_native`` in ``app_settings``):
- ``base_url`` (default: whatever OllamaClient resolves from DB)
- ``timeout_seconds`` (default 120)
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from plugins.llm_provider import Completion, Token

logger = logging.getLogger(__name__)


class OllamaNativeProvider:
    """LLMProvider implementation for local Ollama instances.

    Delegates to ``services.ollama_client.OllamaClient`` to keep the
    existing cost tracking + retry + model-resolution behavior intact.
    The wrapper adds no new semantics — it's purely a Protocol bridge.
    """

    name = "ollama_native"
    supports_streaming = True
    supports_embeddings = True

    def __init__(self):
        # Lazy import — OllamaClient drags in a lot of transitive deps
        # (cost tracking, electricity calc, model router) that shouldn't
        # load every time the registry enumerates plugins. Import on
        # first actual use.
        self._client = None

    def _get_client(self):
        if self._client is None:
            from services.ollama_client import OllamaClient
            self._client = OllamaClient()
        return self._client

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> Completion:
        """Synchronous completion via Ollama's /api/chat.

        Converts the OpenAI-style ``messages`` list to the
        ``prompt`` + ``system`` split that OllamaClient.generate expects.
        For multi-turn conversations this lossy but matches
        current OllamaClient usage across the codebase.
        """
        client = self._get_client()
        system = next((m["content"] for m in messages if m.get("role") == "system"), None)
        prompt_msgs = [m for m in messages if m.get("role") != "system"]
        # Naive concatenation for multi-turn; good enough for our pipeline
        # which almost always sends system + one user message.
        prompt = "\n\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in prompt_msgs
        )

        result = await client.generate(
            prompt=prompt,
            model=model,
            system=system,
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens"),
            stream=False,
        )

        text = result.get("response") or result.get("text") or ""
        return Completion(
            text=text,
            model=result.get("model", model),
            prompt_tokens=result.get("prompt_eval_count", 0),
            completion_tokens=result.get("eval_count", 0),
            total_tokens=result.get("prompt_eval_count", 0) + result.get("eval_count", 0),
            finish_reason=result.get("done_reason", "stop"),
            raw=result,
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> AsyncIterator[Token]:
        """Streamed completion via Ollama's /api/chat with stream=True."""
        client = self._get_client()
        system = next((m["content"] for m in messages if m.get("role") == "system"), None)
        prompt = "\n\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}"
            for m in messages if m.get("role") != "system"
        )

        async for chunk in client.stream_generate(
            prompt=prompt,
            model=model,
            system=system,
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens"),
        ):
            if isinstance(chunk, dict):
                text = chunk.get("response", "") or chunk.get("text", "")
                finish = "stop" if chunk.get("done") else None
                yield Token(text=text, finish_reason=finish, raw=chunk)
            else:
                yield Token(text=str(chunk))

    async def embed(self, text: str, model: str) -> list[float]:
        """Embed via Ollama's /api/embed."""
        client = self._get_client()
        return await client.embed(text, model=model or "nomic-embed-text")
