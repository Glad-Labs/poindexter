"""Unit tests for ``services/jobs/audit_published_quality.py``.

Pool mocked. Focus: SQL arg pass-through, finding detection against
word-count + heading signals, audit_log insert resilience, Gitea
opt-out.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.audit_published_quality import AuditPublishedQualityJob


def _make_pool(
    rows: list[dict] | None = None,
    fetch_raises: BaseException | None = None,
    execute_raises: BaseException | None = None,
) -> Any:
    """Pool whose acquire().__aenter__() returns a conn with fetch+execute stubbed."""
    conn = AsyncMock()
    if fetch_raises is not None:
        conn.fetch = AsyncMock(side_effect=fetch_raises)
    else:
        conn.fetch = AsyncMock(return_value=rows or [])
    if execute_raises is not None:
        conn.execute = AsyncMock(side_effect=execute_raises)
    else:
        conn.execute = AsyncMock(return_value="INSERT 0 1")

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


class TestContract:
    def test_has_required_attrs(self):
        job = AuditPublishedQualityJob()
        assert job.name == "audit_published_quality"
        assert job.schedule == "every 6 hours"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_no_posts_to_audit_is_ok(self):
        pool, _ = _make_pool([])
        job = AuditPublishedQualityJob()
        result = await job.run(pool, {"file_gitea_issue": False})
        assert result.ok is True
        assert result.changes_made == 0
        assert "recently audited" in result.detail

    @pytest.mark.asyncio
    async def test_high_quality_post_no_issues(self):
        # 600 words with headings → zero findings.
        content = "## Heading\n\n" + " ".join(["word"] * 600)
        pool, _ = _make_pool([
            {"id": "p1", "title": "Solid Post", "slug": "solid", "content_preview": content},
        ])
        job = AuditPublishedQualityJob()
        with patch(
            "services.jobs.audit_published_quality.emit_finding",
            new=MagicMock(),
        ) as mock_gitea:
            result = await job.run(pool, {"file_gitea_issue": True})

        assert result.ok is True
        assert result.changes_made == 0
        assert result.metrics == {"posts_audited": 1, "issues_found": 0}
        mock_gitea.assert_not_called()

    @pytest.mark.asyncio
    async def test_low_word_count_flagged(self):
        content = " ".join(["word"] * 100)  # 100 words < default 500
        pool, _ = _make_pool([
            {"id": "p1", "title": "Tiny", "slug": "tiny", "content_preview": content},
        ])
        job = AuditPublishedQualityJob()
        with patch(
            "services.jobs.audit_published_quality.emit_finding",
            new=MagicMock(),
        ) as mock_gitea:
            result = await job.run(pool, {})

        assert result.ok is True
        # Low word count + no heading → 2 findings for one post.
        assert result.metrics["issues_found"] == 2
        mock_gitea.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_headings_flagged(self):
        content = " ".join(["word"] * 800)  # enough words, zero headings
        pool, _ = _make_pool([
            {"id": "p1", "title": "Wall of Text", "slug": "wall", "content_preview": content},
        ])
        job = AuditPublishedQualityJob()
        with patch(
            "services.jobs.audit_published_quality.emit_finding",
            new=MagicMock(),
        ):
            result = await job.run(pool, {})

        assert result.metrics["issues_found"] == 1  # only heading issue

    @pytest.mark.asyncio
    async def test_html_headings_count_as_headings(self):
        """Posts rendered to HTML should not be flagged for lack of '##'."""
        content = "<h2>Title</h2>\n" + " ".join(["word"] * 800)
        pool, _ = _make_pool([
            {"id": "p1", "title": "Rendered", "slug": "rendered", "content_preview": content},
        ])
        job = AuditPublishedQualityJob()
        with patch(
            "services.jobs.audit_published_quality.emit_finding",
            new=MagicMock(),
        ):
            result = await job.run(pool, {"file_gitea_issue": False})

        assert result.metrics["issues_found"] == 0

    @pytest.mark.asyncio
    async def test_batch_size_and_cooldown_threaded_into_query(self):
        pool, conn = _make_pool([])
        job = AuditPublishedQualityJob()
        await job.run(pool, {"batch_size": 11, "cooldown_days": 3, "file_gitea_issue": False})
        args = conn.fetch.call_args.args
        # fetch(query, cooldown_days, batch_size)
        assert args[1] == 3  # cooldown_days
        assert args[2] == 11  # batch_size

    @pytest.mark.asyncio
    async def test_min_words_config_respected(self):
        content = " ".join(["word"] * 300)  # 300 words
        pool, _ = _make_pool([
            {"id": "p1", "title": "Medium", "slug": "m", "content_preview": content},
        ])
        job = AuditPublishedQualityJob()
        # min_words=200 → shouldn't flag word-count. But no headings → 1 issue.
        with patch(
            "services.jobs.audit_published_quality.emit_finding",
            new=MagicMock(),
        ):
            result = await job.run(
                pool,
                {"min_words": 200, "file_gitea_issue": False},
            )
        assert result.metrics["issues_found"] == 1  # only heading

    @pytest.mark.asyncio
    async def test_audit_log_failure_does_not_abort(self):
        """A bad audit_log insert shouldn't lose the entire finding run."""
        content = " ".join(["word"] * 100)
        pool, _ = _make_pool(
            [{"id": "p1", "title": "Short", "slug": "s", "content_preview": content}],
            execute_raises=RuntimeError("table missing"),
        )
        job = AuditPublishedQualityJob()
        with patch(
            "services.jobs.audit_published_quality.emit_finding",
            new=MagicMock(),
        ):
            result = await job.run(pool, {})

        assert result.ok is True
        assert result.metrics["issues_found"] == 2  # still surfaced

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_not_ok(self):
        pool, _ = _make_pool(fetch_raises=RuntimeError("pool closed"))
        job = AuditPublishedQualityJob()
        result = await job.run(pool, {})
        assert result.ok is False
        assert "pool closed" in result.detail

    @pytest.mark.asyncio
    async def test_none_content_does_not_crash(self):
        pool, _ = _make_pool([
            {"id": "p1", "title": "Empty", "slug": "e", "content_preview": None},
        ])
        job = AuditPublishedQualityJob()
        with patch(
            "services.jobs.audit_published_quality.emit_finding",
            new=MagicMock(),
        ):
            result = await job.run(pool, {})
        # 0 words + no headings → 2 findings, no crash.
        assert result.ok is True
