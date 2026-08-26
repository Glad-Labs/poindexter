"""Tests for ``services.llm_providers.ollama_timings`` — the seam that
recovers Ollama's decode/prefill durations through LiteLLM.

Two layers: pure-unit coverage of the stash/extract helpers on fakes, and a
**pin against the installed litellm** — the wrapper monkeypatches
``transform_response`` on litellm's two Ollama config classes, so an upgrade
that renames/moves them must fail HERE loudly instead of the capture silently
going dark (fail-open would leave the cost_logs columns NULL forever).
"""

from __future__ import annotations

import inspect

# Module-scope on purpose (sibling idiom — test_litellm_aiohttp_transport.py):
# litellm lazily fetches its remote model-cost map on first import, and at
# module scope that lands during collection, outside the per-test egress
# guard window.
from litellm.llms.ollama.chat.transformation import OllamaChatConfig
from litellm.llms.ollama.completion.transformation import OllamaConfig

from services.llm_providers.ollama_timings import (
    HIDDEN_PARAMS_KEY,
    _stash_timings,
    _wrap,
    extract_timings,
    install_ollama_timing_capture,
)


class _FakeRawResponse:
    def __init__(self, body=None, raises=False):
        self._body = body or {}
        self._raises = raises

    def json(self):
        if self._raises:
            raise ValueError("not json")
        return self._body


class _FakeModelResponse:
    def __init__(self):
        self._hidden_params = {}


def test_stash_records_split_in_ms():
    mr = _FakeModelResponse()
    _stash_timings(
        mr,
        _FakeRawResponse({"eval_duration": 36_658_000, "prompt_eval_duration": 40_227_000}),
    )
    t = mr._hidden_params[HIDDEN_PARAMS_KEY]
    assert t == {"decode_ms": 36.658, "prefill_ms": 40.227}


def test_stash_skips_bodies_without_eval_duration():
    mr = _FakeModelResponse()
    _stash_timings(mr, _FakeRawResponse({"message": {"content": "hi"}}))
    assert HIDDEN_PARAMS_KEY not in mr._hidden_params


def test_stash_swallows_parse_failure():
    mr = _FakeModelResponse()
    _stash_timings(mr, _FakeRawResponse(raises=True))  # must not raise
    assert mr._hidden_params == {}


def test_wrap_preserves_return_and_stashes():
    class FakeConfig:
        def transform_response(self, model, raw_response, model_response, *a, **kw):
            return model_response

    _wrap(FakeConfig)
    mr = _FakeModelResponse()
    out = FakeConfig().transform_response(
        "m", _FakeRawResponse({"eval_duration": 2_000_000}), mr
    )
    assert out is mr
    assert out._hidden_params[HIDDEN_PARAMS_KEY]["decode_ms"] == 2.0
    assert getattr(FakeConfig.transform_response, "__ollama_timings_wrapped__", False)


def test_extract_roundtrip_and_null_paths():
    mr = _FakeModelResponse()
    assert extract_timings(mr) == (None, None)  # nothing stashed
    assert extract_timings(object()) == (None, None)  # no _hidden_params at all
    mr._hidden_params[HIDDEN_PARAMS_KEY] = {"decode_ms": 123.9, "prefill_ms": 7.2}
    assert extract_timings(mr) == (123, 7)


def test_install_pins_installed_litellm_and_is_idempotent():
    """The seam depends on these two litellm classes keeping this method
    shape. If a litellm upgrade moves them, THIS must fail — the runtime
    wrapper is deliberately fail-open and would just stop capturing."""
    assert install_ollama_timing_capture() is True
    for cls in (OllamaChatConfig, OllamaConfig):
        fn = cls.transform_response
        assert getattr(fn, "__ollama_timings_wrapped__", False), cls.__name__
        params = list(inspect.signature(fn).parameters)
        assert "raw_response" in params and "model_response" in params, cls.__name__
    # second install must not double-wrap
    first = OllamaChatConfig.transform_response
    assert install_ollama_timing_capture() is True
    assert OllamaChatConfig.transform_response is first
