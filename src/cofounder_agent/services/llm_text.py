"""``llm_text`` — local-LLM plain-text chat helper, shared by atoms.

A common surface for atoms / writer modes / the pipeline architect that
need a single plain-text completion against the configured LLM tier.

Routing behavior (2026-05-16 cleanup — propagates LiteLLM cutover to all
writer paths):

- When a ``pool`` is reachable (production / worker): route through
  :func:`services.llm_providers.dispatcher.dispatch_complete` at the
  requested tier. This is what makes
  ``plugin.llm_provider.primary.standard='litellm'`` actually take
  effect for these callers — they were previously hardwired to Ollama
  via direct ``httpx.post`` calls to ``/api/chat``.
- When no ``pool`` is available (tests / bootstrap): fall back to a
  direct ``httpx`` POST to the local Ollama instance. Same behavior as
  before the dispatcher cutover.

This module is the single source of truth for plain-text LLM chat. The
three private ``_ollama_chat_text`` helpers that previously lived in
:mod:`modules.content.atoms.narrate_bundle`,
:mod:`services.writer_rag_modes.deterministic_compositor`, and
:mod:`services.pipeline_architect` were deleted in favor of this
helper. Callers in those modules now import :func:`ollama_chat_text`
directly.

What this provides:

- :func:`ollama_chat_text` — plain text in, plain text out. No JSON
  envelope, no schema constraints. Routes through the dispatcher when
  a pool is provided; falls back to direct httpx otherwise.
- :func:`maybe_unwrap_json` — defensive unwrap for models that emit
  ``{"thought": "..."}`` envelopes even when not asked for JSON.
- :func:`resolve_local_model` — model-name normalizer (strips
  ``ollama/`` prefix, falls back to the writer-model setting).

Local-by-default. The no-paid-APIs policy
(``feedback_no_paid_apis`` memory) means the dispatcher picks the
provider per ``plugin.llm_provider.primary.<tier>``; operators flip to
cloud providers only via that setting AND the cost_guard.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Langfuse @observe — wires every ollama_chat_text call into the
# trace tree so the operator can drill into model/prompt/latency in
# the Langfuse UI at http://localhost:3010. This is the HIGHER-LEVEL
# intent span; ``dispatch_complete`` emits its own lower-level
# transport span via OpenTelemetry. Both are useful — keep both.
from services.langfuse_shim import langfuse_context, observe


def resolve_local_model(model: str | None = None, *, site_config: Any = None) -> str:
    """Pick the local model to call. Removes ``ollama/`` prefix and
    falls back through ``pipeline_writer_model`` → ``cost_tier.standard.model``.

    **Canonical precedence reference.** This function defines the authoritative
    lookup order for writer-model resolution. All other resolvers in the
    codebase (``_resolve_writer_models`` and ``_resolve_rag_writer_model`` in
    ``modules/content/ai_content_generator.py``) must follow this same order:
    ``pipeline_writer_model`` first, ``cost_tier.standard.model`` as fallback.
    See glad-labs-stack#1281 for the bug that had the ACG resolvers inverted.

    Accepts the SiteConfig instance via the DI seam (glad-labs-stack#330).

    2026-05-12 cleanup (poindexter#485): the hardcoded
    ``"glm-4.7-5090:latest"`` fallback that used to live here baked
    Matt's specific custom model name into a public OSS file — forks
    installing Poindexter wouldn't have that model and would get a
    confusing "model not found" error from Ollama at call time. Now
    chains through the tier API instead.

    Raises:
        ValueError when every resolution path is unset — fail loud so
        the operator notices a broken install before content generation
        blows up mid-stage.
    """
    if model:
        return model.removeprefix("ollama/")
    if site_config is None:
        raise ValueError(
            "llm_text.resolve_local_model: site_config is required to "
            "resolve the writer model (no hardcoded fallback by design). "
            "Pass site_config explicitly or set the lifespan-bound instance."
        )
    try:
        writer = (
            site_config.get("pipeline_writer_model", "") or ""
        ).strip()
        if writer:
            return writer.removeprefix("ollama/")
    except Exception as e:  # noqa: BLE001 — defensive against test stubs
        logger.warning(
            "[llm_text] site_config.get('pipeline_writer_model') raised %s — "
            "falling through to cost-tier resolution", e,
        )
    try:
        tier = (
            site_config.get("cost_tier.standard.model", "") or ""
        ).strip()
        if tier:
            return tier.removeprefix("ollama/")
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[llm_text] cost-tier lookup failed (%s) — no writer model "
            "can be resolved", e,
        )
    raise ValueError(
        "llm_text: no writer model resolvable from app_settings — "
        "set ``pipeline_writer_model`` OR ``cost_tier.standard.model`` "
        "via `poindexter set-setting`."
    )


def resolve_structured_model(
    model: str | None = None, *, site_config: Any = None
) -> str:
    """Pick the model for structured-JSON extraction calls (``json_object``).

    Distinct from :func:`resolve_local_model` on purpose. The writer model
    (``pipeline_writer_model``) may be a *reasoning* model (e.g.
    ``glm-4.7-5090``) that emits its tokens into a thinking channel and
    returns an **empty** ``content`` field under
    ``response_format={"type": "json_object"}``. That empty string then
    crashed every ``json.loads`` caller in topic discovery (2026-05-28
    content-generation stall). Structured-extraction call sites resolve
    their model here instead, reading the DB-configurable
    ``structured_extraction_model`` so an operator can pin a
    JSON-reliable instruct model without touching the writer model.

    Resolution order: explicit ``model`` arg → ``structured_extraction_model``
    → ``cost_tier.standard.model``. The ``ollama/`` prefix is stripped to
    match the local-call convention. Raises ``ValueError`` when nothing is
    resolvable (fail loud per ``feedback_no_silent_defaults``).
    """
    if model:
        return model.removeprefix("ollama/")
    if site_config is None:
        raise ValueError(
            "llm_text.resolve_structured_model: site_config is required "
            "(no hardcoded fallback by design)."
        )
    for key in ("structured_extraction_model", "cost_tier.standard.model"):
        try:
            val = (site_config.get(key, "") or "").strip()
        except Exception as e:  # noqa: BLE001 — defensive against test stubs
            logger.warning(
                "[llm_text] site_config.get(%r) raised %s — trying next", key, e,
            )
            continue
        if val:
            return val.removeprefix("ollama/")
    raise ValueError(
        "llm_text: no structured-extraction model resolvable — set "
        "``structured_extraction_model`` OR ``cost_tier.standard.model`` "
        "via `poindexter set-setting`."
    )


@observe(as_type="generation", name="ollama_chat_text")
async def ollama_chat_text(
    prompt: str,
    *,
    model: str | None = None,
    timeout_setting: str = "atom_chat_timeout_seconds",
    timeout_default: float = 120.0,
    system: str | None = None,
    site_config: Any = None,
    pool: Any = None,
    tier: str = "standard",
) -> str:
    """Plain-text LLM chat call.

    Routes through :func:`services.llm_providers.dispatcher.dispatch_complete`
    when ``pool`` is provided (production / worker path — picks up the
    configured provider per ``plugin.llm_provider.primary.<tier>``).
    Falls back to a direct httpx POST to local Ollama's ``/api/chat``
    when no pool is available (tests / bootstrap).

    The function-name retains the historical ``ollama_chat_text``
    spelling because callers reference it under that name. The dispatch
    layer hides whether the actual transport is Ollama, LiteLLM, vllm,
    etc. — picked per app_settings.

    Args:
        prompt: The user message body.
        model: Concrete model name. When provided, sent to the
            provider as-is (after stripping ``ollama/`` prefix via
            :func:`resolve_local_model`). When ``None``, resolves via
            :func:`resolve_local_model`.
        timeout_setting: ``app_settings`` key for the request timeout.
            Default key is generic; per-atom helpers can override
            (e.g. narrate_bundle uses ``niche_ollama_chat_timeout_seconds``).
        timeout_default: Fallback timeout when the setting is unset.
        system: Optional system prompt prepended as a system role
            message.
        site_config: SiteConfig DI seam (glad-labs-stack#330). Required
            for model resolution when ``model`` is unset.
        pool: asyncpg Pool — when provided, routes via the LLM provider
            dispatcher so the call honors
            ``plugin.llm_provider.primary.<tier>`` (LiteLLM / Ollama /
            OpenAI-compat / etc.). When ``None``, falls back to direct
            httpx → local Ollama. Production paths should always pass a
            pool; the httpx fallback is for tests + bootstrap only.
        tier: cost tier passed to the dispatcher when ``pool`` is set
            (``free`` / ``budget`` / ``standard`` / ``premium`` /
            ``flagship``). Ignored on the httpx fallback path.

    Returns:
        The raw assistant content (post-unwrap, see
        :func:`maybe_unwrap_json`). Empty string on missing content.
    """
    resolved_model = resolve_local_model(model, site_config=site_config)
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    # Stamp model + input on the Langfuse generation span before the
    # call so the trace records latency + model even if the dispatch
    # errors. update_current_observation is a no-op when Langfuse
    # isn't wired.
    langfuse_context.update_current_observation(
        model=resolved_model,
        input=messages,
        metadata={"tier": tier, "via": "dispatcher" if pool is not None else "httpx"},
    )

    if pool is not None:
        # Production path — dispatch through the configured LLM
        # provider. Any failure here is a real production issue (not a
        # transient httpx hiccup that warrants a silent fallback), so
        # let the exception propagate. Per ``feedback_no_silent_defaults``:
        # missing-pool / missing-config should fail loud in production.
        from services.llm_providers.dispatcher import dispatch_complete

        timeout = (
            site_config.get_float(timeout_setting, timeout_default)
            if site_config is not None else timeout_default
        )
        completion = await dispatch_complete(
            pool=pool,
            messages=messages,
            model=resolved_model,
            tier=tier,
            timeout_s=int(timeout),
        )
        raw = getattr(completion, "text", "") or ""
        usage_details = {
            "input": int(getattr(completion, "prompt_tokens", 0) or 0),
            "output": int(getattr(completion, "completion_tokens", 0) or 0),
        }
    else:
        # Test / bootstrap fallback — direct httpx → local Ollama. Same
        # behavior as before the dispatcher cutover. Used when there's
        # no DB pool to consult (unit tests stub site_config but not a
        # full asyncpg pool; bootstrap scripts run before any pool is
        # established).
        import httpx

        base_url = (
            (site_config.get("local_llm_api_url", "http://localhost:11434")
                if site_config is not None else "http://localhost:11434").rstrip("/")
        )
        timeout = (
            site_config.get_float(timeout_setting, timeout_default)
            if site_config is not None else timeout_default
        )
        payload = {
            "model": resolved_model,
            "messages": messages,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        raw = (data.get("message") or {}).get("content", "") or ""
        usage_details = {
            "input": data.get("prompt_eval_count") or 0,
            "output": data.get("eval_count") or 0,
        }

    # Strip leaked reasoning / chat-template control tokens BEFORE the JSON
    # unwrap (a model may wrap a JSON envelope in a "<|channel>thought…" header;
    # stripping the markers first lets maybe_unwrap_json still see the ``{…}``).
    # Production calls also strip at the provider boundary; this covers the
    # httpx test/bootstrap fallback above and any non-dispatcher caller. Local
    # import keeps module load light for bootstrap/migration paths (mirrors the
    # dispatch_complete import idiom).
    from services.llm_providers.thinking_models import strip_reasoning_artifacts

    output = maybe_unwrap_json(strip_reasoning_artifacts(raw))
    # Stamp output + token counts so the trace surfaces tokens in the
    # Langfuse UI. ``usage_details`` is the v3 schema (Dict[str, int]);
    # "input"/"output" are the canonical Langfuse generic keys.
    langfuse_context.update_current_observation(output=output, usage_details=usage_details)
    return output


_ENVELOPE_PROSE_KEYS: tuple[str, ...] = (
    # Highest-priority keys first: full-body keys beat fragment keys when
    # both are present, since canonical_blog envelopes pair "title" + "body".
    "body",
    "content",
    # post_body: the canonical_blog writer periodically emits
    # {"title": ..., "post_body": "<markdown>"} — must rank above the
    # generic "post" fragment key so the full body wins. Its absence let a
    # ```json-fenced {"title","post_body"} envelope reach awaiting_approval
    # and render as a raw code block in the mobile preview (2026-05-31).
    "post_body",
    "markdown",
    "article",
    "post",
    "thought",
    "response",
    "text",
    "answer",
    "output",
)


def _strip_markdown_fence(s: str) -> str:
    r"""Strip a single outer ```json / ``` fence if present.

    The writer prompt forbids fenced JSON envelopes, but local models
    still emit them periodically — the 2026-05-27 canonical_blog
    incident put two posts into awaiting_approval with the literal
    "```json\n{\"title\": \"...\", \"body\": \"...\"}\n```" shape.
    Bare ``{ ... }`` is already handled by the caller; this helper
    targets the fence-wrapped variant.
    """
    s = s.strip()
    # ``` or ```json (case-insensitive) opening fence
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl == -1:
            return s
        opening = s[:first_nl].strip().lower()
        # Accept ```, ```json, ```JSON, ```jsonc — anything that LOOKS
        # like a JSON fence. Reject non-JSON fences (```python etc.)
        # because unwrapping those would discard real prose content.
        if opening in {"```", "```json", "```jsonc", "```json5"}:
            body = s[first_nl + 1:].rstrip()
            if body.endswith("```"):
                body = body[: -3].rstrip()
            return body
    return s


def maybe_unwrap_json(prose: str) -> str:
    r"""Defensive unwrap for models that wrap prose in JSON envelopes.

    Some local models emit ``{"thought": "<actual prose>"}`` or
    ``{"title": "...", "body": "<actual markdown>"}`` even when the
    request didn't set ``format=json``. They also frequently wrap that
    envelope in a "```json ... ```" markdown fence even though the
    prompt forbids it. This helper peels both layers and extracts the
    prose value under the first matching known key.

    If the input doesn't look like JSON (after fence stripping), or
    parses but has no recognized prose key, return ``prose`` unchanged
    so plain-markdown output survives untouched.
    """
    import json

    if not prose:
        return prose
    s = _strip_markdown_fence(prose)
    if not (s.startswith("{") and s.endswith("}")):
        return prose
    try:
        envelope = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return prose
    if not isinstance(envelope, dict):
        return prose
    for key in _ENVELOPE_PROSE_KEYS:
        v = envelope.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return prose


__all__ = [
    "maybe_unwrap_json",
    "ollama_chat_text",
    "resolve_local_model",
]
