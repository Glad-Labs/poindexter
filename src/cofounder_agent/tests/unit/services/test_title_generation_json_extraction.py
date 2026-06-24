"""Structured-JSON title extraction for ``generate_canonical_title``.

The durable fix for the title-leak lineage (#1280 → #1821, task bb878d6b):
``generate_canonical_title`` asks the writer for a JSON object and reads the
``title`` field, mirroring ``modules/content/atoms/seo_generate_all_metadata.py``
(which has never leaked for exactly this reason). Any deliberation a reasoning
model emits OUTSIDE the JSON object is discarded by construction — it can never
be selected as the title the way the old bottom-up line scan
(``sanitize_generated_title``) selected rationale bullets, nor can the raw
``{"title": "..."}`` envelope leak through as a "title-like" line.

When no JSON object can be parsed the function returns ``None`` so
``choose_canonical_title`` falls back to the body H1 / topic (and
``_is_junk_title`` stays a belt-and-suspenders backstop).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.title_generation as tg
from services.prompt_manager import UnifiedPromptManager
from services.title_generation import generate_canonical_title

# --------------------------------------------------------------------------- #
# Test scaffolding — drives the real model-pin resolution path.
# --------------------------------------------------------------------------- #


def _provider_returning(text: str) -> MagicMock:
    """A mock ``ollama_native`` provider whose ``complete`` returns ``text``."""
    provider = MagicMock()
    provider.name = "ollama_native"

    async def _complete(**kwargs):
        result = MagicMock()
        result.text = text
        return result

    provider.complete = AsyncMock(side_effect=_complete)
    return provider


def _site_config() -> MagicMock:
    sc = MagicMock()
    sc.get.return_value = "ollama/glm-4.7-5090"  # pipeline_writer_model pin
    sc.get_int.return_value = 4000
    return sc


async def _run(provider_text: str) -> Any:
    """Drive ``generate_canonical_title`` with a provider that returns
    ``provider_text``; the prompt manager is stubbed so the test pins the
    extraction path, not the (separately tested) prompt body."""
    provider = _provider_returning(provider_text)
    with patch(
        "plugins.registry.get_all_llm_providers", return_value=[provider]
    ), patch("services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt.return_value = "PROMPT"
        return await generate_canonical_title(
            topic="RTX 5090 benchmarks",
            primary_keyword="RTX 5090",
            content_excerpt="Body excerpt about GPU benchmarks.",
            site_config=_site_config(),
        )


# --------------------------------------------------------------------------- #
# _extract_json — pure function (tolerates fences + surrounding prose).
# --------------------------------------------------------------------------- #


def test_extract_json_parses_plain_object():
    assert tg._extract_json('{"title": "RTX 5090 Benchmarks"}') == {
        "title": "RTX 5090 Benchmarks"
    }


def test_extract_json_tolerates_markdown_fence():
    raw = '```json\n{"title": "RTX 5090 Benchmarks"}\n```'
    assert tg._extract_json(raw) == {"title": "RTX 5090 Benchmarks"}


def test_extract_json_tolerates_reasoning_preamble_and_trailing_prose():
    raw = (
        "Let me weigh a few options. The 32GB angle is strongest.\n"
        '{"title": "RTX 5090 Benchmarks: The 32GB Threshold"}\n'
        "That should rank well."
    )
    assert tg._extract_json(raw) == {
        "title": "RTX 5090 Benchmarks: The 32GB Threshold"
    }


def test_extract_json_returns_none_for_non_json():
    assert tg._extract_json("No JSON here, just prose about titles.") is None


def test_extract_json_returns_none_for_empty():
    assert tg._extract_json("") is None


# --------------------------------------------------------------------------- #
# generate_canonical_title — reads parsed["title"], discards everything else.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reads_title_from_json_field():
    """The happy path: a clean JSON object yields its ``title`` field value —
    NOT the raw ``{"title": ...}`` envelope the old line-scan would return."""
    out = await _run('{"title": "RTX 5090 Benchmarks: The 32GB Threshold"}')
    assert out == "RTX 5090 Benchmarks: The 32GB Threshold"


@pytest.mark.asyncio
async def test_discards_reasoning_preamble_outside_json():
    """THE leak-proof guarantee. A reasoning model emits its rationale bullet
    (the exact #1821 species, task bb878d6b) ahead of the JSON object the new
    prompt asks for. Only ``parsed["title"]`` is read, so the rationale is
    discarded by construction."""
    leaky = (
        'Avoids the "Dev Diary/PR" style: It is framed as an evergreen '
        "resource rather than a log.\n"
        '{"title": "Vector Databases Explained: A Practical Guide"}'
    )
    out = await _run(leaky)
    assert out == "Vector Databases Explained: A Practical Guide"
    assert "framed as" not in (out or "").lower()


@pytest.mark.asyncio
async def test_extracts_from_json_in_markdown_fence():
    """A model that wraps the object in a ```json fence still parses cleanly."""
    out = await _run('```json\n{"title": "Tiny LLMs Are Eating the Edge"}\n```')
    assert out == "Tiny LLMs Are Eating the Edge"


@pytest.mark.asyncio
async def test_returns_none_when_llm_emits_no_json_object():
    """A reasoning model that ignores the JSON instruction and emits ONLY a
    rationale bullet (the #1280/#1821 leak shape, no braces anywhere). With no
    JSON object to read, the function returns None — leak-proof by construction
    — and the caller falls back to the body H1 / topic instead of leaking the
    rationale as the title."""
    out = await _run(
        'No Provocative Tone: These avoid the "X is Y" framing because they '
        "read as clickbait."
    )
    assert out is None


@pytest.mark.asyncio
async def test_returns_none_when_json_title_field_missing():
    """A JSON object without a ``title`` key has no title to read → None."""
    out = await _run('{"description": "no title key here", "keywords": "a, b"}')
    assert out is None


@pytest.mark.asyncio
async def test_returns_none_when_json_title_field_blank():
    """An empty/whitespace ``title`` value is not a usable title → None."""
    out = await _run('{"title": "   "}')
    assert out is None


# --------------------------------------------------------------------------- #
# Prompt contract — the seo.generate_title default must REQUEST a JSON object
# (so the structured-extraction path has something to parse) while keeping
# {topic} as its only required placeholder (several prompt-manager tests render
# it with topic alone).
# --------------------------------------------------------------------------- #


def test_seo_generate_title_prompt_requests_json_object():
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt("seo.generate_title", topic="vector databases")
    # {topic} is still the (only) substituted placeholder.
    assert "vector databases" in rendered
    # The prompt asks for a JSON object keyed on "title".
    assert '"title"' in rendered
    assert "{" in rendered and "}" in rendered
    assert "JSON" in rendered.upper()
