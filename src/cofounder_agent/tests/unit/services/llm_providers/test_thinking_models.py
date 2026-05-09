"""Contract tests for ``services.llm_providers.thinking_models``.

Pins:
- ``is_thinking_model`` returns True for canonical thinking-model
  identifiers in any common form (bare name, ``ollama/`` prefix,
  uppercased) and False for everything else.
- ``resolve_thinking_substrings`` reads from ``site_config`` when
  available, falls back to the hardcoded list on missing key /
  malformed JSON / non-list / empty list / no site_config.
- The helpers compose: an operator who edits the JSON to add a new
  family gets immediate recognition without a code change.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.llm_providers.thinking_models import (
    _DEFAULT_SUBSTRINGS,
    is_thinking_model,
    resolve_thinking_substrings,
)


class _FakeSiteConfig:
    """Minimal SiteConfig stand-in — only the .get(key, default) seam matters."""

    def __init__(self, value: Any = None, raise_on_get: bool = False) -> None:
        self._value = value
        self._raise = raise_on_get

    def get(self, key: str, default: str = "") -> str:
        if self._raise:
            raise RuntimeError("site_config exploded")
        if self._value is None:
            return default
        return self._value


# ---------------------------------------------------------------------------
# is_thinking_model
# ---------------------------------------------------------------------------


class TestIsThinkingModel:
    @pytest.mark.parametrize(
        "model",
        [
            "qwen3:30b",
            "qwen3.5:35b",
            "ollama/qwen3:8b",
            "QWEN3-CODER:30B",  # case-insensitive
            "glm-4.7-5090:latest",
            "ollama/glm-4.7-5090",
            "deepseek-r1:7b",
        ],
    )
    def test_recognizes_canonical_thinking_models(self, model: str) -> None:
        assert is_thinking_model(model) is True

    @pytest.mark.parametrize(
        "model",
        [
            "gemma3:27b",
            "gemma3:27b-it-qat",
            "llama3:latest",
            "ollama/gemma3:27b",
            "anthropic/claude-haiku-4-5",
            "phi3:latest",
        ],
    )
    def test_returns_false_for_non_thinking_models(self, model: str) -> None:
        assert is_thinking_model(model) is False

    def test_empty_string_returns_false(self) -> None:
        assert is_thinking_model("") is False

    def test_substrings_override_takes_precedence(self) -> None:
        # Even a normally-thinking name is False if substrings exclude it.
        assert is_thinking_model("qwen3:30b", substrings=("gemma",)) is False
        # A normally-non-thinking name is True if substrings include it.
        assert is_thinking_model("gemma3:27b", substrings=("gemma",)) is True


# ---------------------------------------------------------------------------
# resolve_thinking_substrings
# ---------------------------------------------------------------------------


class TestResolveThinkingSubstrings:
    def test_none_site_config_falls_back_to_defaults(self) -> None:
        assert resolve_thinking_substrings(None) == _DEFAULT_SUBSTRINGS

    def test_empty_value_falls_back_to_defaults(self) -> None:
        assert resolve_thinking_substrings(_FakeSiteConfig(value="")) == _DEFAULT_SUBSTRINGS

    def test_malformed_json_falls_back_to_defaults(self) -> None:
        assert resolve_thinking_substrings(_FakeSiteConfig(value="{not json")) == _DEFAULT_SUBSTRINGS

    def test_non_list_json_falls_back_to_defaults(self) -> None:
        assert resolve_thinking_substrings(_FakeSiteConfig(value='{"foo": "bar"}')) == _DEFAULT_SUBSTRINGS

    def test_empty_list_falls_back_to_defaults(self) -> None:
        assert resolve_thinking_substrings(_FakeSiteConfig(value="[]")) == _DEFAULT_SUBSTRINGS

    def test_site_config_get_raising_falls_back_to_defaults(self) -> None:
        assert resolve_thinking_substrings(_FakeSiteConfig(raise_on_get=True)) == _DEFAULT_SUBSTRINGS

    def test_valid_json_list_is_lowercased_and_returned(self) -> None:
        sc = _FakeSiteConfig(value='["FOO","Bar","baz"]')
        assert resolve_thinking_substrings(sc) == ("foo", "bar", "baz")

    def test_operator_can_add_new_family(self) -> None:
        # The point of the registry: an operator drops in a new family
        # and the helpers immediately recognize it.
        sc = _FakeSiteConfig(value='["qwen3","glm-4","deepseek-r1","newmodel-r1"]')
        substrings = resolve_thinking_substrings(sc)
        assert is_thinking_model("ollama/newmodel-r1:7b", substrings=substrings) is True

    def test_operator_can_remove_family(self) -> None:
        # Edit the JSON to drop "deepseek-r1" and the helper stops
        # recognizing it without a code deploy.
        sc = _FakeSiteConfig(value='["qwen3","glm-4"]')
        substrings = resolve_thinking_substrings(sc)
        assert is_thinking_model("deepseek-r1:7b", substrings=substrings) is False
