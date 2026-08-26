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

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from plugins.config import PluginConfig
from plugins.registry import get_all_llm_providers
from services.gpu_scheduler import gpu
from services.task_context import current_task_id
from utils.exception_format import describe_exception

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


def _routes_to_pinned_endpoint(
    model: str, provider_config: dict[str, Any] | None,
) -> bool:
    """Whether this LOCAL dispatch is served by a GPU-pinned second endpoint.

    ``model_api_base_overrides`` routes an eviction-prone model to its own
    Ollama instance on its own card (glad-labs-stack#2051 — qwen3-vl on the
    3090). Such an instance contends for nothing the shared GPU lock protects:
    ``_unload_ollama_models`` only evicts ``ollama_base_url``, and image-gen /
    wan render on ``pipeline_gpu_index`` only. Holding the lock for it merely
    queues it behind the writer until ``gpu_lock_acquire_timeout_seconds``
    fires — which fail-softs the vision rail to "unavailable, passing open".

    Conservative toward SERIALIZING: any doubt (unreadable map, import
    failure) returns False so the call keeps the pre-existing lock behaviour.
    """
    try:
        from services.llm_providers.litellm_provider import pinned_api_base_for

        return pinned_api_base_for(model, provider_config) is not None
    except Exception:  # silent-ok: a routing read must never break dispatch;
        # falling back to "not pinned" preserves the legacy serialize path.
        return False


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

    A model pinned to its OWN endpoint is also exempt: it owns a different
    card, so the shared lock protects it from nothing and only starves it
    (see :func:`_routes_to_pinned_endpoint`). Operators whose second instance
    shares one physical GPU — where two servers genuinely do contend — keep
    the legacy behaviour with ``gpu_pinned_endpoint_skips_lock=false``.
    """
    if _is_paid_llm_call(model, provider_config):
        return False
    pinned = _routes_to_pinned_endpoint(model, provider_config)
    try:
        from services.container_registry import get_container

        container = get_container()
        if container is None:
            # No container bootstrapped (CLI early paths, tests) — serializing
            # is the SAFE default only for models that share the default GPU.
            return not pinned
        site_config = container.site_config
        if not site_config.get_bool("gpu_serialize_llm_dispatch", True):
            return False
        if pinned and site_config.get_bool(
            "gpu_pinned_endpoint_skips_lock", True,
        ):
            return False
        return True
    except Exception:  # silent-ok: a config-read failure defaults to the SAFE
        # behavior (serialize) — raising would break every dispatch, and a
        # per-call warning/finding on this hot path would be log noise.
        return not pinned


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


def _resolve_default_num_ctx(
    phase: str, model: str, provider_config: dict[str, Any] | None,
) -> int | None:
    """Per-phase num_ctx for a LOCAL dispatch that didn't thread one.

    Closes the seam gap where callers other than the writer (vision QA via
    ``MultiModelQA._vision_complete``, media renders, scheduled research)
    reached ``dispatch_complete`` without a ``num_ctx`` — so Ollama loaded the
    model at its Modelfile default (e.g. gemma-4-31B at 262144) and sailed past
    the clamp, which only fires when num_ctx is present.

    Returns the per-phase resolved context for local calls, or ``None`` for
    paid/cloud calls — num_ctx is meaningless there and would force a wasted
    ``/api/show`` against a non-local model in the clamp.
    """
    if _is_paid_llm_call(model, provider_config):
        return None
    from services.container_registry import get_container
    from services.ollama_client import resolve_num_ctx

    container = get_container()
    site_config = container.site_config if container is not None else None
    return resolve_num_ctx(phase, site_config=site_config)


def _local_fallback_or_reraise(
    exhausted: Any,
    model: str,
    provider_config: dict[str, Any] | None,
    *,
    phase: str,
    span: Any,
) -> str:
    """Return a LOCAL model to retry with, or re-raise ``exhausted``.

    The spend cap is a ceiling on *money*, not a kill switch on *content*. With
    the prod writer pinned to a cloud model, propagating ``CostGuardExhausted``
    converts "we've spent enough this month" into "the pipeline stops" — so the
    dispatch degrades to a local model instead, loudly.

    Resolution: ``cost_guard_local_fallback_model`` -> ``pipeline_local_writer_model``.
    Re-raises unchanged when the feature is disabled, when neither key is set
    (no silent invention of a model name — ``feedback_no_silent_defaults``), or
    when the configured fallback is ITSELF paid, which would either loop or
    quietly bill a second provider. That last case is the trap
    ``CostGuardExhausted``'s docstring warns about: never silently retry against
    a different PAID provider.
    """
    from services.container_registry import get_container

    container = get_container()
    site_config = container.site_config if container is not None else None
    if site_config is None:
        raise exhausted

    if not site_config.get_bool("cost_guard_local_fallback_enabled", True):
        raise exhausted

    candidate = (site_config.get("cost_guard_local_fallback_model", "") or "").strip()
    if not candidate:
        candidate = (site_config.get("pipeline_local_writer_model", "") or "").strip()
    if not candidate:
        logger.error(
            "[cost_guard] budget exhausted and no local fallback resolvable — set "
            "cost_guard_local_fallback_model (or pipeline_local_writer_model) to "
            "degrade instead of failing. Propagating.",
        )
        raise exhausted

    if _is_paid_llm_call(candidate, provider_config):
        logger.error(
            "[cost_guard] configured local fallback %r is itself a PAID model — "
            "refusing to swap one paid provider for another. Propagating.",
            candidate,
        )
        raise exhausted

    # Loud by design (``feedback_flag_quality_downgrades``): a cheaper model is
    # an acceptable outcome, a SILENT one is not.
    logger.warning(
        "[cost_guard] %s budget exhausted (%s) — downgrading %s -> %s for phase %s",
        getattr(exhausted, "scope", "?"), exhausted, model, candidate, phase,
    )
    span.set_attribute("llm.cost_guard.downgraded", True)
    span.set_attribute("llm.cost_guard.original_model", model)

    from utils.findings import emit_finding

    emit_finding(
        source="cost_guard",
        kind="paid_call_downgraded_to_local",
        severity="warn",
        title=f"budget exhausted — {model} downgraded to {candidate}",
        body=(
            f"Phase {phase} requested paid model {model}; the "
            f"{getattr(exhausted, 'scope', '?')} cap is reached "
            f"({exhausted}). Served locally with {candidate} instead. Output "
            "quality is the local model's, not the paid model's — raise the cap "
            "or accept the downgrade for the rest of the window."
        ),
        # Per scope, not per call: one alert when the cap bites, not one per
        # dispatch for the remainder of the month.
        dedup_key=f"cost_guard_downgrade_{getattr(exhausted, 'scope', 'unknown')}",
    )
    return candidate


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


# Operator-facing FLAT app_settings rows folded into the nested provider
# ``config`` blob as a fallback (see get_provider_config). Each is a key that
# ``settings_defaults`` seeds, ``poindexter settings set`` writes, and the
# providers read out of the NESTED ``config`` — without the fold the flat row
# is dead (the glad-labs-stack dual-key trap). ``disable_aiohttp_transport``
# joined ``allow_paid_base_url`` for the GlitchTip-736 aiohttp fix.
_FLAT_FOLDED_CONFIG_KEYS: tuple[str, ...] = (
    "allow_paid_base_url",
    "disable_aiohttp_transport",
    "anthropic_prompt_caching",
)


async def get_provider_config(pool: Any, provider_name: str) -> dict[str, Any]:
    """Fetch ``plugin.llm_provider.<name>.config`` from app_settings.

    Also folds the operator-facing FLAT rows in ``_FLAT_FOLDED_CONFIG_KEYS``
    (``plugin.llm_provider.<name>.<key>``) into the returned config as a
    fallback. Those flat keys are what ``settings_defaults`` seeds, what the
    providers' refusal/behavior messages tell operators to flip, and what
    ``poindexter settings set`` writes — but the providers read them out of the
    NESTED ``config`` blob (``PluginConfig.config``). Without this fold the flat
    row is dead: an operator who edits it changes a row nothing reads (the
    glad-labs-stack dual-key trap).

    Resolution is backcompat-safe: a key already present in the nested
    ``config`` WINS, so operators who set the JSON shape keep their exact
    behavior. The flat row is consulted ONLY when the nested key is absent —
    the fresh-install default, where the seeded nested config carries
    api_base/timeout but no flat flag. Providers coerce the raw TEXT value
    (``"true"``/``"false"``) via ``_coerce_bool`` at read time, so passing the
    string straight through is correct.
    """
    cfg = await PluginConfig.load(pool, "llm_provider", provider_name)
    config = dict(cfg.config)
    for flat_key in _FLAT_FOLDED_CONFIG_KEYS:
        if flat_key in config:
            continue
        flat = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            f"plugin.llm_provider.{provider_name}.{flat_key}",
        )
        if flat is not None:
            config[flat_key] = flat
    return config


# Safety cap on a captured I/O payload. Bounds a pathological blob — chiefly the
# base64 image data-URLs the vision rail routes through dispatch_complete
# (multi_model_qa._vision_complete) — from bloating the trace store. Text prompts
# fit with wide headroom (the largest writer draft seen is ~24K chars), so only
# runaway payloads are truncated.
_MAX_SPAN_IO_CHARS = 200_000


def _set_span_io(span: Any, key: str, value: Any) -> None:
    """Best-effort stamp of a reserved Langfuse I/O attribute (input/output).

    Strings pass through unchanged (matches langfuse ``_serialize``); other
    values are JSON-serialized so a ``messages`` list renders as a chat array in
    the trace. Oversize payloads are truncated to ``_MAX_SPAN_IO_CHARS`` with a
    visible marker. Empty values are skipped, and this NEVER raises — an
    observability write must never break the dispatch it is observing.
    """
    try:
        if value is None or value == "":
            return
        payload = value if isinstance(value, str) else json.dumps(value, default=str)
        if len(payload) > _MAX_SPAN_IO_CHARS:
            dropped = len(payload) - _MAX_SPAN_IO_CHARS
            payload = f"{payload[:_MAX_SPAN_IO_CHARS]}…[truncated {dropped} chars]"
        span.set_attribute(key, payload)
    except Exception:  # noqa: BLE001  # silent-ok: an observability attribute write must never break the dispatch it observes; a lost span attr is not page-worthy
        pass


async def dispatch_complete(
    pool: Any,
    messages: list[dict[str, str]],
    model: str,
    tier: str = "standard",
    *,
    task_id: str | None = None,
    phase: str = "dispatch_complete",
    max_wait_s: float | None = None,
    priority: str = "pipeline",
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

    When ``task_id`` is not supplied, the ambient binding from
    ``services.task_context`` (set by ``TemplateRunner.run`` around every
    template run) fills it in, so calls deep inside a run that never
    threaded ``task_id`` still group into the task's Langfuse session and
    attribute their cost rows (poindexter#902). An explicit argument wins.

    GPU wait contracts (poindexter#914 P2). This function is the single seam
    every LOCAL provider call passes through, so it is where a caller's wait
    budget reaches the scheduler:

    - ``max_wait_s`` opts the call into admission. The scheduler estimates the
      current holder's remaining time from its ``gpu_lease_stats`` p90 and
      raises :class:`services.gpu_admission.GpuBusyError` IMMEDIATELY when the
      wait is hopeless, instead of the caller burning up to the 900s lock
      ceiling behind a ~230s image render. ``None`` (default) keeps the legacy
      unbounded behaviour, so unmigrated callers are bit-identical.
    - ``priority`` orders the wait queue: ``pipeline`` > ``operator`` >
      ``background``.

    Both are inert unless ``app_settings.gpu_sched_enabled`` is true. Callers
    that pass a budget MUST handle ``GpuBusyError`` — for fail-soft rails that
    means the existing degraded path (no review + a finding), never a fabricated
    pass.
    """
    if not task_id:
        task_id = current_task_id()
    with _tracer.start_as_current_span("llm.dispatch_complete") as span:
        span.set_attribute("llm.tier", tier)
        span.set_attribute("llm.model", model)
        # Trace-level metadata (poindexter#902): the console's /api/traces list
        # reads TRACE metadata — the model the @observe wrappers stamp lands on
        # the observation, which the trace-list API never surfaces. Reserved
        # prefix LangfuseOtelSpanAttributes.TRACE_METADATA, verified against
        # langfuse 4.13 — a wrong key is a silent no-op, the session.id bug
        # class documented below.
        span.set_attribute("langfuse.trace.metadata.model", model)
        span.set_attribute("llm.messages.count", len(messages))
        if task_id:
            span.set_attribute("langfuse.trace.metadata.task_id", task_id)
            span.set_attribute("llm.task_id", task_id)
            # Reserved Langfuse OTEL attribute (LangfuseOtelSpanAttributes
            # .TRACE_SESSION_ID == "session.id" in langfuse 4.x): stamps the
            # trace's session so a task's LLM calls group into ONE Langfuse
            # session — the id the console task-trace deeplink resolves against
            # (trace_read.get_trace uses task_id as the session id). Without it
            # every trace is session-less (the 2026-07-10 finding: 0/3236 had a
            # session). The exact string is verified against the installed SDK;
            # a wrong key is a silent no-op, the bug class this fixes.
            span.set_attribute("session.id", task_id)
        span.set_attribute("llm.phase", phase)
        # Promote phase to first-class, filterable observation metadata. Langfuse
        # flattens ``langfuse.observation.metadata.<key>`` back into the
        # observation's metadata dict (attributes._flatten_and_serialize_metadata).
        span.set_attribute("langfuse.observation.metadata.phase", phase)
        # Capture the prompt on the span so EVERY dispatch — including direct
        # callers that never go through the ollama_chat_text wrapper (QA rails,
        # citation repair, media scripts, …) — carries its input in Langfuse, not
        # just token counts (the 2026-07-10 finding: llm.dispatch_complete had
        # usage but 0/4073 input). Set BEFORE the call so a failed dispatch still
        # records what was sent. Reserved key OBSERVATION_INPUT.
        _set_span_io(span, "langfuse.observation.input", messages)
        started = time.monotonic()
        provider = None
        provider_config: dict[str, Any] | None = None
        # Local import matches ``_enforce_budget_if_paid``'s idiom for the same
        # module; needed by name in the except clause below.
        from services.cost_guard import CostGuardExhausted

        try:
            provider = await get_provider(pool, tier)
            span.set_attribute("llm.provider.name", provider.name)
            provider_config = await get_provider_config(pool, provider.name)
            kwargs.setdefault("_provider_config", provider_config)
            # Spend cap on the PRIMARY path (audit H2). No-op for local calls;
            # raises CostGuardExhausted (fails closed) for an over-budget or
            # budget-unverifiable PAID call, before the provider fires.
            #
            # On exhaustion we DOWNGRADE to a local model rather than propagate.
            # This is the path CostGuardExhausted's own docstring prescribes
            # ("fall back to a free local provider explicitly via the router
            # policy"), and it matters because the prod writer is pinned to a
            # cloud model: without it, hitting the cap turns a cost ceiling into
            # a content outage — the pipeline errors instead of producing a
            # cheaper article. The swap happens BEFORE the num_ctx backfill
            # below so the local model gets its per-phase context and VRAM clamp
            # exactly as a natively-local dispatch would.
            try:
                await _enforce_budget_if_paid(
                    pool=pool, provider=provider, model=model,
                    provider_config=provider_config,
                )
            except CostGuardExhausted as exhausted:
                model = _local_fallback_or_reraise(
                    exhausted, model, provider_config, phase=phase, span=span,
                )
            # Default num_ctx for LOCAL dispatches that never threaded one, so
            # every local path (vision QA, media, scheduled research) is bounded
            # + clamped like the writer — not left at Ollama's Modelfile default
            # (e.g. gemma-4-31B at 262144). Paid/cloud calls are left untouched.
            if kwargs.get("num_ctx") is None:
                default_ctx = _resolve_default_num_ctx(phase, model, provider_config)
                if default_ctx is not None:
                    kwargs["num_ctx"] = default_ctx
            # VRAM budget guard: clamp num_ctx to the dedicated-VRAM budget
            # before the GPU lock, so a context-hungry call can't project a
            # footprint that spills into system RAM (the WDDM freeze). A guard
            # error logs and falls through to the requested ctx rather than
            # breaking the dispatch.
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
                    max_wait_s=max_wait_s, priority=priority,
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
            # Completion text on the span (reserved key OBSERVATION_OUTPUT) so the
            # trace shows what the model returned, matching the input captured
            # above — same coverage the ollama_chat_text wrapper gave the writer,
            # now for every direct dispatch caller too.
            _set_span_io(
                span, "langfuse.observation.output", getattr(result, "text", "") or "",
            )
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

        # Ollama decode/prefill split (services.llm_providers.ollama_timings →
        # litellm_provider stamps result.raw). NULL when the provider didn't
        # report one (cloud models) — never 0 (feedback_no_dummy_data).
        def _raw_ms(key: str) -> int | None:
            v = raw.get(key) if isinstance(raw, dict) else None
            try:
                return int(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        decode_duration_ms = _raw_ms("decode_duration_ms")
        prefill_duration_ms = _raw_ms("prefill_duration_ms")

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
                except Exception:  # noqa: BLE001 — silent-ok: best-effort site_config fetch; on failure it stays None and CostGuard runs with defaults for the electricity estimate below (attribution-only, never the API cost axis)
                    pass
                guard = CostGuard(pool=pool, site_config=site_config)
                electricity_kwh = guard.estimate_local_kwh(duration_ms=duration_ms)
                # NOTE: do NOT set cost_usd = kwh_to_usd(...) here — that conflated
                # electricity onto the API axis and double-counted the brain's
                # measured power. cost_usd stays 0 for local calls.
            except Exception:  # noqa: BLE001 — silent-ok: best-effort local-electricity estimate; on failure electricity_kwh stays None and cost_usd stays $0 (a valid fallback) — the authoritative electricity spend is the brain's measured power rows, not per-call estimates
                pass

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO cost_logs (
                    task_id, phase, model, provider,
                    input_tokens, output_tokens, total_tokens,
                    cost_usd, cost_type, duration_ms, success,
                    electricity_kwh, error_message,
                    decode_duration_ms, prefill_duration_ms,
                    created_at, updated_at
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                    $14, $15, NOW(), NOW()
                )
                """,
                task_id, phase, model, provider_name,
                prompt_tokens, completion_tokens, total_tokens,
                cost_usd, "inference", duration_ms, success,
                electricity_kwh, error,
                decode_duration_ms, prefill_duration_ms,
            )
    except Exception as e:
        # Demote the per-call line to debug so we don't pollute logs on every
        # call when something's structurally wrong (schema drift, pool
        # exhausted) — but ALSO emit one deduped finding, because for every
        # non-writer dispatch (QA / SEO / title / atom calls) this is the SOLE
        # cost_logs writer (admin_db.log_cost is wired only to writer_core;
        # cost_guard.record fires only for caption/media/youtube record_usage).
        # The budget cap sums cost_logs.cost_usd, so a swallowed PAID-call write
        # silently undercounts spend tracking and the daily/monthly cap — the
        # exact invisibility this would otherwise hide. Non-paging info finding
        # (deduped, Findings board); mirrors cost_guard.record's own
        # cost_log_write_failed audit path (cost_guard.py, severity error).
        logger.debug("dispatcher: cost_logs auto-write skipped: %s", e)
        from utils.findings import emit_finding

        emit_finding(
            source="services.llm_providers.dispatcher",
            kind="dispatch_cost_log_write_failed",
            title="dispatch cost_logs auto-write failed",
            body=(
                f"Writing the cost_logs row after a {model} dispatch "
                f"(phase={phase}, task={task_id}) raised {describe_exception(e)}. "
                f"For non-writer LLM dispatches this is the only cost_logs writer, "
                f"and the budget cap sums cost_logs.cost_usd — a persistent failure "
                f"silently undercounts spend tracking and the spend cap."
            ),
            severity="info",
            dedup_key="dispatch_cost_log_write_failed",
        )


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
