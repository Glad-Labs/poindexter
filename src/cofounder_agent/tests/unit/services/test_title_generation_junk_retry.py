"""Junk reject/retry loop in ``generate_canonical_title``.

The June-2026 pipeline_versions leak lineage: the title-gen LLM answers with
meta-commentary ABOUT the draft ("Focuses on specific metrics (TPS) rather
than high-level conceptual changes.") instead of a headline. The structured-
JSON extraction (#1821) can't help when the junk IS the ``title`` field
value, so ``generate_canonical_title`` now junk-checks the sanitized
candidate and retries with an explicit corrective instruction, up to
``title_junk_regen_max_retries`` extra attempts (default 1). Exhausting the
retries returns None → the ``choose_canonical_title`` H1/topic fallback.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.title_generation import generate_canonical_title

_JUNK_TITLE = (
    "Focuses on specific metrics (TPS) rather than high-level conceptual "
    "changes."
)
_GOOD_TITLE = "RTX 5090 vs RTX 4090: Real Tokens-per-Second Gains"


def _provider_returning_sequence(texts: list[str]) -> MagicMock:
    """A mock ``ollama_native`` provider whose ``complete`` returns each text
    in ``texts`` on successive calls (repeating the last one if exhausted)."""
    provider = MagicMock()
    provider.name = "ollama_native"
    calls: list[dict[str, Any]] = []

    async def _complete(**kwargs):
        calls.append(kwargs)
        text = texts[min(len(calls) - 1, len(texts) - 1)]
        result = MagicMock()
        result.text = text
        return result

    provider.complete = AsyncMock(side_effect=_complete)
    provider._calls = calls
    return provider


def _site_config(junk_retries: int = 1) -> MagicMock:
    sc = MagicMock()
    sc.get.return_value = "ollama/gemma-4-31B"  # pipeline_writer_model pin
    sc.get_int.side_effect = lambda key, default=None: {
        "title_junk_regen_max_retries": junk_retries,
        "title_max_length": 90,
        "content_router_seo_title_max_tokens": 4000,
    }.get(key, default)
    return sc


async def _run(provider: MagicMock, junk_retries: int = 1) -> Any:
    with patch(
        "plugins.registry.get_all_llm_providers", return_value=[provider]
    ), patch("services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt.return_value = "PROMPT"
        return await generate_canonical_title(
            topic="RTX 5090 vs RTX 4090",
            primary_keyword="RTX 5090",
            content_excerpt="Body excerpt about GPU benchmarks.",
            site_config=_site_config(junk_retries=junk_retries),
        )


@pytest.mark.asyncio
async def test_junk_title_triggers_one_retry_then_returns_clean_title():
    """First attempt returns the 2026-06-18 narration capture, the retry
    returns a real headline — the retry result wins."""
    provider = _provider_returning_sequence(
        [f'{{"title": "{_JUNK_TITLE}"}}', f'{{"title": "{_GOOD_TITLE}"}}']
    )
    out = await _run(provider)
    assert out == _GOOD_TITLE
    assert provider.complete.await_count == 2


@pytest.mark.asyncio
async def test_retry_prompt_carries_corrective_instruction():
    """The retry re-sends the original prompt plus an explicit 'you described
    the title instead of stating it' corrective."""
    provider = _provider_returning_sequence(
        [f'{{"title": "{_JUNK_TITLE}"}}', f'{{"title": "{_GOOD_TITLE}"}}']
    )
    await _run(provider)
    first_prompt = provider._calls[0]["messages"][0]["content"]
    retry_prompt = provider._calls[1]["messages"][0]["content"]
    assert retry_prompt.startswith(first_prompt)
    assert "DESCRIBED the title" in retry_prompt


@pytest.mark.asyncio
async def test_persistent_junk_exhausts_retries_and_returns_none():
    """A model that answers meta-commentary on every attempt exhausts the
    retry budget and yields None → caller falls back to H1/topic."""
    provider = _provider_returning_sequence([f'{{"title": "{_JUNK_TITLE}"}}'])
    out = await _run(provider)
    assert out is None
    assert provider.complete.await_count == 2  # 1 initial + 1 retry


@pytest.mark.asyncio
async def test_zero_retries_disables_the_retry():
    """title_junk_regen_max_retries=0 → single attempt, junk falls straight
    through to the fallback (None)."""
    provider = _provider_returning_sequence([f'{{"title": "{_JUNK_TITLE}"}}'])
    out = await _run(provider, junk_retries=0)
    assert out is None
    assert provider.complete.await_count == 1


@pytest.mark.asyncio
async def test_clean_title_needs_no_retry():
    """A clean first answer returns immediately — no extra LLM call."""
    provider = _provider_returning_sequence([f'{{"title": "{_GOOD_TITLE}"}}'])
    out = await _run(provider)
    assert out == _GOOD_TITLE
    assert provider.complete.await_count == 1
