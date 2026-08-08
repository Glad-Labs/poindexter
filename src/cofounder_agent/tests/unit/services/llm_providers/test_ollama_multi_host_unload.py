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


# ---------------------------------------------------------------------------
# Never-unload pins are honoured (poindexter#997)
#
# The multi-host sweep (#992) was right that per-model routing hides a second
# instance — and wrong to evict it unconditionally. The operator box pins
# qwen3-vl to :11435 by UUID on GPU 1 with OLLAMA_KEEP_ALIVE=-1 precisely so
# vision QA is never evicted. Sweeping it frees NOTHING on the render GPU
# (measured: loading it puts 22730 MiB on GPU 1 and moves GPU 0 not at all)
# while costing an ~18-20 GB reload across a x4 slot.
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone

from services.llm_providers.ollama_unload import _is_permanently_pinned


def _iso(dt):
    return dt.isoformat()


@pytest.mark.unit
class TestPermanentPinDetection:

    def test_ollama_keep_alive_minus_one_shape_is_a_pin(self):
        """The literal shape Ollama emits for keep_alive=-1, nanoseconds and
        all (datetime accepts at most 6 fractional digits, Ollama sends 7-9)."""
        entry = {"name": "qwen3-vl:30b",
                 "expires_at": "2318-11-17T22:26:46.3314071-05:00"}
        assert _is_permanently_pinned(entry, 365.0) is True

    def test_ordinary_keep_alive_is_not_a_pin(self):
        soon = datetime.now(timezone.utc) + timedelta(minutes=5)
        assert _is_permanently_pinned({"expires_at": _iso(soon)}, 365.0) is False

    def test_just_inside_the_horizon_is_not_a_pin(self):
        near = datetime.now(timezone.utc) + timedelta(days=364)
        assert _is_permanently_pinned({"expires_at": _iso(near)}, 365.0) is False

    def test_just_past_the_horizon_is_a_pin(self):
        far = datetime.now(timezone.utc) + timedelta(days=366)
        assert _is_permanently_pinned({"expires_at": _iso(far)}, 365.0) is True

    def test_naive_timestamp_is_treated_as_utc_not_crashed_on(self):
        far = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=400)
        assert _is_permanently_pinned({"expires_at": _iso(far)}, 365.0) is True

    @pytest.mark.parametrize("entry", [
        {}, {"expires_at": ""}, {"expires_at": "not-a-date"},
        {"expires_at": None}, None,
    ])
    def test_unparseable_fails_toward_unloading(self, entry):
        """Preserves the pre-#997 sweep exactly. A reclaim that silently stops
        working is far worse than one that evicts a model it needn't have."""
        assert _is_permanently_pinned(entry, 365.0) is False
