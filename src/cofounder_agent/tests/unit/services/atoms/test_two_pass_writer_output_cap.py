"""The two-pass writer's output-token cap and what happens when it is hit.

Until 2026-10-20 the draft, revise and expand calls sent no ``max_tokens``.
Ollama's default is unlimited, so a model that looped without emitting EOS ran
until the 600 s request timeout and the task failed with ``litellm.Timeout``:
8 of 28 quickstart-e2e runs to 2026-10-19, qwen2.5:0.5b on a CPU runner. These
tests pin the cap on every call, the cap-hit signal (which cannot rely on
``finish_reason`` alone), and the repetition-loop trim that keeps a looping
draft from being rejected as truncated.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.modules.content import ai_content_generator as acg
from poindexter.modules.content.atoms import two_pass_writer as two_pass
from poindexter.modules.content.content_validator import detect_truncated_content
from poindexter.services.llm_text import completion_hit_cap, ollama_chat_text
from poindexter.services.site_config import SiteConfig


def _sc(**overrides: str) -> SiteConfig:
    config = {
        "pipeline_writer_model": "ollama/gemma3:27b",
        "thinking_model_substrings": '["qwen3","glm-4"]',
        "writer_length_expansion_enabled": "false",
    }
    config.update(overrides)
    return SiteConfig(initial_config=config)


def _trim(text: str, *, min_repeats: int = 3, min_unit_chars: int = 24) -> tuple[str, int]:
    return two_pass._trim_repetition_loop(
        text, min_repeats=min_repeats, min_unit_chars=min_unit_chars,
    )


# ---------------------------------------------------------------------------
# The budget
# ---------------------------------------------------------------------------


class TestWriterMaxTokens:
    def test_standard_model_takes_the_standard_multiple(self):
        assert acg.writer_max_tokens(_sc(), target_length=1500, model="gemma3:27b") == 12000

    def test_thinking_model_takes_the_thinking_multiple(self):
        assert acg.writer_max_tokens(_sc(), target_length=1500, model="glm-4.7-5090") == 18000

    def test_multiples_are_settings(self):
        sc = _sc(content_gen_token_mult_standard="2.5", content_gen_token_mult_thinking="4")
        assert acg.writer_max_tokens(sc, target_length=1500, model="gemma3:27b") == 3750
        assert acg.writer_max_tokens(sc, target_length=1500, model="qwen3:30b") == 6000

    def test_never_zero(self):
        assert acg.writer_max_tokens(_sc(), target_length=0, model="gemma3:27b") == 1


# ---------------------------------------------------------------------------
# The cap-hit signal
# ---------------------------------------------------------------------------


class TestCompletionHitCap:
    @pytest.mark.parametrize("reason", ["length", "max_tokens", "LENGTH"])
    def test_finish_reason_says_so(self, reason):
        assert completion_hit_cap(finish_reason=reason, completion_tokens=10, max_tokens=100)

    def test_full_budget_with_stop_counts(self):
        """LiteLLM's ``ollama/`` route reports "stop" when Ollama said "length"."""
        assert completion_hit_cap(finish_reason="stop", completion_tokens=3750, max_tokens=3750)

    def test_under_budget_is_finished(self):
        assert not completion_hit_cap(finish_reason="stop", completion_tokens=2000, max_tokens=3750)

    def test_no_cap_means_not_capped(self):
        assert not completion_hit_cap(finish_reason="stop", completion_tokens=99999, max_tokens=None)


class TestOllamaChatTextCompletionInfo:
    def _completion(self, *, tokens: int, finish: str = "stop"):
        completion = MagicMock()
        completion.text = "body"
        completion.prompt_tokens = 5
        completion.completion_tokens = tokens
        completion.finish_reason = finish
        return completion

    def _run(self, completion, **kwargs):
        dispatch = AsyncMock(return_value=completion)
        with patch("poindexter.services.llm_providers.dispatcher.dispatch_complete", dispatch):
            out = asyncio.run(ollama_chat_text("p", site_config=_sc(), pool=MagicMock(), **kwargs))
        return out, dispatch.await_args.kwargs

    def test_reports_a_capped_completion_and_forwards_the_cap(self):
        info: dict = {}
        out, sent = self._run(self._completion(tokens=300), max_tokens=300, completion_info=info)
        assert out == "body"
        assert sent["max_tokens"] == 300
        assert info == {
            "finish_reason": "stop", "completion_tokens": 300, "max_tokens": 300, "capped": True,
        }

    def test_reports_a_finished_completion(self):
        info: dict = {}
        self._run(self._completion(tokens=120), max_tokens=300, completion_info=info)
        assert info["capped"] is False

    def test_no_info_dict_is_a_no_op(self):
        out, sent = self._run(self._completion(tokens=1))
        assert out == "body"
        assert "max_tokens" not in sent


# ---------------------------------------------------------------------------
# The repetition-loop trim
# ---------------------------------------------------------------------------


class TestTrimRepetitionLoop:
    def test_no_loop_is_left_alone(self):
        text = (
            "Docker changed how software ships. Containers made builds repeatable.\n\n"
            "Teams stopped arguing about which machine a bug lived on. And the "
            "registry made distribution a single command"
        )
        assert _trim(text) == (text, 0)

    def test_sentence_loop_on_one_line_is_cut_at_the_first_repeat(self):
        text = (
            "Docker changed how software ships.\n\n"
            + "Containers made every build repeatable. " * 40
            + "Containers made every bu"
        )
        out, removed = _trim(text)
        assert out == "Docker changed how software ships.\n\nContainers made every build repeatable."
        assert removed == len(text) - len(out)
        assert not detect_truncated_content(out)

    def test_numbered_list_loop_counts_as_one_unit(self):
        """A looping list increments its number; the marker is not the content."""
        items = "\n".join(f"{i}. Install the Docker engine on the host" for i in range(1, 30))
        text = "Docker changed deployment for good.\n\n## Steps\n\n" + items + "\n30. Install the Do"
        out, removed = _trim(text)
        # The cut leaves "1. Install..." (no terminal punctuation) under a heading;
        # both are dropped so the text ends where the truncation rail accepts it.
        assert out == "Docker changed deployment for good."
        assert removed > 0
        assert not detect_truncated_content(out)

    def test_paragraph_loop(self):
        para = (
            "Docker images bundle the runtime with the app. That makes the build "
            "the same everywhere it runs."
        )
        text = "# Why Docker changed everything\n\nIntro sentence that sets it up.\n\n" + "\n\n".join(
            [para] * 6
        ) + "\n\nDocker images bun"
        out, _ = _trim(text)
        assert out.endswith(para)
        assert out.count("Docker images bundle") == 1
        assert not detect_truncated_content(out)

    def test_below_min_repeats_is_not_a_loop(self):
        line = "Containers made every build repeatable."
        text = f"{line} {line} And then the article moves on to something else entirely"
        assert _trim(text) == (text, 0)

    def test_short_units_never_read_as_a_loop(self):
        """Fences, rules and terse list items repeat in real articles."""
        text = "- Yes\n- Yes\n- Yes\n- Yes\n```\n```\n```\n```\nthe end of a severed sente"
        assert _trim(text) == (text, 0)

    def test_loop_from_the_first_sentence_keeps_one_copy(self):
        text = "The model repeats this exact sentence forever. " * 50
        out, _ = _trim(text)
        assert out == "The model repeats this exact sentence forever."

    def test_empty_text(self):
        assert _trim("") == ("", 0)


# ---------------------------------------------------------------------------
# Settling a capped completion
# ---------------------------------------------------------------------------


@pytest.fixture
def findings(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr("poindexter.utils.findings.emit_finding", lambda **kw: seen.append(kw))
    return seen


class TestSettleCappedOutput:
    def test_uncapped_text_is_untouched_and_silent(self, findings):
        text = "Loop loop loop. " * 30
        out = two_pass._settle_capped_output(
            text, {"capped": False}, phase="two_pass_revise", task_id="t", site_config=_sc(),
        )
        assert out == text
        assert findings == []

    def test_capped_loop_is_trimmed_and_reported(self, findings):
        text = "Intro line that is fine.\n\n" + "This sentence keeps coming back again. " * 30
        info = {"capped": True, "max_tokens": 3750, "completion_tokens": 3750, "finish_reason": "stop"}
        out = two_pass._settle_capped_output(
            text, info, phase="draft_generation", task_id="t1", site_config=_sc(),
        )
        assert out == "Intro line that is fine.\n\nThis sentence keeps coming back again."
        assert len(findings) == 1
        f = findings[0]
        assert f["kind"] == "writer_output_capped"
        assert f["extra"]["loop_trimmed"] is True
        assert f["extra"]["phase"] == "draft_generation"
        assert f["extra"]["task_id"] == "t1"

    def test_capped_without_a_loop_is_kept_for_the_truncation_rail(self, findings):
        text = "A long article that simply ran past its budget and stopped mid-sente"
        info = {"capped": True, "max_tokens": 12000, "completion_tokens": 12000, "finish_reason": "length"}
        out = two_pass._settle_capped_output(
            text, info, phase="two_pass_expand", task_id=None, site_config=_sc(),
        )
        assert out == text
        assert detect_truncated_content(out)  # QA rejects it by name
        assert findings[0]["extra"]["loop_trimmed"] is False

    def test_loop_settings_are_read(self, findings):
        text = "Intro line that is fine.\n\n" + "This sentence keeps coming back again. " * 4
        info = {"capped": True}
        sc = _sc(writer_repetition_loop_min_repeats="10")
        out = two_pass._settle_capped_output(
            text, info, phase="draft_generation", task_id=None, site_config=sc,
        )
        assert out == text


# ---------------------------------------------------------------------------
# Every two-pass call is capped
# ---------------------------------------------------------------------------


def _pool():
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=ctx)
    return pool


@pytest.fixture
def no_embed(monkeypatch):
    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("poindexter.services.topic_ranking.embed_text", fake_embed)


def test_first_draft_is_capped_by_target_length(monkeypatch):
    seen: dict = {}

    async def fake_chat(prompt, **kw):
        seen.update(kw)
        kw["completion_info"]["capped"] = False
        return "draft"

    async def fake_model(*, site_config):
        return "ollama/gemma3:27b"

    monkeypatch.setattr(acg, "_resolve_rag_writer_model", fake_model)
    monkeypatch.setattr("poindexter.services.llm_text.ollama_chat_text", fake_chat)
    info: dict = {}
    asyncio.run(acg.generate_with_context(
        topic="T", angle="A", snippets=[], site_config=_sc(), target_length=1500,
        completion_info=info,
    ))
    assert seen["max_tokens"] == 12000
    assert seen["phase"] == "draft_generation"
    assert seen["completion_info"] is info


def test_a_looping_first_draft_comes_back_trimmed(monkeypatch, no_embed, findings):
    looping = "Docker changed how software ships.\n\n" + "Containers made every build repeatable. " * 60

    async def fake_draft(topic, angle, snippets, *, completion_info=None, **kw):
        completion_info.update({"capped": True, "max_tokens": 3750, "completion_tokens": 3750})
        return looping

    monkeypatch.setattr(acg, "generate_with_context", fake_draft)
    result = asyncio.run(two_pass.run(
        topic="t", angle="a", niche_id="n", pool=_pool(), site_config=_sc(), target_length=1500,
    ))
    assert result["draft"] == (
        "Docker changed how software ships.\n\nContainers made every build repeatable."
    )
    assert [f["kind"] for f in findings] == ["writer_output_capped"]


def test_revise_is_capped_by_target_length(monkeypatch, no_embed):
    drafts = iter(["First draft with [EXTERNAL_NEEDED: a fact] inside.", "Revised draft."])

    async def fake_draft(topic, angle, snippets, **kw):
        return next(drafts)

    revise_kwargs: list[dict] = []

    async def fake_revise(prompt, **kw):
        revise_kwargs.append(kw)
        return next(drafts)

    async def fake_research(query, max_sources=2, *, site_config=None):
        return "research"

    monkeypatch.setattr(acg, "generate_with_context", fake_draft)
    monkeypatch.setattr("poindexter.services.llm_text.ollama_chat_text", fake_revise)
    monkeypatch.setattr("poindexter.services.research_service.research_topic", fake_research)
    result = asyncio.run(two_pass.run(
        topic="t", angle="a", niche_id="n", pool=_pool(), site_config=_sc(), target_length=1000,
    ))
    assert result["draft"] == "Revised draft."
    assert [kw["phase"] for kw in revise_kwargs] == ["two_pass_revise"]
    assert revise_kwargs[0]["max_tokens"] == 8000
    assert isinstance(revise_kwargs[0]["completion_info"], dict)


def test_expand_is_capped_by_target_length(monkeypatch, no_embed):
    async def fake_draft(topic, angle, snippets, **kw):
        return "A short but real first draft about containers and shipping software."

    expand_kwargs: list[dict] = []

    async def fake_expand(prompt, **kw):
        expand_kwargs.append(kw)
        return "word " * 900

    monkeypatch.setattr(acg, "generate_with_context", fake_draft)
    monkeypatch.setattr("poindexter.services.llm_text.ollama_chat_text", fake_expand)
    result = asyncio.run(two_pass.run(
        topic="t", angle="a", niche_id="n", pool=_pool(),
        site_config=_sc(writer_length_expansion_enabled="true"), target_length=1200,
    ))
    assert result["length_expanded"] is True
    assert [kw["phase"] for kw in expand_kwargs] == ["two_pass_expand"]
    assert expand_kwargs[0]["max_tokens"] == 9600
