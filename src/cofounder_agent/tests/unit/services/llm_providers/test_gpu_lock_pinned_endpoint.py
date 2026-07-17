"""A GPU-pinned model must not serialize on the shared GPU lock.

``model_api_base_overrides`` (glad-labs-stack#2051) routes an eviction-prone
model to a SECOND Ollama instance pinned to its own GPU — qwen3-vl on the 3090
while the writer / image-gen / wan own the pipeline GPU. #2051 fixed placement
but left the lock global, so every vision call still took
``gpu.lock("ollama")`` and queued behind 5090 work.

That lock buys a pinned instance nothing:

- ``_unload_ollama_models`` only evicts ``ollama_base_url`` — instance 2 is
  already exempt;
- image-gen / wan render on ``pipeline_gpu_index`` only, so they never touch
  the pinned card's VRAM.

It only costs: vision calls blew ``gpu_lock_acquire_timeout_seconds`` (900s)
and ``qa.vision`` fail-softed to "unavailable, passing open" on 13-25% of
calls (measured over 14 days of ``cost_logs``: ``qa_shot_vision`` 75.3% ok,
``caption_image`` 84.2%, ``qa_vision_image_relevance`` 86.5%,
``media_qa_topic_match`` 50.0% — every one capped at max_s=900.0).

Contract pinned here: a dispatch whose EFFECTIVE api_base differs from the
install's default endpoint skips the shared lock; everything else is
unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.llm_providers.dispatcher import _gpu_serialize_local_dispatch
from services.llm_providers.litellm_provider import pinned_api_base_for
from services.site_config import SiteConfig

_DEFAULT_BASE = "http://host.docker.internal:11434"
_VISION_BASE = "http://host.docker.internal:11435"


def _config(overrides=None, api_base=_DEFAULT_BASE):
    cfg = {"api_base": api_base, "allow_paid_base_url": "false"}
    if overrides is not None:
        cfg["model_api_base_overrides"] = overrides
    return cfg


def _container(**settings):
    return SimpleNamespace(site_config=SiteConfig(initial_config=settings))


# ---------------------------------------------------------------------------
# pinned_api_base_for — the routing twin of LiteLLMProvider._api_base_for
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pinned_api_base_for_matches_resolved_model():
    assert pinned_api_base_for(
        "ollama/qwen3-vl:30b", _config({"ollama/qwen3-vl:30b": _VISION_BASE}),
    ) == _VISION_BASE


@pytest.mark.unit
def test_pinned_api_base_for_resolves_bare_model_name():
    # Callers pass bare names; the map is keyed on the RESOLVED name. This
    # must match the provider's own _resolve_model or the lock decision
    # drifts from where the call actually lands.
    assert pinned_api_base_for(
        "qwen3-vl:30b", _config({"ollama/qwen3-vl:30b": _VISION_BASE}),
    ) == _VISION_BASE


@pytest.mark.unit
def test_pinned_api_base_for_returns_none_without_override():
    assert pinned_api_base_for(
        "ollama/gemma3:27b", _config({"ollama/qwen3-vl:30b": _VISION_BASE}),
    ) is None


@pytest.mark.unit
def test_pinned_api_base_for_returns_none_for_empty_map():
    assert pinned_api_base_for("ollama/qwen3-vl:30b", _config()) is None


@pytest.mark.unit
def test_override_to_the_default_base_is_not_pinned():
    # An override that points back at the default endpoint is the SAME
    # server — it shares the default GPU and must keep serializing.
    assert pinned_api_base_for(
        "ollama/qwen3-vl:30b", _config({"ollama/qwen3-vl:30b": _DEFAULT_BASE}),
    ) is None


@pytest.mark.unit
def test_pinned_api_base_for_tolerates_garbage_map():
    # A typo'd override row degrades to "no override" in the provider; the
    # lock decision must degrade identically, never raise on the hot path.
    assert pinned_api_base_for("ollama/qwen3-vl:30b", _config("{not json")) is None


# ---------------------------------------------------------------------------
# _gpu_serialize_local_dispatch — the lock decision
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pinned_model_skips_the_shared_gpu_lock():
    with patch(
        "services.container_registry.get_container", return_value=_container(),
    ):
        assert _gpu_serialize_local_dispatch(
            "ollama/qwen3-vl:30b",
            _config({"ollama/qwen3-vl:30b": _VISION_BASE}),
        ) is False


@pytest.mark.unit
def test_default_endpoint_model_still_serializes():
    with patch(
        "services.container_registry.get_container", return_value=_container(),
    ):
        assert _gpu_serialize_local_dispatch(
            "ollama/gemma-4-31B-it-qat:latest",
            _config({"ollama/qwen3-vl:30b": _VISION_BASE}),
        ) is True


@pytest.mark.unit
def test_bare_pinned_model_name_skips_the_lock():
    with patch(
        "services.container_registry.get_container", return_value=_container(),
    ):
        assert _gpu_serialize_local_dispatch(
            "qwen3-vl:30b", _config({"ollama/qwen3-vl:30b": _VISION_BASE}),
        ) is False


@pytest.mark.unit
def test_kill_switch_forces_pinned_model_to_serialize():
    # Escape hatch for an operator whose second instance shares ONE card
    # (two Ollama servers on the same GPU DO contend) — they set this false.
    with patch(
        "services.container_registry.get_container",
        return_value=_container(gpu_pinned_endpoint_skips_lock="false"),
    ):
        assert _gpu_serialize_local_dispatch(
            "ollama/qwen3-vl:30b",
            _config({"ollama/qwen3-vl:30b": _VISION_BASE}),
        ) is True


@pytest.mark.unit
def test_pinned_model_skips_even_without_a_container():
    # CLI early paths / tests bootstrap no container. Serializing is only
    # "the safe default" for models that actually share the default GPU;
    # a pinned model never does.
    with patch("services.container_registry.get_container", return_value=None):
        assert _gpu_serialize_local_dispatch(
            "ollama/qwen3-vl:30b",
            _config({"ollama/qwen3-vl:30b": _VISION_BASE}),
        ) is False


@pytest.mark.unit
def test_unpinned_model_still_serializes_without_a_container():
    with patch("services.container_registry.get_container", return_value=None):
        assert _gpu_serialize_local_dispatch(
            "ollama/gemma3:27b", _config(),
        ) is True


@pytest.mark.unit
def test_global_serialize_opt_out_still_wins():
    with patch(
        "services.container_registry.get_container",
        return_value=_container(gpu_serialize_llm_dispatch="false"),
    ):
        assert _gpu_serialize_local_dispatch("ollama/gemma3:27b", _config()) is False


@pytest.mark.unit
def test_paid_call_never_serializes():
    # Pre-existing contract — a cloud call uses no local GPU.
    with patch(
        "services.container_registry.get_container", return_value=_container(),
    ):
        assert _gpu_serialize_local_dispatch(
            "anthropic/claude-sonnet-5", _config(),
        ) is False
