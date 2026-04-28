"""OpenAICompatProvider — one SDK, every OpenAI-compat backend.

Glad-Labs/poindexter#132 rewrites this provider on top of the official
``openai`` Python SDK so a single implementation covers:

- **Local OAI-compat servers** — vLLM (``vllm serve``), llama.cpp
  (``./server --api-key``), LocalAI, Text Generation Inference, SGLang,
  LM Studio. ``base_url`` points at the local endpoint; cost is $0.
- **Aggregator gateways** — OpenRouter, Together, Groq, DeepInfra,
  Fireworks, LiteLLM proxy. ``base_url`` is the gateway, ``api_key`` is
  the bearer token, cost comes from a per-model rate table or the
  response itself.
- **Direct cloud** — ``api.openai.com`` with the operator's own key.
  Gated by :class:`~services.cost_guard.CostGuard` so an accidental
  config can't run up an unbounded bill.

Disabled by default. The plugin is registered in ``pyproject.toml`` but
``app_settings.plugin.llm_provider.openai_compat.enabled`` defaults to
``false`` — operators opt in by writing the row.

Cost-guard integration is mandatory. Every ``complete()`` and
``embed()`` call:

1. Builds a :class:`~services.cost_guard.CostEstimate` from the request
   shape (token estimates from the message body for ``complete()``,
   ``input_tokens=ceil(len(text)/4)`` for ``embed()``).
2. Calls ``CostGuard.preflight()`` — local backends short-circuit to
   $0; cloud backends raise :class:`CostGuardExhausted` if the running
   spend would exceed the daily or monthly cap.
3. Fires the SDK call.
4. Records the actual cost via ``CostGuard.record()``.

On exhaustion the provider raises ``CostGuardExhausted`` — it does NOT
silently fall through to a different paid provider. The router routes by
the configured ``pipeline_writer_model``; recovery from a budget
exhaustion is the operator's call (raise the cap, switch to local).
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncIterator
from typing import Any

import httpx

from plugins.llm_provider import Completion, Token
from plugins.llm_resilience import (
    CircuitOpenError,
    LLMResilienceManager,
    RetryDecision,
)
from services.cost_guard import (
    CostGuard,
    CostGuardExhausted,
    is_local_base_url,
)


# OpenAI-compat retryable HTTP statuses — same set as Ollama's. The
# venn diagram of "OAI-compat backends I might point this at" includes
# vLLM (returns 503 under load), llama.cpp (404 on missing model is
# permanent — don't retry), and OpenRouter / Together / Groq (429 with
# Retry-After when their gateway is rate-limiting). Treat 408/429/5xx
# as retryable and let the SDK's own backoff handle the rest.
_RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def openai_compat_classifier(exc: BaseException) -> RetryDecision:
    """Classify exceptions raised by ``openai_compat`` SDK / streaming calls.

    Retry on:
        * httpx connect / read / write / pool / protocol errors —
          transient transport issues. Local OAI-compat backends (vLLM,
          llama.cpp) drop connections under contention; cloud backends
          throw these on bad routes.
        * Generic ``httpx.HTTPStatusError`` with 408/425/429/5xx.
        * The official ``openai`` SDK's typed exceptions (``APITimeoutError``,
          ``APIConnectionError``, ``RateLimitError``, ``InternalServerError``).
          We use duck-typing (``__class__.__name__``) so the classifier
          works without an SDK import — the openai package isn't a hard
          dep on every install.
        * 429 ``RateLimitError`` honors a ``Retry-After`` header when
          the SDK exposes it on ``.response.headers``.

    Do NOT retry on:
        * 4xx schema / auth / not-found errors.
        * ``CostGuardExhausted`` — the budget caller decides.
        * Programmer errors (TypeError, ValueError, etc.).
    """
    if isinstance(exc, asyncio.CancelledError):
        return RetryDecision(retry=False, reason="cancelled")
    if isinstance(exc, CircuitOpenError):
        return RetryDecision(retry=False, reason="circuit_open")
    if isinstance(exc, CostGuardExhausted):
        return RetryDecision(retry=False, reason="cost_guard_exhausted")
    # httpx transport errors → retry. Same set as the Ollama classifier.
    if isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.RemoteProtocolError,
            httpx.ConnectTimeout,
        ),
    ):
        return RetryDecision(retry=True, reason=type(exc).__name__)
    # httpx HTTPStatusError → 5xx + 408/425/429 retryable.
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in _RETRYABLE_HTTP_STATUSES:
            wait = _retry_after_seconds(exc.response.headers)
            return RetryDecision(
                retry=True, wait_seconds=wait, reason=f"http_{code}",
            )
        return RetryDecision(retry=False, reason=f"http_{code}")
    # OpenAI SDK typed exceptions — duck-typed by class name so the
    # classifier doesn't need the SDK at import time.
    cls_name = type(exc).__name__
    if cls_name in {
        "APITimeoutError",
        "APIConnectionError",
        "InternalServerError",
    }:
        return RetryDecision(retry=True, reason=cls_name)
    if cls_name == "RateLimitError":
        wait = None
        response = getattr(exc, "response", None)
        if response is not None:
            wait = _retry_after_seconds(getattr(response, "headers", None))
        return RetryDecision(retry=True, wait_seconds=wait, reason="rate_limit")
    if cls_name == "APIStatusError":
        # SDK sub-class with .status_code; only 5xx are retryable.
        code = int(getattr(exc, "status_code", 0) or 0)
        if code in _RETRYABLE_HTTP_STATUSES:
            return RetryDecision(retry=True, reason=f"http_{code}")
        return RetryDecision(retry=False, reason=f"http_{code}")
    # Generic httpx.HTTPError fallback.
    if isinstance(exc, httpx.HTTPError):
        response = getattr(exc, "response", None)
        if response is not None:
            code = int(getattr(response, "status_code", 0) or 0)
            if code in _RETRYABLE_HTTP_STATUSES:
                return RetryDecision(retry=True, reason=f"http_{code}")
            return RetryDecision(retry=False, reason=f"http_{code}")
        return RetryDecision(retry=True, reason=type(exc).__name__)
    return RetryDecision(retry=False, reason="non_retryable")


def _retry_after_seconds(headers: Any) -> float | None:
    """Pull a numeric Retry-After value off a header mapping.

    Tolerates the common shapes (httpx.Headers, plain dict, SDK-wrapped
    response). Returns ``None`` when the header is absent or unparseable
    so the manager falls back to its exponential schedule.
    """
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:  # pragma: no cover — defensive
        return None
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def _compute_cost(
    cfg: dict[str, Any],
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Compute USD cost for an OAI-compat call from the cfg's rate_table.

    Returns 0.0 when the configured base_url is local (vLLM, llama.cpp,
    LocalAI, Ollama-on-host) — the dollar axis isn't meaningful there.
    Electricity is recorded separately via ``record_usage(is_local=True)``.

    Cloud calls look the model up in ``cfg["rate_table"]`` (operator-
    supplied per-config) and fall back to ``_FALLBACK_RATE_PER_1K`` if
    the model is unknown — matching the legacy ``CostGuard.estimate``
    semantics exactly so existing fixture rates still apply.
    """
    if is_local_base_url(cfg.get("base_url")):
        return 0.0
    rates = (cfg.get("rate_table") or {}).get(model) or {
        "input": 0.0005,
        "output": 0.0015,
    }
    in_cost = (max(0, int(prompt_tokens)) / 1000.0) * float(rates.get("input", 0.0))
    out_cost = (max(0, int(completion_tokens)) / 1000.0) * float(rates.get("output", 0.0))
    return in_cost + out_cost

logger = logging.getLogger(__name__)

# Per-model dollar rates per 1K tokens for cloud backends. Operators can
# extend this via ``app_settings.plugin.llm_provider.openai_compat.rate_table``
# (JSON blob keyed by model name → ``{"input": float, "output": float}``).
_DEFAULT_RATE_TABLE: dict[str, dict[str, float]] = {
    # OpenAI direct
    "gpt-4o": {"input": 0.0025, "output": 0.010},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4.1-mini": {"input": 0.00040, "output": 0.0016},
    "text-embedding-3-small": {"input": 0.00002, "output": 0.0},
    "text-embedding-3-large": {"input": 0.00013, "output": 0.0},
    # Groq (very cheap; published rates vary by model)
    "llama-3.1-70b-versatile": {"input": 0.00059, "output": 0.00079},
    # Together (representative)
    "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo": {"input": 0.00088, "output": 0.00088},
}


class OpenAICompatProvider:
    """Generic LLMProvider for any OpenAI-compat /v1 endpoint.

    Instantiated once at registry-discovery time with no constructor
    args (the entry_point loader calls ``OpenAICompatProvider()``). The
    SiteConfig instance is supplied per-call via the dispatcher's
    reserved ``_site_config`` kwarg, mirroring the pattern that
    ``image_providers.sdxl`` already uses.
    """

    name = "openai_compat"
    supports_streaming = True
    supports_embeddings = True

    # Default base URL targets the OAI-compat shim Ollama exposes when
    # the operator hasn't configured anything else. Real value comes from
    # ``app_settings.plugin.llm_provider.openai_compat.base_url`` once
    # ``enabled=true`` is flipped.
    _DEFAULT_BASE_URL = "http://host.docker.internal:11434/v1"
    _DEFAULT_TIMEOUT_S = 120
    _DEFAULT_EMBED_MODEL = "nomic-embed-text"

    def __init__(self, site_config: Any | None = None) -> None:
        # site_config may be supplied at construction time (test path /
        # explicit DI) or via the per-call ``_site_config`` kwarg
        # (production dispatcher path). Either way it's optional — the
        # provider falls back to safe defaults when absent.
        self._site_config = site_config
        # Resilience layer (GH#192) — retry + concurrency cap + circuit
        # breaker, plus cross-provider audit and OTel tracing. Settings
        # are read from ``llm_openai_compat_*`` keys with sensible
        # defaults; operators tune via app_settings without restart.
        self._resilience = LLMResilienceManager(
            provider_name="openai_compat",
            classifier=openai_compat_classifier,
            site_config=site_config,
        )

    # ------------------------------------------------------------------
    # Config resolution
    # ------------------------------------------------------------------

    def _site_config_from(self, kwargs: dict[str, Any]) -> Any | None:
        """Resolve site_config — per-call kwarg beats constructor kwarg."""
        return kwargs.get("_site_config") or self._site_config

    async def _resolve_config(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Merge per-call kwargs with ``_provider_config`` dict.

        The dispatcher seeds ``_provider_config`` from
        ``plugin.llm_provider.openai_compat`` in app_settings (loaded via
        :class:`PluginConfig`). When the operator hasn't written that
        row, ``_provider_config`` is empty and every value falls back to
        the module defaults.

        Secrets path: ``api_key`` is fetched via
        :meth:`SiteConfig.get_secret` so it's decrypted on the fly. The
        config dict from PluginConfig may also hold the key plaintext
        (operators on early-stage installs sometimes drop it there) —
        we accept either, preferring the encrypted-secret path.
        """
        cfg = kwargs.get("_provider_config") or {}
        site_config = self._site_config_from(kwargs)

        api_key = ""
        if site_config is not None:
            try:
                api_key = await site_config.get_secret(
                    "plugin.llm_provider.openai_compat.api_key", "",
                )
            except Exception as e:
                logger.warning("[openai_compat] get_secret failed: %s", e)
        if not api_key:
            api_key = cfg.get("api_key") or ""

        per_call_timeout = kwargs.get("timeout_s")
        timeout = (
            per_call_timeout
            if per_call_timeout is not None
            else cfg.get("request_timeout_s") or cfg.get("timeout_seconds") or self._DEFAULT_TIMEOUT_S
        )

        return {
            "enabled": bool(cfg.get("enabled", False)),
            "base_url": (cfg.get("base_url") or self._DEFAULT_BASE_URL).rstrip("/"),
            "api_key": api_key,
            "default_model": cfg.get("default_model") or "",
            "default_embed_model": cfg.get("default_embed_model") or self._DEFAULT_EMBED_MODEL,
            "timeout": int(timeout),
            "rate_table": cfg.get("rate_table") or _DEFAULT_RATE_TABLE,
        }

    def _ensure_enabled(self, cfg: dict[str, Any]) -> None:
        """Raise if the operator hasn't opted in.

        Disabled-by-default per the project's LLM-provider policy: the
        plugin ships in ``pyproject.toml`` but
        ``app_settings.plugin.llm_provider.openai_compat.enabled`` is
        ``false`` until the operator writes the row.
        """
        if not cfg.get("enabled", False):
            raise RuntimeError(
                "OpenAICompatProvider is disabled. Set "
                "plugin.llm_provider.openai_compat.enabled=true in app_settings "
                "(and configure base_url + api_key) before routing traffic here.",
            )

    def _cost_guard(self, kwargs: dict[str, Any]) -> CostGuard:
        """Construct a per-call CostGuard.

        The guard is stateless aside from holding refs to site_config +
        pool, so building one per call is cheap. The pool is read off
        ``_site_config._pool`` if present; tests pass a synthetic pool
        directly via the ``_cost_guard`` kwarg.
        """
        injected = kwargs.get("_cost_guard")
        if isinstance(injected, CostGuard):
            return injected
        site_config = self._site_config_from(kwargs)
        pool = getattr(site_config, "_pool", None) if site_config else None
        return CostGuard(site_config=site_config, pool=pool)

    # ------------------------------------------------------------------
    # SDK client
    # ------------------------------------------------------------------

    def _build_sdk_client(self, cfg: dict[str, Any]) -> Any:
        """Return an ``openai.AsyncOpenAI`` client configured for ``cfg``.

        Imported lazily — ``openai`` is a real dependency now (the
        cost-guarded plugin can't function without it) but keeping the
        import inside the method means a registry enumeration of
        plugins doesn't pay for the SDK module load when nobody calls
        the provider.
        """
        from openai import AsyncOpenAI

        # ``api_key=""`` is rejected by the SDK constructor; use a stub
        # value when targeting a local backend that doesn't authenticate.
        api_key = cfg["api_key"] or "EMPTY"
        return AsyncOpenAI(
            base_url=cfg["base_url"],
            api_key=api_key,
            timeout=cfg["timeout"],
        )

    # ------------------------------------------------------------------
    # complete()
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> Completion:
        """Chat completion with mandatory cost-guard wrap."""
        cfg = await self._resolve_config(kwargs)
        self._ensure_enabled(cfg)

        target_model = model or cfg["default_model"]
        if not target_model:
            raise ValueError(
                "OpenAICompatProvider.complete: no model — pass `model=...` or "
                "set plugin.llm_provider.openai_compat.default_model.",
            )

        guard = self._cost_guard(kwargs)
        # Cheap input estimate (~4 chars/token) so the preflight has
        # something to compare against the cap. Cloud backends will
        # report the real number in the response.
        prompt_chars = sum(len((m or {}).get("content", "")) for m in messages)
        prompt_tokens_est = max(1, math.ceil(prompt_chars / 4))
        max_tokens = int(kwargs.get("max_tokens") or 1024)
        is_local = is_local_base_url(cfg["base_url"])

        estimated_cost_usd = _compute_cost(
            cfg,
            model=target_model,
            prompt_tokens=prompt_tokens_est,
            completion_tokens=max_tokens,
        )
        # Always raises on exhaustion — never silently falls through.
        await guard.check_budget(
            provider=self.name,
            model=target_model,
            estimated_cost_usd=estimated_cost_usd,
        )

        sdk_kwargs: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
        }
        if "temperature" in kwargs:
            sdk_kwargs["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            sdk_kwargs["max_tokens"] = kwargs["max_tokens"]
        if "top_p" in kwargs:
            sdk_kwargs["top_p"] = kwargs["top_p"]

        client = self._build_sdk_client(cfg)

        async def _do_complete() -> Any:
            return await client.chat.completions.create(**sdk_kwargs)

        try:
            response = await self._resilience.run(
                _do_complete,
                op_name="complete",
                task_id=kwargs.get("task_id"),
            )
        except CostGuardExhausted:
            # Bubbles up — see policy note in module docstring.
            raise
        except Exception as exc:
            logger.warning("[openai_compat] complete(%s) failed: %s", target_model, exc)
            raise

        # The SDK returns a Pydantic model; ``.model_dump()`` gives us a
        # dict for the ``raw`` field on Completion. Use a defensive
        # access so a stub return value (tests) without ``.model_dump``
        # still works.
        if hasattr(response, "model_dump"):
            data = response.model_dump()
        elif isinstance(response, dict):
            data = response
        else:  # pragma: no cover - defensive
            data = {}

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {}) or {}
        usage = data.get("usage", {}) or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))

        # Recompute actual cost from real token counts (the estimate was
        # a lower-bound on input + max_tokens cap on output) and record.
        actual_cost = _compute_cost(
            cfg,
            model=target_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        await guard.record_usage(
            provider=self.name,
            model=target_model,
            cost_usd=actual_cost,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            phase="openai_compat.complete",
            success=True,
            is_local=is_local,
        )

        return Completion(
            text=message.get("content") or "",
            model=data.get("model") or target_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=choice.get("finish_reason") or "stop",
            raw=data,
        )

    # ------------------------------------------------------------------
    # stream()
    # ------------------------------------------------------------------

    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> AsyncIterator[Token]:
        """Streamed chat completion. Cost-guard wraps the same way as
        ``complete()`` — preflight on enter, post-call record on exit.

        Streaming responses don't expose token counts mid-stream; we
        record the cost using the final ``usage`` block when the OAI-
        compat backend includes one (most do; some local llama.cpp
        builds don't, in which case we record ``estimated_usd`` from
        the lower-bound preflight estimate).

        Stream shape kept on the legacy httpx implementation so this
        path stays usable when the openai SDK is unavailable in dev
        environments.
        """
        cfg = await self._resolve_config(kwargs)
        self._ensure_enabled(cfg)

        target_model = model or cfg["default_model"]
        if not target_model:
            raise ValueError("OpenAICompatProvider.stream: model required")

        guard = self._cost_guard(kwargs)
        prompt_chars = sum(len((m or {}).get("content", "")) for m in messages)
        prompt_tokens_est = max(1, math.ceil(prompt_chars / 4))
        max_tokens = int(kwargs.get("max_tokens") or 1024)
        is_local = is_local_base_url(cfg["base_url"])
        estimated_cost_usd = _compute_cost(
            cfg,
            model=target_model,
            prompt_tokens=prompt_tokens_est,
            completion_tokens=max_tokens,
        )
        await guard.check_budget(
            provider=self.name,
            model=target_model,
            estimated_cost_usd=estimated_cost_usd,
        )

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "stream": True,
        }
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]

        headers = {"Content-Type": "application/json"}
        if cfg["api_key"]:
            headers["Authorization"] = f"Bearer {cfg['api_key']}"

        async with httpx.AsyncClient(timeout=cfg["timeout"]) as http:
            async with http.stream(
                "POST",
                f"{cfg['base_url']}/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                resp.raise_for_status()
                async for raw_line in resp.aiter_lines():
                    if not raw_line:
                        continue
                    if raw_line.startswith("data: "):
                        raw_line = raw_line[len("data: "):]
                    if raw_line.strip() == "[DONE]":
                        yield Token(text="", finish_reason="stop")
                        # Streamed responses don't carry usage. We record the
                        # preflight estimate as a lower bound; if a backend
                        # ever does emit usage in [DONE], wire it in here.
                        await guard.record_usage(
                            provider=self.name,
                            model=target_model,
                            cost_usd=estimated_cost_usd,
                            phase="openai_compat.stream",
                            success=True,
                            is_local=is_local,
                        )
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

    # ------------------------------------------------------------------
    # embed()
    # ------------------------------------------------------------------

    async def embed(self, text: str, model: str) -> list[float]:
        """Embedding via /v1/embeddings, cost-guarded."""
        return await self.embed_with(text=text, model=model)

    async def embed_with(
        self,
        *,
        text: str,
        model: str,
        **kwargs: Any,
    ) -> list[float]:
        """Embeddings call with optional kwargs (``_provider_config``,
        ``_site_config``, ``_cost_guard``).

        Splitting :meth:`embed` (Protocol-conformant 2-arg shape) from
        :meth:`embed_with` (kwargs-aware) keeps the Protocol clean while
        letting tests inject fakes via the dispatcher.
        """
        cfg = await self._resolve_config(kwargs)
        self._ensure_enabled(cfg)

        target_model = model or cfg["default_embed_model"]

        guard = self._cost_guard(kwargs)
        # Embedding cost depends almost entirely on input tokens.
        input_tokens_est = max(1, math.ceil(len(text or "") / 4))
        is_local = is_local_base_url(cfg["base_url"])
        estimated_cost_usd = _compute_cost(
            cfg,
            model=target_model,
            prompt_tokens=input_tokens_est,
            completion_tokens=0,
        )
        await guard.check_budget(
            provider=self.name,
            model=target_model,
            estimated_cost_usd=estimated_cost_usd,
        )

        client = self._build_sdk_client(cfg)

        async def _do_embed() -> Any:
            return await client.embeddings.create(input=text, model=target_model)

        try:
            response = await self._resilience.run(
                _do_embed,
                op_name="embed",
                task_id=kwargs.get("task_id"),
            )
        except CostGuardExhausted:
            raise
        except Exception as exc:
            logger.warning("[openai_compat] embed(%s) failed: %s", target_model, exc)
            raise

        if hasattr(response, "model_dump"):
            data = response.model_dump()
        elif isinstance(response, dict):
            data = response
        else:  # pragma: no cover - defensive
            data = {}

        items = data.get("data") or []
        if not items:
            raise ValueError(
                f"OpenAICompatProvider: embed response had no data (model={target_model})",
            )
        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or input_tokens_est)

        actual_cost = _compute_cost(
            cfg,
            model=target_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
        )
        await guard.record_usage(
            provider=self.name,
            model=target_model,
            cost_usd=actual_cost,
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            phase="openai_compat.embed",
            success=True,
            is_local=is_local,
        )

        embedding = items[0].get("embedding")
        if embedding is None:
            raise ValueError("OpenAICompatProvider: embedding payload missing 'embedding' field")
        return list(embedding)

    # ------------------------------------------------------------------
    # Convenience: still expose the local-detection helper for callers
    # that want to special-case routing without re-importing cost_guard.
    # ------------------------------------------------------------------

    @staticmethod
    def is_local(base_url: str | None) -> bool:
        return is_local_base_url(base_url)
