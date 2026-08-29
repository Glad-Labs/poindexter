"""The judge rails must budget for a thinking model's reasoning trace.

On 2026-08-28 all three LLM judge rails (deepeval faithfulness, deepeval
g_eval, ragas) failed on **9 of 9** QA passes. Every deterministic rail was
green, the rails are advisory, so nothing blocked and nothing looked broken —
the advisory judge layer simply went dark.

Cause: the 2026-08-27 QA-placement pass repointed both judges at
``qwen3-vl:30b``. A thinking model spends its output budget on the reasoning
trace before it emits an answer, and raw Ollama returns that trace in a
separate ``thinking`` field. Under-budgeted, the call returns ``content=""``
with ``done_reason='length'`` — so the rail parses ``""`` and reports
``JSONDecodeError: Expecting value: line 1 column 1 (char 0)``, which reads
like a bad judgement and is actually starvation.

The critic path already had this budget. These two rails were the only judge
paths that never adopted it.
"""

from __future__ import annotations

import pytest

from services.llm_providers.thinking_models import resolve_judge_num_predict


class _SC:
    """Minimal SiteConfig stand-in."""

    def __init__(self, values: dict | None = None):
        self._v = values or {}

    def get(self, key, default=None):
        return self._v.get(key, default)


_PROD = {
    "qa_thinking_model_max_tokens": "8000",
    "qa_standard_max_tokens": "1500",
    "thinking_model_substrings": '["qwen3","qwen3.5","glm-4.7-5090","deepseek-r1"]',
}


@pytest.mark.unit
class TestBudgetResolution:
    def test_the_pinned_judge_gets_the_thinking_budget(self) -> None:
        """`qwen3-vl:30b` is what deepeval_judge_model / ragas_judge_model
        actually resolve to in prod, and it matches the `qwen3` substring."""
        assert resolve_judge_num_predict("qwen3-vl:30b", _SC(_PROD)) == 8000

    def test_a_non_thinking_judge_keeps_the_smaller_budget(self) -> None:
        """phi4 was the previous pin — it answers directly and must not be
        handed an 8000-token budget it has no use for."""
        assert resolve_judge_num_predict("phi4:14b", _SC(_PROD)) == 1500

    def test_budget_is_large_enough_for_the_observed_trace(self) -> None:
        """Measured against the live pinned model: ~1800 tokens of thinking
        before the first answer token. A budget under that reproduces the
        blackout exactly, so this is the floor that matters."""
        assert resolve_judge_num_predict("qwen3-vl:30b", _SC(_PROD)) > 1800

    def test_operator_can_tune_both_dials(self) -> None:
        sc = _SC({**_PROD, "qa_thinking_model_max_tokens": "12000",
                  "qa_standard_max_tokens": "900"})
        assert resolve_judge_num_predict("qwen3-vl:30b", sc) == 12000
        assert resolve_judge_num_predict("phi4:14b", sc) == 900

    @pytest.mark.parametrize("sc", [None, _SC({}), _SC({"qa_thinking_model_max_tokens": "not-a-number"})])
    def test_falls_back_loudly_to_safe_defaults(self, sc) -> None:
        """A judge must never end up with a budget of 0/None — that is the
        blackout. Bad or absent config falls back to the working defaults."""
        for model in ("qwen3-vl:30b", "phi4:14b"):
            assert resolve_judge_num_predict(model, sc) >= 1500


@pytest.mark.unit
class TestBothRailsActuallyPassIt:
    """A resolver nothing calls would leave the rails just as dark."""

    def test_deepeval_passes_num_predict_to_the_ollama_model(self, monkeypatch) -> None:
        import services.deepeval_rails as dr

        captured = {}

        class _FakeOllamaModel:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        import deepeval.models as dm
        monkeypatch.setattr(dm, "OllamaModel", _FakeOllamaModel)

        dr._build_deepeval_judge_model("ollama/qwen3-vl:30b", site_config=_SC(_PROD))
        assert captured.get("generation_kwargs", {}).get("num_predict") == 8000

    def test_ragas_passes_num_predict_to_chat_ollama(self) -> None:
        """Guard the wiring by source, since constructing the real Ragas stack
        needs langchain + ragas imported and a live base_url."""
        from pathlib import Path

        src = Path(__file__).resolve().parents[3].joinpath(
            "services", "ragas_eval.py"
        ).read_text(encoding="utf-8")
        assert "num_predict=resolve_judge_num_predict(judge_model, site_config)" in src
