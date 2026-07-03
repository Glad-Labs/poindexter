"""Per-model api_base override resolution for DIRECT Ollama callers.

The ``plugin.llm_provider.litellm`` app_settings row carries
``config.model_api_base_overrides`` — a map of resolved model name
(``ollama/qwen3-vl:30b``) to the endpoint that should serve it, shipped in
PR #2074 so eviction-prone models can be pinned to the second,
GPU-1-pinned Ollama instance (glad-labs-stack#2051 / #2075).

LiteLLM-dispatched calls pick the override up inside ``LiteLLMProvider``
(``PluginConfig.load`` hands the row's ``config`` dict to
``_configure_from`` → ``_api_base_for``). But the vision-QA paths —
``multi_model_qa._check_image_relevance`` / ``_check_rendered_preview``
and ``shot_vision_qa.score_shot_frame`` — POST to Ollama's ``/api/chat``
directly over httpx (they need the native ``images`` payload), so they
bypassed the map entirely and kept calling the primary instance, where
the vision model gets evicted by writer reloads. This helper gives those
direct callers the SAME override semantics, reading the SAME app_settings
row off the SiteConfig cache, so one operator row routes a model for both
dispatch paths.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from services.llm_providers.litellm_provider import _coerce_override_map

logger = logging.getLogger(__name__)

_PLUGIN_ROW_KEY = "plugin.llm_provider.litellm"


def _load_override_map(site_config: Any) -> dict[str, str]:
    """Extract ``config.model_api_base_overrides`` from the litellm plugin row.

    The row value is a JSON object (``{"enabled": ..., "config": {...}}``);
    SiteConfig caches it as raw TEXT, so parse defensively. Any miss —
    row absent, unparseable, no config block — degrades to ``{}``.
    """
    raw = site_config.get(_PLUGIN_ROW_KEY, "")
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "[api_base_overrides] %s row is not valid JSON — "
                "no per-model overrides applied", _PLUGIN_ROW_KEY,
            )
            return {}
    if not isinstance(raw, dict):
        return {}
    cfg = raw.get("config")
    if not isinstance(cfg, dict):
        return {}
    return _coerce_override_map(cfg.get("model_api_base_overrides"))


def resolve_ollama_api_base(model: str, *, site_config: Any, default: str) -> str:
    """Effective Ollama base URL for one DIRECT (non-LiteLLM) model call.

    ``model`` may be bare (``qwen3-vl:30b``) or prefixed
    (``ollama/qwen3-vl:30b``); the override map is keyed on the resolved
    form (matching the per-step ``*_model`` pins), so both spellings are
    checked. Returns ``default`` when no override row matches, the map is
    missing/unparseable, or ``site_config`` is absent — an override typo
    must degrade to "primary instance serves it", never break the call.
    """
    if site_config is None or not model:
        return default
    try:
        overrides = _load_override_map(site_config)
    except Exception as exc:  # noqa: BLE001 — override lookup must never break the call
        logger.warning(
            "[api_base_overrides] override-map read failed (%s) — using %s",
            exc, default,
        )
        return default
    if not overrides:
        return default
    bare = model.removeprefix("ollama/")
    return overrides.get(f"ollama/{bare}") or overrides.get(bare) or default


__all__ = ["resolve_ollama_api_base"]
