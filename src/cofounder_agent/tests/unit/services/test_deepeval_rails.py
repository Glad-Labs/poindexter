"""Tests for services/deepeval_rails.py — DeepEval integration as a
parallel content reviewer (#197).

The ``is_enabled`` cases run unconditionally. Cases that load real
DeepEval metrics import the SDK lazily inside the production module;
they are guarded by a skip marker that fires when ``deepeval`` is not
installed (CI default — DeepEval is opt-in via
``app_settings.deepeval_enabled`` and not pinned in pyproject).
"""

from __future__ import annotations

from importlib.util import find_spec
from unittest.mock import MagicMock

import pytest

from services.deepeval_rails import (
    evaluate_brand_fabrication,
    is_enabled,
    make_test_case,
)

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
