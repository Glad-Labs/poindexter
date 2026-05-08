"""Unit tests for ``services/jobs/detect_duplicate_posts.py``.

Pool mocked. Focus: the word-overlap math, threshold boundaries,
short-title exclusion, and Gitea fan-out.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.detect_duplicate_posts import DetectDuplicatePostsJob


def _make_pool(rows: list[dict] | None = None, raises: BaseException | None = None) -> Any:
    conn = AsyncMock()
    if raises is not None:
        conn.fetch = AsyncMock(side_effect=raises)
    else:
        conn.fetch = AsyncMock(return_value=rows or [])
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


class TestContract:
    def test_has_required_attrs(self):
        job = DetectDuplicatePostsJob()
        assert job.name == "detect_duplicate_posts"
        assert job.schedule == "every 24 hours"
        assert job.idempotent is True


class TestConfigValidation:
    @pytest.mark.asyncio
    async def test_rejects_zero_or_negative_threshold(self):
        pool = _make_pool([])
        job = DetectDuplicatePostsJob()
        result = await job.run(pool, {"overlap_threshold": 0})
        assert result.ok is False
        assert "overlap_threshold" in result.detail

    @pytest.mark.asyncio
    async def test_rejects_threshold_above_one(self):
        pool = _make_pool([])
        job = DetectDuplicatePostsJob()
        result = await job.run(pool, {"overlap_threshold": 1.5})
        assert result.ok is False


class TestRun:
    @pytest.mark.asyncio
    async def test_fewer_than_two_posts_is_ok(self):
        pool = _make_pool([{"id": "p1", "title": "Only One"}])
        job = DetectDuplicatePostsJob()
        result = await job.run(pool, {"file_gitea_issue": False})
        assert result.ok is True
        assert result.changes_made == 0

    @pytest.mark.asyncio
    async def test_dissimilar_titles_not_flagged(self):
        pool = _make_pool([
            {"id": "p1", "title": "intro to rust programming language"},
            {"id": "p2", "title": "python async io tutorial"},
        ])
        job = DetectDuplicatePostsJob()
        result = await job.run(pool, {"file_gitea_issue": False})
        assert result.ok is True
        assert result.changes_made == 0

    @pytest.mark.asyncio
    async def test_near_identical_titles_flagged(self):
        pool = _make_pool([
            {"id": "p1", "title": "getting started with postgres full text search"},
            {"id": "p2", "title": "getting started with postgres full text indexing"},
        ])
        job = DetectDuplicatePostsJob()

        with patch(
            "services.jobs.detect_duplicate_posts.emit_finding",
            new=MagicMock(),
        ) as mock_gitea:
            result = await job.run(pool, {"overlap_threshold": 0.7})

        assert result.ok is True
        assert result.changes_made == 1
        assert result.metrics["duplicate_pairs"] == 1
        mock_gitea.assert_called_once()

    @pytest.mark.asyncio
    async def test_short_titles_skipped(self):
        """Titles under min_words are ineligible regardless of overlap."""
        pool = _make_pool([
            {"id": "p1", "title": "AI News"},          # 2 words
            {"id": "p2", "title": "AI News Update"},   # 3 words
        ])
        job = DetectDuplicatePostsJob()
        result = await job.run(
            pool,
            {"min_words": 4, "file_gitea_issue": False},
        )
        assert result.ok is True
        assert result.changes_made == 0

    @pytest.mark.asyncio
    async def test_threshold_is_strict_greater_than(self):
        """overlap == threshold should NOT flag (boundary behavior)."""
        # 4 words each, 2 overlap → ratio = 0.5 exactly
        pool = _make_pool([
            {"id": "p1", "title": "alpha beta gamma delta"},
            {"id": "p2", "title": "alpha beta epsilon zeta"},
        ])
        job = DetectDuplicatePostsJob()
        result = await job.run(
            pool,
            {"overlap_threshold": 0.5, "file_gitea_issue": False},
        )
        # Ratio 0.5 is NOT > threshold 0.5 → not flagged.
        assert result.changes_made == 0

    @pytest.mark.asyncio
    async def test_max_pairs_reported_caps_gitea_body(self):
        """If many duplicates, only max_pairs_reported appear in the body."""
        # Build 5 titles that all overlap heavily → C(5,2)=10 pairs
        posts = [
            {"id": f"p{i}", "title": f"machine learning pipeline tutorial part {i}"}
            for i in range(5)
        ]
        pool = _make_pool(posts)
        job = DetectDuplicatePostsJob()
        mock_gitea = AsyncMock(return_value=True)
        with patch(
            "services.jobs.detect_duplicate_posts.emit_finding",
            new=mock_gitea,
        ):
            result = await job.run(
                pool,
                {"overlap_threshold": 0.5, "max_pairs_reported": 3},
            )
        assert result.changes_made == 10  # all pairs still counted
        body = mock_gitea.call_args.args[1]
        # Only 3 pair bullets rendered into the issue body.
        assert body.count("\n- \"") == 3

    @pytest.mark.asyncio
    async def test_gitea_opt_out(self):
        pool = _make_pool([
            {"id": "p1", "title": "kubernetes operator development guide"},
            {"id": "p2", "title": "kubernetes operator development walkthrough"},
        ])
        job = DetectDuplicatePostsJob()
        mock_gitea = MagicMock()
        with patch(
            "services.jobs.detect_duplicate_posts.emit_finding",
            new=mock_gitea,
        ):
            await job.run(pool, {"file_gitea_issue": False})
        mock_gitea.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_not_ok(self):
        pool = _make_pool(raises=RuntimeError("connection lost"))
        job = DetectDuplicatePostsJob()
        result = await job.run(pool, {})
        assert result.ok is False
        assert "connection lost" in result.detail

    @pytest.mark.asyncio
    async def test_none_title_does_not_crash(self):
        """Defensive: DB could return title=NULL. Must not raise."""
        pool = _make_pool([
            {"id": "p1", "title": None},
            {"id": "p2", "title": "real published title here four"},
        ])
        job = DetectDuplicatePostsJob()
        result = await job.run(pool, {"file_gitea_issue": False})
        assert result.ok is True
