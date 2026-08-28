"""Thinking budget on the self-review DETECT call.

Reasoning tokens count against ``max_tokens`` and are emitted BEFORE the
numbered findings, so a thinking detector on the standard 1500 budget
truncates its own answer away. Measured 2026-08-28 with glm-4.7-5090 on the
longest published post, same prompt, only the cap differing:

    cap 1500 -> 7,636 chars,  3 numbered findings, cut mid-sentence
    cap 8000 -> 17,663 chars, 9 numbered findings

The list accumulates THROUGH the response, so a harder truncation leaves zero
numbered lines — which ``detect_contradictions`` reads as "no contradictions"
and returns as a clean PASS. That silent false negative is the thing this
budget exists to prevent (Glad-Labs/poindexter#1031).

Deliberately NOT a ``think=False`` fix: disabling reasoning is what the
writer/podcast/video-director paths do, and measured here it took glm's
detection from 4/4 to 0/4.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.self_review import detect_contradictions


def _sc(**over):
    """SiteConfig double whose get_int honours our keys."""
    vals = {
        "enable_writer_self_review": "true",
        "writer_self_review_model": "ollama/gemma-4-31B-it-qat:latest",
        "thinking_model_substrings": '["qwen3","glm-4.7-5090","deepseek-r1"]',
        "content_router_contradiction_review_max_tokens": 1500,
        "writer_self_review_thinking_max_tokens": 8000,
        "writer_self_review_thinking_timeout_seconds": 300,
    }
    vals.update(over)

    def _get(k, d=None):
        return vals.get(k, d)

    def _get_int(k, d=None):
        return int(vals.get(k, d))

    return SimpleNamespace(get=_get, get_int=_get_int, get_float=lambda k, d=None: float(d))


async def _capture(site_config):
    """Run detect and return the kwargs the completion seam was called with."""
    seen = {}

    async def _fake_complete(prompt, **kw):
        seen.update(kw)
        return SimpleNamespace(text="PASS")

    ctx = SimpleNamespace(
        complete=_fake_complete,
        review_model=site_config.get("writer_self_review_review_model")
        or "gemma-4-31B-it-qat:latest",
        revise_model="gemma-4-31B-it-qat:latest",
    )
    with patch("services.self_review._prepare", new=AsyncMock(return_value=ctx)):
        await detect_contradictions("x" * 900, "T", "Topic", site_config=site_config)
    return seen


@pytest.mark.unit
@pytest.mark.asyncio
async def test_thinking_detector_gets_the_larger_budget():
    sc = _sc(writer_self_review_review_model="ollama/glm-4.7-5090:latest")
    seen = await _capture(sc)
    assert seen["max_tokens"] == 8000
    assert seen["timeout_s_override"] == 300


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_thinking_detector_keeps_the_standard_budget():
    """A non-thinking default install must be completely unaffected."""
    sc = _sc()
    seen = await _capture(sc)
    assert seen["max_tokens"] == 1500
    assert seen["timeout_s_override"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_budget_follows_the_detect_pin_not_the_revise_pin():
    """The bump keys off the DETECT model. A thinking reviser paired with a
    non-thinking detector must NOT widen the detect budget — that would be
    reading the wrong pin now that the two can differ."""
    sc = _sc(
        writer_self_review_model="ollama/glm-4.7-5090:latest",
        writer_self_review_review_model="ollama/gemma-4-31B-it-qat:latest",
    )
    seen = await _capture(sc)
    assert seen["max_tokens"] == 1500


@pytest.mark.unit
@pytest.mark.asyncio
async def test_budget_is_db_tunable():
    sc = _sc(
        writer_self_review_review_model="ollama/glm-4.7-5090:latest",
        writer_self_review_thinking_max_tokens=12000,
        writer_self_review_thinking_timeout_seconds=600,
    )
    seen = await _capture(sc)
    assert seen["max_tokens"] == 12000
    assert seen["timeout_s_override"] == 600
