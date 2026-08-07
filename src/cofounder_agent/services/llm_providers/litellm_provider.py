"""LiteLLMProvider — LiteLLM-backed LLMProvider plugin.

Per the OSS audit: hand-rolled cost-tier routing + retries + fallbacks
+ provider health + cost tracking duplicate what LiteLLM already
provides as mature OSS. This provider plugin gives the dispatcher a
LiteLLM-backed option so operators can:

- Route through LiteLLM's provider abstraction (works against Ollama,
  OpenAI, Anthropic, Gemini, vLLM, llama.cpp, OpenRouter, Bedrock,
  Vertex — anything LiteLLM speaks) with one app_settings flip.
- Get authoritative cost tracking from LiteLLM's MODEL_COSTS table
  instead of the drift-prone hand-rolled tables (model_constants.py
  + usage_tracker.py — both deleted 2026-05-08 in favor of cost_lookup).
- Keep retries + fallbacks declarative via LiteLLM's Router config
  rather than the per-provider failure counter in the now-deleted
  services/model_router.py.

Per ``feedback_no_paid_apis``: LiteLLM speaks Ollama natively; the
default install routes to local models. Cloud providers stay opt-in
behind the existing cost_guard.

Config (``plugin.llm_provider.litellm`` in app_settings):

- ``api_base`` (default: ``http://localhost:11434`` for the Ollama
  case; LiteLLM treats this as the base for ``ollama/<model>`` calls).
  Scoped to LOCAL model prefixes: cloud prefixes (``anthropic/``,
  ``openai/``, ...) never inherit it and route to their provider's
  canonical endpoint — an explicit ``api_base`` would override the
  prefix routing and land the "cloud" call on the local server (see
  ``_api_base_applies``). Per-model overrides still apply to any prefix.
- ``timeout_seconds`` (default 120) — per-call default; per-call
  overrides via the ``timeout_s`` kwarg still win.
- ``drop_params`` (default true) — strip params the target backend
  doesn't recognize so a single call signature works across backends.
- ``model_api_base_overrides`` (default empty) — JSON object mapping a
  RESOLVED model name to an api_base, e.g.
  ``{"ollama/qwen3-vl:30b": "http://host.docker.internal:11435"}``.
  Routes specific models to a different endpoint (glad-labs-stack#2051:
  a GPU-pinned second Ollama instance so vision QA never gets evicted
  by the writer). Overrides still pass the paid-endpoint policy.
- ``cloud_max_tokens`` (default 8192) — completion budget applied to
  CLOUD model calls when the caller didn't pass ``max_tokens``. LiteLLM
  defaults ``anthropic/*`` to 4096, and adaptive-thinking Claude models
  (Sonnet 5+) spend thinking + visible text from that ONE budget, so
  4096 truncates long-form drafts mid-word (observed in the 2026-07-06
  A/B writer run, glad-labs-stack#2153). Local prefixes are never
  capped by this — Ollama keeps its unbounded default.
- ``anthropic_prompt_caching`` (bool, default true) — when the resolved
  model is an ``anthropic/`` prefix, annotate the system prefix with a
  ``cache_control: {"type": "ephemeral"}`` breakpoint so a reused writer
  system prompt bills cached input at ~10% (Anthropic prompt caching).
  The litellm cutover otherwise forwards plain OpenAI-shaped messages
  with NO breakpoint, so the Sonnet writer never cached. Local +
  other-vendor targets are untouched (cache_control is Anthropic-only).
  Flip to false to disable. See ``_annotate_system_cache_control``.

Cloud credentials (DB-first — cloud-writer canary, 2026-07):

- :func:`configure_cloud_api_keys` stamps the ``anthropic_api_key`` /
  ``openai_api_key`` / ``gemini_api_key`` secret rows from app_settings
  into the ``*_API_KEY`` env vars LiteLLM auto-discovers, at worker AND
  Prefect-subprocess startup. The paid-endpoint gate below still decides
  whether a cloud call is ALLOWED; this only makes an allowed call
  authenticate without hand-managed container env vars.

Observability — Langfuse tracing (poindexter#373):

- ``langfuse_tracing_enabled`` (bool, default true) — when true,
  ``configure_langfuse_callback`` registers LiteLLM's OTEL integration
  pointed at ``{langfuse_host}/api/public/otel`` so every call emits a
  span to Langfuse. Uses OTEL (not LiteLLM's built-in ``langfuse``
  string callback, which is broken against langfuse SDK>=3.0 due to a
  dropped ``sdk_integration`` constructor kwarg).
- This is ADDITIVE — ``cost_guard.record_cost`` keeps working. The
  Langfuse SDK batches + retries spans in a background worker and
  never blocks the calling LLM request, so a Langfuse outage doesn't
  break content generation.

Per ``feedback_design_for_llm_consumers``: this provider name flows
through the dispatcher's structured logging so future LLM operators
reading capability_outcomes see "model_used=litellm:ollama/glm-4.7-5090"
and can ground decisions on that.
"""

from __future__ import annotations

import base64
import importlib.util
import logging
import os
import re
from collections.abc import AsyncIterator
from typing import Any

from plugins.llm_provider import Completion, Token
from services.cost_guard import is_local_base_url
from services.llm_providers.thinking_models import strip_reasoning_artifacts

# Refuse to import when the backing SDK is absent, so ``plugins.registry``
# excludes this provider instead of registering one that cannot run. Every
# ``import litellm`` in this module sits inside a method, so without this
# guard the module imports cleanly on an install without litellm, registers
# as an available provider, gets selected by the dispatcher (prod pins
# ``plugin.llm_provider.primary.*='litellm'``), and only then explodes —
# surfacing as a per-document ``ModuleNotFoundError`` at call time rather
# than a clean "provider unavailable → fall back to ollama_native".
#
# That is not hypothetical: the auto-embed sidecar ships a deliberately
# minimal image with no litellm. It had been silently protected because the
# module ALSO failed to import for an unrelated missing package; the moment
# that was fixed (poindexter#989 follow-up), litellm registered and every
# embedding store in the sidecar started failing.
#
# ``find_spec`` rather than a real ``import``: litellm is slow to import and
# the worker pays that cost lazily at first call today. This keeps that.
if importlib.util.find_spec("litellm") is None:  # pragma: no cover - env-dependent
    raise ImportError(
        "LiteLLMProvider requires the 'litellm' package, which is not "
        "installed in this environment. The provider is being skipped rather "
        "than registered — callers fall back to the next configured provider "
        "(typically ollama_native). Install litellm to enable it."
    )

logger = logging.getLogger(__name__)


# Module-level idempotency guard — Langfuse callback registration is a
# process-wide mutation of ``litellm.callbacks``, so we only do it once
# even if the worker calls ``configure_langfuse_callback`` multiple times
# (e.g. main.py + CLI re-init paths).
_LANGFUSE_CALLBACK_REGISTERED = False


# Conservative-deny allowlist for the paid-endpoint policy. Anything not
# here is treated as paid and refused unless the operator opts in via
# ``plugin.llm_provider.litellm.allow_paid_base_url=true``.
#
# Why allowlist instead of denylist: LiteLLM keeps adding cloud vendors
# (the registry crossed 100+ in 2026). A denylist drifts every release;
# the local-provider set is small and stable.
_LOCAL_MODEL_PREFIXES: frozenset[str] = frozenset({
    "ollama",
    "ollama_chat",
    "vllm",
    "lm_studio",
    "openai_compat",
    "custom",
    "text-completion-openai-compatible",
})


# Request params that are meaningful ONLY to a local Ollama backend and must
# NOT reach a cloud provider. LiteLLM's ``drop_params`` strips *OpenAI-spec*
# params the target doesn't support — but these aren't OpenAI-spec, so litellm
# forwards them verbatim and Anthropic/OpenAI 400 ("Extra inputs are not
# permitted"). ``num_ctx`` is Ollama's context-window option; ``think`` is
# Ollama's reasoning-channel toggle (#2163, added for the gemma writer). Both
# get dropped for any non-local prefix (2026-07-07 cloud-writer canary — this
# was the second Ollama-ism to leak into the Sonnet path after api_base).
_OLLAMA_ONLY_PARAMS: frozenset[str] = frozenset({"num_ctx", "think"})


# Default completion budget for CLOUD models when the caller didn't pass
# ``max_tokens``. Anthropic's API requires max_tokens and LiteLLM fills in
# 4096 when absent; on adaptive-thinking Claude models (Sonnet 5+) thinking
# and visible text share that ONE budget, so 4096 starves a long-form draft
# mid-word once the model spends ~3K tokens thinking (2026-07-06 A/B run,
# glad-labs-stack#2153). Operators tune per-install via the
# ``cloud_max_tokens`` key on the ``plugin.llm_provider.litellm`` config
# row. Local prefixes are never capped by this floor.
_DEFAULT_CLOUD_MAX_TOKENS = 8192


# app_settings secret row → env var that LiteLLM auto-discovers cloud
# credentials from at call time (the same axis-2 seam
# ``_enforce_paid_endpoint_policy`` guards). Consumed by
# :func:`configure_cloud_api_keys` at process startup. Row names follow the
# settings_service env-fallback convention (``anthropic_api_key`` ↔
# ``ANTHROPIC_API_KEY``).
_CLOUD_API_KEY_ENV_MAP: dict[str, str] = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
}


def _coerce_bool(value: Any) -> bool:
    """app_settings.value is TEXT; coerce common truthy strings.

    Matches the pattern used by SiteConfig.get_bool and OpenAICompatProvider's
    sibling helper — operators write ``true`` / ``True`` / ``1`` / ``yes``
    interchangeably across rows.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _coerce_override_map(value: Any) -> dict[str, str]:
    """Normalize ``model_api_base_overrides`` config into a str→str dict.

    PluginConfig JSONB hands a dict through; a raw app_settings TEXT row
    arrives as a JSON string. Anything unparseable is logged + ignored —
    a typo in an override row must degrade to "no override" (the default
    ``api_base`` keeps serving every model), never break dispatch.
    """
    if not value:
        return {}
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            logger.warning(
                "[litellm_provider] model_api_base_overrides is not valid "
                "JSON (%s) — ignoring the override map", exc,
            )
            return {}
    if not isinstance(value, dict):
        logger.warning(
            "[litellm_provider] model_api_base_overrides must be a JSON "
            "object of model→api_base, got %s — ignoring",
            type(value).__name__,
        )
        return {}
    return {
        str(k): str(v).strip()
        for k, v in value.items()
        if str(v or "").strip()
    }


# Default namespace prefix for a bare model name ("gemma3:27b" →
# "ollama/gemma3:27b"). Module-level so the provider instance and the routing
# twin below resolve names identically; an install overrides it per-call via
# the ``default_prefix`` config key.
_DEFAULT_MODEL_PREFIX = "ollama/"


def resolve_model_name(
    model: str, default_prefix: str = _DEFAULT_MODEL_PREFIX,
) -> str:
    """Apply the default provider prefix to a bare model name.

    ``ollama/gemma3:27b`` and inline ``http(s)://`` bases pass through; bare
    ``gemma3:27b`` becomes ``ollama/gemma3:27b``. Shared by
    ``LiteLLMProvider._resolve_model`` and :func:`pinned_api_base_for` so a
    caller reasoning about WHERE a call lands resolves the name exactly the
    way the dispatch will.
    """
    model = (model or "").strip()
    if model.startswith("http"):
        return model
    if "/" in model:
        return model
    prefix = (default_prefix or _DEFAULT_MODEL_PREFIX).rstrip("/")
    return f"{prefix}/{model}"


def pinned_api_base_for(
    model: str, provider_config: dict[str, Any] | None,
) -> str | None:
    """The NON-DEFAULT api_base this model is pinned to, or ``None``.

    Module-level twin of ``LiteLLMProvider._api_base_for`` for callers that
    must know where a call lands BEFORE the provider runs — notably the
    dispatcher's GPU-lock decision: a model served by a second Ollama pinned
    to its own GPU (glad-labs-stack#2051) contends for nothing the shared
    ``gpu.lock("ollama")`` protects, so serializing it only starves it.

    ``None`` means the model uses the install's default endpoint — the OSS
    path, where no override map is configured. Never raises: a malformed map
    degrades to "no override" exactly as it does for the provider, so the
    lock decision cannot diverge from the routing.
    """
    cfg = provider_config or {}
    overrides = _coerce_override_map(cfg.get("model_api_base_overrides"))
    if not overrides:
        return None
    resolved = resolve_model_name(
        model, str(cfg.get("default_prefix") or _DEFAULT_MODEL_PREFIX),
    )
    pinned = str(overrides.get(resolved) or "").strip()
    if not pinned:
        return None
    # An override pointing back at the default endpoint is the SAME server: it
    # shares the default GPU, so it is not a distinct placement.
    default_base = str(cfg.get("api_base") or "").strip()
    if default_base and pinned == default_base:
        return None
    return pinned


class LangfuseConfigError(RuntimeError):
    """Raised when ``langfuse_tracing_enabled=true`` but a credential is
    missing.

    Per ``feedback_no_silent_defaults``: rather than quietly skipping
    callback registration (which would mean zero spans land while the
    operator believes tracing is on), we raise loudly at worker
    startup. The fix is either populate the missing row in
    ``app_settings`` or set ``langfuse_tracing_enabled=false`` to
    explicitly opt out.
    """


async def configure_langfuse_callback(site_config: Any) -> bool:
    """Wire LiteLLM → Langfuse success/failure callbacks at startup.

    Reads three credential rows from ``app_settings`` (the same ones
    the prompt manager uses — see ``services/prompt_manager.py:317``)
    and a fourth bool toggle ``langfuse_tracing_enabled``.

    Behavior:

    - ``langfuse_tracing_enabled=false`` → log + return False without
      touching ``litellm.success_callback``. Lets the operator kill
      tracing without nuking prompt management if Langfuse is down.
    - ``langfuse_tracing_enabled=true`` and any credential empty →
      raise :class:`LangfuseConfigError`. No silent defaults.
    - ``langfuse_tracing_enabled=true`` and credentials present →
      stamp the three values into ``LANGFUSE_HOST`` /
      ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` env vars
      (which is how LiteLLM's built-in Langfuse integration discovers
      them — see litellm.utils._init_logging_callbacks), then append an
      ``OpenTelemetry`` CustomLogger instance to ``litellm.callbacks``.
      It MUST go in ``litellm.callbacks`` (not just
      ``litellm.success_callback``) or litellm never fires
      ``async_log_success_event`` for ``acompletion`` calls — the
      async path the whole pipeline uses.

    Idempotent — safe to call multiple times, only the first call
    registers the callbacks.

    Returns True if the callback got registered (or was already
    registered on a prior call), False if tracing is explicitly
    disabled. Caller doesn't need to act on the return value; it's
    there for tests + diagnostic logging.

    Per the issue brief: this lives in ``LiteLLMProvider`` so it ships
    with the provider that needs it, but it's intentionally a
    module-level async function (not an instance method) because the
    underlying ``litellm`` config is process-global. main.py invokes
    this once at lifespan startup, after ``site_config`` is loaded
    but before any LLM call fires.
    """
    global _LANGFUSE_CALLBACK_REGISTERED

    if site_config is None:
        # No-op when called outside the worker (e.g. CLI scripts that
        # don't construct a SiteConfig). Tests can still exercise the
        # function by passing a fake site_config.
        logger.debug(
            "[litellm_provider] configure_langfuse_callback: "
            "site_config is None, skipping",
        )
        return False

    enabled = site_config.get_bool("langfuse_tracing_enabled", True)
    if not enabled:
        logger.info(
            "[litellm_provider] Langfuse tracing disabled "
            "(langfuse_tracing_enabled=false); skipping callback "
            "registration. LLM calls will NOT emit spans.",
        )
        return False

    host = (site_config.get("langfuse_host", "") or "").strip()
    # Both halves of the Langfuse key pair are ``is_secret=true`` rows in
    # app_settings (encrypted at rest), so SiteConfig filters them out of
    # its non-secret startup cache — they MUST be read via the async,
    # decrypting ``get_secret`` accessor. Reading ``langfuse_public_key``
    # through the sync ``site_config.get()`` silently missed the cache and
    # only resolved from the ``LANGFUSE_PUBLIC_KEY`` env var, so a clean
    # operator install that configured the keys in the DB (via
    # ``poindexter setup``, no env vars) lost tracing entirely. Fetch both
    # under one guard so a DB read failure on either surfaces the same
    # clean LangfuseConfigError the caller already catches.
    try:
        public_key_raw = await site_config.get_secret("langfuse_public_key", "")
        secret_key_raw = await site_config.get_secret("langfuse_secret_key", "")
    except Exception as exc:  # noqa: BLE001
        raise LangfuseConfigError(
            f"langfuse_tracing_enabled=true but reading the Langfuse "
            f"credential rows (langfuse_public_key / langfuse_secret_key) "
            f"failed: {exc!s}. Either populate them in app_settings "
            f"(see migration 0153) or set langfuse_tracing_enabled=false.",
        ) from exc
    public_key = (public_key_raw or "").strip()
    secret_key = (secret_key_raw or "").strip()

    missing = [
        name for name, val in (
            ("langfuse_host", host),
            ("langfuse_public_key", public_key),
            ("langfuse_secret_key", secret_key),
        ) if not val
    ]
    if missing:
        joined = ", ".join(missing)
        raise LangfuseConfigError(
            f"langfuse_tracing_enabled=true but the following "
            f"app_settings rows are empty: {joined}. Populate them via "
            f"migration 0153 + the settings CLI, or set "
            f"langfuse_tracing_enabled=false to opt out of tracing.",
        )

    # Stamp env vars so other Langfuse SDK paths (prompt manager, @observe
    # decorators in langfuse_shim) pick up the DB-configured credentials
    # instead of whatever the container was started with.
    os.environ["LANGFUSE_HOST"] = host
    os.environ["LANGFUSE_PUBLIC_KEY"] = public_key
    os.environ["LANGFUSE_SECRET_KEY"] = secret_key

    if _LANGFUSE_CALLBACK_REGISTERED:
        logger.debug(
            "[litellm_provider] Langfuse callback already registered "
            "(refreshed env vars in case credentials rotated)",
        )
        return True

    try:
        import litellm
        from litellm.integrations.opentelemetry import OpenTelemetry, OpenTelemetryConfig
    except ImportError as exc:
        raise LangfuseConfigError(
            "langfuse_tracing_enabled=true but the litellm package is "
            "not installed. Install it (it's pulled in by the standard "
            "poindexter dependencies) or set langfuse_tracing_enabled "
            "=false.",
        ) from exc

    # Use LiteLLM's OTEL integration rather than the built-in "langfuse"
    # string callback. The built-in LangFuseLogger constructs the legacy
    # Langfuse SDK client with a ``sdk_integration`` kwarg that was dropped
    # in langfuse>=3.0, causing a TypeError on every call. The OTEL path
    # sends standard OTLP spans to Langfuse's /api/public/otel endpoint,
    # which the server has supported since v3.x and is SDK-version-agnostic.
    b64_creds = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    otel_config = OpenTelemetryConfig(
        exporter="otlp_http",
        endpoint=f"{host}/api/public/otel",
        headers=f"Authorization=Basic {b64_creds}",
    )
    otel_callback = OpenTelemetry(config=otel_config)
    # Register the CustomLogger INSTANCE in ``litellm.callbacks`` — that is the
    # only list litellm consults for async ``acompletion`` success/failure
    # logging in litellm>=1.85. The pipeline is async-everywhere, so an instance
    # placed solely in ``litellm.success_callback`` (the legacy list, which only
    # wires the *sync* ``completion`` path and bare string names like
    # ``"langfuse"``) never fires ``async_log_success_event`` and silently emits
    # zero spans. That was the 2026-05-28 regression that #1387 only half-fixed:
    # a manual test span reached Langfuse, but real (async) pipeline traffic
    # stayed dark. Append-if-absent so we preserve litellm's own internal
    # callbacks (e.g. SkillsInjectionHook) and stay idempotent within a process.
    existing_callbacks = list(getattr(litellm, "callbacks", None) or [])
    if otel_callback not in existing_callbacks:
        existing_callbacks.append(otel_callback)
    litellm.callbacks = existing_callbacks
    _LANGFUSE_CALLBACK_REGISTERED = True
    logger.info(
        "[litellm_provider] Langfuse tracing active via OTEL (host=%s) — every "
        "LLM call routed through LiteLLMProvider will emit a span.",
        host,
    )
    return True


async def configure_cloud_api_keys(site_config: Any) -> list[str]:
    """Stamp DB-stored cloud API keys into the process env for LiteLLM.

    LiteLLM discovers cloud credentials from the process env at call time
    (``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` / ``GEMINI_API_KEY`` — the
    same axis-2 seam :meth:`LiteLLMProvider._enforce_paid_endpoint_policy`
    guards). The canonical store for those credentials is the
    ``is_secret=true`` app_settings rows (``anthropic_api_key`` /
    ``openai_api_key`` / ``gemini_api_key``) per the DB-first config
    contract — worker containers are NOT started with cloud keys in their
    env. This function bridges the two at startup, mirroring the Langfuse
    credential stamping in :func:`configure_langfuse_callback` above.

    Call sites (idempotent; re-running refreshes rotated keys):

    - ``main.py`` lifespan startup (FastAPI worker process)
    - ``services/flows/content_generation.py`` flow-body wiring — the
      Prefect subprocess never runs main.py's lifespan, so it re-wires
      here for the same reason Langfuse / OTel / Sentry do.

    Behavior:

    - ``site_config is None`` → no-op ``[]`` (CLI scripts that don't
      construct a SiteConfig).
    - Empty / missing secret rows are the NORMAL local-only state — they
      are skipped without complaint. The paid-endpoint gate
      (``allow_paid_base_url``, default false) already refuses cloud
      prefixes on such installs, and an operator who opens the gate
      without a key gets a loud per-call auth error naming the provider.
    - Non-empty secret → stamped over any existing env var (the DB value
      wins over stale container env, same contract as the Langfuse
      block). A ``get_secret`` failure propagates to the caller — both
      call sites wrap in their existing warn-and-continue handling.

    Returns the list of env var NAMES that were set. Key values are never
    logged or returned in any other form.
    """
    if site_config is None:
        logger.debug(
            "[litellm_provider] configure_cloud_api_keys: site_config is "
            "None, skipping",
        )
        return []

    stamped: list[str] = []
    for settings_key, env_var in _CLOUD_API_KEY_ENV_MAP.items():
        value = ((await site_config.get_secret(settings_key, "")) or "").strip()
        if not value:
            continue
        os.environ[env_var] = value
        stamped.append(env_var)
    if stamped:
        logger.info(
            "[litellm_provider] cloud API keys stamped into env from "
            "app_settings: %s",
            ", ".join(stamped),
        )
    return stamped


_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _recover_reasoning_text(msg: Any) -> str:
    """Recover a usable assistant payload from a reasoning model that
    returned empty ``content``.

    Reasoning models (e.g. GLM, DeepSeek-R1 family) routed through LiteLLM
    surface their chain-of-thought in a separate ``reasoning_content``
    field, and under ``response_format=json_object`` can leave ``content``
    empty entirely. The structured answer the model produced is then only
    reachable via ``reasoning_content``. Some models instead inline the
    thinking as ``<think>...</think>`` followed by the answer.

    Strategy (best-effort, returns "" when nothing usable is found):
    1. Prefer ``reasoning_content`` when present; strip any ``<think>``
       wrapper so a downstream ``json.loads`` sees the bare payload.
    2. If the reasoning is *only* a think-block with the answer after the
       closing tag, the strip leaves that trailing answer.
    """
    reasoning = (getattr(msg, "reasoning_content", None) or "").strip()
    if not reasoning:
        return ""
    stripped = _THINK_TAG_RE.sub("", reasoning).strip()
    # If stripping the think wrapper left nothing, the whole reasoning body
    # IS the candidate payload (the model never closed a think tag) — return
    # it raw so the caller's parser gets a shot at it.
    return stripped or reasoning


def _extract_response_cost(response: Any) -> float | None:
    """Pull the LiteLLM-computed call cost off a completion response.

    LiteLLM stores the price it derived from ``litellm.model_cost`` on the
    response's ``_hidden_params`` dict — there is NO top-level
    ``response.response_cost`` attribute (``hasattr`` returns False), so the
    naive read logged $0 for every paid call. Order:

    1. ``response._hidden_params["response_cost"]`` — the canonical location,
       already computed, no recomputation cost.
    2. ``litellm.completion_cost(completion_response=response)`` — recomputes
       from usage + the model_cost table; covers responses where the hidden
       param wasn't populated (streaming, some providers).

    Returns ``None`` when neither yields a usable number so the caller leaves
    ``cost_usd`` at its 0 default rather than stamping a bogus value — the
    downstream electricity fallback only fires for genuinely-local ($0) calls,
    so a paid call with no recoverable price is logged at 0 (visible
    under-report) rather than crashing the dispatch.
    """
    hidden = getattr(response, "_hidden_params", None)
    if isinstance(hidden, dict):
        rc = hidden.get("response_cost")
        if rc is not None:
            try:
                return float(rc)
            except (TypeError, ValueError):
                # silent-ok: a malformed hidden-params value falls through to
                # the completion_cost() recompute below — cost is advisory.
                pass
    try:
        import litellm

        rc = litellm.completion_cost(completion_response=response)
        if rc is not None:
            return float(rc)
    except Exception:  # noqa: BLE001  # silent-ok: cost is advisory — a failed lookup logs $0 (visible under-report), never breaks the LLM dispatch
        return None
    return None


def _is_anthropic_model(resolved_model: str) -> bool:
    """Whether ``resolved_model`` routes to Anthropic's API directly.

    Prompt-cache breakpoints are an Anthropic feature; LiteLLM forwards a
    ``cache_control`` content-block annotation to the Messages API only for
    the ``anthropic/`` prefix. Scope the injection to that prefix — the
    writer's live cloud target is ``anthropic/claude-sonnet-5``. Other
    Claude routes (``bedrock/``, ``vertex_ai/``, ``openrouter/anthropic/``)
    are deliberately out of scope until an operator actually pins one.
    """
    return resolved_model.startswith("anthropic/")


def _annotate_system_cache_control(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a COPY of ``messages`` with an ephemeral cache breakpoint on
    the system prefix, in the content-block shape LiteLLM forwards to
    Anthropic.

    Every string-content ``system`` turn is rewritten to a one-item ``text``
    block list (uniform shape so litellm's Anthropic transform concatenates
    them cleanly); the ``cache_control`` breakpoint lands on the LAST such
    block, covering the whole reused system prefix with one breakpoint
    (Anthropic caps at 4; the writer uses exactly one). A system turn that
    already carries a content-block list (vision / pre-annotated) is left
    untouched — never clobber a caller-supplied shape.

    Returns the ORIGINAL object unchanged (no copy) when there is no
    string-content system turn to annotate, so a user-only conversation
    passes through untouched. The caller's list is never mutated in place —
    the writer atom may log or replay it, and an Anthropic-only block shape
    leaking back into the pipeline would break the Ollama path.
    """
    sys_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") == "system" and isinstance(m.get("content"), str)
    ]
    if not sys_indices:
        return messages
    last = sys_indices[-1]
    out: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        if i in sys_indices:
            block: dict[str, Any] = {"type": "text", "text": m.get("content") or ""}
            if i == last:
                block["cache_control"] = {"type": "ephemeral"}
            out.append({**m, "content": [block]})
        else:
            out.append(m)
    return out


class LiteLLMProvider:
    """LLMProvider implementation backed by LiteLLM.

    Delegates to ``litellm.acompletion`` for chat + ``litellm.aembedding``
    for embeddings. The LiteLLM library normalizes provider quirks (each
    backend's auth, base url, response shape, finish-reason vocabulary)
    into the OpenAI shape we already expect — so this provider stays
    thin.

    Model strings follow LiteLLM's namespacing convention:

    - ``ollama/glm-4.7-5090:latest`` — local Ollama
    - ``openai/gpt-4o-mini`` — OpenAI direct
    - ``anthropic/claude-haiku-4-5`` — Anthropic direct
    - ``vertex_ai/gemini-2.0-flash`` — Vertex AI
    - ``openrouter/anthropic/claude-haiku-4-5`` — via OpenRouter
    - ``http://host:port/v1`` (with custom api_base) — any
      OpenAI-compat backend.

    Callers that pass bare model names ("gemma3:27b") get the configured
    default provider prefix appended ("ollama/gemma3:27b") so existing
    code keeps working without churn.
    """

    name = "litellm"
    supports_streaming = True
    supports_embeddings = True
    # OpenAI-style function calling: ``complete(tools=[...])`` forwards the
    # schemas and populates ``Completion.tool_calls`` (poindexter#947 — the
    # console chat agent loop is the first consumer).
    supports_tools = True

    def __init__(self) -> None:
        self._configured = False
        self._default_prefix = _DEFAULT_MODEL_PREFIX
        self._api_base: str | None = None
        self._timeout = 120.0
        self._drop_params = True
        # Paid-endpoint opt-in flag — per ``feedback_no_paid_apis``. False
        # means any non-local ``api_base`` OR non-local model prefix
        # (``openai/``, ``anthropic/``, ``gemini/``, ...) refuses to fire.
        # Mirrors the gate added to ``OpenAICompatProvider`` so the same
        # protection extends to the LiteLLM router that's now the default
        # for every cost tier.
        self._allow_paid_base_url = False
        # Reasoning-model fallback (2026-05-29 content-gen stall fix). A
        # reasoning model (e.g. ``glm-4.7-5090``) under
        # ``response_format=json_object`` can emit all its tokens into a
        # thinking channel and return an EMPTY ``content`` field — which
        # crashed every ``json.loads`` caller in topic discovery. When this
        # is on and ``content`` is empty, fall back to the response's
        # ``reasoning_content`` (and strip any ``<think>`` wrapper) so the
        # structured payload the model reasoned out is still recoverable.
        # DB-configurable via
        # ``plugin.llm_provider.litellm.config.reasoning_content_fallback``.
        self._reasoning_content_fallback = True
        # Per-model api_base overrides (glad-labs-stack#2051) — resolved
        # model name → endpoint, so eviction-prone models (qwen3-vl) can
        # be served by a GPU-pinned second Ollama instance. Empty = every
        # model uses ``self._api_base`` (the OSS default path).
        self._model_api_base_overrides: dict[str, str] = {}
        # Cloud completion-budget floor — applied by
        # ``_apply_cloud_max_tokens`` when a caller didn't pass
        # ``max_tokens`` and the resolved model is a cloud prefix. See
        # the ``_DEFAULT_CLOUD_MAX_TOKENS`` comment for the adaptive-
        # thinking rationale. DB-tunable via the ``cloud_max_tokens``
        # key on the ``plugin.llm_provider.litellm`` config row.
        self._cloud_max_tokens = _DEFAULT_CLOUD_MAX_TOKENS
        # aiohttp-transport opt-out (default ON → litellm uses httpx). LiteLLM
        # 1.89.x defaults to a custom aiohttp transport
        # (``llms/custom_httpx/aiohttp_transport.py``) that pools keep-alive
        # connections. In the sparse nightly pipeline window Ollama closes idle
        # sockets server-side; aiohttp then reuses a pooled-but-dead socket and
        # its first read fails INSTANTLY with ``SocketTimeoutError: Timeout on
        # reading data from socket`` (``Timeout passed=90.0, time taken=0.001
        # seconds``), which litellm's ``exception_type`` mislabels
        # ``litellm.Timeout`` → ``APIConnectionError: OllamaException``
        # (GlitchTip issue 736, 13 events 2026-07-04→09). httpx discards closed
        # pooled connections on reuse instead of failing instantly, so we
        # default to it. DB-tunable via the flat
        # ``plugin.llm_provider.litellm.disable_aiohttp_transport`` row — set
        # ``false`` to restore litellm's aiohttp default. Applied process-wide
        # once in ``_apply_global_litellm_config``; a flip takes effect on the
        # next worker start (litellm caches its transport per process).
        self._disable_aiohttp_transport = True
        # Anthropic prompt caching (default ON). When the resolved model is
        # an ``anthropic/`` prefix, annotate the system prefix with a
        # ``cache_control: {"type": "ephemeral"}`` breakpoint so a reused
        # writer system prompt bills cached input at ~10% on the Sonnet
        # writer path (the litellm cutover otherwise sends plain messages
        # with NO breakpoint, so nothing caches). Local + other-vendor
        # targets are untouched — cache_control is Anthropic-only. Killable
        # per-install via the flat
        # ``plugin.llm_provider.litellm.anthropic_prompt_caching`` row.
        self._anthropic_prompt_caching = True

    def _configure_from(self, provider_config: dict[str, Any]) -> None:
        """Apply per-call provider config from PluginConfig (dispatcher
        injects this via the ``_provider_config`` kwarg).

        Mutating instance state on every call is fine — config rarely
        changes within a single process and the cost is one dict lookup.
        Idempotent.

        ``api_base`` resolution order (first non-empty wins):
        1. ``plugin.llm_provider.litellm.config.api_base`` (DB-first,
           the canonical source per feedback_db_first_config).
        2. ``OLLAMA_API_BASE`` env var (LiteLLM's own contract).
        3. ``OLLAMA_BASE_URL`` env var (Poindexter's docker-compose
           convention — set on every worker container so flow-body
           LLM calls reach the host's Ollama instead of the
           container's localhost). This fallback is the asymmetry-
           breaker for the 2026-05-16 dispatcher cutover: every legacy
           direct-httpx caller read OLLAMA_BASE_URL out of the env, so
           the migration to LiteLLM has to honor the same contract or
           every dockerized deploy needs a hand-edited
           app_settings.plugin.llm_provider.litellm row.
        """
        import os
        env_fallback = os.environ.get("OLLAMA_API_BASE") or os.environ.get("OLLAMA_BASE_URL")
        self._api_base = (
            provider_config.get("api_base") or self._api_base or env_fallback
        )
        self._timeout = float(provider_config.get("timeout_seconds", self._timeout))
        self._drop_params = bool(
            provider_config.get("drop_params", self._drop_params)
        )
        self._reasoning_content_fallback = bool(
            provider_config.get(
                "reasoning_content_fallback", self._reasoning_content_fallback
            )
        )
        # Read BEFORE ``_apply_global_litellm_config`` (called at the tail of
        # this method) so the once-only global wiring sees the configured
        # value. Coerced because the flat app_settings row arrives as TEXT.
        self._disable_aiohttp_transport = _coerce_bool(
            provider_config.get(
                "disable_aiohttp_transport", self._disable_aiohttp_transport
            )
        )
        # Re-read on every call so flipping the flat row takes effect on the
        # next dispatch without a worker restart. Instance default (True) is
        # the fallback, so an absent row keeps caching ON — matching the
        # seeded default; ``_coerce_bool`` maps the TEXT ``"false"`` kill
        # switch to a real bool.
        self._anthropic_prompt_caching = _coerce_bool(
            provider_config.get(
                "anthropic_prompt_caching", self._anthropic_prompt_caching
            )
        )
        prefix = provider_config.get("default_prefix")
        if prefix:
            self._default_prefix = prefix
        # Re-read on every call so flipping the app_setting takes effect on
        # the next dispatch without a worker restart, same contract as
        # ``api_base`` / ``timeout_seconds`` above. The value may arrive from
        # the nested ``config`` blob OR the flat
        # ``plugin.llm_provider.litellm.allow_paid_base_url`` row — the
        # dispatcher's ``get_provider_config`` folds the flat row in as a
        # fallback (nested wins), so both shapes reach here identically.
        self._allow_paid_base_url = _coerce_bool(
            provider_config.get("allow_paid_base_url"),
        )
        self._model_api_base_overrides = _coerce_override_map(
            provider_config.get("model_api_base_overrides"),
        )
        raw_cloud_max = provider_config.get("cloud_max_tokens")
        if raw_cloud_max not in (None, ""):
            try:
                self._cloud_max_tokens = int(str(raw_cloud_max).strip())
            except (TypeError, ValueError):
                logger.warning(
                    "[litellm_provider] cloud_max_tokens=%r is not an "
                    "integer — keeping %d",
                    raw_cloud_max, self._cloud_max_tokens,
                )
        if not self._configured:
            self._apply_global_litellm_config()
            self._configured = True

    def _apply_global_litellm_config(self) -> None:
        """Wire LiteLLM's process-wide knobs once.

        ``set_verbose=False`` keeps litellm out of our logs unless the
        operator opts in. ``drop_params`` lets one call signature work
        against backends with different param vocabularies.
        ``disable_aiohttp_transport`` routes litellm onto httpx (see the
        ``__init__`` note) — a process-wide knob litellm reads when it builds
        its transport, so it must be set before the first ``acompletion``;
        this method runs once at the top of the first ``complete``/``stream``/
        ``embed`` call, which is that point.
        """
        # Deliberately does NOT set ``litellm.api_base``: litellm's ollama
        # branch resolves the endpoint as ``litellm.api_base or api_base
        # or env or default`` — the module global beats the per-call
        # kwarg — so setting it here silently defeats
        # ``model_api_base_overrides`` (glad-labs-stack#2051 prod
        # regression, 2026-07-02). ``complete()`` / ``stream()`` always
        # pass the effective base as a per-call kwarg.
        try:
            import litellm
            litellm.set_verbose = False  # type: ignore[attr-defined]  # noqa: SLF001
            litellm.drop_params = self._drop_params
            litellm.disable_aiohttp_transport = self._disable_aiohttp_transport
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[litellm_provider] global config apply failed: %s", exc,
            )

    def _resolve_model(self, model: str) -> str:
        """Apply the default provider prefix when the caller passed a
        bare model name. ``ollama/gemma3:27b`` stays as-is;
        ``gemma3:27b`` becomes ``ollama/gemma3:27b``.

        Delegates to the module-level :func:`resolve_model_name` so callers
        that must resolve a name WITHOUT a provider instance (the
        dispatcher's GPU-lock decision, via :func:`pinned_api_base_for`)
        cannot drift from this behaviour.
        """
        return resolve_model_name(model, self._default_prefix)

    def _api_base_for(self, resolved_model: str) -> str | None:
        """Effective api_base for one call — per-model override wins.

        Keyed on the RESOLVED model name (``ollama/qwen3-vl:30b``), the
        same string operators put in ``qa_vision_model`` etc., so the
        override map reads naturally next to the per-step model pins.
        glad-labs-stack#2051: routes eviction-prone models to a
        GPU-pinned second Ollama instance.
        """
        return (
            self._model_api_base_overrides.get(resolved_model)
            or self._api_base
        )

    def _api_base_applies(self, resolved_model: str) -> bool:
        """Whether the effective ``api_base`` should be attached to this call.

        The configured ``api_base`` row is the LOCAL backend endpoint
        (Ollama in the default install) — every local-prefix model routes
        there. A CLOUD prefix (``anthropic/``, ``openai/``, ...) must NOT
        inherit it: litellm treats an explicit ``api_base`` as
        authoritative over the prefix's canonical endpoint, so the "cloud"
        call silently lands on the local server instead. Ollama ≥0.6 even
        answers ``/v1/messages`` with an Anthropic-SHAPED error body
        ("model 'claude-sonnet-5' not found", request_id and all), which
        made exactly this failure masquerade as a real Anthropic 404
        during the 2026-07-07 cloud-writer canary.

        Cloud prefixes therefore fall through to litellm's per-provider
        default endpoints (auth via the env keys
        ``configure_cloud_api_keys`` stamps). An explicit per-model
        override still wins for ANY prefix — that's the operator saying
        "this exact model goes here", and the paid-endpoint policy has
        already validated the override URL on axis 1.
        """
        if resolved_model in self._model_api_base_overrides:
            return True
        return self._is_local_prefix(resolved_model)

    def _is_local_prefix(self, resolved_model: str) -> bool:
        """Whether the resolved model routes to a LOCAL backend.

        Decides which request params are safe to forward (Ollama-only
        params must be dropped for cloud — see ``_OLLAMA_ONLY_PARAMS``).
        An inline ``http(s)://`` model string is local iff the URL is a
        loopback / docker-internal host; otherwise the call is by
        namespace prefix.
        """
        if resolved_model.startswith("http"):
            return is_local_base_url(resolved_model)
        prefix = resolved_model.split("/", 1)[0].lower()
        return prefix in _LOCAL_MODEL_PREFIXES

    def _apply_cloud_max_tokens(
        self, resolved_model: str, completion_kwargs: dict[str, Any],
    ) -> None:
        """Apply the cloud completion-budget floor in place.

        Fires only when the caller didn't pass ``max_tokens`` AND the
        resolved model is a cloud prefix (not in
        ``_LOCAL_MODEL_PREFIXES``, not an inline ``http`` base). LiteLLM
        defaults ``anthropic/*`` to ``max_tokens=4096``, and on
        adaptive-thinking Claude models (Sonnet 5+) thinking + visible
        text draw from that ONE budget — ~3K thinking tokens starve a
        long-form draft mid-word (2026-07-06 A/B run,
        glad-labs-stack#2153). Local backends are untouched: Ollama's
        default is unbounded generation, and callers that want caps pass
        ``max_tokens`` explicitly.
        """
        if "max_tokens" in completion_kwargs:
            return
        if resolved_model.startswith("http"):
            return
        prefix = resolved_model.split("/", 1)[0].lower()
        if prefix in _LOCAL_MODEL_PREFIXES:
            return
        completion_kwargs["max_tokens"] = self._cloud_max_tokens

    def _enforce_paid_endpoint_policy(self, resolved_model: str) -> None:
        """Refuse paid LiteLLM targets unless the operator opted in.

        LiteLLM routes to a paid backend via either axis:

        1. ``api_base`` pointing at a cloud host (api.openai.com,
           api.anthropic.com, generativelanguage.googleapis.com,
           openrouter.ai, api.groq.com, ...).
        2. Model namespace prefix (``openai/gpt-4o``, ``anthropic/
           claude-haiku-4-5``, ``gemini/...``) — LiteLLM auto-discovers
           ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY`` / ``GEMINI_API_KEY``
           from env, so a bare model string fires a paid call without
           any explicit ``api_base``. This is the bypass that closed
           cycle-4's #615 fix on OpenAICompatProvider — same incident
           class, one layer up the stack.

        The gate refuses both unless ``plugin.llm_provider.litellm.
        allow_paid_base_url=true`` is set. Local backends (Ollama / vllm
        / lm_studio / generic OAI-compat HTTP) cost zero dollars
        regardless of traffic and are always allowed.

        Conservative-deny by allowlist: anything not in
        ``_LOCAL_MODEL_PREFIXES`` is treated as paid. LiteLLM keeps
        adding cloud vendors (100+ as of 2026); a denylist drifts every
        release, the local set is small and stable.

        Raised as ``RuntimeError`` (not ``CostGuardExhausted``) because
        the call is being refused on the configuration axis, not the
        budget axis. The error message names the exact app_setting an
        operator must flip to authorise the paid path.
        """
        if self._allow_paid_base_url:
            return

        # Axis 1 — explicit api_base. ``resolved_model`` starting with
        # http:// also flows through this branch since LiteLLM treats
        # that as an inline base URL. The EFFECTIVE base is checked, so a
        # per-model override can't smuggle a cloud URL past the gate.
        candidate_url: str | None = None
        effective_base = self._api_base_for(resolved_model)
        if resolved_model.startswith("http"):
            candidate_url = resolved_model
        elif effective_base:
            candidate_url = effective_base
        if candidate_url and not is_local_base_url(candidate_url):
            raise RuntimeError(
                f"LiteLLMProvider refuses non-local api_base "
                f"{candidate_url!r} — authorise paid endpoints by setting "
                f"plugin.llm_provider.litellm.allow_paid_base_url=true "
                f"(e.g. `poindexter settings set "
                f"plugin.llm_provider.litellm.allow_paid_base_url true`). "
                f"Default is false per feedback_no_paid_apis to prevent "
                f"unmonitored spend when the dispatch target is "
                f"swapped via DB edit."
            )

        # Axis 2 — model namespace prefix. LiteLLM's "<provider>/<model>"
        # convention is the auth-discovery seam (``openai/`` reads
        # ``OPENAI_API_KEY``, etc.). Anything not in the local allowlist
        # is treated as paid.
        if resolved_model.startswith("http"):
            return  # already handled by axis 1, no prefix to extract
        prefix = resolved_model.split("/", 1)[0].lower()
        if prefix in _LOCAL_MODEL_PREFIXES:
            return
        raise RuntimeError(
            f"LiteLLMProvider refuses paid model prefix {prefix!r} "
            f"(resolved_model={resolved_model!r}) — LiteLLM routes "
            f"this through a cloud vendor and auto-discovers the API "
            f"key from env. Authorise paid endpoints by setting "
            f"plugin.llm_provider.litellm.allow_paid_base_url=true "
            f"(e.g. `poindexter settings set "
            f"plugin.llm_provider.litellm.allow_paid_base_url true`), "
            f"or fix the caller / per-step *_model setting to use a "
            f"local prefix ({', '.join(sorted(_LOCAL_MODEL_PREFIXES))}). "
            f"Default-deny per feedback_no_paid_apis."
        )

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> Completion:
        provider_config = kwargs.pop("_provider_config", {}) or {}
        self._configure_from(provider_config)

        import litellm

        resolved_model = self._resolve_model(model)
        self._enforce_paid_endpoint_policy(resolved_model)
        # Anthropic prompt caching: annotate the system prefix with an
        # ephemeral breakpoint on the ``anthropic/`` path (returns a copy;
        # a no-op on local + other-vendor targets and when disabled).
        call_messages = messages
        if self._anthropic_prompt_caching and _is_anthropic_model(resolved_model):
            call_messages = _annotate_system_cache_control(messages)
        timeout = float(kwargs.pop("timeout_s", self._timeout))
        completion_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": call_messages,
            "timeout": timeout,
            "stream": False,
        }
        effective_base = self._api_base_for(resolved_model)
        if (
            effective_base
            and not resolved_model.startswith("http")
            and self._api_base_applies(resolved_model)
        ):
            completion_kwargs["api_base"] = effective_base
        # ``response_format`` forwarding (2026-05-28): the topic_ranking +
        # writer-RAG JSON callers need to force structured-JSON output. The
        # OpenAI convention is ``response_format={"type": "json_object"}``;
        # LiteLLM maps that to Ollama's ``format=json`` automatically when
        # the resolved provider is ollama / ollama_chat, and forwards it
        # unchanged to OpenAI / Anthropic / etc. With ``drop_params=True``
        # set globally, providers that don't recognize the param ignore it
        # rather than 4xx — so this is the safe seam to retire the legacy
        # direct-httpx ``"format": "json"`` payload key that the
        # ``_ollama_chat_json`` survivors were using.
        #
        # ``think`` forwarding (2026-07-06): a thinking-capable Ollama model
        # (e.g. the gemma-4-31B-it-qat writer) burns its generation budget in a
        # hidden reasoning channel, truncating the VISIBLE draft. LiteLLM's
        # ollama transformation forwards ``think`` as a top-level request field
        # (verified in-container: think=False → complete draft vs baseline
        # truncation), so the writer path passes ``think=False`` to disable the
        # channel. Non-thinking backends ignore the param under drop_params.
        # ``tools`` / ``tool_choice`` forwarding (poindexter#947): OpenAI-spec
        # function-calling params. LiteLLM maps them per-backend (Ollama /api/chat
        # ``tools``, OpenAI/Anthropic native) and returns the OpenAI shape, so the
        # tool_calls extraction below is backend-uniform.
        for key in (
            "temperature", "max_tokens", "top_p", "response_format",
            "num_ctx", "think", "tools", "tool_choice",
        ):
            if key in kwargs:
                completion_kwargs[key] = kwargs[key]
        # Ollama-only params (num_ctx / think) 400 a cloud provider — litellm
        # forwards them verbatim because they aren't OpenAI-spec, so
        # drop_params never strips them. Drop them for any non-local target.
        if not self._is_local_prefix(resolved_model):
            for _p in _OLLAMA_ONLY_PARAMS:
                completion_kwargs.pop(_p, None)
        self._apply_cloud_max_tokens(resolved_model, completion_kwargs)

        logger.debug(
            "[litellm_provider] complete: model=%s timeout=%s",
            resolved_model, timeout,
        )

        try:
            response = await litellm.acompletion(**completion_kwargs)
        except Exception as exc:
            logger.exception(
                "[litellm_provider] acompletion failed for model=%s: %s",
                resolved_model, exc,
            )
            raise

        # LiteLLM normalizes responses to OpenAI shape — same fields
        # whether the backend is Ollama, OpenAI, Anthropic, etc.
        choice = response.choices[0] if response.choices else None
        text = ""
        finish_reason = ""
        tool_calls: list[dict[str, Any]] | None = None
        if choice is not None:
            msg = getattr(choice, "message", None)
            text = (getattr(msg, "content", None) or "") if msg else ""
            # Tool-call extraction (poindexter#947). Normalize the OpenAI
            # shape into plain dicts so Completion callers never touch
            # litellm's pydantic objects. ``arguments`` stays the raw JSON
            # string — parse + repair belong to the agent layer.
            raw_tool_calls = getattr(msg, "tool_calls", None) if msg else None
            if raw_tool_calls:
                tool_calls = []
                for tc in raw_tool_calls:
                    fn = getattr(tc, "function", None)
                    tool_calls.append({
                        "id": getattr(tc, "id", "") or "",
                        "name": (getattr(fn, "name", "") or "") if fn else "",
                        "arguments": (
                            (getattr(fn, "arguments", "") or "") if fn else ""
                        ),
                    })
            # Strip leaked reasoning / chat-template control tokens a
            # mis-templated or reasoning-channel model inlines into the main
            # content channel (e.g. "<|channel>thought<channel|>…" — two real
            # prod leaks 2026-06-09). This is the single chokepoint every
            # dispatcher call converges on, so one strip here covers the
            # writer / title / SEO / QA prose paths uniformly. Runs BEFORE the
            # empty-content fallback so an all-control-token body still
            # triggers reasoning_content recovery. Fence-aware + no-op on
            # clean prose (see strip_reasoning_artifacts).
            #
            # SKIP for JSON-mode calls: the stripper is a PROSE cleaner, and a
            # structured response_format=json_object payload can legitimately
            # carry a control-token literal inside a string value (e.g. a
            # topic-ranking explanation about chat templates). The JSON path is
            # already covered by maybe_unwrap_json downstream; mutating string
            # values here would be a silent data-quality regression.
            _rf = kwargs.get("response_format")
            _json_mode = isinstance(_rf, dict) and _rf.get("type") == "json_object"
            if not _json_mode:
                text = strip_reasoning_artifacts(text)
            finish_reason = getattr(choice, "finish_reason", "") or ""
            # Reasoning-model fallback: a thinking model under json mode can
            # return empty ``content`` with all tokens in ``reasoning_content``.
            # Recover the reasoned payload so downstream json.loads doesn't
            # get an empty string. See __init__ for the why.
            # SKIP when the model returned tool_calls: an empty content
            # channel is the NORMAL shape for a tool-call turn, and
            # "recovering" reasoning text would fabricate an assistant
            # answer alongside the calls (poindexter#947).
            if not text.strip() and not tool_calls and self._reasoning_content_fallback and msg:
                recovered = _recover_reasoning_text(msg)
                if recovered:
                    logger.warning(
                        "[litellm_provider] empty content for model=%s; "
                        "recovered %d chars from reasoning_content",
                        resolved_model, len(recovered),
                    )
                    text = recovered

        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        completion_tokens = (
            int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        )
        total_tokens = int(getattr(usage, "total_tokens", 0) or 0) if usage else 0

        # LiteLLM computes the call cost but stores it on the response's
        # ``_hidden_params`` dict — NOT as a top-level attribute, so
        # ``hasattr(response, "response_cost")`` is ALWAYS False. Reading it
        # that way meant every PAID cloud call logged cost_usd=0, which
        # silently defeats the cost_guard cap (it sums cost_logs.cost_usd,
        # so a table of zeros never trips the daily/monthly limit). Latent
        # until the 2026-07-07 Sonnet cutover started making real paid calls
        # — the first canary draft (8k in / 3.9k out) logged $0. Read the
        # hidden-params value, then fall back to ``completion_cost()`` (both
        # verified in-container to return the true price). ``_response_ms``
        # has the same top-level-attr issue but is cosmetic (latency
        # display), so it stays best-effort.
        raw: dict[str, Any] = {}
        try:
            raw = (
                response.model_dump() if hasattr(response, "model_dump")
                else dict(response) if isinstance(response, dict) else {}
            )
        except Exception:  # noqa: BLE001
            raw = {}
        if hasattr(response, "_response_ms"):
            raw["_response_ms"] = response._response_ms  # noqa: SLF001
        response_cost = _extract_response_cost(response)
        if response_cost is not None:
            raw["response_cost"] = response_cost

        # Prompt-cache visibility: surface Anthropic's cache hit/miss token
        # counts (mirrors the native anthropic provider's ``_extract_usage``).
        # LiteLLM mirrors these onto ``usage`` for the anthropic path; they
        # read 0 on local / other-vendor calls, so the stamp is skipped there.
        # Cost is already correct via ``response_cost`` (litellm prices cache
        # reads); this is purely so cost_logs + Langfuse can confirm the
        # breakpoint is actually landing instead of silently no-op'ing.
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0) if usage else 0
        cache_creation = (
            int(getattr(usage, "cache_creation_input_tokens", 0) or 0) if usage else 0
        )
        if cache_read or cache_creation:
            raw["cache_read_input_tokens"] = cache_read
            raw["cache_creation_input_tokens"] = cache_creation

        return Completion(
            text=text,
            model=resolved_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            raw=raw,
            tool_calls=tool_calls,
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        **kwargs: Any,
    ) -> AsyncIterator[Token]:
        provider_config = kwargs.pop("_provider_config", {}) or {}
        self._configure_from(provider_config)

        import litellm

        resolved_model = self._resolve_model(model)
        self._enforce_paid_endpoint_policy(resolved_model)
        timeout = float(kwargs.pop("timeout_s", self._timeout))
        completion_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "timeout": timeout,
            "stream": True,
        }
        effective_base = self._api_base_for(resolved_model)
        if (
            effective_base
            and not resolved_model.startswith("http")
            and self._api_base_applies(resolved_model)
        ):
            completion_kwargs["api_base"] = effective_base
        for key in ("temperature", "max_tokens", "top_p"):
            if key in kwargs:
                completion_kwargs[key] = kwargs[key]
        self._apply_cloud_max_tokens(resolved_model, completion_kwargs)

        response = await litellm.acompletion(**completion_kwargs)
        async for chunk in response:
            choice = chunk.choices[0] if getattr(chunk, "choices", None) else None
            if choice is None:
                continue
            delta = getattr(choice, "delta", None)
            text = (getattr(delta, "content", None) or "") if delta else ""
            finish_reason = getattr(choice, "finish_reason", None)
            yield Token(text=text, finish_reason=finish_reason)

    async def embed(self, text: str, model: str, **kwargs: Any) -> list[float]:
        """Embed via ``litellm.aembedding``.

        ``**kwargs`` exists so the dispatcher can inject ``_provider_config``
        (carrying ``api_base`` / ``allow_paid_base_url`` / ``timeout_seconds``)
        symmetrically with ``complete()`` / ``stream()``. Without that, the
        paid-endpoint policy would be bypassed by routing through the embed
        path on a paid backend — same runaway-cost class the policy was
        added to prevent. The dispatcher already supplies the kwarg (see
        ``dispatch_embed`` after PR #615).

        api_base attachment mirrors ``complete()`` exactly (poindexter#878).
        This method used to read ``_provider_config`` and then never pass the
        resolved endpoint to litellm, so litellm fell back to its built-in
        ollama default (``localhost:11434``) — unreachable from inside the
        worker container, making EVERY ``dispatch_embed`` call fail. It killed
        the ``self_consistency`` rail for 9+ days without a peep, because that
        rail's skip path scored the un-run check 100 (fixed separately in
        glad-labs-stack#2655).
        """
        provider_config = kwargs.pop("_provider_config", {}) or {}
        self._configure_from(provider_config)

        import litellm

        # LiteLLM's embedding API takes the same model namespace as
        # acompletion — "ollama/nomic-embed-text" routes to local Ollama.
        resolved_model = self._resolve_model(model)
        self._enforce_paid_endpoint_policy(resolved_model)
        embed_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "input": [text],
            "timeout": self._timeout,
        }
        # Same three guards as complete(): a cloud prefix must not inherit the
        # local endpoint, an inline http(s):// model is litellm's to parse, and
        # a per-model override still wins for any prefix.
        effective_base = self._api_base_for(resolved_model)
        if (
            effective_base
            and not resolved_model.startswith("http")
            and self._api_base_applies(resolved_model)
        ):
            embed_kwargs["api_base"] = effective_base
        response = await litellm.aembedding(**embed_kwargs)
        data = response.data if hasattr(response, "data") else response.get("data", [])
        if not data:
            return []
        embedding = data[0]
        if hasattr(embedding, "embedding"):
            return list(embedding.embedding)
        if isinstance(embedding, dict):
            return list(embedding.get("embedding", []))
        return list(embedding)


__all__ = [
    "LangfuseConfigError",
    "LiteLLMProvider",
    "configure_cloud_api_keys",
    "configure_langfuse_callback",
]
