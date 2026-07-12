"""Vision QA thinking-budget tests (vision_scorer_unavailable RCA 2026-07-12).

qwen3-vl is a thinking model: its ``<think>`` trace shares the ``num_predict``
budget with the JSON scores, and at the 1024 base the trace exhausts the budget
before the scores are emitted — the leg returns ``None`` and ``qa.vision``
falsely pages "vision model unavailable" though the model ran fine (it spent GPU
+ tokens). ``_maybe_bump_vision_thinking_budget`` mirrors the text critic's
thinking-aware budget so a thinking vision model gets room to emit the scores.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.content.multi_model_qa import MultiModelQA
from services.site_config import SiteConfig


def _qa(thinking_budget: str = "8000") -> MultiModelQA:
    settings = SimpleNamespace(get=AsyncMock(return_value=thinking_budget))
    return MultiModelQA(pool=None, settings_service=settings, site_config=SiteConfig())


@pytest.mark.unit
class TestVisionThinkingBudget:
    async def test_thinking_vision_model_bumped(self):
        # qwen3-vl matches the default thinking substrings ("qwen3")
        qa = _qa("8000")
        assert await qa._maybe_bump_vision_thinking_budget("qwen3-vl:30b", 1024) == 8000

    async def test_ollama_prefixed_thinking_model_detected(self):
        qa = _qa("8000")
        assert (
            await qa._maybe_bump_vision_thinking_budget("ollama/qwen3-vl:30b", 1024) == 8000
        )

    async def test_non_thinking_vision_model_keeps_base(self):
        # llava is a non-thinking vision model — no bump, base preserved
        qa = _qa("8000")
        assert await qa._maybe_bump_vision_thinking_budget("llava:13b", 1024) == 1024

    async def test_larger_base_is_not_lowered(self):
        # operator already tuned qa_vision_num_predict high — max() keeps it
        qa = _qa("2000")
        assert await qa._maybe_bump_vision_thinking_budget("qwen3-vl:30b", 9000) == 9000

    async def test_no_settings_uses_default_thinking_budget(self):
        qa = MultiModelQA(pool=None, settings_service=None, site_config=SiteConfig())
        assert await qa._maybe_bump_vision_thinking_budget("qwen3-vl:30b", 1024) == 8000
