"""Unit tests for ``services/jobs/fix_missing_seo.py``.

Pool mocked. Focus on: limit pass-through, missing/partial SEO rows,
Gitea issue opt-out, and query failure handling.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services.jobs.fix_missing_seo import FixMissingSeoJob


def _make_pool(
    posts: list[dict] | None = None,
    fetch_raises: BaseException | None = None,
    execute_raises: BaseException | None = None,
) -> tuple[Any, Any]:
    conn = AsyncMock()
    if fetch_raises is not None:
        conn.fetch = AsyncMock(side_effect=fetch_raises)
    else:
        conn.fetch = AsyncMock(return_value=posts or [])
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
        job = FixMissingSeoJob()
        assert job.name == "fix_missing_seo"
        assert job.schedule == "every 24 hours"
        assert job.idempotent is True


@pytest.mark.asyncio
class TestRun:
    async def test_no_missing_seo_posts_is_ok(self):
        pool, _ = _make_pool([])
        job = FixMissingSeoJob()

        result = await job.run(pool, {"file_gitea_issue": False})

        assert result.ok is True
        assert result.changes_made == 0
        assert "already have SEO metadata" in result.detail

    async def test_fills_missing_seo_fields(self):
        pool, conn = _make_pool([
            {
                "id": "p1",
                "title": "AI content pipeline",
                "content": "This post explains how the pipeline works.",
                "seo_title": None,
                "seo_description": None,
                "seo_keywords": None,
            }
        ])
        job = FixMissingSeoJob()
        with patch(
            "poindexter.services.jobs.fix_missing_seo.emit_finding",
            new=MagicMock(),
        ) as mock_emitter:
            result = await job.run(pool, {})

        assert result.ok is True
        assert result.changes_made == 1
        assert conn.execute.await_count == 1
        mock_emitter.assert_called_once()

    async def test_preserves_existing_fields_and_updates_only_missing(self):
        pool, conn = _make_pool([
            {
                "id": "p2",
                "title": "AI content pipeline",
                "content": "This post explains how the pipeline works.",
                "seo_title": "Existing SEO title",
                "seo_description": None,
                "seo_keywords": "ai, content",
            }
        ])
        with patch(
            "poindexter.services.jobs.fix_missing_seo.emit_finding",
            new=MagicMock(),
        ):
            result = await FixMissingSeoJob().run(pool, {})

        assert result.ok is True
        assert result.changes_made == 1
        assert conn.execute.await_count == 1
        executed_sql, title, description, keywords, post_id = conn.execute.call_args.args
        assert title == "Existing SEO title"
        assert description != ""
        assert keywords == "ai, content"
        assert post_id == "p2"

    async def test_file_issue_opt_out(self):
        pool, conn = _make_pool([
            {
                "id": "p3",
                "title": "Pipeline SEO",
                "content": "Short content.",
                "seo_title": None,
                "seo_description": None,
                "seo_keywords": None,
            }
        ])
        mock_emitter = MagicMock()
        with patch(
            "poindexter.services.jobs.fix_missing_seo.emit_finding",
            new=mock_emitter,
        ):
            result = await FixMissingSeoJob().run(pool, {"file_gitea_issue": False})

        assert result.ok is True
        assert result.changes_made == 1
        mock_emitter.assert_not_called()

    async def test_fetch_failure_returns_not_ok(self):
        pool, _ = _make_pool(fetch_raises=RuntimeError("pool closed"))
        job = FixMissingSeoJob()

        result = await job.run(pool, {})

        assert result.ok is False
        assert "pool closed" in result.detail


@pytest.mark.asyncio
class TestDevDiaryIsNotExcluded:
    """The default used to be ``excluded_templates=["dev_diary"]``, which meant
    the one job that backfills missing SEO metadata skipped the ONLY posts
    missing it — all 39 offenders were dev_diary. Its sibling flag job carried
    the same exclusion, so nothing reported the backlog either (last
    ``missing_seo`` finding: 2026-06-05, while the backlog grew to 39).
    """

    async def _excluded_arg(self, config: dict) -> list[str]:
        pool, conn = _make_pool([])
        await FixMissingSeoJob().run(pool, {"file_gitea_issue": False, **config})
        # run() passes (sql, limit, excluded_templates)
        args = conn.fetch.await_args.args
        return list(args[2])

    async def test_default_excludes_nothing(self):
        assert await self._excluded_arg({}) == []

    async def test_dev_diary_is_not_excluded_by_default(self):
        assert "dev_diary" not in await self._excluded_arg({})

    async def test_operators_can_still_exclude_explicitly(self):
        assert await self._excluded_arg(
            {"excluded_templates": ["dev_diary", "scratch"]},
        ) == ["dev_diary", "scratch"]


class _ReleasableConn:
    """A connection that behaves like asyncpg's: unusable once released.

    The old fake stayed usable after ``async with pool.acquire()`` exited, so
    the job's writes on a released connection passed every test while failing
    every run on prod (poindexter#1079).
    """

    def __init__(self, rows):
        self.rows = rows
        self.released = False
        self.updates: list[tuple] = []

    async def fetch(self, *a):
        self._check()
        return self.rows

    async def execute(self, sql, *args):
        self._check()
        self.updates.append(args)
        return "UPDATE 1"

    def _check(self):
        if self.released:
            raise RuntimeError(
                "cannot call Connection.execute(): connection has been released back to the pool"
            )


class _RealisticPool:
    def __init__(self, rows):
        self.rows = rows
        self.conns: list[_ReleasableConn] = []

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                conn = _ReleasableConn(pool.rows)
                pool.conns.append(conn)
                self.conn = conn
                return conn

            async def __aexit__(self, *exc):
                self.conn.released = True
                return False

        return _Ctx()


_MISSING = {
    "id": "p1", "title": "AI content pipeline",
    "content": "This post explains how the pipeline works.",
    "seo_title": None, "seo_description": None, "seo_keywords": None,
}


@pytest.mark.asyncio
class TestReleasedConnection:
    async def test_updates_run_on_a_live_connection(self):
        pool = _RealisticPool([dict(_MISSING), dict(_MISSING, id="p2")])
        with patch("poindexter.services.jobs.fix_missing_seo.emit_finding", new=MagicMock()):
            result = await FixMissingSeoJob().run(pool, {})
        assert result.ok is True
        assert result.changes_made == 2
        assert sum(len(c.updates) for c in pool.conns) == 2

    async def test_every_update_failing_is_not_ok(self):
        """'0 of N updated' must not report success."""
        pool, _ = _make_pool([dict(_MISSING)], execute_raises=RuntimeError("boom"))
        result = await FixMissingSeoJob().run(pool, {"file_gitea_issue": False})
        assert result.ok is False
        assert result.changes_made == 0
        assert "boom" in result.detail
        assert result.metrics["posts_failed"] == 1
