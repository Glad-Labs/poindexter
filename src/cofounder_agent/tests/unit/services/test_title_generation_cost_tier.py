"""Cost-tier resolution path for ``services.title_generation.generate_canonical_title``.

Lane B sweep #2 (Writer / content surface). Pins the new
``resolve_tier_model(pool, "standard")`` integration: when the cost-tier
mapping is configured, the resolved model flows through to the provider
call. When it isn't, the per-call-site ``pipeline_writer_model`` setting
is the last-ditch fallback. When BOTH miss, ``notify_operator()`` fires
and the function returns None — no silent literal default.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.title_generation import (
    generate_canonical_title,
    sanitize_generated_title,
)


class _FakeConn:
    def __init__(self, value: str | None):
        self._value = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def fetchval(self, query: str, *args: Any) -> str | None:
        return self._value


class _FakePool:
    def __init__(self, value: str | None):
        self._value = value

    def acquire(self):
        return _FakeConn(self._value)


def _make_provider(captured: dict[str, Any]) -> MagicMock:
    """Return a mock ollama_native provider that captures the model arg."""
    provider = MagicMock()
    provider.name = "ollama_native"

    async def _complete(**kwargs):
        captured["model"] = kwargs.get("model")
        captured["called"] = True
        result = MagicMock()
        result.text = "A Crisp SEO-Optimized Title"
        return result

    provider.complete = AsyncMock(side_effect=_complete)
    return provider


@pytest.mark.asyncio
async def test_uses_cost_tier_standard_when_mapping_present():
    """Tier mapping is the primary path; ``ollama/`` prefix is stripped."""
    captured: dict[str, Any] = {}
    provider = _make_provider(captured)

    fake_sc = MagicMock()
    fake_sc._pool = _FakePool("ollama/gemma3:27b")
    fake_sc.get.return_value = ""  # pipeline_writer_model unused on the happy path
    fake_sc.get_int.return_value = 4000

    with patch("services.title_generation.site_config", fake_sc), \
         patch(
             "plugins.registry.get_all_llm_providers",
             return_value=[provider],
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt.return_value = "PROMPT"
        out = await generate_canonical_title(
            topic="AI trends",
            primary_keyword="AI",
            content_excerpt="Body excerpt",
        )

    assert captured["called"] is True
    # ollama/ prefix stripped — providers expect the bare name
    assert captured["model"] == "gemma3:27b"
    assert out == "A Crisp SEO-Optimized Title"


@pytest.mark.asyncio
async def test_falls_back_to_pipeline_writer_model_when_tier_missing():
    """When ``cost_tier.standard.model`` is empty, the legacy setting wins."""
    captured: dict[str, Any] = {}
    provider = _make_provider(captured)

    fake_sc = MagicMock()
    fake_sc._pool = _FakePool(None)  # tier mapping missing
    # pipeline_writer_model is the last-ditch fallback per the no-silent-
    # defaults guarantee (resolves through the legacy code path).
    fake_sc.get.return_value = "ollama/glm-4.7-5090"
    fake_sc.get_int.return_value = 4000

    with patch("services.title_generation.site_config", fake_sc), \
         patch(
             "plugins.registry.get_all_llm_providers",
             return_value=[provider],
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt.return_value = "PROMPT"
        out = await generate_canonical_title(
            topic="AI", primary_keyword="AI", content_excerpt="x",
        )

    assert captured["called"] is True
    assert captured["model"] == "glm-4.7-5090"
    assert out is not None


@pytest.mark.asyncio
async def test_pages_operator_when_both_miss():
    """No tier mapping AND no pipeline_writer_model — fail loud, return None."""
    fake_sc = MagicMock()
    fake_sc._pool = _FakePool(None)
    fake_sc.get.return_value = ""  # pipeline_writer_model also empty
    fake_sc.get_int.return_value = 4000

    notify = AsyncMock()
    provider = MagicMock()
    provider.name = "ollama_native"
    provider.complete = AsyncMock()

    with patch("services.title_generation.site_config", fake_sc), \
         patch(
             "plugins.registry.get_all_llm_providers",
             return_value=[provider],
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm, \
         patch(
             "services.integrations.operator_notify.notify_operator",
             new=notify,
         ):
        pm.return_value.get_prompt.return_value = "PROMPT"
        out = await generate_canonical_title(
            topic="AI", primary_keyword="AI", content_excerpt="x",
        )

    assert out is None
    notify.assert_awaited_once()
    msg = notify.await_args.args[0]
    assert "title_generation" in msg
    assert "cost_tier" in msg
    # Provider was never called because we bailed before dispatch.
    provider.complete.assert_not_awaited()


# ---------------------------------------------------------------------------
# sanitize_generated_title — pure-function edge cases.
# Pins the thinking-model failure-mode handling described in the docstring
# (#198 follow-up). No async / no mocks — these are the cheapest assertions
# in the module and were entirely uncovered before this expansion.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "   ", "\n\n\t  \n"])
def test_sanitize_returns_none_for_empty_or_whitespace(raw: str):
    """Empty / whitespace-only input has no salvageable title."""
    assert sanitize_generated_title(raw) is None


def test_sanitize_strips_think_block_then_returns_clean_title():
    """``<think>…</think>`` deliberation traces must be removed before the
    line walk; what's left is the actual title."""
    raw = (
        "<think>Hmm, the user wants something punchy. Let me consider a few "
        "options...</think>The Rise of Tiny LLMs"
    )
    assert sanitize_generated_title(raw) == "The Rise of Tiny LLMs"


def test_sanitize_walks_in_reverse_skipping_bullet_deliberation():
    """When the model deliberates above its final answer, the reverse walk
    picks the last viable line and the deliberation lines never beat it."""
    raw = (
        "Option 1: First rough idea\n"
        "* Let's go with the question form\n"
        "The Definitive Guide to Vector Databases"
    )
    assert (
        sanitize_generated_title(raw)
        == "The Definitive Guide to Vector Databases"
    )


def test_sanitize_strips_list_markers_and_bold_wrappers():
    """Numbered list prefix ``1.`` plus ``**…**`` bold wrapper both removed."""
    assert sanitize_generated_title("1. **Bold Title Here**") == "Bold Title Here"


def test_sanitize_strips_markdown_header_and_quotes():
    """Header ``#`` prefix and surrounding double-quotes both stripped."""
    assert (
        sanitize_generated_title("# A Comprehensive Header Title")
        == "A Comprehensive Header Title"
    )
    assert (
        sanitize_generated_title('"Quoted Title Goes Here"')
        == "Quoted Title Goes Here"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "Hi",                              # 2 chars — under the 5-char floor
        "Here are 3 options for you",      # deliberation marker "here are"
        "Let's go with the question form", # deliberation marker "let's go with"
        "A" * 250,                          # single line >200 chars — skipped
    ],
)
def test_sanitize_rejects_unsalvageable_outputs(raw: str):
    """Too-short, deliberation-flavored, or absurdly-long lines all yield None."""
    assert sanitize_generated_title(raw) is None


def test_sanitize_truncates_long_titles_with_ellipsis():
    """Lines between 101–200 chars get truncated to 100 chars total
    (97 + ``...``); anything past 200 is dropped, not truncated."""
    raw = "A" * 105
    out = sanitize_generated_title(raw)
    assert out is not None
    assert len(out) == 100
    assert out.endswith("...")
    # The 97-char prefix is preserved before the ellipsis
    assert out[:97] == "A" * 97


# ---------------------------------------------------------------------------
# generate_canonical_title — error / fallback paths the cost-tier suite
# above did not exercise.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_none_when_ollama_native_provider_missing():
    """If ``ollama_native`` isn't in the registry, bail out early — no
    silent default to another provider."""
    fake_sc = MagicMock()
    fake_sc._pool = _FakePool("ollama/gemma3:27b")
    fake_sc.get.return_value = ""
    fake_sc.get_int.return_value = 4000

    other = MagicMock()
    other.name = "claude_haiku"  # registered, but not the writer

    with patch("services.title_generation.site_config", fake_sc), \
         patch(
             "plugins.registry.get_all_llm_providers",
             return_value=[other],
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt.return_value = "PROMPT"
        out = await generate_canonical_title(
            topic="AI", primary_keyword="AI", content_excerpt="x",
        )

    assert out is None


@pytest.mark.asyncio
async def test_returns_none_when_sanitizer_rejects_llm_output():
    """When the writer emits a deliberation trace the sanitizer can't
    salvage, the function returns None instead of the raw text."""
    captured: dict[str, Any] = {}
    provider = MagicMock()
    provider.name = "ollama_native"

    async def _complete(**_kwargs):
        captured["called"] = True
        result = MagicMock()
        result.text = "Here are 3 options for you to consider"
        return result

    provider.complete = AsyncMock(side_effect=_complete)

    fake_sc = MagicMock()
    fake_sc._pool = _FakePool("ollama/gemma3:27b")
    fake_sc.get.return_value = ""
    fake_sc.get_int.return_value = 4000

    with patch("services.title_generation.site_config", fake_sc), \
         patch(
             "plugins.registry.get_all_llm_providers",
             return_value=[provider],
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt.return_value = "PROMPT"
        out = await generate_canonical_title(
            topic="AI", primary_keyword="AI", content_excerpt="x",
        )

    assert captured.get("called") is True
    assert out is None


@pytest.mark.asyncio
async def test_existing_titles_appended_to_avoidance_prompt():
    """When ``existing_titles`` is supplied the avoidance preamble must
    appear in the prompt sent to the writer — pins the regen-loop contract
    that the content-router relies on."""
    captured: dict[str, Any] = {}
    provider = MagicMock()
    provider.name = "ollama_native"

    async def _complete(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        result = MagicMock()
        result.text = "A Distinctly Different Title"
        return result

    provider.complete = AsyncMock(side_effect=_complete)

    fake_sc = MagicMock()
    fake_sc._pool = _FakePool("ollama/gemma3:27b")
    fake_sc.get.return_value = ""
    fake_sc.get_int.return_value = 4000

    with patch("services.title_generation.site_config", fake_sc), \
         patch(
             "plugins.registry.get_all_llm_providers",
             return_value=[provider],
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt.return_value = "BASE PROMPT"
        out = await generate_canonical_title(
            topic="AI",
            primary_keyword="AI",
            content_excerpt="x",
            existing_titles="- Old Title One\n- Old Title Two",
        )

    assert out == "A Distinctly Different Title"
    prompt = captured["prompt"]
    assert "BASE PROMPT" in prompt
    assert "AVOID SIMILARITY" in prompt
    assert "Old Title One" in prompt
    assert "DISTINCTLY DIFFERENT" in prompt
