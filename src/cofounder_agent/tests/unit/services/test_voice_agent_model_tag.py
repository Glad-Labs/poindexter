"""Unit tests for voice_agent._normalize_ollama_tag.

The voice agent hands ``voice_agent_llm_model`` straight to Pipecat's
``OLLamaLLMService``, which passes it verbatim to Ollama's OpenAI-compatible
endpoint. That endpoint 404s on the LiteLLM-style ``ollama/`` prefix which
almost every OTHER ``*_model`` setting requires — so the same string is
correct in one setting and fatal in another.

Prod carried exactly that state (``ollama/gemma-4-E2B-Q2:latest``): the model
was also uninstalled, which masked the fact that the FORMAT was wrong too.
The startup validator can't catch it either — it strips the prefix before
checking, so a prefixed value looks perfectly installed.

Reuses the heavy-pipecat stub harness so ``import services.voice_agent``
resolves without pipecat installed (it lives in the voice image, not the
unit-test env).
"""

from __future__ import annotations

import pytest

from tests.unit.services.test_voice_agent_service_mode import (
    _ensure_pipecat_stubs,
)


def _normalize():
    _ensure_pipecat_stubs()
    from services.voice_agent import _normalize_ollama_tag
    return _normalize_ollama_tag


class _RecordingLog:
    def __init__(self) -> None:
        self.warnings: list[tuple] = []

    def warning(self, *args) -> None:
        self.warnings.append(args)


@pytest.mark.unit
class TestNormalizeOllamaTag:
    def test_bare_tag_is_unchanged(self):
        assert _normalize()("qwen2.5:7b") == "qwen2.5:7b"

    def test_litellm_prefix_is_stripped(self):
        """The whole point: Ollama's own API 404s on 'ollama/qwen2.5:7b'."""
        assert _normalize()("ollama/qwen2.5:7b") == "qwen2.5:7b"

    def test_prefix_strip_is_case_insensitive(self):
        assert _normalize()("Ollama/qwen2.5:7b") == "qwen2.5:7b"

    def test_whitespace_is_trimmed(self):
        assert _normalize()("  qwen2.5:7b  ") == "qwen2.5:7b"

    def test_empty_stays_empty_for_the_fail_loud_check(self):
        """Must return falsy so the caller's 'unset' ValueError still fires —
        normalizing must not manufacture a value out of nothing."""
        assert _normalize()("") == ""
        assert _normalize()("   ") == ""
        assert _normalize()(None) == ""

    def test_stripping_warns_so_the_misconfig_is_visible(self):
        """Silently accepting the wrong format would hide a real config error
        that breaks every other consumer reading the same key."""
        log = _RecordingLog()
        assert _normalize()("ollama/qwen2.5:7b", log=log) == "qwen2.5:7b"
        assert len(log.warnings) == 1
        assert "ollama/qwen2.5:7b" in repr(log.warnings[0])

    def test_bare_tag_does_not_warn(self):
        log = _RecordingLog()
        _normalize()("qwen2.5:7b", log=log)
        assert log.warnings == []

    def test_a_model_named_like_the_prefix_is_not_mangled(self):
        """Only a leading 'ollama/' path segment is a provider prefix. A tag
        that merely CONTAINS the word must survive intact."""
        n = _normalize()
        assert n("my-ollama-tune:latest") == "my-ollama-tune:latest"
        assert n("hf.co/someone/ollama-ft:Q4") == "hf.co/someone/ollama-ft:Q4"

    def test_hf_style_tags_survive(self):
        """Ollama accepts hf.co/... tags directly; they carry slashes but are
        not provider prefixes."""
        tag = "hf.co/unsloth/GLM-4.7-Flash-GGUF:UD-Q5_K_XL"
        assert _normalize()(tag) == tag
