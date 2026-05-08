"""Unit tests for ``services/jobs/check_published_links.py``.

DB pool and httpx client are mocked; no real HTTP or DB calls happen.
Focus: URL extraction, status-code → broken classification, internal
link skip, and Gitea-issue opt-out.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.jobs.check_published_links import CheckPublishedLinksJob


def _make_pool(rows: list[dict]) -> Any:
    """asyncpg pool whose conn.fetch returns the given rows."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


class _FakeHeadResponse:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code


def _patched_httpx_client(head_responses: dict[str, Any]):
    """Return a context manager that yields a mock httpx client.

    ``head_responses`` maps URL → either an int (status_code) or an
    Exception (to raise from head()).
    """
    async def _head(url: str, timeout: float = 8) -> _FakeHeadResponse:
        resp = head_responses.get(url, 200)
        if isinstance(resp, Exception):
            raise resp
        return _FakeHeadResponse(status_code=resp)

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.head = AsyncMock(side_effect=_head)
    return client


class TestContract:
    def test_has_required_attrs(self):
        job = CheckPublishedLinksJob()
        assert job.name == "check_published_links"
        assert job.schedule == "every 6 hours"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_no_published_posts_is_ok(self):
        pool = _make_pool([])
        job = CheckPublishedLinksJob()
        result = await job.run(pool, {"file_gitea_issue": False})
        assert result.ok is True
        assert result.changes_made == 0
        assert "no published posts" in result.detail

    @pytest.mark.asyncio
    async def test_all_urls_healthy_returns_zero_broken(self):
        pool = _make_pool([
            {"id": "p1", "title": "Post 1", "content": "see https://ok.example"},
        ])
        client = _patched_httpx_client({"https://ok.example": 200})

        with patch(
            "services.jobs.check_published_links.httpx.AsyncClient",
            return_value=client,
        ):
            job = CheckPublishedLinksJob()
            result = await job.run(pool, {"file_gitea_issue": False})

        assert result.ok is True
        assert result.changes_made == 0
        assert result.metrics["urls_checked"] == 1
        assert result.metrics["urls_broken"] == 0

    @pytest.mark.asyncio
    async def test_404_counts_as_broken(self):
        pool = _make_pool([
            {"id": "p1", "title": "Post with 404", "content": "bad: https://dead.example"},
        ])
        client = _patched_httpx_client({"https://dead.example": 404})

        with patch(
            "services.jobs.check_published_links.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.check_published_links.emit_finding",
            new=MagicMock(),
        ):
            job = CheckPublishedLinksJob()
            result = await job.run(pool, {})

        assert result.ok is True
        assert result.changes_made == 1
        assert result.metrics["urls_broken"] == 1

    @pytest.mark.asyncio
    async def test_httpx_error_counts_as_unreachable(self):
        pool = _make_pool([
            {"id": "p1", "title": "Post", "content": "broken: https://dns-fail.example"},
        ])
        client = _patched_httpx_client({
            "https://dns-fail.example": httpx.ConnectError("DNS resolution failed"),
        })

        with patch(
            "services.jobs.check_published_links.httpx.AsyncClient",
            return_value=client,
        ):
            job = CheckPublishedLinksJob()
            result = await job.run(pool, {"file_gitea_issue": False})

        assert result.ok is True
        assert result.changes_made == 1

    @pytest.mark.asyncio
    async def test_internal_links_are_skipped(self):
        pool = _make_pool([
            {
                "id": "p1",
                "title": "Post",
                "content": "external https://ok.example and https://www.gladlabs.io/foo",
            },
        ])
        client = _patched_httpx_client({"https://ok.example": 200})

        # DI seam (glad-labs-stack#330) — pass site_config via the
        # config dict; scheduler does this for production runs.
        sc_stub = MagicMock()
        sc_stub.get.side_effect = lambda k, d=None: "gladlabs.io" if k == "site_domain" else d
        with patch(
            "services.jobs.check_published_links.httpx.AsyncClient",
            return_value=client,
        ):
            job = CheckPublishedLinksJob()
            result = await job.run(
                pool, {"file_gitea_issue": False, "_site_config": sc_stub},
            )

        # Only the external one is checked.
        assert result.metrics["urls_checked"] == 1
        client.head.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_urls_per_post_cap_respected(self):
        # 15 external URLs in the body; default cap is 10.
        urls = [f"https://ok{i}.example" for i in range(15)]
        pool = _make_pool([
            {"id": "p1", "title": "Post", "content": " ".join(urls)},
        ])
        client = _patched_httpx_client(dict.fromkeys(urls, 200))

        with patch(
            "services.jobs.check_published_links.httpx.AsyncClient",
            return_value=client,
        ):
            job = CheckPublishedLinksJob()
            result = await job.run(pool, {"file_gitea_issue": False})

        assert result.metrics["urls_checked"] == 10

    @pytest.mark.asyncio
    async def test_file_gitea_issue_skipped_when_opted_out(self):
        pool = _make_pool([
            {"id": "p1", "title": "Post", "content": "bad: https://dead.example"},
        ])
        client = _patched_httpx_client({"https://dead.example": 500})
        mock_gitea = MagicMock()

        with patch(
            "services.jobs.check_published_links.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.check_published_links.emit_finding",
            new=mock_gitea,
        ):
            job = CheckPublishedLinksJob()
            await job.run(pool, {"file_gitea_issue": False})

        mock_gitea.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_not_ok(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=RuntimeError("connection lost"))
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)

        job = CheckPublishedLinksJob()
        result = await job.run(pool, {})
        assert result.ok is False
        assert "connection lost" in result.detail
