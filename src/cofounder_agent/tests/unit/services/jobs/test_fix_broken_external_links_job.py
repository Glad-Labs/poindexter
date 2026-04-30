"""Unit tests for ``services/jobs/fix_broken_external_links.py``.

Pool + httpx mocked. Focus on URL extraction + filtering, 404 detection,
update propagation, and Gitea fan-out.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.fix_broken_external_links import (
    FixBrokenExternalLinksJob,
    _extract_external_urls,
    _strip_url_from_content,
)


def _make_pool(
    rows: list[dict] | None = None,
    fetch_raises: BaseException | None = None,
    execute_raises: BaseException | None = None,
) -> Any:
    conn = AsyncMock()
    if fetch_raises is not None:
        conn.fetch = AsyncMock(side_effect=fetch_raises)
    else:
        conn.fetch = AsyncMock(return_value=rows or [])
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


class _FakeResp:
    def __init__(self, status_code: int):
        self.status_code = status_code


def _patched_client(status_map: dict[str, Any]):
    """httpx.AsyncClient mock. status_map: url → int or Exception."""
    async def _get(url: str, headers=None, timeout: float = 8) -> _FakeResp:
        resp = status_map.get(url, 200)
        if isinstance(resp, Exception):
            raise resp
        return _FakeResp(status_code=resp)

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(side_effect=_get)
    return client


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestExtractExternalUrls:
    def test_markdown_link_extracted(self):
        content = "Check [docs](https://external.com/a) here."
        urls = _extract_external_urls(content, "my-site.io", ())
        assert urls == {"https://external.com/a"}

    def test_html_anchor_extracted(self):
        content = 'See <a href="https://external.com/b">this</a>.'
        urls = _extract_external_urls(content, "my-site.io", ())
        assert urls == {"https://external.com/b"}

    def test_internal_links_filtered(self):
        content = "[int](https://my-site.io/posts/x) [ext](https://other.com/y)"
        urls = _extract_external_urls(content, "my-site.io", ())
        assert urls == {"https://other.com/y"}

    def test_skip_domains_filtered(self):
        content = (
            "[pex](https://images.pexels.com/a.jpg) "
            "[cdn](https://res.cloudinary.com/b.jpg) "
            "[ok](https://other.com/z)"
        )
        urls = _extract_external_urls(
            content, "my-site.io", ("pexels", "cloudinary"),
        )
        assert urls == {"https://other.com/z"}

    def test_trailing_punctuation_stripped(self):
        """URLs followed by comma/period/paren should have them trimmed."""
        content = "Visit https://external.com/a, or [b](https://external.com/b)."
        # Only the bracketed forms are extracted; the bare URL isn't
        # picked up because the regex targets `[text](url)` and `href="..."`.
        urls = _extract_external_urls(content, "my-site.io", ())
        assert urls == {"https://external.com/b"}


class TestStripUrlFromContent:
    def test_markdown_link_replaced_with_text(self):
        content = "See [docs](https://ext.com/a) here."
        assert _strip_url_from_content(content, "https://ext.com/a") == "See docs here."

    def test_html_anchor_replaced_with_label(self):
        content = 'See <a href="https://ext.com/a" class="x">docs</a> here.'
        assert _strip_url_from_content(
            content, "https://ext.com/a",
        ) == "See docs here."

    def test_other_urls_preserved(self):
        content = "[a](https://x.com) [b](https://y.com)"
        out = _strip_url_from_content(content, "https://x.com")
        assert "a" in out
        assert "[b](https://y.com)" in out


# ---------------------------------------------------------------------------
# Job.run
# ---------------------------------------------------------------------------


class TestContract:
    def test_has_required_attrs(self):
        job = FixBrokenExternalLinksJob()
        assert job.name == "fix_broken_external_links"
        assert job.schedule == "every 24 hours"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_no_candidates_returns_ok(self):
        pool, _ = _make_pool([])
        job = FixBrokenExternalLinksJob()
        result = await job.run(pool, {"file_gitea_issue": False})
        assert result.ok is True
        assert result.changes_made == 0
        assert "no published posts" in result.detail

    @pytest.mark.asyncio
    async def test_healthy_urls_not_rewritten(self):
        pool, conn = _make_pool([
            {"id": "p1", "title": "t", "content": "[ok](https://other.com/live)"},
        ])
        client = _patched_client({"https://other.com/live": 200})
        with patch(
            "services.jobs.fix_broken_external_links.httpx.AsyncClient",
            return_value=client,
        ):
            job = FixBrokenExternalLinksJob()
            result = await job.run(pool, {"file_gitea_issue": False})
        assert result.ok is True
        assert result.changes_made == 0
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_404_triggers_rewrite(self):
        pool, conn = _make_pool([
            {
                "id": "p1",
                "title": "t",
                "content": "Live [ok](https://other.com/live) + [dead](https://other.com/404)",
            },
        ])
        client = _patched_client({
            "https://other.com/live": 200,
            "https://other.com/404": 404,
        })
        with patch(
            "services.jobs.fix_broken_external_links.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.fix_broken_external_links.create_gitea_issue",
            new=AsyncMock(return_value=True),
        ) as mock_gitea:
            job = FixBrokenExternalLinksJob()
            result = await job.run(pool, {})

        assert result.ok is True
        assert result.changes_made == 1
        assert result.metrics["urls_removed"] == 1
        conn.execute.assert_awaited_once()
        # Rewritten content: "dead" preserved as plain text, 404 URL gone.
        update_args = conn.execute.call_args.args
        assert "dead" in update_args[1]
        assert "https://other.com/404" not in update_args[1]
        assert "https://other.com/live" in update_args[1]
        mock_gitea.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unreachable_counts_as_broken(self):
        import httpx
        pool, conn = _make_pool([
            {"id": "p1", "title": "t", "content": "[gone](https://dns-fail.example/)"},
        ])
        client = _patched_client({
            "https://dns-fail.example/": httpx.ConnectError("no DNS"),
        })
        with patch(
            "services.jobs.fix_broken_external_links.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.fix_broken_external_links.create_gitea_issue",
            new=AsyncMock(return_value=True),
        ):
            job = FixBrokenExternalLinksJob()
            result = await job.run(pool, {})
        assert result.changes_made == 1
        conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_500_is_not_removed(self):
        """Non-404 server errors (500, 503) should NOT trigger removal —
        transient server issues shouldn't cost us real links."""
        pool, conn = _make_pool([
            {"id": "p1", "title": "t", "content": "[flaky](https://other.com/err)"},
        ])
        client = _patched_client({"https://other.com/err": 500})
        with patch(
            "services.jobs.fix_broken_external_links.httpx.AsyncClient",
            return_value=client,
        ):
            job = FixBrokenExternalLinksJob()
            result = await job.run(pool, {"file_gitea_issue": False})
        assert result.changes_made == 0
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_urls_per_post_cap(self):
        # 15 external URLs, cap = 3
        md_urls = " ".join(f"[link{i}](https://ext{i}.com)" for i in range(15))
        pool, _ = _make_pool([{"id": "p1", "title": "t", "content": md_urls}])
        client = _patched_client({
            f"https://ext{i}.com": 200 for i in range(15)
        })
        with patch(
            "services.jobs.fix_broken_external_links.httpx.AsyncClient",
            return_value=client,
        ):
            job = FixBrokenExternalLinksJob()
            result = await job.run(
                pool, {"urls_per_post": 3, "file_gitea_issue": False},
            )
        assert result.metrics["urls_checked"] == 3

    @pytest.mark.asyncio
    async def test_gitea_opt_out(self):
        pool, _ = _make_pool([
            {"id": "p1", "title": "t", "content": "[dead](https://other.com/404)"},
        ])
        client = _patched_client({"https://other.com/404": 404})
        mock_gitea = AsyncMock(return_value=False)
        with patch(
            "services.jobs.fix_broken_external_links.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.fix_broken_external_links.create_gitea_issue",
            new=mock_gitea,
        ):
            job = FixBrokenExternalLinksJob()
            await job.run(pool, {"file_gitea_issue": False})
        mock_gitea.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_not_ok(self):
        pool, _ = _make_pool(fetch_raises=RuntimeError("pool closed"))
        job = FixBrokenExternalLinksJob()
        result = await job.run(pool, {})
        assert result.ok is False
        assert "pool closed" in result.detail

    @pytest.mark.asyncio
    async def test_update_failure_does_not_abort_batch(self):
        """If one post's UPDATE fails, subsequent posts should still be checked."""
        pool, _ = _make_pool(
            [
                {"id": "p1", "title": "t", "content": "[dead](https://a.com/404)"},
                {"id": "p2", "title": "t", "content": "[dead](https://b.com/404)"},
            ],
            execute_raises=RuntimeError("row locked"),
        )
        client = _patched_client({
            "https://a.com/404": 404,
            "https://b.com/404": 404,
        })
        with patch(
            "services.jobs.fix_broken_external_links.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "services.jobs.fix_broken_external_links.create_gitea_issue",
            new=AsyncMock(return_value=False),
        ):
            job = FixBrokenExternalLinksJob()
            result = await job.run(pool, {"file_gitea_issue": False})
        # Both updates failed, so changes_made=0, but the job still completes.
        assert result.ok is True
        assert result.metrics["urls_checked"] == 2
