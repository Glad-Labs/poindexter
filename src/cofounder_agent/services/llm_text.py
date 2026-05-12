"""``llm_text`` — local-LLM plain-text chat helper, shared by atoms.

A common surface for atoms that need the local Ollama text-completion
path without going through the model_router. The router is the right
choice when an atom wants automatic tier resolution + fallbacks; this
helper is the right choice when the atom already knows which local
model it wants and just needs to submit a prompt.

What this provides:

- :func:`ollama_chat_text` — plain text in, plain text out. No JSON
  envelope, no schema constraints. Resolves model from arg or from
  ``site_config['pipeline_writer_model']`` default.
- :func:`maybe_unwrap_json` — defensive unwrap for models that emit
  ``{"thought": "..."}`` envelopes even when not asked for JSON.
- :func:`resolve_local_model` — model-name normalizer (strips
  ``ollama/`` prefix, falls back to the writer-model setting).

All three were originally duplicated in
:mod:`services.atoms.narrate_bundle` and
:mod:`services.pipeline_architect`. Centralizing here so the next
atom that needs them doesn't make a third copy.

Local-only. The no-paid-APIs policy
(``feedback_no_paid_apis`` memory) restricts atoms to local models
unless the operator explicitly opts in via the model_router fallback
chain. This helper has no cloud-API path on purpose.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Langfuse @observe — wires every ollama_chat_text call into the
# trace tree so the operator can drill into model/prompt/latency in
# the Langfuse UI at http://localhost:3010. Shared with the other
# Ollama-calling modules via services.langfuse_shim (poindexter#485
# follow-up: previously this module was the only @observe'd entry
# point — see the shim docstring for the broader rollout context).
from services.langfuse_shim import langfuse_context, observe


def resolve_local_model(model: str | None = None, *, site_config: Any = None) -> str:
    """Pick the local model to call. Removes ``ollama/`` prefix and
    falls back through ``pipeline_writer_model`` → ``cost_tier.standard.model``.

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


@observe(as_type="generation", name="ollama_chat_text")
async def ollama_chat_text(
    prompt: str,
    *,
    model: str | None = None,
    timeout_setting: str = "atom_chat_timeout_seconds",
    timeout_default: float = 120.0,
    system: str | None = None,
    site_config: Any = None,
) -> str:
    """Plain-text Ollama chat call.

    Args:
        prompt: The user message body.
        model: Concrete Ollama model name. ``None`` resolves via
            :func:`resolve_local_model`.
        timeout_setting: ``app_settings`` key for the request timeout.
            Default key is generic; per-atom helpers can override
            (e.g. narrate_bundle uses ``niche_ollama_chat_timeout_seconds``).
        timeout_default: Fallback timeout when the setting is unset.
        system: Optional system prompt prepended as a system role
            message.
        site_config: SiteConfig DI seam (glad-labs-stack#330). When
            ``None``, falls through to ``localhost:11434`` and the
            default timeout — matches the behavior the singleton's
            empty-default-config produced before the lifespan shim.

    Returns:
        The raw assistant content (post-unwrap, see
        :func:`maybe_unwrap_json`). Empty string on missing content.
    """
    import httpx

    resolved_model = resolve_local_model(model, site_config=site_config)
    base_url = (
        (site_config.get("local_llm_api_url", "http://localhost:11434")
            if site_config is not None else "http://localhost:11434").rstrip("/")
    )
    timeout = (
        site_config.get_float(timeout_setting, timeout_default)
        if site_config is not None else timeout_default
    )
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": resolved_model,
        "messages": messages,
        "stream": False,
    }
    # Stamp model + input on the Langfuse generation span before the
    # call so the trace records latency + model even if Ollama errors.
    # update_current_observation is a no-op when Langfuse isn't wired.
    langfuse_context.update_current_observation(
        model=resolved_model,
        input=messages,
        metadata={"base_url": base_url, "timeout": timeout},
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
    raw = (data.get("message") or {}).get("content", "") or ""
    output = maybe_unwrap_json(raw)
    # Stamp output + ollama's prompt/completion token counts when present
    # so the trace surfaces tokens in the Langfuse UI (the JSON envelope
    # is the same shape Anthropic SDK Langfuse-integrations use).
    usage = {
        "input": data.get("prompt_eval_count"),
        "output": data.get("eval_count"),
    }
    langfuse_context.update_current_observation(output=output, usage=usage)
    return output


def maybe_unwrap_json(prose: str) -> str:
    """Defensive unwrap for models that wrap prose in JSON envelopes.

    Some local models emit ``{"thought": "<actual prose>"}`` even when
    the request didn't set ``format=json``. Walk common envelope keys
    and extract the inner string. If the input doesn't look like JSON
    or is JSON-without-prose-keys, return it unchanged.
    """
    import json

    s = (prose or "").strip()
    if not s or not (s.startswith("{") and s.endswith("}")):
        return prose
    try:
        envelope = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return prose
    if not isinstance(envelope, dict):
        return prose
    for key in ("thought", "content", "response", "text", "answer", "output"):
        v = envelope.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return prose


__all__ = [
    "maybe_unwrap_json",
    "ollama_chat_text",
    "resolve_local_model",
]
