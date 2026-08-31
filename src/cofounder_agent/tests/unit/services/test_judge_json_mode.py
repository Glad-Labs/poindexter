"""JSON mode must be withheld from a THINKING judge (2026-08-31).

`response_format={"type":"json_object"}` maps to Ollama's constrained
decoding. A thinking judge cannot emit its reasoning trace as valid JSON, so
under the constraint it stops after ~30 tokens with EMPTY content — deepeval
then raised `JSONDecodeError: Expecting value: line 1 column 1 (char 0)` and
ragas recorded the -1.0 sentinel for every metric. All three LLM rails were
dark on 100% of QA passes from 2026-08-28 until this fix.

Measured against the pinned judge, same prompt, only response_format differing:
    json_object -> len(content) [0, 0, 0]       completion_tokens=30
    plain       -> len(content) [147, 242, 234] completion_tokens=960+

These tests stub at the ``dispatch_complete`` boundary and exercise the real
adapters, so reverting the production change fails them.
"""
from __future__ import annotations

import pytest

from services.llm_providers import dispatcher as _dispatcher
from services.llm_providers.thinking_models import judge_json_mode_supported
from services.site_config import SiteConfig

THINKING = "ollama/qwen3-vl:30b"
PLAIN = "ollama/phi4:14b"


def _cfg(**over):
    base = {"thinking_model_substrings": '["qwen3","deepseek-r1"]'}
    base.update(over)
    return SiteConfig(initial_config=base)


class TestJudgeJsonModeSupported:
    def test_thinking_judge_loses_json_mode(self):
        assert judge_json_mode_supported(THINKING, _cfg()) is False

    def test_non_thinking_judge_keeps_json_mode(self):
        """#1910: constrained decoding is load-bearing for weaker judges."""
        assert judge_json_mode_supported(PLAIN, _cfg()) is True

    def test_operator_can_force_json_mode_back_on(self):
        cfg = _cfg(qa_judge_json_mode_thinking_enabled="true")
        assert judge_json_mode_supported(THINKING, cfg) is True

    def test_no_site_config_fails_closed_for_thinking(self):
        assert judge_json_mode_supported(THINKING, None) is False

    def test_substring_list_is_configurable(self):
        cfg = SiteConfig(initial_config={"thinking_model_substrings": '["phi4"]'})
        assert judge_json_mode_supported(PLAIN, cfg) is False
        assert judge_json_mode_supported(THINKING, cfg) is True


class _Completion:
    def __init__(self, text="{\"score\": 0.9}"):
        self.text = text


@pytest.fixture
def captured(monkeypatch):
    """Capture kwargs at the dispatcher boundary; return a mutable list."""
    seen: list[dict] = []

    async def fake_dispatch_complete(**kwargs):
        seen.append(kwargs)
        return _Completion()

    monkeypatch.setattr(_dispatcher, "dispatch_complete", fake_dispatch_complete)
    return seen


class TestDeepEvalDispatcherJudge:
    """The path production actually takes (pool is not None)."""

    @pytest.mark.asyncio
    async def test_thinking_judge_sends_no_response_format(self, captured):
        deepeval_rails = pytest.importorskip("services.deepeval_rails")

        class _Schema:
            @staticmethod
            def model_validate(d):
                return d

        model = deepeval_rails._build_dispatcher_judge_model(
            THINKING, pool=object(), site_config=_cfg()
        )
        if model is None:
            pytest.skip("deepeval not installed")
        # A schema MUST be passed: with schema=None the adapter never sets
        # response_format at all, so the assertion would hold no matter what
        # judge_json_mode_supported returned (caught by mutation, 2026-08-31).
        await model.a_generate("judge this", schema=_Schema)
        assert captured, "dispatcher was never called"
        assert "response_format" not in captured[0]

    @pytest.mark.asyncio
    async def test_non_thinking_judge_still_sends_response_format(self, captured):
        deepeval_rails = pytest.importorskip("services.deepeval_rails")

        class _Schema:
            @staticmethod
            def model_validate(d):
                return d

        model = deepeval_rails._build_dispatcher_judge_model(
            PLAIN, pool=object(), site_config=_cfg()
        )
        if model is None:
            pytest.skip("deepeval not installed")
        await model.a_generate("judge this", schema=_Schema)
        assert captured[0]["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_empty_content_names_its_cause(self, monkeypatch):
        """A bare JSONDecodeError read as a malformed judgement for weeks."""
        deepeval_rails = pytest.importorskip("services.deepeval_rails")

        async def empty_dispatch(**kwargs):
            return _Completion(text="")

        monkeypatch.setattr(_dispatcher, "dispatch_complete", empty_dispatch)
        model = deepeval_rails._build_dispatcher_judge_model(
            THINKING, pool=object(), site_config=_cfg()
        )
        if model is None:
            pytest.skip("deepeval not installed")
        with pytest.raises(ValueError, match="EMPTY content"):
            await model.a_generate("judge this", schema=None)


class TestRagasDispatcherJudge:
    @pytest.mark.asyncio
    async def test_thinking_judge_sends_no_response_format(self, captured):
        ragas_eval = pytest.importorskip("services.ragas_eval")
        from langchain_core.messages import HumanMessage

        llm, _emb = ragas_eval._build_dispatcher_ragas_wrappers(
            pool=object(), judge_model="qwen3-vl:30b",
            embed_model="nomic-embed-text", site_config=_cfg(),
        )
        await llm.langchain_llm._agenerate([HumanMessage(content="judge this")])
        assert captured, "dispatcher was never called"
        assert "response_format" not in captured[0]

    @pytest.mark.asyncio
    async def test_non_thinking_judge_still_sends_response_format(self, captured):
        ragas_eval = pytest.importorskip("services.ragas_eval")
        from langchain_core.messages import HumanMessage

        llm, _emb = ragas_eval._build_dispatcher_ragas_wrappers(
            pool=object(), judge_model="phi4:14b",
            embed_model="nomic-embed-text", site_config=_cfg(),
        )
        await llm.langchain_llm._agenerate([HumanMessage(content="judge this")])
        assert captured[0]["response_format"] == {"type": "json_object"}
