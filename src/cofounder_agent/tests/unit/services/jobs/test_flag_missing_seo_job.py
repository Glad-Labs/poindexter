"""Unit tests for ``services/jobs/flag_missing_seo.py``.

Pool mocked; Gitea call mocked. Focus on SQL arg pass-through, empty
result handling, and Gitea opt-out.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.flag_missing_seo import FlagMissingSeoJob


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
    return pool, conn


class TestContract:
    def test_has_required_attrs(self):
        job = FlagMissingSeoJob()
        assert job.name == "flag_missing_seo"
        assert job.schedule == "every 12 hours"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_zero_posts_missing_seo(self):
        pool, _ = _make_pool([])
        job = FlagMissingSeoJob()
        result = await job.run(pool, {"file_gitea_issue": False})
        assert result.ok is True
        assert result.changes_made == 0
        assert "all published posts" in result.detail.lower()

    @pytest.mark.asyncio
    async def test_flags_rows_and_returns_count(self):
        rows = [
            {"id": "p1", "title": "Post 1"},
            {"id": "p2", "title": "Post 2"},
            {"id": "p3", "title": "Post 3"},
        ]
        pool, _ = _make_pool(rows)
        job = FlagMissingSeoJob()

        with patch(
            "services.jobs.flag_missing_seo.emit_finding",
            new=MagicMock(),
        ) as mock_gitea:
            result = await job.run(pool, {"limit": 10})

        assert result.ok is True
        assert result.changes_made == 3
        assert result.metrics["posts_missing_seo"] == 3
        mock_gitea.assert_called_once()
        # emit_finding is keyword-only; the human-readable message is title=.
        title = mock_gitea.call_args.kwargs["title"]
        assert "3 posts" in title

    @pytest.mark.asyncio
    async def test_respects_limit_config(self):
        pool, conn = _make_pool([])
        job = FlagMissingSeoJob()
        await job.run(pool, {"limit": 25, "file_gitea_issue": False})
        # fetch should be called with limit=25
        call_args = conn.fetch.call_args.args
        assert call_args[1] == 25

    @pytest.mark.asyncio
    async def test_gitea_opt_out(self):
        rows = [{"id": "p1", "title": "Post 1"}]
        pool, _ = _make_pool(rows)
        job = FlagMissingSeoJob()
        mock_gitea = MagicMock()
        with patch(
            "services.jobs.flag_missing_seo.emit_finding", new=mock_gitea,
        ):
            await job.run(pool, {"file_gitea_issue": False})
        mock_gitea.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_title_handled_gracefully(self):
        """If a post row has title=None (shouldn't happen, but defensive)."""
        rows = [{"id": "p1", "title": None}]
        pool, _ = _make_pool(rows)
        job = FlagMissingSeoJob()
        with patch(
            "services.jobs.flag_missing_seo.emit_finding",
            new=MagicMock(),
        ):
            result = await job.run(pool, {})
        assert result.ok is True
        assert result.changes_made == 1

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_not_ok(self):
        pool, _ = _make_pool(raises=RuntimeError("db unreachable"))
        job = FlagMissingSeoJob()
        result = await job.run(pool, {})
        assert result.ok is False
        assert "db unreachable" in result.detail
