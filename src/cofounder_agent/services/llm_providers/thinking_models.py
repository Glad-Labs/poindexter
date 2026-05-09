"""Thinking-model detection.

Classifies whether a given model identifier is a "thinking" model — one
that produces ``<think>...</think>`` reasoning blocks before its final
answer. Thinking models need different handling than chat-completion
models:

- Higher max_tokens budget (the reasoning trace eats budget before the
  final output begins).
- The ``/nothink`` directive (qwen3 family) to suppress reasoning when
  the caller wants direct JSON output.
- Output stripping (``<think>...</think>`` blocks must be removed
  before parsing).

Pre-2026-05-09, four call sites each carried their own hardcoded list
of thinking-model substrings (``"qwen3"`` / ``"glm-4"`` /
``"deepseek-r1"``), and the lists drifted. ``ai_content_generator``
included ``"deepseek-r1"`` while ``image_decision_agent`` did not;
``multi_model_qa`` used ``"qwen3.5"`` and ``"qwen3:30b"`` but
``ai_content_generator`` only matched on the bare ``"qwen3"`` prefix.

This module is the one place that decides. Substrings live in
``app_settings.thinking_model_substrings`` so operators can add a new
thinking model without a code change.
"""

from __future__ import annotations

import json
from typing import Any

# Hardcoded fallback used only when ``app_settings`` is unreachable
# (test paths, fresh installs before the seed migration runs). Mirrors
# the union of the historical inline lists pre-2026-05-09 so existing
# behavior is preserved when the DB is silent.
_DEFAULT_SUBSTRINGS: tuple[str, ...] = (
    "qwen3",
    "qwen3.5",
    "glm-4",
    "glm-4.7",
    "deepseek-r1",
)


def is_thinking_model(model: str, *, substrings: tuple[str, ...] | list[str] | None = None) -> bool:
    """Return True if ``model`` looks like a thinking-model identifier.

    Args:
        model: The model identifier string. May be a bare name
            (``"glm-4.7-5090:latest"``), a LiteLLM-prefixed identifier
            (``"ollama/qwen3:30b"``), or any operator-supplied form.
            Comparison is case-insensitive.
        substrings: Override list. If ``None``, the caller is
            responsible for resolving the configured list and passing
            it in (typical pattern: read once via
            ``resolve_thinking_substrings(site_config)`` at the start
            of a hot path, then reuse). If unset and not provided, the
            module falls back to ``_DEFAULT_SUBSTRINGS``.

    Returns:
        True when any configured substring is found inside the
        lowercased model name; False otherwise.
    """
    if not model:
        return False
    needle = model.lower()
    pool = substrings if substrings is not None else _DEFAULT_SUBSTRINGS
    return any(s in needle for s in pool)


def resolve_thinking_substrings(site_config: Any) -> tuple[str, ...]:
    """Resolve the configured thinking-model substring list.

    Reads ``app_settings.thinking_model_substrings`` (a JSON array of
    strings). Falls back to ``_DEFAULT_SUBSTRINGS`` on missing key,
    empty value, or parse failure. Never raises — this helper is
    on every LLM-call hot path.

    Args:
        site_config: A ``SiteConfig`` instance (DI-provided). May also
            be ``None`` for legacy test paths; falls through to defaults.

    Returns:
        Tuple of substring needles, lowercase. Always non-empty.
    """
    if site_config is None:
        return _DEFAULT_SUBSTRINGS
    try:
        raw = site_config.get("thinking_model_substrings", "")
    except Exception:
        return _DEFAULT_SUBSTRINGS
    if not raw:
        return _DEFAULT_SUBSTRINGS
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return _DEFAULT_SUBSTRINGS
    if not isinstance(parsed, list) or not parsed:
        return _DEFAULT_SUBSTRINGS
    return tuple(str(s).lower() for s in parsed if s)
