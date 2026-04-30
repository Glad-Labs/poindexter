"""Unit tests for ``services/jobs/detect_anomalies.py``.

Covers the z-score math, audit_log insertion, and Gitea-issue gating
(only fires when >= issue_threshold anomalies are present in one cycle).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.detect_anomalies import DetectAnomaliesJob


def _make_pool(recent_values, hist_stats):
    """Pool whose fetchval returns recent values in order, fetchrow
    returns historical {mean, stddev} dicts in order."""
    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=recent_values)
    pool.fetchrow = AsyncMock(side_effect=hist_stats)
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    return pool


@pytest.mark.unit
class TestDetectAnomaliesJobMetadata:
    def test_name(self):
        assert DetectAnomaliesJob.name == "detect_anomalies"

    def test_idempotent(self):
        assert DetectAnomaliesJob.idempotent is True


@pytest.mark.unit
@pytest.mark.asyncio
class TestDetectAnomaliesJobRun:
    async def test_no_anomalies_returns_normal_range(self):
        # All four metrics sit right at their mean → z_score = 0.
        pool = _make_pool(
            recent_values=[0.05, 80.0, 1.0, 3],
            hist_stats=[
                {"mean": 0.05, "stddev": 0.02},
                {"mean": 80.0, "stddev": 5.0},
                {"mean": 1.0, "stddev": 0.5},
                {"mean": 3, "stddev": 1.0},
            ],
        )
        result = await DetectAnomaliesJob().run(pool, {})
        assert result.ok is True
        assert result.changes_made == 0
        assert "normal range" in result.detail

    async def test_single_spike_does_not_file_issue(self):
        """One metric spiking shouldn't fire a Gitea issue — too noisy."""
        pool = _make_pool(
            recent_values=[0.5, 80.0, 1.0, 3],  # task_failure_rate huge spike
            hist_stats=[
                {"mean": 0.05, "stddev": 0.02},   # z ≈ 22.5, definitely >2
                {"mean": 80.0, "stddev": 5.0},
                {"mean": 1.0, "stddev": 0.5},
                {"mean": 3, "stddev": 1.0},
            ],
        )
        with patch("utils.gitea_issues.create_gitea_issue",
                   new=AsyncMock(return_value=True)) as gitea_mock:
            result = await DetectAnomaliesJob().run(pool, {})
        assert result.ok is True
        assert result.changes_made == 1
        gitea_mock.assert_not_awaited()
        # Audit log row was inserted.
        pool.execute.assert_awaited_once()

    async def test_two_anomalies_file_gitea_issue(self):
        pool = _make_pool(
            recent_values=[0.5, 20.0, 1.0, 3],  # 2 spikes: failure_rate + quality_score drop
            hist_stats=[
                {"mean": 0.05, "stddev": 0.02},
                {"mean": 80.0, "stddev": 5.0},   # z ≈ -12, definitely >|2|
                {"mean": 1.0, "stddev": 0.5},
                {"mean": 3, "stddev": 1.0},
            ],
        )
        with patch("utils.gitea_issues.create_gitea_issue",
                   new=AsyncMock(return_value=True)) as gitea_mock:
            result = await DetectAnomaliesJob().run(pool, {})
        assert result.ok is True
        assert result.changes_made == 2
        gitea_mock.assert_awaited_once()
        # Title includes the count.
        title_arg = gitea_mock.await_args.args[0]
        assert "2 metrics" in title_arg

    async def test_zero_stddev_skips_metric(self):
        """If a metric has zero variance in the baseline window, it can't
        be an anomaly — skip rather than divide by zero."""
        pool = _make_pool(
            recent_values=[1.0, 80.0, 1.0, 3],
            hist_stats=[
                {"mean": 0.05, "stddev": 0},   # zero stddev → skip
                {"mean": 80.0, "stddev": 5.0},
                {"mean": 1.0, "stddev": 0.5},
                {"mean": 3, "stddev": 1.0},
            ],
        )
        result = await DetectAnomaliesJob().run(pool, {})
        assert result.ok is True
        assert result.changes_made == 0

    async def test_null_recent_or_hist_skips_metric(self):
        pool = _make_pool(
            recent_values=[None, 80.0, 1.0, 3],
            hist_stats=[
                {"mean": 0.05, "stddev": 0.02},
                None,  # no historical data
                {"mean": 1.0, "stddev": 0.5},
                {"mean": 3, "stddev": 1.0},
            ],
        )
        result = await DetectAnomaliesJob().run(pool, {})
        assert result.ok is True
        # First two metrics skipped due to None; last two are at-mean → no anomalies.
        assert result.changes_made == 0

    async def test_config_overrides_thresholds(self):
        """issue_threshold=1 means a single spike files an issue."""
        pool = _make_pool(
            recent_values=[0.5, 80.0, 1.0, 3],
            hist_stats=[
                {"mean": 0.05, "stddev": 0.02},
                {"mean": 80.0, "stddev": 5.0},
                {"mean": 1.0, "stddev": 0.5},
                {"mean": 3, "stddev": 1.0},
            ],
        )
        with patch("utils.gitea_issues.create_gitea_issue",
                   new=AsyncMock(return_value=True)) as gitea_mock:
            result = await DetectAnomaliesJob().run(pool, {"issue_threshold": 1})
        assert result.changes_made == 1
        gitea_mock.assert_awaited_once()
