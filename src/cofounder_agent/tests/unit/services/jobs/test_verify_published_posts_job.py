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


def _sc(site_url: str = "https://gladlabs.io") -> MagicMock:
    """Mock SiteConfig — replaces patch("services.jobs.verify_published_posts.site_config.get").

    Job migrated to DI seam in glad-labs-stack#330; tests pass it via
    config dict instead.
    """
    sc = MagicMock()
    sc.get.side_effect = lambda k, d=None: site_url if k == "site_url" else d
    return sc


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
    def __init__(self, status_code: int = 200, headers: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}


def _patched_client(status_map: dict[str, Any]):
    """httpx client mock. status_map: URL → int | _FakeResp | Exception."""
    async def _get(url: str, timeout: float = 10) -> _FakeResp:
        outcome = status_map.get(url, 200)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, _FakeResp):
            return outcome
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
        result = await job.run(pool, {"_site_config": _sc("")})
        assert result.ok is False
        assert "site_url not configured" in result.detail

    @pytest.mark.asyncio
    async def test_no_recent_posts_is_ok(self):
        pool, _ = _make_pool([])
        job = VerifyPublishedPostsJob()
        result = await job.run(pool, {"window_hours": 12, "_site_config": _sc()})
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
            "services.jobs.verify_published_posts.httpx.AsyncClient",
            return_value=client,
        ):
            job = VerifyPublishedPostsJob()
            result = await job.run(pool, {"file_gitea_issue": False, "_site_config": _sc()})
        assert result.ok is True
        assert result.changes_made == 0
        assert result.metrics == {
            "posts_checked": 2, "posts_verified": 2, "posts_failed": 0,
            "posts_edge_blocked": 0,
        }

    @pytest.mark.asyncio
    async def test_404_counts_as_failure(self):
        pool, conn = _make_pool([
            {"id": "p1", "title": "Vanished", "slug": "vanished"},
        ])
        client = _patched_client({"https://gladlabs.io/posts/vanished": 404})
        with patch(
            "services.jobs.verify_published_posts.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.verify_published_posts.emit_finding",
            new=MagicMock(),
        ) as mock_gitea:
            job = VerifyPublishedPostsJob()
            result = await job.run(pool, {"_site_config": _sc()})
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
            "services.jobs.verify_published_posts.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.verify_published_posts.emit_finding",
            new=MagicMock(),
        ):
            job = VerifyPublishedPostsJob()
            result = await job.run(pool, {"_site_config": _sc()})
        assert result.metrics["posts_failed"] == 1

    @pytest.mark.asyncio
    async def test_trailing_slash_on_site_url_is_stripped(self):
        """Avoid building "https://site.io//posts/slug" when site_url ends in /."""
        pool, _ = _make_pool([{"id": "p1", "title": "T", "slug": "t"}])
        client = _patched_client({"https://site.io/posts/t": 200})
        with patch(
            "services.jobs.verify_published_posts.httpx.AsyncClient",
            return_value=client,
        ):
            job = VerifyPublishedPostsJob()
            result = await job.run(
                pool, {"file_gitea_issue": False, "_site_config": _sc("https://site.io/")},
            )
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
            "services.jobs.verify_published_posts.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.verify_published_posts.emit_finding",
            new=MagicMock(),
        ):
            job = VerifyPublishedPostsJob()
            result = await job.run(pool, {"_site_config": _sc()})
        assert result.ok is True
        assert result.metrics["posts_failed"] == 1

    @pytest.mark.asyncio
    async def test_gitea_opt_out(self):
        pool, _ = _make_pool([{"id": "p1", "title": "T", "slug": "bad"}])
        client = _patched_client({"https://gladlabs.io/posts/bad": 503})
        mock_gitea = MagicMock()
        with patch(
            "services.jobs.verify_published_posts.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.verify_published_posts.emit_finding",
            new=mock_gitea,
        ):
            job = VerifyPublishedPostsJob()
            await job.run(pool, {"file_gitea_issue": False, "_site_config": _sc()})
        mock_gitea.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_not_ok(self):
        pool, _ = _make_pool(fetch_raises=RuntimeError("pool closed"))
        job = VerifyPublishedPostsJob()
        result = await job.run(pool, {"_site_config": _sc()})
        assert result.ok is False
        assert "pool closed" in result.detail


class TestEdgeChallenge:
    """A Cloudflare bot challenge (403 + cf-mitigated) is NOT a content
    outage — it must not fire the critical 'post not reachable' page."""

    @pytest.mark.asyncio
    async def test_cf_challenge_not_counted_as_failure(self):
        pool, conn = _make_pool([{"id": "p1", "title": "T", "slug": "t1"}])
        client = _patched_client({
            "https://gladlabs.io/posts/t1": _FakeResp(
                403, {"cf-mitigated": "challenge", "server": "cloudflare"},
            ),
        })
        finds = MagicMock()
        with patch(
            "services.jobs.verify_published_posts.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.verify_published_posts.emit_finding", new=finds,
        ):
            job = VerifyPublishedPostsJob()
            result = await job.run(pool, {"_site_config": _sc()})
        # Classified as edge-blocked, NOT a content failure.
        assert result.metrics["posts_failed"] == 0
        assert result.metrics["posts_edge_blocked"] == 1
        # audit row uses the distinct event type, not publish_verify_failed.
        assert conn.execute.await_args.args[1] == "publish_verify_edge_blocked"
        # A warning finding fires — never the critical one.
        kinds = [c.kwargs.get("kind") for c in finds.call_args_list]
        assert "verify_blocked_by_edge" in kinds
        assert "post_verification_failure" not in kinds

    @pytest.mark.asyncio
    async def test_plain_403_without_cf_header_is_real_failure(self):
        """A 403 lacking cf-mitigated is a genuine failure (still pages)."""
        pool, _ = _make_pool([{"id": "p1", "title": "T", "slug": "t1"}])
        client = _patched_client({
            "https://gladlabs.io/posts/t1": _FakeResp(403, {"server": "vercel"}),
        })
        finds = MagicMock()
        with patch(
            "services.jobs.verify_published_posts.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.verify_published_posts.emit_finding", new=finds,
        ):
            job = VerifyPublishedPostsJob()
            result = await job.run(pool, {"_site_config": _sc()})
        assert result.metrics["posts_failed"] == 1
        assert result.metrics["posts_edge_blocked"] == 0
        kinds = [c.kwargs.get("kind") for c in finds.call_args_list]
        assert "post_verification_failure" in kinds


class TestUserAgent:
    """The monitor identifies with the shared crawler UA (PoindexterMonitor)
    so the edge can allowlist it by UA, and the OSS contact-URL leak guard
    applies. The UA swap alone does NOT bypass the IP+UA-based CF challenge
    (see the job docstring) — but it makes the allowlist-by-UA remediation
    in the finding body real. #1969 follow-up."""

    @pytest.mark.asyncio
    async def test_sends_crawler_user_agent_contactless_by_default(self):
        pool, _ = _make_pool([{"id": "p1", "title": "T", "slug": "t1"}])
        client = _patched_client({"https://gladlabs.io/posts/t1": 200})
        mock_cls = MagicMock(return_value=client)
        with patch(
            "services.jobs.verify_published_posts.httpx.AsyncClient", mock_cls,
        ):
            job = VerifyPublishedPostsJob()
            await job.run(
                pool, {"file_gitea_issue": False, "_site_config": _sc()},
            )

        ua = mock_cls.call_args.kwargs["headers"]["User-Agent"]
        # _sc() returns the default for crawler_contact_url → contact-less.
        assert ua == "Mozilla/5.0 (compatible; PoindexterMonitor/1.0)"

    @pytest.mark.asyncio
    async def test_user_agent_includes_contact_when_configured(self):
        pool, _ = _make_pool([{"id": "p1", "title": "T", "slug": "t1"}])
        client = _patched_client({"https://gladlabs.io/posts/t1": 200})
        sc = MagicMock()
        sc.get.side_effect = lambda k, d=None: {
            "site_url": "https://gladlabs.io",
            "crawler_contact_url": "https://gladlabs.io/bot",
        }.get(k, d)
        mock_cls = MagicMock(return_value=client)
        with patch(
            "services.jobs.verify_published_posts.httpx.AsyncClient", mock_cls,
        ):
            job = VerifyPublishedPostsJob()
            await job.run(
                pool, {"file_gitea_issue": False, "_site_config": sc},
            )

        ua = mock_cls.call_args.kwargs["headers"]["User-Agent"]
        assert ua == (
            "Mozilla/5.0 (compatible; PoindexterMonitor/1.0; "
            "+https://gladlabs.io/bot)"
        )

    @pytest.mark.asyncio
    async def test_edge_finding_prose_is_accurate(self):
        """The edge-challenge remediation prose names the real UA the monitor
        sends (PoindexterMonitor) and no longer cites the stale
        ``verify_published_posts_user_agent`` setting that no code reads."""
        pool, _ = _make_pool([{"id": "p1", "title": "T", "slug": "t1"}])
        client = _patched_client({
            "https://gladlabs.io/posts/t1": _FakeResp(
                403, {"cf-mitigated": "challenge", "server": "cloudflare"},
            ),
        })
        finds = MagicMock()
        with patch(
            "services.jobs.verify_published_posts.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.verify_published_posts.emit_finding", new=finds,
        ):
            job = VerifyPublishedPostsJob()
            await job.run(pool, {"_site_config": _sc()})

        edge_call = next(
            c for c in finds.call_args_list
            if c.kwargs.get("kind") == "verify_blocked_by_edge"
        )
        body = edge_call.kwargs["body"]
        assert "PoindexterMonitor" in body
        assert "verify_published_posts_user_agent" not in body
        assert "GladLabsMonitor" not in body
