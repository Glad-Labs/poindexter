"""Tests for services/self_consistency_rail.py — HalluCounter-style
sampler-based hallucination signal (#196)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.self_consistency_rail import evaluate, is_enabled


def _site_config(values: dict | None = None) -> MagicMock:
    sc = MagicMock()
    values = values or {}
    sc.get.side_effect = lambda key, default="": values.get(key, default)
    sc.get_int.side_effect = lambda key, default: values.get(key, default)
    sc.get_float.side_effect = lambda key, default: values.get(key, default)
    sc.get_bool.side_effect = lambda key, default=False: values.get(key, default)
    return sc


# ---------------------------------------------------------------------------
# is_enabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsEnabled:
    def test_no_site_config_returns_false(self):
        assert is_enabled(None) is False

    def test_default_returns_false(self):
        assert is_enabled(_site_config()) is False

    def test_true_setting_enables(self):
        assert is_enabled(_site_config({"self_consistency_enabled": True})) is True


# ---------------------------------------------------------------------------
# evaluate — guards
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEvaluateGuards:
    @pytest.mark.asyncio
    async def test_empty_content_skips_cleanly(self):
        passed, score, reason = await evaluate(content="", topic="X")
        assert passed is True
        assert score == 1.0
        assert "empty content" in reason

    @pytest.mark.asyncio
    async def test_whitespace_content_skips_cleanly(self):
        passed, score, reason = await evaluate(content="   \n\n  ", topic="X")
        assert passed is True
        assert score == 1.0
        assert "empty content" in reason


# ---------------------------------------------------------------------------
# evaluate — sample failure paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEvaluateSampleFailures:
    @pytest.mark.asyncio
    async def test_zero_samples_returned_skips(self):
        with patch(
            "services.self_consistency_rail._sample_summaries",
            return_value=[],
        ):
            passed, score, reason = await evaluate(
                content="a body", topic="t",
            )
        assert passed is True
        assert score == 1.0
        assert "self-consistency-skipped" in reason

    @pytest.mark.asyncio
    async def test_one_sample_returned_skips(self):
        with patch(
            "services.self_consistency_rail._sample_summaries",
            return_value=["only one"],
        ):
            passed, score, reason = await evaluate(
                content="a body", topic="t",
            )
        assert passed is True
        assert score == 1.0
        assert "1 valid sample" in reason

    @pytest.mark.asyncio
    async def test_embedding_failure_skips(self):
        with patch(
            "services.self_consistency_rail._sample_summaries",
            return_value=["s1", "s2", "s3"],
        ), patch(
            "services.self_consistency_rail._pairwise_mean_cosine",
            return_value=-1.0,
        ):
            passed, score, reason = await evaluate(
                content="a body", topic="t",
            )
        assert passed is True
        assert score == 1.0
        assert "embedding step failed" in reason


# ---------------------------------------------------------------------------
# evaluate — happy path with stubbed sample + embed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEvaluateHappyPath:
    @pytest.mark.asyncio
    async def test_high_consistency_passes(self):
        with patch(
            "services.self_consistency_rail._sample_summaries",
            return_value=["sample 1", "sample 2", "sample 3"],
        ), patch(
            "services.self_consistency_rail._pairwise_mean_cosine",
            return_value=0.85,
        ):
            passed, score, reason = await evaluate(
                content="a body", topic="t",
                site_config=_site_config({"self_consistency_threshold": 0.55}),
            )
        assert passed is True
        assert score == 0.85
        assert "PASS" in reason

    @pytest.mark.asyncio
    async def test_low_consistency_fails(self):
        with patch(
            "services.self_consistency_rail._sample_summaries",
            return_value=["s1", "s2", "s3"],
        ), patch(
            "services.self_consistency_rail._pairwise_mean_cosine",
            return_value=0.30,
        ):
            passed, score, reason = await evaluate(
                content="a body", topic="t",
                site_config=_site_config({"self_consistency_threshold": 0.55}),
            )
        assert passed is False
        assert score == 0.30
        assert "FAIL" in reason
        assert "unstable" in reason


# ---------------------------------------------------------------------------
# evaluate — exception swallowing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEvaluateNeverRaises:
    @pytest.mark.asyncio
    async def test_unexpected_error_returns_clean_skip(self):
        with patch(
            "services.self_consistency_rail._sample_summaries",
            side_effect=RuntimeError("ollama exploded"),
        ):
            passed, score, reason = await evaluate(
                content="a body", topic="t",
            )
        assert passed is True
        assert score == 1.0
        assert "self-consistency-skipped" in reason
