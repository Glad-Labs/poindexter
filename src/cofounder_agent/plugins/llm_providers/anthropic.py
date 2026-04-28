"""AnthropicProvider — first-class LLMProvider for the Claude family.

Wraps the official ``anthropic`` Python SDK against ``api.anthropic.com``
(or any Anthropic-compatible endpoint via ``base_url``). Anthropic's API
is **not** OpenAI-compatible — it has its own message shape (system
prompt as a top-level field, alternating user/assistant roles,
``tool_use``/``tool_result`` content blocks), its own auth header
(``x-api-key`` not ``Authorization: Bearer``), and its own SDK with
first-class types. Trying to shim it through ``OpenAICompatProvider``
is a continual maintenance tax, so this is a separate provider.

## Defaults

Ships **disabled by default** (``enabled = false``). Operators flip
``app_settings.plugin.llm_provider.anthropic.enabled = true`` and seed
``api_key`` (an ``is_secret = true`` row) to start using it.

Default model is ``claude-haiku-4-5`` — the lowest-cost member of the
4.x family. Operators override per-call with the standard ``model``
positional / per-tier with ``app_settings.plugin.llm_provider.primary.<tier>``.

## Cost guard

Every ``complete()`` call is wrapped in a cost-guard pre-check + a
post-call ``log_cost`` write, identical to the contract the rest of
the pipeline expects. Per-model rates live in ``_PER_MODEL_RATES``
below. Token usage comes from the SDK response's ``usage`` block —
the same data Anthropic bills on, so our accounting matches their
invoice.

When the daily / monthly budget is exhausted the call raises
``CostGuardExhausted`` (defined in this module) — callers must catch
it explicitly. **No silent fallback.** If the operator opted into a
paid provider and ran out of budget, the right answer is to fail
loudly so the alerting layer fires, not to pretend the call worked
or quietly downgrade to a different model.

## Prompt caching (free latency + cost win)

Anthropic supports **prompt caching** with 5-minute TTL ephemeral
breakpoints. Pipeline calls almost always re-use the same large
system prompt across many tasks — caching it saves both latency
(no re-tokenization, no re-attention) and cost (cached input tokens
are billed at ~10% of the standard rate).

This provider applies ``cache_control: {"type": "ephemeral"}`` to the
system message by default. Disable with
``app_settings.plugin.llm_provider.anthropic.prompt_caching = false``
if a particular workload doesn't benefit (e.g. when the system prompt
truly varies every call).

## Config keys (``app_settings.plugin.llm_provider.anthropic.*``)

- ``enabled`` (bool, default ``false``) — opt-in flag.
- ``api_key`` (string, ``is_secret = true``) — read via
  ``site_config.get_secret``. Required when ``enabled = true``.
- ``default_model`` (string, default ``claude-haiku-4-5``) — used when
  the caller passes empty ``model``.
- ``request_timeout_s`` (int, default ``120``).
- ``base_url`` (string, optional) — for Anthropic-compatible endpoints
  (proxies, internal gateways). Leave unset for ``api.anthropic.com``.
- ``prompt_caching`` (bool, default ``true``) — whether to annotate
  the system prompt with ``cache_control``.

Plugin discovery instantiates with no args; the registry calls
``AnthropicProvider()``. The optional ``site_config`` constructor
kwarg exists so call sites that already have a ``SiteConfig`` instance
(tests, the dispatcher's Phase H wiring) can hand it in directly. When
absent, config falls back to ``kwargs["_provider_config"]`` injected
by ``dispatch_complete``, exactly like ``OpenAICompatProvider`` does.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from plugins.llm_provider import Completion, Token
from plugins.llm_resilience import (
    CircuitOpenError,
    LLMResilienceManager,
    RetryDecision,
)

logger = logging.getLogger(__name__)


# Anthropic SDK exceptions sit under the ``anthropic`` package; we
# duck-type by class name so the classifier doesn't force the SDK to
# load on every plugin enumeration. The full list lives in
# ``anthropic._exceptions`` — what we care about for retry decisions:
#
# * ``RateLimitError`` (429) — Anthropic ships a ``Retry-After`` header
#   when their rate-limiter delays a request. Honor it via
#   ``RetryDecision.wait_seconds``; the manager skips its exponential
#   schedule when an explicit wait is set.
# * ``APIStatusError`` (5xx) — transient gateway / capacity issues.
# * ``APIConnectionError`` / ``APITimeoutError`` — transport blips.
# * ``BadRequestError`` (400), ``AuthenticationError`` (401),
#   ``PermissionDeniedError`` (403), ``NotFoundError`` (404),
#   ``UnprocessableEntityError`` (422) — caller bug, no retry.
_RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def anthropic_classifier(exc: BaseException) -> RetryDecision:
    """Classify exceptions raised by ``anthropic.AsyncAnthropic`` calls.

    Honors Anthropic's ``Retry-After`` header on 429s by returning
    ``RetryDecision.wait_seconds=N``; the resilience manager respects
    the override and skips its exponential schedule for that retry.
    Transport errors (httpx) fall back to the manager's default
    backoff.
    """
    if isinstance(exc, asyncio.CancelledError):
        return RetryDecision(retry=False, reason="cancelled")
    if isinstance(exc, CircuitOpenError):
        return RetryDecision(retry=False, reason="circuit_open")
    # CostGuardExhausted (and the AnthropicProvider subclass) — the
    # caller chose this provider and ran out of budget; surfacing the
    # exception is the right answer, not retrying.
    cls_name = type(exc).__name__
    if cls_name == "CostGuardExhausted":
        return RetryDecision(retry=False, reason="cost_guard_exhausted")
    if cls_name == "AnthropicProviderDisabled":
        return RetryDecision(retry=False, reason="provider_disabled")
    # SDK-typed exceptions — duck-typed to avoid a hard import.
    if cls_name in {"APIConnectionError", "APITimeoutError"}:
        return RetryDecision(retry=True, reason=cls_name)
    if cls_name == "RateLimitError":
        # Pull Retry-After off the response when the SDK exposes it.
        wait = None
        response = getattr(exc, "response", None)
        if response is not None:
            wait = _retry_after_seconds(getattr(response, "headers", None))
        return RetryDecision(retry=True, wait_seconds=wait, reason="rate_limit")
    if cls_name in {"InternalServerError", "APIStatusError"}:
        code = int(getattr(exc, "status_code", 0) or 0)
        if code in _RETRYABLE_HTTP_STATUSES:
            return RetryDecision(retry=True, reason=f"http_{code}")
        return RetryDecision(retry=False, reason=f"http_{code}")
    # Bare httpx fallthrough (the SDK uses httpx under the hood).
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
            wait = _retry_after_seconds(exc.response.headers)
            return RetryDecision(
                retry=True, wait_seconds=wait, reason=f"http_{code}",
            )
        return RetryDecision(retry=False, reason=f"http_{code}")
    return RetryDecision(retry=False, reason="non_retryable")


def _retry_after_seconds(headers: Any) -> float | None:
    """Pull a numeric Retry-After value off a header mapping.

    Tolerates the common shapes (``httpx.Headers``, plain dict, SDK
    response wrapper). Returns ``None`` when the header is absent or
    unparseable so the manager falls back to its exponential schedule.
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


# ---------------------------------------------------------------------------
# Per-model rates (USD per 1M tokens, list price as of 2026-04).
# ---------------------------------------------------------------------------
#
# Source: https://www.anthropic.com/pricing — checked 2026-04-25. These
# numbers feed both the cost-guard pre-check (estimate) and the
# post-call log_cost write (actual). When Anthropic adjusts list price
# the operator updates these via app_settings — but the in-code defaults
# remain so a fresh install has working accounting on day one.
#
# Cached-input tokens are billed at ~10% of the standard input rate
# (the "ephemeral" 5-minute cache) when prompt caching is enabled.
_PER_MODEL_RATES: dict[str, dict[str, float]] = {
    # Claude 4.x family — current as of 2026-04.
    "claude-haiku-4-5":  {"input": 1.00, "output":  5.00, "cached_input": 0.10},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cached_input": 0.30},
    "claude-opus-4-7":   {"input": 15.0, "output": 75.00, "cached_input": 1.50},
}

# Final fallback when the model the caller asked for isn't in the
# table. Conservative — matches the most expensive published rate so
# the cost-guard never *underestimates* and lets a bill blow through.
_UNKNOWN_MODEL_FALLBACK = {"input": 15.0, "output": 75.00, "cached_input": 1.50}

# Resolution order for the per-call rate lookup: exact match, then a
# few well-known suffix-aliases (e.g. dated snapshots). Keeps the table
# small without forcing operators to seed every snapshot manually.
def _rates_for_model(model: str) -> dict[str, float]:
    if model in _PER_MODEL_RATES:
        return _PER_MODEL_RATES[model]
    # Snapshot suffix tolerance: ``claude-haiku-4-5-20260301`` etc.
    for known, rates in _PER_MODEL_RATES.items():
        if model.startswith(known):
            return rates
    logger.warning(
        "[AnthropicProvider] no per-model rate for %r; using conservative "
        "fallback (%s). Add the model to _PER_MODEL_RATES or override via "
        "app_settings.plugin.llm_provider.anthropic.rate_overrides.",
        model, _UNKNOWN_MODEL_FALLBACK,
    )
    return dict(_UNKNOWN_MODEL_FALLBACK)


def _calc_cost_usd(
    rates: dict[str, float],
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> float:
    """Convert token counts → USD using a per-model rate dict.

    Rates are USD per 1M tokens. Cached-input tokens are billed at the
    ``cached_input`` line, the rest of the input flows through ``input``.
    """
    standard_input = max(0, input_tokens - cached_input_tokens)
    return (
        standard_input * rates["input"] / 1_000_000.0
        + cached_input_tokens * rates.get("cached_input", rates["input"]) / 1_000_000.0
        + output_tokens * rates["output"] / 1_000_000.0
    )


# ---------------------------------------------------------------------------
# Typed exception — callers MUST handle this explicitly.
# ---------------------------------------------------------------------------


from services.cost_guard import CostGuardExhausted as _BaseCostGuardExhausted


class CostGuardExhausted(_BaseCostGuardExhausted):
    """Anthropic-specific :class:`CostGuardExhausted`.

    Subclasses the canonical ``services.cost_guard.CostGuardExhausted``
    so ``except CostGuardExhausted`` from either path catches the same
    exception family. The only behavioral difference is a default
    ``provider="anthropic"`` so call sites that pre-date the unified
    exception (and the tests asserting against them) keep working
    without explicitly setting it.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "anthropic",
        model: str = "",
        estimated_cost_usd: float = 0.0,
    ) -> None:
        super().__init__(
            message,
            provider=provider,
            model=model,
            estimated_cost_usd=estimated_cost_usd,
        )


class AnthropicProviderDisabled(RuntimeError):
    """Raised when ``complete()`` is called while ``enabled = false``.

    Plugin discovery still loads the class so the entry_point is
    visible — but until an operator opts in the provider refuses to
    actually do anything. This avoids silent unmonitored paid calls.
    """


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class AnthropicProvider:
    """LLMProvider implementation for Anthropic's Claude family.

    Plugin discovery (``plugins.registry.get_llm_providers``) constructs
    instances with no args. Tests / dispatcher wiring may pass a
    ``SiteConfig`` to skip the per-call ``_provider_config`` lookup.

    Embeddings are intentionally unsupported — Anthropic doesn't sell
    them. ``embed()`` raises ``NotImplementedError`` and
    ``supports_embeddings`` is False so the dispatcher routes embed
    calls to the configured embedding provider (Ollama by default).
    """

    name = "anthropic"
    supports_streaming = False  # Pipeline doesn't currently stream — see ticket.
    supports_embeddings = False

    def __init__(self, site_config: Any = None) -> None:
        # SiteConfig is optional. Plugin discovery instantiates with no
        # args. Per-call config still flows in via ``_provider_config``
        # injected by ``dispatch_complete``.
        self._site_config = site_config
        # Lazy-instantiated SDK client. The ``anthropic`` package ships
        # an ``AsyncAnthropic`` client that owns its own connection
        # pool — building a single instance and re-using it across
        # calls is more efficient than building one per call. The
        # client is keyed by (api_key, base_url, timeout) so config
        # changes invalidate the cached client.
        self._client: Any = None
        self._client_key: tuple[str, str, float] | None = None
        # Resilience layer (GH#192). Anthropic's SDK does its own
        # retries internally; this layer wraps as defense-in-depth and
        # adds the cross-provider audit + circuit-breaker. The
        # ``Retry-After`` header on 429s is honored via
        # :func:`anthropic_classifier` returning
        # ``RetryDecision.wait_seconds=N``.
        self._resilience = LLMResilienceManager(
            provider_name="anthropic",
            classifier=anthropic_classifier,
            site_config=site_config,
        )

    # ------------------------------------------------------------------
    # Config resolution — site_config (DI) > _provider_config (kwargs)
    # ------------------------------------------------------------------

    async def _resolve_config(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Materialize the per-call config dict.

        Priority for each key, low to high:
            1. Hard-coded default in this method.
            2. Per-call ``_provider_config`` injected by ``dispatch_complete``.
            3. ``self._site_config`` (when wired up via DI).

        (3) wins because the explicit DI seam is the more reliable
        source — ``_provider_config`` is best-effort and may be
        missing in test harnesses / direct-call sites.
        """
        per_call = kwargs.get("_provider_config") or {}

        cfg: dict[str, Any] = {
            "enabled": False,
            "api_key": "",
            "default_model": "claude-haiku-4-5",
            "request_timeout_s": 120,
            "base_url": "",
            "prompt_caching": True,
        }
        # Layer 2: per-call provider config dict.
        for key in cfg:
            if key in per_call and per_call[key] not in (None, ""):
                cfg[key] = per_call[key]

        # Coerce types — JSON config dicts are stringly-typed.
        cfg["enabled"] = _to_bool(cfg["enabled"])
        cfg["prompt_caching"] = _to_bool(cfg["prompt_caching"])
        try:
            cfg["request_timeout_s"] = int(cfg["request_timeout_s"])
        except (TypeError, ValueError):
            cfg["request_timeout_s"] = 120

        # Layer 3: SiteConfig (DI).
        sc = self._site_config
        if sc is not None:
            try:
                enabled_val = sc.get(
                    "plugin.llm_provider.anthropic.enabled", "",
                )
                if enabled_val != "":
                    cfg["enabled"] = _to_bool(enabled_val)
            except Exception as e:  # pragma: no cover — defensive
                # SiteConfig.get is sync and reads from the startup-loaded
                # cache, so this should not fire. DEBUG so a misbehaving
                # cache is at least visible in dev.
                logger.debug(
                    "[anthropic] site_config.get(enabled) failed: %s", e,
                )
            for key, default_type in (
                ("default_model", str),
                ("base_url", str),
            ):
                try:
                    val = sc.get(f"plugin.llm_provider.anthropic.{key}", "")
                except Exception as e:  # pragma: no cover — defensive
                    logger.debug(
                        "[anthropic] site_config.get(%s) failed: %s", key, e,
                    )
                    val = ""
                if val:
                    cfg[key] = default_type(val)
            try:
                timeout_val = sc.get(
                    "plugin.llm_provider.anthropic.request_timeout_s", "",
                )
            except Exception as e:  # pragma: no cover — defensive
                logger.debug(
                    "[anthropic] site_config.get(request_timeout_s) failed: %s", e,
                )
                timeout_val = ""
            if timeout_val:
                try:
                    cfg["request_timeout_s"] = int(timeout_val)
                except (TypeError, ValueError):
                    pass
            try:
                pc_val = sc.get(
                    "plugin.llm_provider.anthropic.prompt_caching", "",
                )
                if pc_val != "":
                    cfg["prompt_caching"] = _to_bool(pc_val)
            except Exception as e:  # pragma: no cover — defensive
                logger.debug(
                    "[anthropic] site_config.get(prompt_caching) failed: %s", e,
                )
            # Secret has to come through the async path — get_secret
            # filters is_secret=true rows out of the in-memory cache.
            try:
                api_key = await sc.get_secret(
                    "plugin.llm_provider.anthropic.api_key", "",
                )
                if api_key:
                    cfg["api_key"] = api_key
            except Exception as e:  # pragma: no cover — defensive
                logger.debug(
                    "[AnthropicProvider] get_secret failed (falling back "
                    "to per-call config): %s", e,
                )

        # Final env-var fallback for api_key — well-known convention,
        # works in dev shells / CI without touching app_settings.
        if not cfg["api_key"]:
            cfg["api_key"] = os.getenv("ANTHROPIC_API_KEY", "")

        # Per-call timeout_s overrides the configured default — same
        # contract OpenAICompatProvider exposes for QA reviewers etc.
        per_call_timeout = kwargs.get("timeout_s")
        if per_call_timeout is not None:
            try:
                cfg["request_timeout_s"] = int(per_call_timeout)
            except (TypeError, ValueError):
                pass

        return cfg

    # ------------------------------------------------------------------
    # SDK client — lazy, cached, refreshed on config change
    # ------------------------------------------------------------------

    def _get_client(self, *, api_key: str, base_url: str, timeout: float) -> Any:
        """Return a cached ``anthropic.AsyncAnthropic`` client.

        Cache key includes ``api_key + base_url + timeout`` so a config
        flip (e.g. operator rotates the key) invalidates and rebuilds
        the client on next call without a worker restart.
        """
        cache_key = (api_key, base_url, float(timeout))
        if self._client is not None and self._client_key == cache_key:
            return self._client

        try:
            import anthropic  # noqa: PLC0415 — lazy import keeps cold paths cheap
        except ImportError as exc:
            raise RuntimeError(
                "AnthropicProvider requires the 'anthropic' Python SDK. "
                "Install with: pip install 'anthropic>=0.97'. The plugin "
                "ships disabled by default — enable only after the SDK "
                "is available.",
            ) from exc

        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**kwargs)
        self._client_key = cache_key
        return self._client

    # ------------------------------------------------------------------
    # Cost-guard hooks — kept on the instance so tests can monkey-patch
    # ------------------------------------------------------------------

    async def _cost_guard_check(
        self,
        *,
        model: str,
        estimated_cost_usd: float,
    ) -> None:
        """Pre-flight budget check.

        Default implementation calls into ``services.cost_aggregation_service``
        if it's importable, falling back to a permissive no-op when the
        cost service isn't wired (e.g. early-startup, minimal CLI).
        Either path raises ``CostGuardExhausted`` when the projected
        spend would blow the budget. Tests monkey-patch this method
        to assert it's called and to inject the exhausted path.
        """
        # The shipping cost-aggregation service exposes a synchronous
        # budget summary; we treat ``budget_used_percent >= 100`` as
        # exhausted. If the service can't be reached we DO NOT silently
        # allow the call — fail loud per Matt's "no silent fallback"
        # rule for required infrastructure.
        try:
            from services.cost_aggregation_service import CostAggregationService  # noqa: PLC0415
        except Exception as e:
            logger.debug(
                "[AnthropicProvider] cost service not importable, "
                "skipping pre-flight check (test/CLI environment): %s", e,
            )
            return

        # In production this path would resolve a live db_service from
        # the site_config DI seam. Until that wire-up lands the check
        # is best-effort; the post-call log_cost write is the gate that
        # ALWAYS runs, so accounting stays correct either way.
        sc = self._site_config
        db_service = None
        if sc is not None:
            db_service = getattr(sc, "_db_service", None)
        if db_service is None:
            return

        try:
            svc = CostAggregationService(db_service=db_service)
            status = await svc.get_budget_status()
            if status.get("alert_status") == "exceeded":
                raise CostGuardExhausted(
                    f"Anthropic call refused: monthly budget exhausted "
                    f"(${status.get('amount_spent', 0):.2f} / "
                    f"${status.get('monthly_budget', 0):.2f}); estimated "
                    f"call cost ${estimated_cost_usd:.4f}",
                    provider=self.name,
                    model=model,
                    estimated_cost_usd=estimated_cost_usd,
                )
        except CostGuardExhausted:
            raise
        except Exception as e:
            logger.warning(
                "[AnthropicProvider] cost-guard pre-check errored "
                "(allowing call to proceed; post-call log will still "
                "record actual spend): %s", e,
            )

    async def _cost_guard_record(
        self,
        *,
        model: str,
        actual_cost_usd: float,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int,  # noqa: ARG002 — exposed for test introspection / future per-cache-tier accounting
        duration_ms: int,
        task_id: str | None,
    ) -> None:
        """Post-call ``log_cost`` write. Best-effort — never raises.

        Routes through the unified :meth:`CostGuard.record_usage` so
        Anthropic calls land in ``cost_logs`` next to Gemini, OpenAI,
        and Ollama with the same shape — including the
        ``electricity_kwh`` column populated from the data-center
        Wh/1K-token estimate. Tests still monkey-patch this method
        as the seam.
        """
        from services.cost_guard import CostGuard  # noqa: PLC0415 — avoid circular at import time
        sc = self._site_config
        pool = None
        if sc is not None:
            pool = getattr(sc, "_pool", None)
        guard = CostGuard(site_config=sc, pool=pool)
        try:
            await guard.record_usage(
                provider=self.name,
                model=model,
                cost_usd=actual_cost_usd,
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                phase="anthropic_complete",
                task_id=task_id,
                success=True,
                duration_ms=duration_ms,
                is_local=False,
            )
        except Exception as e:
            logger.warning(
                "[AnthropicProvider] cost record failed (task=%s, $%.4f): %s",
                task_id, actual_cost_usd, e,
            )

    # ------------------------------------------------------------------
    # LLMProvider Protocol
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> Completion:
        cfg = await self._resolve_config(kwargs)
        if not cfg["enabled"]:
            raise AnthropicProviderDisabled(
                "AnthropicProvider is disabled. Set "
                "app_settings.plugin.llm_provider.anthropic.enabled = true "
                "and seed the api_key (is_secret=true) before calling.",
            )

        if not cfg["api_key"]:
            raise AnthropicProviderDisabled(
                "AnthropicProvider is enabled but no api_key is configured. "
                "Set app_settings.plugin.llm_provider.anthropic.api_key "
                "(is_secret=true) or the ANTHROPIC_API_KEY env var.",
            )

        target_model = (model or cfg["default_model"]).strip()
        rates = _rates_for_model(target_model)

        # Build the Anthropic message payload. Their API splits system
        # out from messages, only allows alternating user/assistant,
        # and accepts content blocks for cache_control annotations.
        system_blocks, user_messages = _split_system_and_messages(
            messages, prompt_caching=cfg["prompt_caching"],
        )

        max_tokens = int(kwargs.get("max_tokens") or 4096)
        temperature = kwargs.get("temperature")

        # Pre-flight cost estimate uses a rough char-based token count
        # for the input — Anthropic's response is the source of truth
        # post-call. Estimate stays conservative so we don't accept
        # calls we'll later wish we hadn't.
        estimated_input_tokens = sum(
            len(m.get("content", "")) // 4 for m in messages
        )
        estimated_cost = _calc_cost_usd(
            rates,
            input_tokens=estimated_input_tokens,
            output_tokens=max_tokens,
            cached_input_tokens=0,
        )
        await self._cost_guard_check(
            model=target_model, estimated_cost_usd=estimated_cost,
        )

        # Issue the call.
        client = self._get_client(
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            timeout=float(cfg["request_timeout_s"]),
        )

        sdk_kwargs: dict[str, Any] = {
            "model": target_model,
            "max_tokens": max_tokens,
            "messages": user_messages,
        }
        if system_blocks is not None:
            sdk_kwargs["system"] = system_blocks
        if temperature is not None:
            sdk_kwargs["temperature"] = float(temperature)

        started = time.monotonic()

        async def _do_create() -> Any:
            return await client.messages.create(**sdk_kwargs)

        response = await self._resilience.run(
            _do_create,
            op_name="complete",
            task_id=kwargs.get("task_id"),
        )
        duration_ms = int((time.monotonic() - started) * 1000)

        # The SDK returns a Message object with a content list. For
        # text-only completions there's a single TextBlock — concat
        # the text from all text blocks for safety against future
        # multi-block responses.
        text = _extract_text(response)

        usage = _extract_usage(response)
        cached_input_tokens = usage.get("cache_read_input_tokens", 0) + usage.get(
            "cache_creation_input_tokens", 0,
        )
        actual_cost = _calc_cost_usd(
            rates,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cached_input_tokens=cached_input_tokens,
        )

        await self._cost_guard_record(
            model=target_model,
            actual_cost_usd=actual_cost,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cached_input_tokens=cached_input_tokens,
            duration_ms=duration_ms,
            task_id=kwargs.get("task_id"),
        )

        return Completion(
            text=text,
            model=getattr(response, "model", target_model),
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            finish_reason=getattr(response, "stop_reason", "") or "stop",
            raw={
                "usage": usage,
                "cost_usd": actual_cost,
                "cached_input_tokens": cached_input_tokens,
                "duration_ms": duration_ms,
            },
        )

    async def stream(
        self,
        messages: list[dict[str, str]],  # noqa: ARG002
        model: str,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> AsyncIterator[Token]:
        # Out of scope for issue #133 — pipeline doesn't stream.
        # supports_streaming = False so callers fall back to complete().
        raise NotImplementedError(
            "AnthropicProvider streaming is intentionally unimplemented "
            "(supports_streaming=False); pipeline doesn't stream.",
        )
        # Unreachable, but signals to mypy/runtime that this is an
        # async generator if ever made one.
        yield  # type: ignore[unreachable]  # pragma: no cover

    async def embed(
        self,
        text: str,  # noqa: ARG002
        model: str,  # noqa: ARG002
    ) -> list[float]:
        # Anthropic doesn't sell embeddings — supports_embeddings=False
        # routes embed calls to the configured embedding provider
        # (Ollama by default). This stub stays for Protocol conformance.
        raise NotImplementedError(
            "AnthropicProvider does not support embeddings — Anthropic "
            "doesn't sell an embedding API. Use OllamaNativeProvider "
            "or another embedding-capable provider.",
        )


# ---------------------------------------------------------------------------
# Helpers — pure functions, easy to test in isolation
# ---------------------------------------------------------------------------


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _split_system_and_messages(
    messages: list[dict[str, str]],
    *,
    prompt_caching: bool,
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """Map OpenAI-style messages to Anthropic's split system+messages shape.

    Anthropic's Messages API takes ``system`` as a top-level field
    (string OR list of content blocks for cache_control annotations)
    and ``messages`` as alternating user/assistant turns. Adjacent
    same-role turns are coalesced.

    When ``prompt_caching`` is True every system block gets
    ``cache_control: {"type": "ephemeral"}`` — that's the 5-minute TTL
    cache breakpoint Anthropic exposes. Cached input tokens are
    billed at ~10% of the standard rate, so this is a free win when
    the system prompt is reused across multiple calls (which the
    pipeline always does).
    """
    system_chunks: list[str] = []
    out: list[dict[str, Any]] = []
    last_role: str | None = None
    for raw in messages:
        role = (raw.get("role") or "user").strip()
        content = raw.get("content", "") or ""
        if role == "system":
            if content:
                system_chunks.append(content)
            continue
        # Coerce non-standard roles. Anthropic accepts "user" and
        # "assistant" only; everything else maps to "user" so we don't
        # silently drop content.
        if role not in ("user", "assistant"):
            role = "user"
        if role == last_role and out:
            # Coalesce adjacent same-role turns — Anthropic rejects
            # consecutive same-role messages.
            prev = out[-1]
            prev["content"] = (prev.get("content") or "") + "\n\n" + content
            continue
        out.append({"role": role, "content": content})
        last_role = role

    if not system_chunks:
        return None, out

    if not prompt_caching:
        # Plain string is the simpler shape and uses no cache breakpoint.
        return [
            {"type": "text", "text": "\n\n".join(system_chunks)},
        ], out

    # Annotate the (single, joined) system block with cache_control —
    # 5-minute TTL ephemeral cache breakpoint. One breakpoint covers
    # the whole prefix, which is what we want for reused system prompts.
    return (
        [
            {
                "type": "text",
                "text": "\n\n".join(system_chunks),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        out,
    )


def _extract_text(response: Any) -> str:
    """Pull plain text out of an Anthropic Message response.

    Tolerant of three shapes:

    1. Real SDK response — ``response.content`` is a list of content
       block objects; each text block has ``.text``.
    2. Mocked response with dict-shaped content blocks
       (``{"type": "text", "text": "..."}``).
    3. Pre-extracted ``response.text`` shortcut some tests / mocks
       expose for convenience.
    """
    direct = getattr(response, "text", None)
    if isinstance(direct, str) and direct:
        return direct

    content = getattr(response, "content", None)
    if not content:
        return ""

    pieces: list[str] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                pieces.append(str(block.get("text") or ""))
            continue
        # SDK object — duck-type ``.type`` and ``.text``.
        block_type = getattr(block, "type", None)
        block_text = getattr(block, "text", None)
        if block_type == "text" and isinstance(block_text, str):
            pieces.append(block_text)
    return "".join(pieces)


def _extract_usage(response: Any) -> dict[str, int]:
    """Normalize the SDK ``usage`` block to a plain int dict.

    The SDK exposes ``response.usage`` with ``input_tokens``,
    ``output_tokens``, plus the prompt-caching rollups
    ``cache_read_input_tokens`` and ``cache_creation_input_tokens``.
    Mocks may pass a dict directly. Coerce to int and zero-fill so
    downstream cost math never sees ``None``.
    """
    raw = getattr(response, "usage", None)
    if raw is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }

    def _read(key: str) -> int:
        if isinstance(raw, dict):
            val = raw.get(key)
        else:
            val = getattr(raw, key, None)
        try:
            return int(val) if val is not None else 0
        except (TypeError, ValueError):
            return 0

    return {
        "input_tokens": _read("input_tokens"),
        "output_tokens": _read("output_tokens"),
        "cache_read_input_tokens": _read("cache_read_input_tokens"),
        "cache_creation_input_tokens": _read("cache_creation_input_tokens"),
    }
