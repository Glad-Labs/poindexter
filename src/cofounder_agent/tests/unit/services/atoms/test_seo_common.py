"""Tests for services/atoms/_seo_common.py — the shared SEO atom helper."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.atoms import _seo_common as sc

# No module-level asyncio mark: ``asyncio_mode = "auto"`` (pyproject.toml)
# already auto-marks coroutine tests. An explicit mark wrongly tagged the
# sync tests here, emitting a PytestWarning (Glad-Labs/glad-labs-stack#997).


def _state(**over):
    s = {
        "content": "We shipped a regex fix for the validator. It fired 8x per post.",
        "topic": "regex validator bug",
        "title": "The Regex Validator Bug",
        "site_config": MagicMock(),
        "database_service": None,
    }
    s.update(over)
    return s


def test_clean_oneline_strips_quotes_and_collapses():
    assert sc.clean_oneline('  "Hello   world\n"  ') == "Hello world"


def test_clamp_words_truncates_at_word_boundary():
    out = sc.clamp_words("alpha beta gamma delta", 12)
    assert out == "alpha beta"
    assert len(out) <= 12


def test_clamp_words_passes_short_text():
    assert sc.clamp_words("short", 160) == "short"


def test_fallback_title_uses_canonical():
    assert sc.fallback_title(_state()).startswith("The Regex")


def test_fallback_description_uses_first_paragraph():
    out = sc.fallback_description(_state(content="First para text.\n\n# Heading\n\nSecond."))
    assert out.startswith("First para")
    assert len(out) <= 160


def test_fallback_keywords_delegates(monkeypatch):
    monkeypatch.setattr(sc, "extract_keywords_from_text", lambda t, count=5: ["regex", "validator"])
    assert sc.fallback_keywords(_state()) == ["regex", "validator"]


async def test_run_seo_llm_returns_text(monkeypatch):
    monkeypatch.setattr(sc, "ollama_chat_text", AsyncMock(return_value="  Best Title  "))
    monkeypatch.setattr(sc, "get_prompt_manager", lambda: MagicMock(get_prompt=lambda key, **k: "PROMPT"))
    out = await sc.run_seo_llm(_state(), "atoms.seo.generate_title", topic="x")
    assert out == "Best Title"


async def test_run_seo_llm_retries_then_raises(monkeypatch):
    call = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(sc, "ollama_chat_text", call)
    monkeypatch.setattr(sc, "get_prompt_manager", lambda: MagicMock(get_prompt=lambda key, **k: "P"))
    monkeypatch.setattr(sc.asyncio, "sleep", AsyncMock())  # no real backoff
    with pytest.raises(RuntimeError):
        await sc.run_seo_llm(_state(), "atoms.seo.generate_title", max_attempts=2)
    assert call.await_count == 2


def test_degraded_logs_warning(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        sc.degraded("title", RuntimeError("x"))
    assert "degraded" in caplog.text.lower()
