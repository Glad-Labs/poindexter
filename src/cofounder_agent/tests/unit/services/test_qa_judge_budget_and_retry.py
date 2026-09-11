"""poindexter#1035 — the dispatcher judge path forwards a real output budget
and retries once on EMPTY content.

Since #3519 withheld JSON mode, every remaining DeepEval faithfulness failure
in ``cost_logs`` was a call with 11.8k–15.5k output tokens against a 16k
window: the thinking judge reasoned to exhaustion because the dispatcher path
never forwarded a budget (``num_predict`` only reached the stock OllamaModel
fallback, and the LiteLLM provider forwards ``max_tokens``). These tests stub
at the ``dispatch_complete`` boundary like ``test_judge_json_mode.py``.
"""

from __future__ import annotations

import pytest

from poindexter.services.llm_providers import dispatcher as _dispatcher
from poindexter.services.llm_providers.thinking_models import resolve_judge_num_predict
from poindexter.services.site_config import SiteConfig

THINKING = "ollama/qwen3-vl:30b"


def _cfg(**over):
    base = {"thinking_model_substrings": '["qwen3","deepseek-r1"]'}
    base.update(over)
    return SiteConfig(initial_config=base)


class _Completion:
    def __init__(self, text: str):
        self.text = text


def _judge(monkeypatch, texts: list[str], **cfg_over):
    """Build the dispatcher judge with a scripted sequence of completions;
    returns (model, captured_kwargs_list)."""
    deepeval_rails = pytest.importorskip("poindexter.services.deepeval_rails")
    seen: list[dict] = []
    script = list(texts)

    async def fake_dispatch_complete(**kwargs):
        seen.append(kwargs)
        return _Completion(script.pop(0) if script else texts[-1])

    monkeypatch.setattr(_dispatcher, "dispatch_complete", fake_dispatch_complete)
    monkeypatch.setattr("poindexter.services.gpu_scheduler.qa_rail_wait_budget_s", lambda: 1.0, raising=False)
    model = deepeval_rails._build_dispatcher_judge_model(
        THINKING, pool=object(), site_config=_cfg(**cfg_over),
    )
    if model is None:
        pytest.skip("deepeval not installed")
    return model, seen


@pytest.mark.unit
@pytest.mark.asyncio
class TestJudgeBudgetForwarded:
    async def test_max_tokens_is_the_judge_budget(self, monkeypatch):
        model, seen = _judge(monkeypatch, ["verdict"])
        await model.a_generate("judge this", schema=None)
        assert seen and seen[0]["max_tokens"] == resolve_judge_num_predict(THINKING, _cfg())
        assert seen[0]["max_tokens"] == 8000  # thinking budget default
        assert "num_predict" not in seen[0]  # the provider never read that key

    async def test_num_ctx_forwarded_when_configured(self, monkeypatch):
        model, seen = _judge(monkeypatch, ["verdict"], qa_deepeval_judge_num_ctx="16384")
        await model.a_generate("judge this", schema=None)
        assert seen[0]["num_ctx"] == 16384

    async def test_no_num_ctx_when_unset(self, monkeypatch):
        model, seen = _judge(monkeypatch, ["verdict"])
        await model.a_generate("judge this", schema=None)
        assert "num_ctx" not in seen[0]


@pytest.mark.unit
@pytest.mark.asyncio
class TestEmptyContentRetry:
    async def test_empty_then_text_retries_once_and_returns_text(self, monkeypatch):
        model, seen = _judge(monkeypatch, ["", "verdict"])
        out = await model.a_generate("judge this", schema=None)
        assert out == "verdict"
        assert len(seen) == 2

    async def test_empty_twice_still_names_the_cause(self, monkeypatch):
        model, seen = _judge(monkeypatch, ["", ""])
        with pytest.raises(ValueError, match="EMPTY content"):
            await model.a_generate("judge this", schema=None)
        assert len(seen) == 2  # default = one retry, then give up

    async def test_retries_are_a_setting(self, monkeypatch):
        model, seen = _judge(monkeypatch, ["", "verdict"], deepeval_judge_empty_retries="0")
        with pytest.raises(ValueError, match="EMPTY content"):
            await model.a_generate("judge this", schema=None)
        assert len(seen) == 1

    async def test_first_try_success_never_retries(self, monkeypatch):
        model, seen = _judge(monkeypatch, ["verdict", ""])
        assert await model.a_generate("judge this", schema=None) == "verdict"
        assert len(seen) == 1


@pytest.mark.unit
def test_settings_are_seeded():
    from poindexter.services.settings_defaults import DEFAULTS, METADATA

    for k in ("deepeval_judge_empty_retries", "ragas_job_timeout_seconds", "ragas_max_workers"):
        assert k in DEFAULTS and k in METADATA, k
    assert DEFAULTS["ragas_job_timeout_seconds"] == "600"
    assert DEFAULTS["deepeval_judge_empty_retries"] == "1"
