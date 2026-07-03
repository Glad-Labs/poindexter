"""Tests for ``resolve_ollama_api_base`` — per-model api_base override
resolution for DIRECT (non-LiteLLM) Ollama callers.

The vision-QA paths POST to ``/api/chat`` over raw httpx (they need the
native ``images`` payload), so they can't pick the
``model_api_base_overrides`` map up inside ``LiteLLMProvider``. This
helper gives them the same semantics off the same
``plugin.llm_provider.litellm`` app_settings row — one operator row
routes a model for both dispatch paths (#2075).
"""
from __future__ import annotations

import json

from services.llm_providers.api_base_overrides import resolve_ollama_api_base
from services.site_config import SiteConfig

_ROW_KEY = "plugin.llm_provider.litellm"
_DEFAULT = "http://host.docker.internal:11434"
_PINNED = "http://host.docker.internal:11435"


def _sc(overrides: dict | None = None, *, row: str | None = None) -> SiteConfig:
    """SiteConfig with the litellm plugin row carrying the override map.

    ``row`` overrides the whole row value (for malformed-row tests)."""
    cfg: dict[str, str] = {}
    if row is not None:
        cfg[_ROW_KEY] = row
    elif overrides is not None:
        cfg[_ROW_KEY] = json.dumps({
            "enabled": True,
            "config": {
                "api_base": _DEFAULT,
                "model_api_base_overrides": overrides,
            },
        })
    return SiteConfig(initial_config=cfg)


def test_no_site_config_returns_default():
    assert resolve_ollama_api_base(
        "qwen3-vl:30b", site_config=None, default=_DEFAULT,
    ) == _DEFAULT


def test_empty_model_returns_default():
    assert resolve_ollama_api_base(
        "", site_config=_sc({"ollama/x": "http://y"}), default=_DEFAULT,
    ) == _DEFAULT


def test_missing_plugin_row_returns_default():
    assert resolve_ollama_api_base(
        "qwen3-vl:30b", site_config=_sc(), default=_DEFAULT,
    ) == _DEFAULT


def test_prefixed_map_key_matches_bare_model():
    sc = _sc({"ollama/qwen3-vl:30b": _PINNED})
    assert resolve_ollama_api_base(
        "qwen3-vl:30b", site_config=sc, default=_DEFAULT,
    ) == _PINNED


def test_prefixed_map_key_matches_prefixed_model():
    sc = _sc({"ollama/qwen3-vl:30b": _PINNED})
    assert resolve_ollama_api_base(
        "ollama/qwen3-vl:30b", site_config=sc, default=_DEFAULT,
    ) == _PINNED


def test_bare_map_key_matches_prefixed_model():
    sc = _sc({"qwen3-vl:30b": _PINNED})
    assert resolve_ollama_api_base(
        "ollama/qwen3-vl:30b", site_config=sc, default=_DEFAULT,
    ) == _PINNED


def test_unrelated_model_returns_default():
    sc = _sc({"ollama/qwen3-vl:30b": _PINNED})
    assert resolve_ollama_api_base(
        "gemma3:27b", site_config=sc, default=_DEFAULT,
    ) == _DEFAULT


def test_row_without_config_block_returns_default():
    sc = _sc(row='{"enabled": true}')
    assert resolve_ollama_api_base(
        "qwen3-vl:30b", site_config=sc, default=_DEFAULT,
    ) == _DEFAULT


def test_row_without_override_map_returns_default():
    sc = _sc(row='{"enabled": true, "config": {"api_base": "http://x"}}')
    assert resolve_ollama_api_base(
        "qwen3-vl:30b", site_config=sc, default=_DEFAULT,
    ) == _DEFAULT


def test_invalid_row_json_degrades_to_default():
    sc = _sc(row="{not json")
    assert resolve_ollama_api_base(
        "qwen3-vl:30b", site_config=sc, default=_DEFAULT,
    ) == _DEFAULT


def test_prod_shaped_row_routes_to_pinned_instance():
    """The literal prod row shape (2026-07-02 operator config) resolves."""
    sc = _sc(row=json.dumps({
        "enabled": True,
        "interval_seconds": 0,
        "config": {
            "api_base": "http://host.docker.internal:11434",
            "timeout_seconds": 300,
            "drop_params": True,
            "model_api_base_overrides": {
                "ollama/qwen3-vl:30b": "http://host.docker.internal:11435",
            },
        },
    }))
    assert resolve_ollama_api_base(
        "qwen3-vl:30b", site_config=sc, default=_DEFAULT,
    ) == _PINNED
