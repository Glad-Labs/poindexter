"""OllamaNativeProvider — thin LLMProvider wrapper around OllamaClient.

Preserves every Ollama-specific behavior already implemented in
``services/ollama_client.py``: electricity-cost tracking, retry/backoff,
``/api/embed`` for embeddings, model resolution via the router. This
wrapper just exposes that logic through the standard Protocol so the
rest of the refactor (model_router, future Phase J follow-ups) can
talk to it uniformly with OpenAICompatProvider.

Cost tracking: every successful or failed call writes a row to
``cost_logs`` via ``CostGuard.record_usage`` with ``is_local=True``,
so the dashboard surfaces Ollama spend (electricity-derived dollars)
on the same axes as cloud providers.

Config (``plugin.llm_provider.ollama_native`` in ``app_settings``):
- ``base_url`` (default: whatever OllamaClient resolves from DB)
- ``timeout_seconds`` (default 120)
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from plugins.llm_provider import Completion, Token
from services.cost_guard import CostGuard

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

    def __init__(self, site_config: Any = None):
        # Lazy import — OllamaClient drags in a lot of transitive deps
        # (cost tracking, electricity calc, model router) that shouldn't
        # load every time the registry enumerates plugins. Import on
        # first actual use.
        self._client = None
        self._site_config = site_config

    def _get_client(self):
        if self._client is None:
            from services.ollama_client import OllamaClient
            self._client = OllamaClient()
        return self._client

    def _build_cost_guard(self, kwargs: dict[str, Any]) -> CostGuard:
        """Resolve a CostGuard for this call.

        Tests can short-circuit by passing ``_cost_guard``. Production
        plumbing seeds ``_pool`` on site_config; absent that the guard
        runs in offline mode (record_usage no-ops on insert) so unit
        tests don't need a live DB.
        """
        injected = kwargs.get("_cost_guard")
        if isinstance(injected, CostGuard):
            return injected
        site_config = kwargs.get("_site_config", self._site_config)
        pool = kwargs.get("_pool")
        if pool is None and site_config is not None:
            pool = getattr(site_config, "_pool", None)
        return CostGuard(site_config=site_config, pool=pool)

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

        Recognized kwargs: ``temperature``, ``max_tokens``, ``timeout_s``
        (per-call override of OllamaClient.timeout for callers like
        self-review that need tighter QA windows).
        """
        client = self._get_client()
        system = next((m["content"] for m in messages if m.get("role") == "system"), None)
        prompt_msgs = [m for m in messages if m.get("role") != "system"]
        # Naive concatenation for multi-turn; good enough for our pipeline
        # which almost always sends system + one user message.
        prompt = "\n\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in prompt_msgs
        )

        cost_guard = self._build_cost_guard(kwargs)
        started = time.perf_counter()
        success = True
        result: dict[str, Any] = {}
        try:
            result = await client.generate(
                prompt=prompt,
                model=model,
                system=system,
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=kwargs.get("max_tokens"),
                stream=False,
                timeout=kwargs.get("timeout_s"),
            )
        except Exception:
            success = False
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            try:
                await cost_guard.record_usage(
                    provider=self.name,
                    model=model,
                    prompt_tokens=int(result.get("prompt_eval_count", 0) or 0),
                    completion_tokens=int(result.get("eval_count", 0) or 0),
                    phase=str(kwargs.get("phase", "llm_call")),
                    task_id=kwargs.get("task_id"),
                    success=success,
                    duration_ms=duration_ms,
                    is_local=True,
                )
            except Exception as e:
                logger.warning(
                    "[OllamaNativeProvider] cost recording failed: %s", e,
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

    async def embed(self, text: str, model: str, **kwargs: Any) -> list[float]:
        """Embed via Ollama's /api/embed.

        Records electricity usage to cost_logs the same way
        :meth:`complete` does — embeddings hit the GPU too, just for a
        fraction of the wall-clock of a generation.
        """
        client = self._get_client()
        resolved_model = model or "nomic-embed-text"
        cost_guard = self._build_cost_guard(kwargs)
        started = time.perf_counter()
        success = True
        try:
            return await client.embed(text, model=resolved_model)
        except Exception:
            success = False
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            try:
                await cost_guard.record_usage(
                    provider=self.name,
                    model=resolved_model,
                    prompt_tokens=max(1, len(text or "") // 4),
                    completion_tokens=0,
                    phase="embed",
                    task_id=kwargs.get("task_id"),
                    success=success,
                    duration_ms=duration_ms,
                    is_local=True,
                )
            except Exception as e:
                logger.warning(
                    "[OllamaNativeProvider] embed cost recording failed: %s", e,
                )

    async def embed_batch(
        self, texts: list[str], model: str = "nomic-embed-text", **kwargs: Any,
    ) -> list[list[float]]:
        """Batch embed — Ollama-specific, for EmbeddingService.embed_all_posts.

        Not part of the LLMProvider Protocol itself; callers duck-type
        via ``getattr(provider, "embed_batch", None)`` and fall back to
        a loop of single ``embed()`` calls when absent. Exposing it here
        keeps the batch-HTTP optimization available through the Provider
        interface so EmbeddingService can migrate cleanly.
        """
        client = self._get_client()
        cost_guard = self._build_cost_guard(kwargs)
        started = time.perf_counter()
        success = True
        try:
            return await client.embed_batch(texts, model=model)
        except Exception:
            success = False
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            total_chars = sum(len(t or "") for t in texts)
            try:
                await cost_guard.record_usage(
                    provider=self.name,
                    model=model,
                    prompt_tokens=max(1, total_chars // 4),
                    completion_tokens=0,
                    phase="embed_batch",
                    task_id=kwargs.get("task_id"),
                    success=success,
                    duration_ms=duration_ms,
                    is_local=True,
                )
            except Exception as e:
                logger.warning(
                    "[OllamaNativeProvider] embed_batch cost recording failed: %s", e,
                )
