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


def resolve_local_model(model: str | None = None, *, site_config: Any = None) -> str:
    """Pick the local model to call. Removes ``ollama/`` prefix and
    falls back through ``pipeline_writer_model`` → hard default.

    Accepts the SiteConfig instance via the DI seam (glad-labs-stack#330).
    Falls through to the hard default when ``site_config`` is None.
    """
    if model:
        return model.removeprefix("ollama/")
    if site_config is None:
        return "glm-4.7-5090:latest"
    return (
        site_config.get("pipeline_writer_model", "glm-4.7-5090:latest")
        or "glm-4.7-5090:latest"
    ).removeprefix("ollama/")


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
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
    raw = (data.get("message") or {}).get("content", "") or ""
    return maybe_unwrap_json(raw)


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
