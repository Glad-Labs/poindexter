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
import time
from typing import TYPE_CHECKING, Any

from plugins.config import PluginConfig
from plugins.registry import get_all_llm_providers
from services.gpu_scheduler import gpu

if TYPE_CHECKING:
    from services.vram_budget import ModelArch

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

    _tracer = _NoopTracer()  # type: ignore[assignment]


# Positive sentinel cost so check_budget runs its ACCUMULATED daily/monthly
# spend checks (it short-circuits on estimated<=0). Pre-call we don't know the
# token count; the meaningful gate is "have we already hit today's cap", which
# the accumulated comparison enforces regardless of this call's exact cost.
_PAID_PREFLIGHT_SENTINEL_USD = 1e-9


def _is_paid_llm_call(model: str, provider_config: dict[str, Any] | None) -> bool:
    """Best-effort: is this dispatch a PAID (non-local) LLM call?

    Local Ollama/vLLM/etc. cost $0 and are exempt from budget gating. Mirrors
    the litellm provider's ``_enforce_paid_endpoint_policy`` local-detection
    (same ``_LOCAL_MODEL_PREFIXES`` + ``is_local_base_url``) so the two stay
    consistent. CONSERVATIVE toward LOCAL — only returns True when the target
    is unambiguously non-local — so a misclassification can never block a free
    local call.
    """
    from services.cost_guard import is_local_base_url
    from services.llm_providers.litellm_provider import _LOCAL_MODEL_PREFIXES

    model = (model or "").strip()
    cfg = provider_config or {}

    # Inline http(s) model string is itself an api_base.
    if model.startswith("http"):
        return not is_local_base_url(model)
    # Explicit api_base pointing at a non-local host (the DB-swap bypass the
    # litellm policy guards — treat as paid).
    api_base = cfg.get("api_base")
    if api_base and not is_local_base_url(api_base):
        return True
    # Model namespace prefix. A bare name uses the default ollama prefix (local).
    if "/" not in model:
        return False
    prefix = model.split("/", 1)[0].lower()
    return prefix not in _LOCAL_MODEL_PREFIXES


def _gpu_serialize_local_dispatch(
    model: str, provider_config: dict[str, Any] | None,
) -> bool:
    """Whether this dispatch should hold ``gpu.lock("ollama")`` around
    ``provider.complete`` (GPU-serialize fix).

    Local Ollama inference shares the one GPU with media renders (image-gen / wan).
    Content stages already wrap their LLM work in ``gpu.lock("ollama")``, but
    scheduled worker jobs (topic research, SEO, newsletter) reach the LLM only
    through ``dispatch_complete`` — so without this they can load the ~19 GB
    writer concurrently with an in-flight render and exceed 32 GB VRAM.
    Serializing every LOCAL dispatch through the GPU lock (reentrant, so a
    no-op inside a stage that already holds it) makes that contention
    impossible by construction. Paid/cloud calls use no local GPU, so they
    never serialize. Operators with abundant VRAM opt out via
    ``gpu_serialize_llm_dispatch=false``.
    """
    if _is_paid_llm_call(model, provider_config):
        return False
    try:
        from services.container_registry import get_container

        container = get_container()
        if container is None:
            # No container bootstrapped (CLI early paths, tests) — default to
            # serializing; the reentrant lock is cheap when uncontended.
            return True
        return container.site_config.get_bool("gpu_serialize_llm_dispatch", True)
    except Exception:  # silent-ok: a config-read failure defaults to the SAFE
        # behavior (serialize) — raising would break every dispatch, and a
        # per-call warning/finding on this hot path would be log noise.
        return True


# ---------------------------------------------------------------------------
# VRAM budget guard (Plan 2 Task 5)
# ---------------------------------------------------------------------------
# Before a LOCAL dispatch takes the GPU lock, project the model footprint at the
# requested num_ctx and clamp it down if it would breach the dedicated-VRAM
# budget (total - desktop_reserve). This stops the NVIDIA driver spilling VRAM
# into system RAM — a WDDM sysmem-fallback that freezes the desktop. The pure
# math lives in services/vram_budget.py; here is the wiring + config read.


async def _budget_inputs(provider_config: dict[str, Any]) -> tuple[float, float, float]:
    """``(total_gb, desktop_reserve_gb, kv_bytes_per_elem)`` from app_settings.

    ``gpu_vram_total_gb`` defaults to ``"auto"`` — the total VRAM pool detected
    across all GPUs via :class:`GPURegistry`. An explicit number overrides.
    Falls back to the seeded defaults when no container is bootstrapped
    (CLI, tests).
    """
    from services.container_registry import get_container
    from services.vram_budget import kv_bytes_per_elem

    container = get_container()
    if container is None:
        return 32.0, 3.0, kv_bytes_per_elem("q8_0")
    sc = container.site_config
    raw = (sc.get("gpu_vram_total_gb", "auto") or "auto").strip().lower()
    if raw in ("", "auto"):
        total = await _resolve_auto_total(container)
    else:
        try:
            total = float(raw)
        except ValueError:
            total = await _resolve_auto_total(container)
    reserve = sc.get_float("gpu_desktop_reserve_gb", 3.0)
    kv = kv_bytes_per_elem(sc.get("ollama_kv_cache_type", "q8_0") or "q8_0")
    return total, reserve, kv


async def _resolve_auto_total(container: Any) -> float:
    """Detected VRAM pool (GB), or the configurable fallback + a fail-loud
    finding when detection has never succeeded."""
    detected = await container.gpu_registry.total_vram_gb()
    if detected is not None:
        return detected
    fallback = container.site_config.get_float("gpu_vram_autodetect_fallback_gb", 32.0)
    from utils.findings import emit_finding

    emit_finding(
        source="vram_budget",
        kind="vram_autodetect_failed",
        severity="warn",
        title="GPU VRAM auto-detect unavailable",
        body=(
            f"Could not read total GPU VRAM from Prometheus; using fallback "
            f"{fallback}GB for the num_ctx budget guard until detection recovers."
        ),
        dedup_key="vram_autodetect_failed",
    )
    return fallback


async def _read_arch_for_budget(model: str) -> ModelArch | None:
    """Read the model arch off Ollama ``/api/show`` (cached per tag). Returns
    None when unreachable so the clamp fails open."""
    import httpx

    from services.container_registry import get_container
    from services.vram_budget import read_model_arch

    base = "http://host.docker.internal:11434"
    container = get_container()
    if container is not None:
        sc = container.site_config
        base = sc.get("ollama_base_url", "") or sc.get("ollama_host", "") or base
    async with httpx.AsyncClient() as client:
        return await read_model_arch(model, base, client)


async def _clamp_num_ctx_to_budget(
    pool: Any, model: str, num_ctx: int, provider_config: dict[str, Any],
) -> int:
    """Clamp num_ctx to the largest context that fits the VRAM budget.

    Fails OPEN — returns num_ctx unchanged when the model arch can't be read
    (the no-sysmem-fallback driver setting is the backstop). Emits a ``warn``
    finding when it actually clamps so the operator sees the budget pressure.
    """
    from services.vram_budget import (
        estimate_kv_cache_gb,
        estimate_model_vram_gb,
        fits,
        max_safe_num_ctx,
    )

    arch = await _read_arch_for_budget(model)
    if arch is None:
        return num_ctx
    total, reserve, kv = await _budget_inputs(provider_config)
    footprint = estimate_model_vram_gb(arch, estimate_kv_cache_gb(arch, num_ctx, kv))
    ok, _headroom = fits(footprint, total, reserve)
    if ok:
        return num_ctx
    safe = max_safe_num_ctx(arch, total, reserve, kv)
    from utils.findings import emit_finding

    emit_finding(
        source="vram_budget",
        kind="num_ctx_clamped",
        severity="warn",
        title=f"num_ctx clamped {num_ctx}->{safe} for {model}",
        body=(
            f"Requested context {num_ctx} would put {model} at ~{footprint:.1f}GB, "
            f"over the VRAM budget (total {total}GB - desktop reserve {reserve}GB). "
            f"Clamped to {safe} to avoid a sysmem spill / desktop freeze."
        ),
        dedup_key=f"num_ctx_clamp_{model}",
    )
    return safe


def _vram_guard_enabled() -> bool:
    """Master switch for the clamp. Default ON; a config-read failure leaves the
    guard ON (its clamp fails open anyway) rather than blocking the dispatch."""
    try:
        from services.container_registry import get_container

        container = get_container()
        if container is None:
            return True
        return container.site_config.get_bool("vram_budget_guard_enabled", True)
    except Exception:  # silent-ok: a config-read failure on this hot dispatch
        # path defaults to the guard's SAFE behavior (clamp stays ON; it fails
        # open if arch is unreadable). Per-call logging would be noise here —
        # mirrors _gpu_serialize_local_dispatch's silent-ok config read.
        return True


async def _enforce_budget_if_paid(
    *,
    pool: Any,
    provider: Any,
    model: str,
    provider_config: dict[str, Any] | None,
) -> None:
    """Enforce the daily/monthly USD cap before a PAID LLM call fires (audit H2).

    The cost-guard dollar cap was previously only enforced by the gemini /
    anthropic plugin providers — the PRIMARY litellm dispatch path had no
    spend cap, so once ``allow_paid_base_url=true`` was set there was no dollar
    backstop. This closes that gap at the single dispatch choke point.

    Local calls ($0) are a no-op — zero behavior change to the local path. For
    paid calls, ``check_budget`` raises ``CostGuardExhausted`` when the
    accumulated daily/monthly spend is already at the cap, and fails CLOSED
    (raises) when cost_logs can't be read (the M4 strict-read fix).
    """
    if not _is_paid_llm_call(model, provider_config):
        return
    from services.cost_guard import CostGuard

    site_config = None
    try:
        from services.integrations.shared_context import get_site_config

        site_config = get_site_config()
    except Exception:  # noqa: BLE001 — DI seam optional; CostGuard uses defaults
        site_config = None

    guard = CostGuard(pool=pool, site_config=site_config)
    await guard.check_budget(
        provider=getattr(provider, "name", "unknown"),
        model=model,
        estimated_cost_usd=_PAID_PREFLIGHT_SENTINEL_USD,
    )


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
    *,
    task_id: str | None = None,
    phase: str = "dispatch_complete",
    **kwargs: Any,
) -> Any:
    """One-shot ``complete`` call using the tier's configured provider.

    Convenience wrapper that combines ``get_provider`` +
    ``get_provider_config`` + ``provider.complete``. New call sites
    should use this instead of importing OllamaClient directly.

    On success, writes a row to ``cost_logs`` so every LLM call routed
    through the dispatcher shows up in cost accounting. Callers can
    supply ``task_id`` + ``phase`` for richer attribution; missing
    values fall back to ``None`` / ``"dispatch_complete"`` so historical
    callers don't need to be updated. The write is best-effort — a
    failure never breaks the call path.
    """
    with _tracer.start_as_current_span("llm.dispatch_complete") as span:
        span.set_attribute("llm.tier", tier)
        span.set_attribute("llm.model", model)
        span.set_attribute("llm.messages.count", len(messages))
        if task_id:
            span.set_attribute("llm.task_id", task_id)
        span.set_attribute("llm.phase", phase)
        started = time.monotonic()
        provider = None
        provider_config: dict[str, Any] | None = None
        try:
            provider = await get_provider(pool, tier)
            span.set_attribute("llm.provider.name", provider.name)
            provider_config = await get_provider_config(pool, provider.name)
            kwargs.setdefault("_provider_config", provider_config)
            # Spend cap on the PRIMARY path (audit H2). No-op for local calls;
            # raises CostGuardExhausted (fails closed) for an over-budget or
            # budget-unverifiable PAID call, before the provider fires.
            await _enforce_budget_if_paid(
                pool=pool, provider=provider, model=model,
                provider_config=provider_config,
            )
            # VRAM budget guard: clamp num_ctx to the dedicated-VRAM budget
            # before the GPU lock, so a context-hungry writer can't project a
            # footprint that spills into system RAM (the WDDM freeze). Only
            # fires when a caller actually threads num_ctx (the LiteLLM writer
            # path); a guard error logs and falls through to the requested ctx
            # rather than breaking the dispatch.
            req_num_ctx = kwargs.get("num_ctx")
            if req_num_ctx and _vram_guard_enabled():
                try:
                    kwargs["num_ctx"] = await _clamp_num_ctx_to_budget(
                        pool, model, int(req_num_ctx), provider_config or {},
                    )
                except Exception as exc:  # guard must never break dispatch
                    logger.warning("[vram_budget] clamp skipped (%s)", exc)
            # GPU-serialize fix: hold gpu.lock("ollama") around a LOCAL provider
            # call so it can't run concurrently with a media render (which
            # evicts Ollama on gpu.lock("video") and needs the freed VRAM). The
            # lock is reentrant, so this is a no-op inside content stages that
            # already hold it; cloud calls use no local GPU and skip it.
            if _gpu_serialize_local_dispatch(model, provider_config):
                async with gpu.lock(
                    "ollama", model=model, task_id=task_id, phase=phase,
                ):
                    result = await provider.complete(
                        messages=messages, model=model, **kwargs,
                    )
            else:
                result = await provider.complete(
                    messages=messages, model=model, **kwargs,
                )
            # Completion has .prompt_tokens / .completion_tokens when the
            # provider populates them; safe getattr for non-standard shapes.
            pt = getattr(result, "prompt_tokens", 0)
            ct = getattr(result, "completion_tokens", 0)
            span.set_attribute("llm.tokens.prompt", int(pt or 0))
            span.set_attribute("llm.tokens.completion", int(ct or 0))
            finish = getattr(result, "finish_reason", "")
            if finish:
                span.set_attribute("llm.finish_reason", finish)
            duration_ms = int((time.monotonic() - started) * 1000)
            await _record_dispatch_cost(
                pool=pool,
                provider=provider,
                model=model,
                result=result,
                task_id=task_id,
                phase=phase,
                duration_ms=duration_ms,
                success=True,
                provider_config=provider_config,
            )
            return result
        except Exception as exc:
            span.record_exception(exc)
            # Log a failure row too so cost-tracking dashboards see
            # the call attempt — important for understanding how often
            # paid providers are failing closed vs swallowing budget.
            duration_ms = int((time.monotonic() - started) * 1000)
            await _record_dispatch_cost(
                pool=pool,
                provider=provider,
                model=model,
                result=None,
                task_id=task_id,
                phase=phase,
                duration_ms=duration_ms,
                success=False,
                provider_config=provider_config,
                error=str(exc)[:300],
            )
            raise


async def _record_dispatch_cost(
    *,
    pool: Any,
    provider: Any,
    model: str,
    result: Any,
    task_id: str | None,
    phase: str,
    duration_ms: int,
    success: bool,
    provider_config: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Write a cost_logs row for the dispatch_complete call.

    Best-effort — never raises out of the call path. Uses LiteLLM's
    ``response_cost`` when present (the litellm provider stamps it on
    ``result.raw``) for PAID calls; for local calls (``_is_paid_llm_call``
    is False) any reported ``response_cost`` is discarded as phantom and
    cost falls back to GPU power × duration via CostGuard, so cost_usd
    reflects true electricity spend rather than a fictional cloud price.
    ``electricity_kwh`` is populated for every local call so the Cost &
    Analytics dashboard can show per-call energy attribution.
    """
    if pool is None:
        return
    try:
        provider_name = getattr(provider, "name", "unknown") if provider else "unknown"
        raw: dict[str, Any] = getattr(result, "raw", {}) or {} if result is not None else {}
        cost_usd = 0.0
        rc = raw.get("response_cost") if isinstance(raw, dict) else None
        if rc is not None:
            try:
                cost_usd = float(rc)
            except (TypeError, ValueError):
                cost_usd = 0.0
        prompt_tokens = int(getattr(result, "prompt_tokens", 0) or 0) if result is not None else 0
        completion_tokens = int(getattr(result, "completion_tokens", 0) or 0) if result is not None else 0
        total_tokens = int(getattr(result, "total_tokens", 0) or 0) if result is not None else 0

        # Local calls cost $0 in API terms — discard any phantom response_cost.
        # A bare local tag like "llama3.2:3b" collides with a *hosted* llama
        # price in litellm.model_cost, so LiteLLM stamps ~$0.0135/call on free
        # local inference (the 2026-06-21 triage incident: 311 calls logged
        # $4.16 and tripped DailySpendOverBudget). Zero it here and let the
        # electricity fallback below record true GPU cost. Mirrors the budget
        # gate (_enforce_budget_if_paid), which already treats this call as
        # free — the cost log and the gate must agree on local-vs-paid.
        if not _is_paid_llm_call(model, provider_config):
            cost_usd = 0.0

        # Local calls attribute electricity for the dashboard/savings view, but
        # cost_usd STAYS 0 — the API axis is paid-cloud only, and the electricity
        # BILL comes from the brain's measured power rows, not per-call estimates
        # (cost-control attribution spec, P1 invariant). Paid/cloud calls keep
        # their LiteLLM-reported price (cost_usd != 0.0 skips this branch).
        electricity_kwh: float | None = None
        if cost_usd == 0.0:
            try:
                from services.cost_guard import CostGuard

                site_config = None
                try:
                    from services.integrations.shared_context import get_site_config
                    site_config = get_site_config()
                except Exception:  # noqa: BLE001
                    pass
                guard = CostGuard(pool=pool, site_config=site_config)
                electricity_kwh = guard.estimate_local_kwh(duration_ms=duration_ms)
                # NOTE: do NOT set cost_usd = kwh_to_usd(...) here — that conflated
                # electricity onto the API axis and double-counted the brain's
                # measured power. cost_usd stays 0 for local calls.
            except Exception:  # noqa: BLE001 — best-effort; $0 is still a valid fallback
                pass

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO cost_logs (
                    task_id, phase, model, provider,
                    input_tokens, output_tokens, total_tokens,
                    cost_usd, cost_type, duration_ms, success,
                    electricity_kwh, error_message, created_at, updated_at
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, NOW(), NOW()
                )
                """,
                task_id, phase, model, provider_name,
                prompt_tokens, completion_tokens, total_tokens,
                cost_usd, "inference", duration_ms, success,
                electricity_kwh, error,
            )
    except Exception as e:
        # Demote to debug so we don't pollute logs on every call when
        # something's structurally wrong (schema drift, pool exhausted).
        # The cost_log_write_failed audit_log path is reserved for
        # cost_guard.record; the dispatcher's auto-log is additive
        # observability and not load-bearing for budget enforcement.
        logger.debug("dispatcher: cost_logs auto-write skipped: %s", e)


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
            # Symmetric with dispatch_complete — inject _provider_config so
            # the embed call honors the same paid-endpoint policy +
            # base_url config as completion. Closes a runaway-cost bypass
            # where embeddings on a paid backend escaped the gate.
            provider_config = await get_provider_config(pool, provider.name)
            return await provider.embed(
                text=text, model=model, _provider_config=provider_config,
            )
        except Exception as exc:
            span.record_exception(exc)
            raise
