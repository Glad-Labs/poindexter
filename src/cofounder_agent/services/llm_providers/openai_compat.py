"""OpenAICompatProvider — one HTTP client, every OpenAI-compat backend.

The OSS local-inference ecosystem converged on the OpenAI-compatible
chat/completions API. One generic HTTP client covers all of them:

- Ollama (``/v1/chat/completions`` in addition to the native API)
- llama.cpp server (``--api``)
- vllm (``vllm serve ...``)
- SGLang (``python -m sglang.launch_server``)
- HuggingFace TGI (``text-generation-launcher``)
- LM Studio (built-in local server)
- LocalAI (its whole purpose)
- LiteLLM gateway (proxies to anything)

And, by config, the paid vendors that implement the same interface:
Groq, OpenRouter, Together, Fireworks, Anthropic's OpenAI-compat mode,
etc. Matt's "core" install ships with Ollama as the default target
(free, local); swapping to a different backend is one ``app_settings``
row edit.

Config (``plugin.llm_provider.openai_compat`` in ``app_settings``):
- ``base_url`` (default ``"http://host.docker.internal:11434/v1"``) —
  where the OpenAI-compat endpoint lives
- ``api_key`` — optional; paid vendors require, local backends don't
- ``timeout_seconds`` (default 120)
- ``default_embed_model`` (default ``"nomic-embed-text"``)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from plugins.llm_provider import Completion, Token

logger = logging.getLogger(__name__)


class OpenAICompatProvider:
    """Generic LLMProvider for any OpenAI-compat API.

    Instantiated once at registry discovery time; reads its
    per-install config from app_settings on each call. That means
    changing ``base_url`` in the DB takes effect on the next call,
    no worker restart needed.
    """

    name = "openai_compat"
    supports_streaming = True
    supports_embeddings = True

    def __init__(self):
        self._default_base_url = "http://host.docker.internal:11434/v1"

    def _resolve_config(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Per-call overrides from PluginConfig merge with kwargs.

        ``kwargs["timeout_s"]`` beats the config's ``timeout_seconds``
        — lets callers pin a tight per-request timeout (QA reviewers,
        self-review) without editing the provider config.
        """
        cfg = kwargs.get("_provider_config") or {}
        per_call_timeout = kwargs.get("timeout_s")
        timeout = (
            per_call_timeout
            if per_call_timeout is not None
            else cfg.get("timeout_seconds", 120)
        )
        return {
            "base_url": (cfg.get("base_url") or self._default_base_url).rstrip("/"),
            "api_key": cfg.get("api_key") or "",
            "timeout": timeout,
            "default_embed_model": cfg.get("default_embed_model", "nomic-embed-text"),
        }

    def _headers(self, api_key: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> Completion:
        cfg = self._resolve_config(kwargs)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
        if "top_p" in kwargs:
            payload["top_p"] = kwargs["top_p"]

        async with httpx.AsyncClient(timeout=cfg["timeout"]) as http:
            resp = await http.post(
                f"{cfg['base_url']}/chat/completions",
                json=payload,
                headers=self._headers(cfg["api_key"]),
            )
            resp.raise_for_status()
            data = resp.json()

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {})
        usage = data.get("usage", {}) or {}
        return Completion(
            text=msg.get("content", ""),
            model=data.get("model", model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            finish_reason=choice.get("finish_reason", "stop"),
            raw=data,
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> AsyncIterator[Token]:
        cfg = self._resolve_config(kwargs)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]

        async with httpx.AsyncClient(timeout=cfg["timeout"]) as http:
            async with http.stream(
                "POST",
                f"{cfg['base_url']}/chat/completions",
                json=payload,
                headers=self._headers(cfg["api_key"]),
            ) as resp:
                resp.raise_for_status()
                async for raw_line in resp.aiter_lines():
                    if not raw_line:
                        continue
                    # Server-Sent Events format: "data: {...}"
                    if raw_line.startswith("data: "):
                        raw_line = raw_line[len("data: "):]
                    if raw_line.strip() == "[DONE]":
                        yield Token(text="", finish_reason="stop")
                        return
                    try:
                        import json as _json
                        chunk = _json.loads(raw_line)
                    except Exception:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content", "")
                    finish = choices[0].get("finish_reason")
                    if text or finish:
                        yield Token(text=text, finish_reason=finish, raw=chunk)

    async def embed(self, text: str, model: str) -> list[float]:
        """Embed via the OpenAI-compat /v1/embeddings endpoint."""
        cfg = self._resolve_config({})
        embed_model = model or cfg["default_embed_model"]

        async with httpx.AsyncClient(timeout=cfg["timeout"]) as http:
            resp = await http.post(
                f"{cfg['base_url']}/embeddings",
                json={"model": embed_model, "input": text},
                headers=self._headers(cfg["api_key"]),
            )
            resp.raise_for_status()
            data = resp.json()

        items = data.get("data") or []
        if not items:
            raise ValueError(
                f"OpenAICompatProvider: embed response had no data (model={embed_model})"
            )
        return list(items[0].get("embedding") or [])
