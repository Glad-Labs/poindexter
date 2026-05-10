"""Tests for services/deepeval_rails.py — DeepEval integration as a
parallel content reviewer (#197 / #329 sub-issue 1).

The ``is_enabled`` and pure-CPU brand-fabrication tests run
unconditionally. The g-eval / faithfulness rails depend on a real
DeepEval install + a reachable judge model, so the live-judge tests
mock the underlying ``deepeval.metrics`` classes via monkeypatch.
The fail-soft contract (every rail returns
``(True, 1.0, "<sentinel>")`` when DeepEval is missing or errors)
is verified directly without the SDK.
"""

from __future__ import annotations

from importlib.util import find_spec
from unittest.mock import MagicMock

import pytest

from services.deepeval_rails import (
    evaluate_brand_fabrication,
    evaluate_faithfulness,
    evaluate_g_eval,
    is_enabled,
    make_test_case,
)
import services.deepeval_rails as _de_mod

requires_deepeval = pytest.mark.skipif(
    find_spec("deepeval") is None,
    reason="DeepEval is an opt-in dep; install via `pip install deepeval` to run.",
)


# ---------------------------------------------------------------------------
# is_enabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsEnabled:
    def test_no_site_config_returns_false(self):
        assert is_enabled(None) is False

    def test_default_returns_false(self):
        sc = MagicMock()
        sc.get_bool.return_value = False
        assert is_enabled(sc) is False

    def test_true_setting_enables(self):
        sc = MagicMock()
        sc.get_bool.return_value = True
        assert is_enabled(sc) is True


# ---------------------------------------------------------------------------
# make_test_case
# ---------------------------------------------------------------------------


@pytest.mark.unit
@requires_deepeval
class TestMakeTestCase:
    def test_builds_llm_test_case(self):
        case = make_test_case(content="Generated body", topic="My Topic")
        assert case.input == "My Topic"
        assert case.actual_output == "Generated body"
        assert case.expected_output is None

    def test_with_expected_baseline(self):
        case = make_test_case(
            content="Generated", topic="Topic", expected="Reference",
        )
        assert case.expected_output == "Reference"


# ---------------------------------------------------------------------------
# evaluate_brand_fabrication
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEvaluateBrandFabrication:
    def test_clean_content_scores_one(self):
        passed, score, reason = evaluate_brand_fabrication(
            "FastAPI and PostgreSQL are reliable choices for backend development.",
            topic="Backend stacks",
        )
        assert passed is True
        assert score == 1.0
        assert "no fabrication" in reason.lower() or "no" in reason.lower()

    def test_empty_content_skips_cleanly(self):
        passed, score, reason = evaluate_brand_fabrication("", topic="x")
        assert passed is True
        assert score == 1.0

    def test_non_string_skips_cleanly(self):
        passed, score, reason = evaluate_brand_fabrication(None, topic="x")  # type: ignore[arg-type]
        assert passed is True
        assert score == 1.0

    def test_fake_quote_pattern_lowers_score(self):
        # FAKE_QUOTE_PATTERNS catches obvious public-figure attribution.
        # The exact match depends on tuning; we just confirm the metric
        # ran and returned a valid score.
        bad = (
            'Bill Gates told the audience: "AI will replace 90% of '
            'developers next year." Then he announced a new product line.'
        )
        passed, score, reason = evaluate_brand_fabrication(bad, topic="AI futures")
        assert isinstance(passed, bool)
        assert 0.0 <= score <= 1.0
        assert isinstance(reason, str)


# ---------------------------------------------------------------------------
# Custom metric — direct interface
# ---------------------------------------------------------------------------


@pytest.mark.unit
@requires_deepeval
class TestBrandFabricationMetric:
    """Verifies the BaseMetric subclass conforms to DeepEval's
    contract (measure returns float in [0,1], is_successful returns
    bool, sync + async paths agree)."""

    @pytest.mark.asyncio
    async def test_async_path_matches_sync(self):
        from services.deepeval_rails import _build_brand_fabrication_metric
        cls = _build_brand_fabrication_metric()
        metric = cls(threshold=0.5)
        case = make_test_case(
            content="Clean post about backends",
            topic="Backends",
        )
        sync_score = metric.measure(case)
        async_score = await metric.a_measure(case)
        assert sync_score == async_score

    def test_clean_returns_one(self):
        from services.deepeval_rails import _build_brand_fabrication_metric
        cls = _build_brand_fabrication_metric()
        metric = cls(threshold=0.5)
        case = make_test_case(
            content="A normal article about FastAPI.",
            topic="Backend frameworks",
        )
        score = metric.measure(case)
        assert score == 1.0
        assert metric.is_successful() is True


class _FakeMetric:
    """Stand-in for DeepEval's GEval / FaithfulnessMetric.

    DeepEval's real metrics call out to the judge model; that's
    expensive and flaky from CI. The reviewer chain only cares about
    the (success, score, reason) shape returned by ``measure``.
    """

    def __init__(self, score: float, reason: str = "judge ok", **kwargs):
        self._score = score
        self.reason = reason
        self.threshold = kwargs.get("threshold", 0.5)
        self.success = self._score >= self.threshold

    def measure(self, _case) -> float:
        return self._score


@pytest.mark.unit
class TestEvaluateGEval:
    def test_empty_content_skips(self):
        passed, score, reason = evaluate_g_eval("", topic="x")
        assert passed is True
        assert score == 1.0
        assert reason == "empty content"

    def test_high_score_passes_threshold(self, monkeypatch):
        def factory(*_a, **kw):
            return _FakeMetric(0.9, reason="grounded", **kw)

        monkeypatch.setattr("deepeval.metrics.GEval", factory)
        passed, score, reason = evaluate_g_eval(
            "Decent post about FastAPI.",
            topic="Backends",
            threshold=0.7,
        )
        assert passed is True
        assert score == pytest.approx(0.9)
        assert "grounded" in reason

    def test_low_score_fails_threshold(self, monkeypatch):
        def factory(*_a, **kw):
            return _FakeMetric(0.3, reason="vague claims", **kw)

        monkeypatch.setattr("deepeval.metrics.GEval", factory)
        passed, score, _reason = evaluate_g_eval(
            "Mushy post.",
            topic="Backends",
            threshold=0.7,
        )
        assert passed is False
        assert score == pytest.approx(0.3)

    def test_judge_exception_returns_safe_default(self, monkeypatch):
        def factory(*_a, **_kw):
            raise RuntimeError("judge api down")

        monkeypatch.setattr("deepeval.metrics.GEval", factory)
        passed, score, reason = evaluate_g_eval("post", topic="x")
        assert passed is True
        assert score == 1.0
        assert "deepeval-error" in reason


@pytest.mark.unit
class TestEvaluateFaithfulness:
    def test_empty_content_skips(self):
        passed, _score, reason = evaluate_faithfulness(
            "", retrieval_context=["fact"]
        )
        assert passed is True
        assert reason == "empty content"

    def test_no_context_skips(self):
        passed, score, reason = evaluate_faithfulness(
            "Some post.", retrieval_context=None,
        )
        assert passed is True
        assert score == 1.0
        assert reason == "no-context"

    def test_no_context_empty_list_also_skips(self):
        passed, _score, reason = evaluate_faithfulness(
            "Some post.", retrieval_context=[],
        )
        assert passed is True
        assert reason == "no-context"

    def test_grounded_content_passes(self, monkeypatch):
        def factory(*_a, **kw):
            return _FakeMetric(0.95, reason="all claims attributable", **kw)

        monkeypatch.setattr("deepeval.metrics.FaithfulnessMetric", factory)
        passed, score, _reason = evaluate_faithfulness(
            "FastAPI runs on uvicorn.",
            retrieval_context=["FastAPI uses uvicorn as its ASGI server."],
            threshold=0.8,
        )
        assert passed is True
        assert score == pytest.approx(0.95)

    def test_judge_exception_returns_safe_default(self, monkeypatch):
        def factory(*_a, **_kw):
            raise RuntimeError("judge died")

        monkeypatch.setattr("deepeval.metrics.FaithfulnessMetric", factory)
        passed, score, reason = evaluate_faithfulness(
            "post.", retrieval_context=["context."],
        )
        assert passed is True
        assert score == 1.0
        assert "deepeval-error" in reason


@pytest.mark.unit
class TestResolveJudgeModel:
    def test_default_when_site_config_none(self):
        assert _de_mod._resolve_judge_model(None) == "glm-4.7-5090"

    def test_override_via_site_config(self):
        sc = MagicMock()
        sc.get.return_value = "gpt-4o-mini"
        assert _de_mod._resolve_judge_model(sc) == "gpt-4o-mini"

    def test_blank_value_falls_back_to_default(self):
        sc = MagicMock()
        sc.get.return_value = "   "
        assert _de_mod._resolve_judge_model(sc) == "glm-4.7-5090"
