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
- ``timeout_seconds`` (default 120) — per-call default; per-call
  overrides via the ``timeout_s`` kwarg still win.
- ``drop_params`` (default true) — strip params the target backend
  doesn't recognize so a single call signature works across backends.

Observability — Langfuse tracing (poindexter#373):

- ``langfuse_tracing_enabled`` (bool, default true) — when true,
  ``configure_langfuse_callback`` registers Langfuse as LiteLLM's
  ``success_callback`` + ``failure_callback`` so every call emits a
  span to the Langfuse host configured via the same three credential
  rows that the prompt manager already uses (``langfuse_host``,
  ``langfuse_public_key``, ``langfuse_secret_key``).
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

import logging
import os
import re
from collections.abc import AsyncIterator
from typing import Any

from plugins.llm_provider import Completion, Token
from services.cost_guard import is_local_base_url

logger = logging.getLogger(__name__)


# Module-level idempotency guard — Langfuse callback registration is a
# process-wide mutation of ``litellm.success_callback`` /
# ``failure_callback``, so we only do it once even if the worker calls
# ``configure_langfuse_callback`` multiple times (e.g. main.py +
# CLI re-init paths).
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
      them — see litellm.utils._init_logging_callbacks), then set
      ``litellm.success_callback = ["langfuse"]`` +
      ``litellm.failure_callback = ["langfuse"]``.

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
    public_key = (site_config.get("langfuse_public_key", "") or "").strip()
    try:
        secret_key_raw = await site_config.get_secret("langfuse_secret_key", "")
    except Exception as exc:  # noqa: BLE001
        raise LangfuseConfigError(
            f"langfuse_tracing_enabled=true but reading "
            f"langfuse_secret_key failed: {exc!s}. Either populate the "
            f"row in app_settings (see migration 0153) or set "
            f"langfuse_tracing_enabled=false.",
        ) from exc
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

    # LiteLLM's Langfuse integration (litellm/integrations/langfuse.py)
    # reads these three env vars on first callback fire. Stamping them
    # at startup means we don't need a custom Langfuse client wired in
    # — LiteLLM constructs its own + reuses across calls.
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
    except ImportError as exc:
        raise LangfuseConfigError(
            "langfuse_tracing_enabled=true but the litellm package is "
            "not installed. Install it (it's pulled in by the standard "
            "poindexter dependencies) or set langfuse_tracing_enabled "
            "=false.",
        ) from exc

    # LiteLLM accepts callback names as strings; the integration
    # registry turns "langfuse_otel" into a LangfuseOtelLogger instance
    # that ships spans via OTLP to ``LANGFUSE_HOST/api/public/otel``.
    # Per LiteLLM docs, success + failure go to the same callback
    # (different code paths in the logger).
    #
    # We use ``langfuse_otel`` (not the older ``langfuse``) because the
    # legacy integration constructs the v2 ``Langfuse`` SDK client
    # which is incompatible with langfuse>=3.0 — passing the dropped
    # ``sdk_integration`` kwarg raises ``TypeError`` on every call.
    # ``langfuse_otel`` talks to the public OTEL ingest endpoint
    # directly and reads the same three env vars we just stamped.
    litellm.success_callback = ["langfuse_otel"]
    litellm.failure_callback = ["langfuse_otel"]
    _LANGFUSE_CALLBACK_REGISTERED = True
    logger.info(
        "[litellm_provider] Langfuse tracing active (host=%s) — every "
        "LLM call routed through LiteLLMProvider will emit a span.",
        host,
    )
    return True


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

    def __init__(self) -> None:
        self._configured = False
        self._default_prefix = "ollama/"
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
        prefix = provider_config.get("default_prefix")
        if prefix:
            self._default_prefix = prefix
        # Re-read on every call so flipping the app_setting takes effect on
        # the next dispatch without a worker restart, same contract as
        # ``api_base`` / ``timeout_seconds`` above.
        self._allow_paid_base_url = _coerce_bool(
            provider_config.get("allow_paid_base_url"),
        )
        if not self._configured:
            self._apply_global_litellm_config()
            self._configured = True

    def _apply_global_litellm_config(self) -> None:
        """Wire LiteLLM's process-wide knobs once.

        ``set_verbose=False`` keeps litellm out of our logs unless the
        operator opts in. ``drop_params`` lets one call signature work
        against backends with different param vocabularies. The Ollama
        default api_base is what every other Ollama caller uses.
        """
        try:
            import litellm
            litellm.set_verbose = False  # noqa: SLF001 — public surface
            litellm.drop_params = self._drop_params
            if self._api_base:
                litellm.api_base = self._api_base
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[litellm_provider] global config apply failed: %s", exc,
            )

    def _resolve_model(self, model: str) -> str:
        """Apply the default provider prefix when the caller passed a
        bare model name. ``ollama/gemma3:27b`` stays as-is;
        ``gemma3:27b`` becomes ``ollama/gemma3:27b``.
        """
        if "/" in model and not model.startswith("http"):
            return model
        if model.startswith("http"):
            return model
        return f"{self._default_prefix.rstrip('/')}/{model}"

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
        # that as an inline base URL.
        candidate_url: str | None = None
        if resolved_model.startswith("http"):
            candidate_url = resolved_model
        elif self._api_base:
            candidate_url = self._api_base
        if candidate_url and not is_local_base_url(candidate_url):
            raise RuntimeError(
                f"LiteLLMProvider refuses non-local api_base "
                f"{candidate_url!r} — set "
                f"plugin.llm_provider.litellm.allow_paid_base_url=true "
                f"in app_settings to authorise paid endpoints. Default "
                f"is false per feedback_no_paid_apis to prevent "
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
            f"key from env. Set "
            f"plugin.llm_provider.litellm.allow_paid_base_url=true in "
            f"app_settings to authorise paid endpoints, or fix the "
            f"caller / cost_tier.<tier>.model row to use a local "
            f"prefix ({', '.join(sorted(_LOCAL_MODEL_PREFIXES))}). "
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
        timeout = float(kwargs.pop("timeout_s", self._timeout))
        completion_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "timeout": timeout,
            "stream": False,
        }
        if self._api_base and not resolved_model.startswith("http"):
            completion_kwargs["api_base"] = self._api_base
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
        for key in ("temperature", "max_tokens", "top_p", "response_format"):
            if key in kwargs:
                completion_kwargs[key] = kwargs[key]

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
        if choice is not None:
            msg = getattr(choice, "message", None)
            text = (getattr(msg, "content", None) or "") if msg else ""
            finish_reason = getattr(choice, "finish_reason", "") or ""
            # Reasoning-model fallback: a thinking model under json mode can
            # return empty ``content`` with all tokens in ``reasoning_content``.
            # Recover the reasoned payload so downstream json.loads doesn't
            # get an empty string. See __init__ for the why.
            if not text.strip() and self._reasoning_content_fallback and msg:
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

        # LiteLLM stamps response_cost on the response when its cost
        # table knows the model. Surface that in raw so cost_logs can
        # consume it without re-deriving the price.
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
        if hasattr(response, "response_cost"):
            raw["response_cost"] = response.response_cost

        return Completion(
            text=text,
            model=resolved_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            raw=raw,
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
        if self._api_base and not resolved_model.startswith("http"):
            completion_kwargs["api_base"] = self._api_base
        for key in ("temperature", "max_tokens", "top_p"):
            if key in kwargs:
                completion_kwargs[key] = kwargs[key]

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
        """
        provider_config = kwargs.pop("_provider_config", {}) or {}
        self._configure_from(provider_config)

        import litellm

        # LiteLLM's embedding API takes the same model namespace as
        # acompletion — "ollama/nomic-embed-text" routes to local Ollama.
        resolved_model = self._resolve_model(model)
        self._enforce_paid_endpoint_policy(resolved_model)
        response = await litellm.aembedding(
            model=resolved_model,
            input=[text],
            timeout=self._timeout,
        )
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
    "configure_langfuse_callback",
]
