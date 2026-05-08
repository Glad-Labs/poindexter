"""Unit tests for ``services/jobs/fix_broken_internal_links.py``.

Pool mocked. Focus on the regex stripping (the meat of the job), the
published-vs-linked set diff, and update propagation.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.fix_broken_internal_links import (
    FixBrokenInternalLinksJob,
    _strip_slug_references,
)


def _make_pool(
    pub_rows: list[dict] | None = None,
    candidate_rows: list[dict] | None = None,
    fetch_raises: BaseException | None = None,
    execute_raises: BaseException | None = None,
) -> Any:
    """Pool whose fetch returns the right rowset for each query."""
    call_count = {"n": 0}

    async def _fetch(query: str, *args: Any) -> list[dict]:
        if fetch_raises is not None:
            raise fetch_raises
        call_count["n"] += 1
        # First call = SELECT slug (published), second = candidates.
        if "SELECT slug" in query:
            return pub_rows or []
        return candidate_rows or []

    conn = AsyncMock()
    conn.fetch = AsyncMock(side_effect=_fetch)
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


class TestStripSlugReferences:
    """Unit-level regex checks — the job depends on these being correct."""

    def test_markdown_link_replaced_with_anchor_text(self):
        content = "See also [our post](/posts/old-slug) for details."
        assert _strip_slug_references(content, "old-slug") == (
            "See also our post for details."
        )

    def test_html_anchor_replaced_with_label(self):
        content = 'Check <a href="/posts/old-slug" class="x">this</a> out.'
        assert _strip_slug_references(content, "old-slug") == "Check this out."

    def test_sidebar_li_removed_wholesale(self):
        content = (
            '<ul><li class="r"><a href="/posts/old-slug">Related</a></li></ul>'
        )
        assert _strip_slug_references(content, "old-slug") == "<ul></ul>"

    def test_other_slug_untouched(self):
        content = "[still good](/posts/live-slug) + [gone](/posts/old-slug)"
        out = _strip_slug_references(content, "old-slug")
        assert "[still good](/posts/live-slug)" in out
        assert "old-slug" not in out

    def test_multiple_occurrences_all_cleaned(self):
        content = (
            "[a](/posts/old-slug) and [b](/posts/old-slug) again"
        )
        assert _strip_slug_references(content, "old-slug") == "a and b again"


class TestContract:
    def test_has_required_attrs(self):
        job = FixBrokenInternalLinksJob()
        assert job.name == "fix_broken_internal_links"
        assert job.schedule == "every 24 hours"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_no_candidates_returns_ok(self):
        pool, _ = _make_pool(
            pub_rows=[{"slug": "live"}],
            candidate_rows=[],
        )
        job = FixBrokenInternalLinksJob()
        result = await job.run(pool, {"file_gitea_issue": False})
        assert result.ok is True
        assert result.changes_made == 0
        assert "no posts contain" in result.detail

    @pytest.mark.asyncio
    async def test_all_links_valid_nothing_rewritten(self):
        pool, conn = _make_pool(
            pub_rows=[{"slug": "live-1"}, {"slug": "live-2"}],
            candidate_rows=[
                {"id": "p1", "title": "t", "content": "[a](/posts/live-1) [b](/posts/live-2)"},
            ],
        )
        job = FixBrokenInternalLinksJob()
        result = await job.run(pool, {"file_gitea_issue": False})
        assert result.ok is True
        assert result.changes_made == 0
        # No UPDATE should fire when nothing needs cleanup.
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stale_link_triggers_update(self):
        pool, conn = _make_pool(
            pub_rows=[{"slug": "live"}],
            candidate_rows=[
                {
                    "id": "p1",
                    "title": "t",
                    "content": "Check [gone](/posts/deleted) and [still](/posts/live).",
                },
            ],
        )
        job = FixBrokenInternalLinksJob()
        with patch(
            "services.jobs.fix_broken_internal_links.emit_finding",
            new=MagicMock(),
        ) as mock_gitea:
            result = await job.run(pool, {})

        assert result.ok is True
        assert result.changes_made == 1
        conn.execute.assert_awaited_once()
        # The UPDATE should be called with the stripped content.
        update_args = conn.execute.call_args.args
        assert "gone" in update_args[1]  # anchor text preserved
        assert "/posts/deleted" not in update_args[1]  # broken link gone
        mock_gitea.assert_called_once()

    @pytest.mark.asyncio
    async def test_gitea_opt_out(self):
        pool, _ = _make_pool(
            pub_rows=[{"slug": "live"}],
            candidate_rows=[
                {"id": "p1", "title": "t", "content": "[gone](/posts/deleted)"},
            ],
        )
        job = FixBrokenInternalLinksJob()
        mock_gitea = MagicMock()
        with patch(
            "services.jobs.fix_broken_internal_links.emit_finding",
            new=mock_gitea,
        ):
            await job.run(pool, {"file_gitea_issue": False})
        mock_gitea.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_failure_logs_but_does_not_abort(self):
        """If UPDATE for one post fails, the others should still be attempted."""
        pool, _ = _make_pool(
            pub_rows=[{"slug": "live"}],
            candidate_rows=[
                {"id": "p1", "title": "t", "content": "[x](/posts/deleted)"},
                {"id": "p2", "title": "t", "content": "[y](/posts/deleted)"},
            ],
            execute_raises=RuntimeError("row locked"),
        )
        job = FixBrokenInternalLinksJob()
        with patch(
            "services.jobs.fix_broken_internal_links.emit_finding",
            new=MagicMock(),
        ):
            result = await job.run(pool, {"file_gitea_issue": False})
        # Both updates failed, so 0 fixed, but the job still completes
        # cleanly — a bad DB state shouldn't mask the job's purpose.
        assert result.ok is True
        assert result.changes_made == 0

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_not_ok(self):
        pool, _ = _make_pool(fetch_raises=RuntimeError("pool closed"))
        job = FixBrokenInternalLinksJob()
        result = await job.run(pool, {})
        assert result.ok is False
        assert "pool closed" in result.detail

    @pytest.mark.asyncio
    async def test_none_content_does_not_crash(self):
        pool, _ = _make_pool(
            pub_rows=[{"slug": "live"}],
            candidate_rows=[
                {"id": "p1", "title": "t", "content": None},
            ],
        )
        job = FixBrokenInternalLinksJob()
        result = await job.run(pool, {"file_gitea_issue": False})
        assert result.ok is True
        # None content has no links to fix.
        assert result.changes_made == 0
