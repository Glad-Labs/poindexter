"""Multi-host Ollama discovery for VRAM reclaim (poindexter#992).

A single ``ollama_base_url`` was not the whole picture: per-model routing pins
a model to its own instance via the LiteLLM plugin's
``model_api_base_overrides`` (the operator box runs a second GPU-pinned Ollama
for qwen3-vl). The reclaim cleared only the primary, so the ~20 GB vision
model — loaded by the media pipeline's OWN vision QA immediately before the
hero render — stayed resident and wan OOM'd regardless.
"""

from __future__ import annotations

import json

import pytest

from services.llm_providers.ollama_unload import ollama_base_urls
from services.site_config import SiteConfig


def _sc(**over):
    base = {"ollama_base_url": "http://host.docker.internal:11434"}
    base.update(over)
    return SiteConfig(initial_config=base)


@pytest.mark.unit
def test_primary_only_when_no_overrides():
    """One instance must behave exactly as before — a single-element list."""
    assert ollama_base_urls(_sc()) == ["http://host.docker.internal:11434"]


@pytest.mark.unit
def test_pinned_vision_host_is_discovered():
    cfg = json.dumps({
        "config": {
            "api_base": "http://host.docker.internal:11434",
            "model_api_base_overrides": {
                "ollama/qwen3-vl:30b": "http://host.docker.internal:11435",
                "ollama_chat/qwen3-vl:30b": "http://host.docker.internal:11435",
            },
        },
    })
    urls = ollama_base_urls(_sc(**{"plugin.llm_provider.litellm": cfg}))
    assert urls[0] == "http://host.docker.internal:11434"   # primary first
    assert "http://host.docker.internal:11435" in urls
    assert len(urls) == 2                                    # deduped


@pytest.mark.unit
def test_openai_style_endpoints_are_not_probed():
    """An override may point at a cloud endpoint; it has no /api/ps and must
    not be swept."""
    cfg = json.dumps({
        "config": {
            "model_api_base_overrides": {
                "gpt-x": "https://api.example.com/v1",
            },
        },
    })
    assert ollama_base_urls(_sc(**{"plugin.llm_provider.litellm": cfg})) == [
        "http://host.docker.internal:11434",
    ]


@pytest.mark.unit
def test_malformed_plugin_config_falls_back_to_primary():
    """A config read must never break the reclaim path."""
    urls = ollama_base_urls(_sc(**{"plugin.llm_provider.litellm": "{not json"}))
    assert urls == ["http://host.docker.internal:11434"]
