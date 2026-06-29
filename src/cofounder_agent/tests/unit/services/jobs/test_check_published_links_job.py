"""Unit tests for ``services/jobs/check_published_links.py``.

DB pool and httpx client are mocked; no real HTTP or DB calls happen.
Focus: URL extraction, status-code → broken classification, internal
link skip, Gitea-issue opt-out, the crawler User-Agent (root-cause fix
for WAF 403 false positives), the access-restricted (401/403/429) skip,
and the 405-HEAD → GET fallback.
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
    def __init__(self, status_code: int = 200, headers: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}


def _patched_httpx_client(head_responses: dict[str, Any]):
    """Return a context manager that yields a mock httpx client.

    ``head_responses`` maps URL → an int (status_code), a
    ``_FakeHeadResponse`` (to control headers), or an Exception (to raise).
    """
    async def _head(url: str, timeout: float = 8) -> _FakeHeadResponse:
        resp = head_responses.get(url, 200)
        if isinstance(resp, Exception):
            raise resp
        if isinstance(resp, _FakeHeadResponse):
            return resp
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
    async def test_edge_challenge_not_counted_as_broken(self):
        """A destination behind Cloudflare that challenges our HEAD
        (403 + cf-mitigated) is reachable to real users — not a broken link."""
        pool = _make_pool([
            {"id": "p1", "title": "Post", "content": "cf-site: https://cf.example"},
        ])
        client = _patched_httpx_client({
            "https://cf.example": _FakeHeadResponse(403, {"cf-mitigated": "challenge"}),
        })
        finds = MagicMock()
        with patch(
            "services.jobs.check_published_links.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.check_published_links.emit_finding", new=finds,
        ):
            job = CheckPublishedLinksJob()
            result = await job.run(pool, {})

        assert result.metrics["urls_broken"] == 0
        assert result.metrics["urls_edge_challenged"] == 1
        assert result.changes_made == 0
        # No broken-link finding should be filed for a CDN challenge.
        finds.assert_not_called()

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


class TestAccessRestricted:
    """401/403/429 = the host is up but refusing our automated/anonymous
    probe (bot-block, auth-gate, rate-limit). The link works for a human
    reader, so it must NOT be filed as broken."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [401, 403, 429])
    async def test_access_restricted_codes_not_broken(self, code):
        pool = _make_pool([
            {"id": "p1", "title": "Post", "content": "ref: https://gated.example"},
        ])
        client = _patched_httpx_client({"https://gated.example": code})
        finds = MagicMock()
        with patch(
            "services.jobs.check_published_links.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.check_published_links.emit_finding", new=finds,
        ):
            job = CheckPublishedLinksJob()
            result = await job.run(pool, {})

        assert result.metrics["urls_broken"] == 0
        assert result.metrics["urls_access_restricted"] == 1
        assert result.changes_made == 0
        finds.assert_not_called()  # no broken-link finding for an access-gate

    @pytest.mark.asyncio
    async def test_skip_codes_are_operator_configurable(self):
        """Tightening the policy: an operator who drops 403 from the skip set
        gets 403 counted as broken again (only 429 stays access-restricted)."""
        pool = _make_pool([
            {"id": "p1", "title": "Post", "content": "ref: https://403.example"},
        ])
        client = _patched_httpx_client({"https://403.example": 403})
        sc = MagicMock()
        sc.get.side_effect = lambda k, d=None: (
            "429" if k == "link_check_skip_status_codes" else d
        )
        with patch(
            "services.jobs.check_published_links.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.check_published_links.emit_finding", new=MagicMock(),
        ):
            job = CheckPublishedLinksJob()
            result = await job.run(pool, {"_site_config": sc})

        assert result.metrics["urls_broken"] == 1
        assert result.metrics["urls_access_restricted"] == 0

    @pytest.mark.asyncio
    async def test_500_still_counts_as_broken(self):
        """Genuine server errors are NOT access-restricted — still broken."""
        pool = _make_pool([
            {"id": "p1", "title": "Post", "content": "ref: https://err.example"},
        ])
        client = _patched_httpx_client({"https://err.example": 500})
        with patch(
            "services.jobs.check_published_links.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.check_published_links.emit_finding", new=MagicMock(),
        ):
            job = CheckPublishedLinksJob()
            result = await job.run(pool, {})

        assert result.metrics["urls_broken"] == 1
        assert result.metrics["urls_access_restricted"] == 0


class TestUserAgent:
    """The root-cause fix: send a real crawler UA, not the default httpx one
    that WAFs (Wikipedia) 403 into a false positive."""

    @pytest.mark.asyncio
    async def test_sends_browser_ish_user_agent(self):
        pool = _make_pool([
            {"id": "p1", "title": "Post", "content": "ext: https://ok.example"},
        ])
        client = _patched_httpx_client({"https://ok.example": 200})
        mock_cls = MagicMock(return_value=client)
        with patch(
            "services.jobs.check_published_links.httpx.AsyncClient", mock_cls,
        ):
            job = CheckPublishedLinksJob()
            await job.run(pool, {"file_gitea_issue": False})

        ua = mock_cls.call_args.kwargs["headers"]["User-Agent"]
        # No _site_config → contact-less form (OSS leak guard).
        assert ua == "Mozilla/5.0 (compatible; PoindexterLinkCheck/1.0)"

    @pytest.mark.asyncio
    async def test_user_agent_includes_contact_when_configured(self):
        pool = _make_pool([
            {"id": "p1", "title": "Post", "content": "ext: https://ok.example"},
        ])
        client = _patched_httpx_client({"https://ok.example": 200})
        sc = MagicMock()
        sc.get.side_effect = lambda k, d=None: (
            "https://gladlabs.io/bot" if k == "crawler_contact_url" else d
        )
        mock_cls = MagicMock(return_value=client)
        with patch(
            "services.jobs.check_published_links.httpx.AsyncClient", mock_cls,
        ):
            job = CheckPublishedLinksJob()
            await job.run(pool, {"file_gitea_issue": False, "_site_config": sc})

        ua = mock_cls.call_args.kwargs["headers"]["User-Agent"]
        assert ua == (
            "Mozilla/5.0 (compatible; PoindexterLinkCheck/1.0; "
            "+https://gladlabs.io/bot)"
        )


class TestHeadToGetFallback:
    """Some servers 405 a HEAD but serve GET fine — retry once before judging."""

    @pytest.mark.asyncio
    async def test_405_head_retries_with_get_and_is_not_broken(self):
        pool = _make_pool([
            {"id": "p1", "title": "Post", "content": "ext: https://head405.example"},
        ])
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.head = AsyncMock(return_value=_FakeHeadResponse(405))
        client.get = AsyncMock(return_value=_FakeHeadResponse(200))
        with patch(
            "services.jobs.check_published_links.httpx.AsyncClient",
            return_value=client,
        ):
            job = CheckPublishedLinksJob()
            result = await job.run(pool, {"file_gitea_issue": False})

        client.get.assert_awaited_once()  # GET fallback fired
        assert result.metrics["urls_broken"] == 0

    @pytest.mark.asyncio
    async def test_405_then_get_404_counts_as_broken(self):
        pool = _make_pool([
            {"id": "p1", "title": "Post", "content": "ext: https://gone.example"},
        ])
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.head = AsyncMock(return_value=_FakeHeadResponse(405))
        client.get = AsyncMock(return_value=_FakeHeadResponse(404))
        with patch(
            "services.jobs.check_published_links.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.check_published_links.emit_finding", new=MagicMock(),
        ):
            job = CheckPublishedLinksJob()
            result = await job.run(pool, {})

        assert result.metrics["urls_broken"] == 1
