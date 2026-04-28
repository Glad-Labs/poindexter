"""GeminiProvider — Google AI Studio (key-based) LLM plugin.

Wraps the official ``google-genai`` Python SDK against the Gemini
Developer API. The SDK exposes capabilities the OpenAI-compat shim
silently drops (very long context, native search grounding, video
understanding); a dedicated provider preserves them.

Tracks GitHub issue ``Glad-Labs/poindexter#134``.

Per-install configuration lives in ``app_settings`` under the
``plugin.llm_provider.gemini.*`` namespace:

- ``enabled`` (default ``"false"``) — gate. Disabled plugins fail
  loud on every method call rather than silently no-oping.
- ``api_key`` (``is_secret=true``) — fetched via
  ``site_config.get_secret`` so it stays out of the in-memory
  config dump.
- ``default_model`` (default ``"gemini-2.5-flash"``) — used when
  callers pass an empty model string.
- ``request_timeout_s`` (default ``120``) — SDK timeout. Per-call
  ``timeout_s`` kwarg overrides per-call.
- ``embed_model`` (default ``"text-embedding-004"``) — embedding
  model. Operators can flip to ``gemini-embedding-2-preview`` once
  it leaves preview.

Cost-guard integration is mandatory and uniform across ``complete()``
and ``embed()``: pre-call ``check_budget``, post-call ``record_usage``.
On :class:`CostGuardExhausted` the provider re-raises so the
dispatcher can fall back to a free provider (Ollama).

Out of scope for this ticket (tracked separately):
- Multimodal inputs (vision, audio, video, files).
- Tool use / function calling.
- Vertex AI service-account auth.
- Streaming via ``stream()`` — the Protocol method is implemented,
  but it currently emits the full completion as a single ``Token``;
  true SSE streaming lands in a follow-up.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx

from plugins.llm_provider import Completion, Token
from plugins.llm_resilience import (
    CircuitOpenError,
    LLMResilienceManager,
    RetryDecision,
)
from services.cost_guard import CostGuard, CostGuardExhausted
from services.logger_config import get_logger

logger = get_logger(__name__)


# Gemini SDK's exception hierarchy lives at ``google.genai.errors``;
# the retryable members are:
#
# * ``ClientError`` — 4xx, mostly non-retryable (auth, bad model, etc.)
# * ``ServerError`` — 5xx, transient.
# * ``APIError`` — base class with ``code`` attribute (HTTP status).
# * Quota / rate-limit errors come back as 429 with code on the
#   ``APIError``; some SDK builds raise ``ResourceExhausted`` instead.
#
# We duck-type by class name so the classifier doesn't force the SDK
# import — the ``google-genai`` package isn't a hard dep on every
# install (the provider ships disabled by default).
_RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def gemini_classifier(exc: BaseException) -> RetryDecision:
    """Classify exceptions raised by ``google.genai.Client`` calls.

    Retry on:
        * ``ServerError`` and any ``APIError`` with a 408/425/429/5xx
          ``code`` attribute. ``ResourceExhausted`` (quota) maps to
          429.
        * Transport errors via ``httpx`` — the SDK uses httpx under
          the hood so connection drops surface as the same exception
          types.

    Do NOT retry on:
        * ``ClientError`` with a 4xx code (auth, schema, bad model).
        * ``CostGuardExhausted`` / ``GeminiProviderError`` — caller
          policy decision, not transient.
    """
    if isinstance(exc, asyncio.CancelledError):
        return RetryDecision(retry=False, reason="cancelled")
    if isinstance(exc, CircuitOpenError):
        return RetryDecision(retry=False, reason="circuit_open")
    if isinstance(exc, CostGuardExhausted):
        return RetryDecision(retry=False, reason="cost_guard_exhausted")
    cls_name = type(exc).__name__
    if cls_name in {"GeminiProviderError"}:
        return RetryDecision(retry=False, reason="provider_disabled")
    # SDK-typed exceptions (ducked).
    if cls_name in {"ServerError", "InternalServerError"}:
        return RetryDecision(retry=True, reason=cls_name)
    if cls_name in {"ResourceExhausted", "ResourceExhaustedError"}:
        # Gemini quota exhausted — treat like a 429. SDK doesn't ship
        # a Retry-After value here (the API returns the wait suggestion
        # in the message body), so fall back to the manager's
        # exponential schedule.
        return RetryDecision(retry=True, reason="resource_exhausted")
    if cls_name in {"APIError", "ClientError"}:
        code = int(getattr(exc, "code", 0) or 0)
        if code in _RETRYABLE_HTTP_STATUSES:
            return RetryDecision(retry=True, reason=f"http_{code}")
        return RetryDecision(retry=False, reason=f"http_{code}")
    # httpx fallback.
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
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in _RETRYABLE_HTTP_STATUSES:
            return RetryDecision(retry=True, reason=f"http_{code}")
        return RetryDecision(retry=False, reason=f"http_{code}")
    return RetryDecision(retry=False, reason="non_retryable")


_DEFAULT_MODEL = "gemini-2.5-flash"
_DEFAULT_EMBED_MODEL = "text-embedding-004"
_DEFAULT_TIMEOUT_S = 120


class GeminiProviderError(RuntimeError):
    """Raised when a GeminiProvider call cannot proceed.

    Disjoint from :class:`CostGuardExhausted` (which is raised when
    a budget would be blown). This one fires when configuration is
    missing or the SDK isn't installed, so the dispatcher can
    distinguish "this provider is misconfigured" from "this
    provider would overspend right now".
    """


class GeminiProvider:
    """LLMProvider implementation for the Gemini Developer API.

    The plugin is constructed by ``plugins.registry`` with no args,
    so configuration is read lazily from ``site_config`` on each
    call (lets app_settings edits take effect without a worker
    restart). A ``site_config`` parameter is accepted for direct
    instantiation in tests; in production it's populated from the
    dispatcher-injected ``_site_config`` kwarg.

    The ``google-genai`` SDK is imported lazily on first call so
    plugin discovery doesn't pay an import cost when the operator
    hasn't enabled the plugin.
    """

    name = "gemini"
    supports_streaming = True
    supports_embeddings = True

    def __init__(self, site_config: Any = None):
        self._site_config = site_config
        # Cached SDK client. Re-built when api_key changes between calls
        # so a runtime rotation in app_settings takes effect on the next
        # call without a restart.
        self._client: Any = None
        self._client_api_key: str | None = None
        # Resilience layer (GH#192). Wraps the SDK call as
        # defense-in-depth on top of google-genai's own retries.
        self._resilience = LLMResilienceManager(
            provider_name="gemini",
            classifier=gemini_classifier,
            site_config=site_config,
        )

    # ------------------------------------------------------------------
    # Config resolution helpers
    # ------------------------------------------------------------------

    def _resolve_site_config(self, kwargs: dict[str, Any]) -> Any:
        """Pick the best available SiteConfig.

        Priority: dispatcher-injected ``_site_config`` kwarg
        > constructor-provided > None. ``None`` means no config —
        everything except an explicit per-call API key will fall
        through to defaults, and ``enabled`` will read as False.
        """
        injected = kwargs.get("_site_config")
        if injected is not None:
            return injected
        return self._site_config

    def _is_enabled(self, site_config: Any) -> bool:
        if site_config is None:
            return False
        try:
            raw = site_config.get("plugin.llm_provider.gemini.enabled", "false")
        except Exception as e:
            # Defensive — SiteConfig.get is sync and reads from cache.
            # DEBUG so a misbehaving cache surfaces in dev.
            logger.debug("[gemini] site_config.get(enabled) failed: %s", e)
            return False
        return str(raw).strip().lower() in ("true", "1", "yes", "on")

    def _resolve_default_model(self, site_config: Any) -> str:
        if site_config is None:
            return _DEFAULT_MODEL
        try:
            return (
                str(
                    site_config.get(
                        "plugin.llm_provider.gemini.default_model",
                        _DEFAULT_MODEL,
                    )
                )
                or _DEFAULT_MODEL
            )
        except Exception as e:
            logger.debug(
                "[gemini] site_config.get(default_model) failed: %s", e,
            )
            return _DEFAULT_MODEL

    def _resolve_embed_model(self, site_config: Any) -> str:
        if site_config is None:
            return _DEFAULT_EMBED_MODEL
        try:
            return (
                str(
                    site_config.get(
                        "plugin.llm_provider.gemini.embed_model",
                        _DEFAULT_EMBED_MODEL,
                    )
                )
                or _DEFAULT_EMBED_MODEL
            )
        except Exception as e:
            logger.debug(
                "[gemini] site_config.get(embed_model) failed: %s", e,
            )
            return _DEFAULT_EMBED_MODEL

    def _resolve_timeout_s(self, site_config: Any, kwargs: dict[str, Any]) -> int:
        per_call = kwargs.get("timeout_s")
        if per_call is not None:
            try:
                return int(per_call)
            except (TypeError, ValueError):
                pass
        if site_config is None:
            return _DEFAULT_TIMEOUT_S
        try:
            raw = site_config.get(
                "plugin.llm_provider.gemini.request_timeout_s",
                str(_DEFAULT_TIMEOUT_S),
            )
            return int(raw) if raw else _DEFAULT_TIMEOUT_S
        except (TypeError, ValueError):
            return _DEFAULT_TIMEOUT_S

    async def _resolve_api_key(self, site_config: Any) -> str:
        if site_config is None:
            return ""
        try:
            value = await site_config.get_secret(
                "plugin.llm_provider.gemini.api_key", ""
            )
            return str(value or "")
        except Exception as e:
            logger.warning("[GeminiProvider] get_secret failed: %s", e)
            return ""

    # ------------------------------------------------------------------
    # Client construction
    # ------------------------------------------------------------------

    def _get_client(self, api_key: str, timeout_s: int) -> Any:
        """Build (or rebuild) a ``google.genai.Client`` for ``api_key``.

        Rebuilds the client when the API key changes so credential
        rotation through ``app_settings`` doesn't require a restart.
        Raises :class:`GeminiProviderError` if the SDK isn't
        installed — the operator-facing message names the package so
        the operator knows what to ``pip install``.

        Skips the SDK import when a cached client matches the api_key
        so tests can inject a ``_FakeClient`` (or production code can
        run a single-shot extra call) without the
        ``google-genai`` package being installed.
        """
        if self._client is not None and self._client_api_key == api_key:
            return self._client

        try:
            from google import genai  # type: ignore[import-untyped]
            from google.genai import types as genai_types  # noqa: F401
        except ImportError as e:
            raise GeminiProviderError(
                "google-genai SDK is not installed. Add `google-genai` to "
                "the worker image / poetry environment to enable the "
                "Gemini provider plugin."
            ) from e

        timeout_ms = max(1, int(timeout_s)) * 1000
        try:
            http_options = genai_types.HttpOptions(timeout=timeout_ms)
        except Exception:
            # SDKs older than 1.0 didn't accept timeout in HttpOptions —
            # fall back to constructing without it. The SDK's own
            # default kicks in.
            http_options = None

        kwargs: dict[str, Any] = {"api_key": api_key}
        if http_options is not None:
            kwargs["http_options"] = http_options
        self._client = genai.Client(**kwargs)
        self._client_api_key = api_key
        return self._client

    def _build_cost_guard(self, site_config: Any, kwargs: dict[str, Any]) -> CostGuard:
        """Return a fresh :class:`CostGuard` bound to the call context.

        Tests can short-circuit construction by passing
        ``_cost_guard`` as a kwarg. In production the dispatcher
        seeds ``_pool`` (asyncpg pool) on the site_config so cost
        rows can be inserted; absent that, the guard runs in
        offline mode (limit checks succeed, record_usage is a
        log line).
        """
        injected = kwargs.get("_cost_guard")
        if isinstance(injected, CostGuard):
            return injected
        pool = kwargs.get("_pool")
        if pool is None and site_config is not None:
            pool = getattr(site_config, "_pool", None)
        return CostGuard(site_config=site_config, pool=pool)

    # ------------------------------------------------------------------
    # Message conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _messages_to_contents(
        messages: list[dict[str, str]],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Translate OpenAI-style ``messages`` into the Gemini shape.

        Returns ``(system_instruction, contents)``. System messages
        are concatenated into a single string passed via the SDK's
        ``system_instruction`` field; user / assistant turns become
        ``{role, parts}`` dicts.

        Gemini calls the assistant role ``"model"``, not
        ``"assistant"``. We translate transparently so callers
        don't have to remember.
        """
        system_chunks: list[str] = []
        contents: list[dict[str, Any]] = []
        for m in messages:
            role = (m.get("role") or "user").lower()
            content = m.get("content", "") or ""
            if role == "system":
                if content:
                    system_chunks.append(content)
                continue
            mapped_role = "model" if role in ("assistant", "model") else "user"
            contents.append({"role": mapped_role, "parts": [{"text": content}]})
        if not contents:
            # Gemini requires at least one user-content entry. Empty
            # string keeps it well-formed without inventing a turn.
            contents.append({"role": "user", "parts": [{"text": ""}]})
        return "\n\n".join(system_chunks), contents

    # ------------------------------------------------------------------
    # LLMProvider Protocol
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> Completion:
        site_config = self._resolve_site_config(kwargs)
        if not self._is_enabled(site_config):
            raise GeminiProviderError(
                "GeminiProvider is disabled. Set "
                "`plugin.llm_provider.gemini.enabled=true` in app_settings "
                "and configure `plugin.llm_provider.gemini.api_key` to use it."
            )

        api_key = await self._resolve_api_key(site_config)
        if not api_key:
            raise GeminiProviderError(
                "GeminiProvider is enabled but no api_key is set. Add the "
                "secret at `plugin.llm_provider.gemini.api_key` "
                "(is_secret=true) before calling complete()."
            )

        resolved_model = (model or "").strip() or self._resolve_default_model(
            site_config
        )
        timeout_s = self._resolve_timeout_s(site_config, kwargs)

        # Cost guard pre-check. Estimating from the input string is
        # rough, but it gives us a per-call ceiling before we hand the
        # bill to Google. Output budget is the configured request cap
        # or a conservative 1024 fallback.
        cost_guard = self._build_cost_guard(site_config, kwargs)
        prompt_chars = sum(len(m.get("content", "") or "") for m in messages)
        estimated_prompt_tokens = max(1, prompt_chars // 4)
        max_tokens = int(kwargs.get("max_tokens") or 1024)
        try:
            estimate = await cost_guard.estimate_cost(
                provider=self.name,
                model=resolved_model,
                prompt_tokens=estimated_prompt_tokens,
                completion_tokens=max_tokens,
            )
            await cost_guard.check_budget(
                provider=self.name,
                model=resolved_model,
                estimated_cost_usd=estimate,
            )
        except CostGuardExhausted:
            raise

        client = self._get_client(api_key, timeout_s)
        system_instruction, contents = self._messages_to_contents(messages)
        config = self._build_generate_config(
            kwargs,
            system_instruction=system_instruction,
            max_tokens=max_tokens,
        )

        async def _do_generate() -> Any:
            return await client.aio.models.generate_content(
                model=resolved_model,
                contents=contents,
                config=config,
            )

        try:
            response = await self._resilience.run(
                _do_generate,
                op_name="complete",
                task_id=kwargs.get("task_id"),
            )
        except Exception as e:
            logger.error(
                "[GeminiProvider] generate_content failed (model=%s): %s",
                resolved_model,
                e,
            )
            await cost_guard.record_usage(
                provider=self.name,
                model=resolved_model,
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=0.0,
                phase=str(kwargs.get("phase", "")),
                task_id=kwargs.get("task_id"),
                success=False,
            )
            raise

        text = self._extract_text(response)
        prompt_tokens, completion_tokens, total_tokens = self._extract_usage(
            response
        )
        finish_reason = self._extract_finish_reason(response)

        await cost_guard.record_usage(
            provider=self.name,
            model=resolved_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=None,
            phase=str(kwargs.get("phase", "")),
            task_id=kwargs.get("task_id"),
            success=True,
        )

        return Completion(
            text=text,
            model=resolved_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            raw=self._response_to_raw(response),
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> AsyncIterator[Token]:
        """Best-effort streaming.

        The SDK exposes ``generate_content_stream``; full SSE
        translation lives behind a follow-up ticket. For now this
        emits the entire ``complete()`` result as a single token so
        callers that key off ``stream()`` still get a usable shape.
        """
        completion = await self.complete(messages, model, **kwargs)
        yield Token(
            text=completion.text,
            finish_reason=completion.finish_reason or "stop",
            raw=completion.raw,
        )

    async def embed(self, text: str, model: str = "") -> list[float]:
        site_config = self._site_config
        if not self._is_enabled(site_config):
            raise GeminiProviderError(
                "GeminiProvider is disabled. Cannot embed via Gemini "
                "while `plugin.llm_provider.gemini.enabled` is false."
            )

        api_key = await self._resolve_api_key(site_config)
        if not api_key:
            raise GeminiProviderError(
                "GeminiProvider is enabled but no api_key is set. Add the "
                "secret at `plugin.llm_provider.gemini.api_key` "
                "(is_secret=true) before calling embed()."
            )

        resolved_model = (model or "").strip() or self._resolve_embed_model(
            site_config
        )
        timeout_s = self._resolve_timeout_s(site_config, {})

        # Cost guard pre-check — embeddings are cheap but unlimited
        # cheap calls add up fast on a tight monthly budget. Be
        # conservative with the token estimate (chars/4) and assume
        # zero output tokens (embedding endpoints don't emit text).
        cost_guard = self._build_cost_guard(site_config, {})
        estimated_prompt_tokens = max(1, len(text or "") // 4)
        try:
            estimate = await cost_guard.estimate_cost(
                provider=self.name,
                model=resolved_model,
                prompt_tokens=estimated_prompt_tokens,
                completion_tokens=0,
            )
            await cost_guard.check_budget(
                provider=self.name,
                model=resolved_model,
                estimated_cost_usd=estimate,
            )
        except CostGuardExhausted:
            raise

        client = self._get_client(api_key, timeout_s)

        async def _do_embed() -> Any:
            return await client.aio.models.embed_content(
                model=resolved_model,
                contents=[text],
            )

        try:
            response = await self._resilience.run(
                _do_embed,
                op_name="embed",
            )
        except Exception as e:
            logger.error(
                "[GeminiProvider] embed_content failed (model=%s): %s",
                resolved_model,
                e,
            )
            await cost_guard.record_usage(
                provider=self.name,
                model=resolved_model,
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=0.0,
                phase="embed",
                task_id=None,
                success=False,
            )
            raise

        vector = self._extract_embedding_vector(response)
        await cost_guard.record_usage(
            provider=self.name,
            model=resolved_model,
            prompt_tokens=estimated_prompt_tokens,
            completion_tokens=0,
            cost_usd=None,
            phase="embed",
            task_id=None,
            success=True,
        )
        return vector

    # ------------------------------------------------------------------
    # SDK response unpacking — small enough to keep here, but factored
    # out of the methods above so tests can target each piece directly
    # without standing up the full client mock.
    # ------------------------------------------------------------------

    def _build_generate_config(
        self,
        kwargs: dict[str, Any],
        *,
        system_instruction: str,
        max_tokens: int,
    ) -> Any:
        """Translate standard ``LLMProvider`` kwargs to the Gemini config.

        Falls back to a plain dict when ``GenerateContentConfig`` isn't
        importable (older google-genai versions). The SDK accepts
        either form on the ``config`` parameter.
        """
        config_kwargs: dict[str, Any] = {}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if "temperature" in kwargs and kwargs["temperature"] is not None:
            config_kwargs["temperature"] = float(kwargs["temperature"])
        if "top_p" in kwargs and kwargs["top_p"] is not None:
            config_kwargs["top_p"] = float(kwargs["top_p"])
        if max_tokens:
            config_kwargs["max_output_tokens"] = int(max_tokens)
        try:
            from google.genai import types as genai_types  # type: ignore[import-untyped]
            return genai_types.GenerateContentConfig(**config_kwargs)
        except Exception:
            return config_kwargs

    @staticmethod
    def _extract_text(response: Any) -> str:
        text = getattr(response, "text", None)
        if isinstance(text, str) and text:
            return text
        # Fall through: walk candidates → content → parts.
        try:
            candidates = getattr(response, "candidates", None) or []
            for cand in candidates:
                content = getattr(cand, "content", None)
                if content is None:
                    continue
                parts = getattr(content, "parts", None) or []
                for part in parts:
                    part_text = getattr(part, "text", None)
                    if isinstance(part_text, str) and part_text:
                        return part_text
        except Exception as e:
            logger.warning(
                "[gemini] walking candidates/content/parts to extract "
                "text failed; returning empty string: %s", e,
            )
        return ""

    @staticmethod
    def _extract_usage(response: Any) -> tuple[int, int, int]:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return 0, 0, 0
        prompt = int(getattr(usage, "prompt_token_count", 0) or 0)
        completion = int(getattr(usage, "candidates_token_count", 0) or 0)
        total = int(getattr(usage, "total_token_count", 0) or (prompt + completion))
        return prompt, completion, total

    @staticmethod
    def _extract_finish_reason(response: Any) -> str:
        try:
            candidates = getattr(response, "candidates", None) or []
            if not candidates:
                return "stop"
            reason = getattr(candidates[0], "finish_reason", None)
            if reason is None:
                return "stop"
            # Gemini exposes the finish_reason as an enum; ``.name`` is
            # the canonical string ("STOP", "MAX_TOKENS", "SAFETY"...).
            name = getattr(reason, "name", None)
            return str(name).lower() if name else str(reason).lower()
        except Exception:
            return "stop"

    @staticmethod
    def _response_to_raw(response: Any) -> dict[str, Any]:
        """Best-effort serialization for the ``Completion.raw`` field.

        Pydantic models on the SDK side expose ``model_dump`` /
        ``to_dict``. When neither exists we fall back to a small
        snapshot so observability tooling at least has finish_reason
        and usage available without re-querying.
        """
        for attr in ("model_dump", "to_dict"):
            dumper = getattr(response, attr, None)
            if callable(dumper):
                try:
                    result = dumper()
                    if isinstance(result, dict):
                        return result
                except Exception:
                    continue
        return {
            "finish_reason": GeminiProvider._extract_finish_reason(response),
            "usage_metadata": {
                "prompt_token_count": GeminiProvider._extract_usage(response)[0],
                "candidates_token_count": GeminiProvider._extract_usage(response)[1],
                "total_token_count": GeminiProvider._extract_usage(response)[2],
            },
        }

    @staticmethod
    def _extract_embedding_vector(response: Any) -> list[float]:
        """Pull the first embedding's ``values`` from an EmbedContentResponse."""
        embeddings = getattr(response, "embeddings", None)
        if embeddings is None and isinstance(response, dict):
            embeddings = response.get("embeddings")
        if not embeddings:
            raise ValueError(
                "GeminiProvider.embed: response had no embeddings"
            )
        first = embeddings[0]
        values = getattr(first, "values", None)
        if values is None and isinstance(first, dict):
            values = first.get("values")
        if not values:
            raise ValueError(
                "GeminiProvider.embed: first embedding had no values"
            )
        return [float(v) for v in values]


# Re-exported so callers can ``except`` the typed exceptions without
# importing services.cost_guard themselves.
__all__ = [
    "GeminiProvider",
    "GeminiProviderError",
    "CostGuardExhausted",
]


# ``asyncio`` is referenced through the lazy SDK callbacks; the import
# here keeps lint quiet about an unused-but-load-bearing dependency
# when the module is imported in offline test runs.
_ = asyncio
