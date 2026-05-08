"""Unit tests for ``services/jobs/fix_uncategorized_posts.py``.

Pool mocked. Focus on: batch-size pass-through, default-category
lookup failure, per-post UPDATE tolerance, Gitea opt-out.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.fix_uncategorized_posts import FixUncategorizedPostsJob


def _make_pool(
    posts: list[dict] | None = None,
    default_cat: Any = "cat-tech",
    fetch_raises: BaseException | None = None,
    execute_raises: BaseException | None = None,
) -> Any:
    conn = AsyncMock()
    if fetch_raises is not None:
        conn.fetch = AsyncMock(side_effect=fetch_raises)
    else:
        conn.fetch = AsyncMock(return_value=posts or [])
    conn.fetchval = AsyncMock(return_value=default_cat)
    if execute_raises is not None:
        conn.execute = AsyncMock(side_effect=execute_raises)
    else:
        conn.execute = AsyncMock(return_value="UPDATE 1")

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


class TestContract:
    def test_has_required_attrs(self):
        job = FixUncategorizedPostsJob()
        assert job.name == "fix_uncategorized_posts"
        assert job.schedule == "every 24 hours"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_no_uncategorized_posts_is_ok(self):
        pool, _ = _make_pool([])
        job = FixUncategorizedPostsJob()
        result = await job.run(pool, {"file_gitea_issue": False})
        assert result.ok is True
        assert result.changes_made == 0
        assert "already categorized" in result.detail

    @pytest.mark.asyncio
    async def test_assigns_default_category(self):
        pool, conn = _make_pool([
            {"id": "p1", "title": "T1"},
            {"id": "p2", "title": "T2"},
            {"id": "p3", "title": "T3"},
        ])
        job = FixUncategorizedPostsJob()
        with patch(
            "services.jobs.fix_uncategorized_posts.emit_finding",
            new=MagicMock(),
        ) as mock_gitea:
            result = await job.run(pool, {})

        assert result.ok is True
        assert result.changes_made == 3
        assert conn.execute.await_count == 3
        mock_gitea.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_size_and_slug_pass_through(self):
        # Need at least one post so the code path actually reaches fetchval.
        pool, conn = _make_pool([{"id": "p1", "title": "T"}])
        job = FixUncategorizedPostsJob()
        with patch(
            "services.jobs.fix_uncategorized_posts.emit_finding",
            new=MagicMock(),
        ):
            await job.run(pool, {
                "batch_size": 25,
                "default_category_slug": "news",
                "file_gitea_issue": False,
            })
        fetch_args = conn.fetch.call_args.args
        assert fetch_args[1] == 25  # batch_size
        fetchval_args = conn.fetchval.call_args.args
        assert fetchval_args[1] == "news"

    @pytest.mark.asyncio
    async def test_missing_default_category_returns_not_ok(self):
        """If the default slug isn't in the categories table, fail loudly."""
        pool, _ = _make_pool(
            [{"id": "p1", "title": "T"}],
            default_cat=None,
        )
        job = FixUncategorizedPostsJob()
        result = await job.run(pool, {"default_category_slug": "nope"})
        assert result.ok is False
        assert "'nope'" in result.detail
        assert "not found" in result.detail

    @pytest.mark.asyncio
    async def test_per_post_update_failure_does_not_abort_batch(self):
        """One bad UPDATE shouldn't lose the rest of the batch."""
        # Side effect: 1st update OK, 2nd raises, 3rd OK
        results = [None, RuntimeError("row locked"), None]

        async def _execute_side_effect(*args: Any) -> None:
            outcome = results.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return "UPDATE 1"

        pool, conn = _make_pool([
            {"id": "p1", "title": "T1"},
            {"id": "p2", "title": "T2"},
            {"id": "p3", "title": "T3"},
        ])
        conn.execute = AsyncMock(side_effect=_execute_side_effect)

        job = FixUncategorizedPostsJob()
        with patch(
            "services.jobs.fix_uncategorized_posts.emit_finding",
            new=MagicMock(),
        ):
            result = await job.run(pool, {"file_gitea_issue": False})

        assert result.ok is True
        # 1st + 3rd succeeded; 2nd failed.
        assert result.changes_made == 2

    @pytest.mark.asyncio
    async def test_gitea_opt_out(self):
        pool, _ = _make_pool([{"id": "p1", "title": "T"}])
        job = FixUncategorizedPostsJob()
        mock_gitea = MagicMock()
        with patch(
            "services.jobs.fix_uncategorized_posts.emit_finding",
            new=mock_gitea,
        ):
            await job.run(pool, {"file_gitea_issue": False})
        mock_gitea.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_not_ok(self):
        pool, _ = _make_pool(fetch_raises=RuntimeError("pool closed"))
        job = FixUncategorizedPostsJob()
        result = await job.run(pool, {})
        assert result.ok is False
        assert "pool closed" in result.detail
