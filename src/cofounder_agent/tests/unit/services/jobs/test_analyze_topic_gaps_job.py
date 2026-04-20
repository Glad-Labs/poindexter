"""Unit tests for ``services/jobs/analyze_topic_gaps.py``.

Pool mocked. Covers each finding class (empty/low/stale), threshold
config pass-through, Gitea opt-in/out, and fetch failure.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.analyze_topic_gaps import AnalyzeTopicGapsJob


def _make_pool(
    categories: list[dict] | None = None,
    stale: list[dict] | None = None,
    fetch_raises: BaseException | None = None,
) -> Any:
    """Return a pool whose fetch routes by query content:
    - contains 'HAVING' → stale_rows
    - otherwise         → categories
    """
    conn = AsyncMock()

    async def _fetch(query: str, *args: Any) -> list[dict]:
        if fetch_raises is not None:
            raise fetch_raises
        if "HAVING" in query:
            return stale or []
        return categories or []

    conn.fetch = AsyncMock(side_effect=_fetch)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


class TestContract:
    def test_has_required_attrs(self):
        job = AnalyzeTopicGapsJob()
        assert job.name == "analyze_topic_gaps"
        assert job.schedule == "every 24 hours"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_all_healthy_returns_zero_suggestions(self):
        pool, _ = _make_pool(
            categories=[
                {"name": "AI", "posts": 10},
                {"name": "Hardware", "posts": 7},
            ],
            stale=[],
        )
        job = AnalyzeTopicGapsJob()
        result = await job.run(pool, {"file_gitea_issue": False})
        assert result.ok is True
        assert result.changes_made == 0
        assert "all categories healthy" in result.detail
        assert result.metrics == {
            "empty_categories": 0,
            "low_coverage_categories": 0,
            "stale_categories": 0,
        }

    @pytest.mark.asyncio
    async def test_empty_category_flagged(self):
        pool, _ = _make_pool(
            categories=[
                {"name": "Abandoned", "posts": 0},
                {"name": "Active", "posts": 10},
            ],
            stale=[],
        )
        job = AnalyzeTopicGapsJob()
        with patch(
            "services.jobs.analyze_topic_gaps.create_gitea_issue",
            new=AsyncMock(return_value=True),
        ) as mock_gitea:
            result = await job.run(pool, {})
        assert result.metrics["empty_categories"] == 1
        assert result.changes_made == 1
        mock_gitea.assert_awaited_once()
        # Issue title should include the empty count.
        call_args = mock_gitea.call_args.args
        assert "1 empty" in call_args[0]

    @pytest.mark.asyncio
    async def test_low_coverage_flagged(self):
        pool, _ = _make_pool(
            categories=[
                {"name": "Thin", "posts": 2},
                {"name": "Thick", "posts": 50},
            ],
            stale=[],
        )
        job = AnalyzeTopicGapsJob()
        with patch(
            "services.jobs.analyze_topic_gaps.create_gitea_issue",
            new=AsyncMock(return_value=True),
        ):
            result = await job.run(pool, {"low_threshold": 5})
        assert result.metrics["low_coverage_categories"] == 1
        assert "Thin (2)" in result.detail or result.changes_made == 1

    @pytest.mark.asyncio
    async def test_stale_category_flagged(self):
        pool, _ = _make_pool(
            categories=[{"name": "OldNews", "posts": 10}],
            stale=[
                {"name": "OldNews", "latest": datetime(2025, 1, 1, tzinfo=timezone.utc)},
            ],
        )
        job = AnalyzeTopicGapsJob()
        with patch(
            "services.jobs.analyze_topic_gaps.create_gitea_issue",
            new=AsyncMock(return_value=True),
        ):
            result = await job.run(pool, {})
        assert result.metrics["stale_categories"] == 1

    @pytest.mark.asyncio
    async def test_low_threshold_config_honored(self):
        """low_threshold=3 → 2 posts is low, 3 is NOT low (strictly <)."""
        pool, _ = _make_pool(
            categories=[
                {"name": "A", "posts": 2},
                {"name": "B", "posts": 3},
                {"name": "C", "posts": 4},
            ],
            stale=[],
        )
        job = AnalyzeTopicGapsJob()
        with patch(
            "services.jobs.analyze_topic_gaps.create_gitea_issue",
            new=AsyncMock(return_value=True),
        ):
            result = await job.run(pool, {"low_threshold": 3})
        # Only A (posts=2) is below threshold 3.
        assert result.metrics["low_coverage_categories"] == 1

    @pytest.mark.asyncio
    async def test_stale_days_passed_to_query(self):
        pool, conn = _make_pool(categories=[], stale=[])
        job = AnalyzeTopicGapsJob()
        await job.run(pool, {"stale_days": 30, "file_gitea_issue": False})
        # Two fetch calls; second = stale query with stale_days as $1.
        second_call = conn.fetch.await_args_list[1]
        assert second_call.args[1] == 30

    @pytest.mark.asyncio
    async def test_gitea_opt_out(self):
        pool, _ = _make_pool(
            categories=[{"name": "Abandoned", "posts": 0}],
            stale=[],
        )
        job = AnalyzeTopicGapsJob()
        mock_gitea = AsyncMock(return_value=False)
        with patch(
            "services.jobs.analyze_topic_gaps.create_gitea_issue", new=mock_gitea,
        ):
            await job.run(pool, {"file_gitea_issue": False})
        mock_gitea.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_not_ok(self):
        pool, _ = _make_pool(fetch_raises=RuntimeError("pool closed"))
        job = AnalyzeTopicGapsJob()
        result = await job.run(pool, {})
        assert result.ok is False
        assert "pool closed" in result.detail

    @pytest.mark.asyncio
    async def test_mixed_findings_aggregate_in_detail(self):
        pool, _ = _make_pool(
            categories=[
                {"name": "Empty1", "posts": 0},
                {"name": "Empty2", "posts": 0},
                {"name": "Low", "posts": 3},
            ],
            stale=[
                {"name": "Old", "latest": datetime(2025, 1, 1, tzinfo=timezone.utc)},
            ],
        )
        job = AnalyzeTopicGapsJob()
        with patch(
            "services.jobs.analyze_topic_gaps.create_gitea_issue",
            new=AsyncMock(return_value=True),
        ):
            result = await job.run(pool, {})
        assert result.metrics == {
            "empty_categories": 2,
            "low_coverage_categories": 1,
            "stale_categories": 1,
        }
        # 3 suggestion classes, all triggered.
        assert result.changes_made == 3
