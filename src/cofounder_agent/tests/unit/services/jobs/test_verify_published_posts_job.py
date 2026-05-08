"""Unit tests for ``services/jobs/verify_published_posts.py``.

Pool + httpx mocked. Focus: site_url missing, no posts, healthy posts,
non-200 failures, connection errors, Gitea opt-out.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.jobs.verify_published_posts import VerifyPublishedPostsJob


def _make_pool(
    rows: list[dict] | None = None,
    fetch_raises: BaseException | None = None,
) -> Any:
    conn = AsyncMock()
    if fetch_raises is not None:
        conn.fetch = AsyncMock(side_effect=fetch_raises)
    else:
        conn.fetch = AsyncMock(return_value=rows or [])
    conn.execute = AsyncMock(return_value="INSERT 0 1")

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


class _FakeResp:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code


def _patched_client(status_map: dict[str, Any]):
    """httpx client mock. status_map: URL → int or Exception."""
    async def _get(url: str, timeout: float = 10) -> _FakeResp:
        outcome = status_map.get(url, 200)
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResp(status_code=outcome)

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(side_effect=_get)
    return client


class TestContract:
    def test_has_required_attrs(self):
        job = VerifyPublishedPostsJob()
        assert job.name == "verify_published_posts"
        assert job.schedule == "every 30 minutes"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_missing_site_url_returns_not_ok(self):
        pool, _ = _make_pool([])
        job = VerifyPublishedPostsJob()
        with patch(
            "services.jobs.verify_published_posts.site_config.get",
            side_effect=lambda k, d=None: "" if k == "site_url" else d,
        ):
            result = await job.run(pool, {})
        assert result.ok is False
        assert "site_url not configured" in result.detail

    @pytest.mark.asyncio
    async def test_no_recent_posts_is_ok(self):
        pool, _ = _make_pool([])
        job = VerifyPublishedPostsJob()
        with patch(
            "services.jobs.verify_published_posts.site_config.get",
            side_effect=lambda k, d=None: "https://gladlabs.io" if k == "site_url" else d,
        ):
            result = await job.run(pool, {"window_hours": 12})
        assert result.ok is True
        assert result.changes_made == 0
        assert "no posts published" in result.detail
        assert "12h" in result.detail

    @pytest.mark.asyncio
    async def test_all_posts_return_200(self):
        pool, _ = _make_pool([
            {"id": "p1", "title": "T1", "slug": "t1"},
            {"id": "p2", "title": "T2", "slug": "t2"},
        ])
        client = _patched_client({
            "https://gladlabs.io/posts/t1": 200,
            "https://gladlabs.io/posts/t2": 200,
        })
        with patch(
            "services.jobs.verify_published_posts.site_config.get",
            side_effect=lambda k, d=None: "https://gladlabs.io" if k == "site_url" else d,
        ), patch(
            "services.jobs.verify_published_posts.httpx.AsyncClient",
            return_value=client,
        ):
            job = VerifyPublishedPostsJob()
            result = await job.run(pool, {"file_gitea_issue": False})
        assert result.ok is True
        assert result.changes_made == 0
        assert result.metrics == {
            "posts_checked": 2, "posts_verified": 2, "posts_failed": 0,
        }

    @pytest.mark.asyncio
    async def test_404_counts_as_failure(self):
        pool, conn = _make_pool([
            {"id": "p1", "title": "Vanished", "slug": "vanished"},
        ])
        client = _patched_client({"https://gladlabs.io/posts/vanished": 404})
        with patch(
            "services.jobs.verify_published_posts.site_config.get",
            side_effect=lambda k, d=None: "https://gladlabs.io" if k == "site_url" else d,
        ), patch(
            "services.jobs.verify_published_posts.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.verify_published_posts.emit_finding",
            new=MagicMock(),
        ) as mock_gitea:
            job = VerifyPublishedPostsJob()
            result = await job.run(pool, {})
        assert result.changes_made == 1
        assert result.metrics["posts_failed"] == 1
        # audit_log insert should have fired.
        conn.execute.assert_awaited_once()
        mock_gitea.assert_called_once()

    @pytest.mark.asyncio
    async def test_connection_error_counts_as_failure(self):
        pool, _ = _make_pool([
            {"id": "p1", "title": "T", "slug": "no-dns"},
        ])
        client = _patched_client({
            "https://gladlabs.io/posts/no-dns": httpx.ConnectError("dns fail"),
        })
        with patch(
            "services.jobs.verify_published_posts.site_config.get",
            side_effect=lambda k, d=None: "https://gladlabs.io" if k == "site_url" else d,
        ), patch(
            "services.jobs.verify_published_posts.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.verify_published_posts.emit_finding",
            new=MagicMock(),
        ):
            job = VerifyPublishedPostsJob()
            result = await job.run(pool, {})
        assert result.metrics["posts_failed"] == 1

    @pytest.mark.asyncio
    async def test_trailing_slash_on_site_url_is_stripped(self):
        """Avoid building "https://site.io//posts/slug" when site_url ends in /."""
        pool, _ = _make_pool([{"id": "p1", "title": "T", "slug": "t"}])
        client = _patched_client({"https://site.io/posts/t": 200})
        with patch(
            "services.jobs.verify_published_posts.site_config.get",
            side_effect=lambda k, d=None: "https://site.io/" if k == "site_url" else d,
        ), patch(
            "services.jobs.verify_published_posts.httpx.AsyncClient",
            return_value=client,
        ):
            job = VerifyPublishedPostsJob()
            result = await job.run(pool, {"file_gitea_issue": False})
        assert result.metrics["posts_verified"] == 1

    @pytest.mark.asyncio
    async def test_audit_log_insert_failure_does_not_abort(self):
        """A broken audit_log shouldn't crash the whole verification run."""
        pool, conn = _make_pool([
            {"id": "p1", "title": "T", "slug": "failed"},
        ])
        conn.execute = AsyncMock(side_effect=RuntimeError("audit_log missing"))
        client = _patched_client({"https://gladlabs.io/posts/failed": 500})
        with patch(
            "services.jobs.verify_published_posts.site_config.get",
            side_effect=lambda k, d=None: "https://gladlabs.io" if k == "site_url" else d,
        ), patch(
            "services.jobs.verify_published_posts.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.verify_published_posts.emit_finding",
            new=MagicMock(),
        ):
            job = VerifyPublishedPostsJob()
            result = await job.run(pool, {})
        assert result.ok is True
        assert result.metrics["posts_failed"] == 1

    @pytest.mark.asyncio
    async def test_gitea_opt_out(self):
        pool, _ = _make_pool([{"id": "p1", "title": "T", "slug": "bad"}])
        client = _patched_client({"https://gladlabs.io/posts/bad": 503})
        mock_gitea = MagicMock()
        with patch(
            "services.jobs.verify_published_posts.site_config.get",
            side_effect=lambda k, d=None: "https://gladlabs.io" if k == "site_url" else d,
        ), patch(
            "services.jobs.verify_published_posts.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.verify_published_posts.emit_finding",
            new=mock_gitea,
        ):
            job = VerifyPublishedPostsJob()
            await job.run(pool, {"file_gitea_issue": False})
        mock_gitea.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_not_ok(self):
        pool, _ = _make_pool(fetch_raises=RuntimeError("pool closed"))
        job = VerifyPublishedPostsJob()
        with patch(
            "services.jobs.verify_published_posts.site_config.get",
            side_effect=lambda k, d=None: "https://gladlabs.io" if k == "site_url" else d,
        ):
            result = await job.run(pool, {})
        assert result.ok is False
        assert "pool closed" in result.detail
