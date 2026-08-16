"""Unit tests for RssSource (poindexter#1017).

No real HTTP. Mocks ``httpx.AsyncClient`` and feeds crafted RSS 2.0 / Atom
XML through the source — feedparser itself runs for real, since its dialect
normalization IS the behavior under test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from plugins.topic_source import TopicSource
from services.topic_sources.rss import RssSource

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _rfc822(dt: datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _rss2(items: list[dict[str, str]]) -> bytes:
    body = "".join(
        "<item>"
        f"<title>{i['title']}</title>"
        f"<link>{i.get('link', 'https://example.com/p')}</link>"
        + (f"<pubDate>{i['pubdate']}</pubDate>" if "pubdate" in i else "")
        + (f"<description>{i['summary']}</description>" if "summary" in i else "")
        + "</item>"
        for i in items
    )
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        f"<title>Feed</title>{body}</channel></rss>"
    ).encode()


def _atom(entries: list[dict[str, str]]) -> bytes:
    body = "".join(
        "<entry>"
        f"<title>{e['title']}</title>"
        f"<link href=\"{e.get('link', 'https://example.com/a')}\"/>"
        f"<updated>{e['updated']}</updated>"
        "</entry>"
        for e in entries
    )
    return (
        '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
        f"<title>Feed</title>{body}</feed>"
    ).encode()


def _make_client(content: bytes, status: int = 200):
    client = AsyncMock()

    async def get(url: str, **kwargs):
        resp = MagicMock()
        resp.status_code = status
        resp.content = content
        if status >= 400:
            resp.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError(
                    f"{status}", request=MagicMock(), response=resp
                )
            )
        else:
            resp.raise_for_status = MagicMock()
        return resp

    client.get = AsyncMock(side_effect=get)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


_NOW = datetime.now(timezone.utc)
_CFG = {"feed_url": "https://example.com/feed.xml"}


class TestRssSource:
    async def test_satisfies_topic_source_protocol(self):
        assert isinstance(RssSource(), TopicSource)

    async def test_parses_rss2(self):
        feed = _rss2([
            {
                "title": "Understanding vector databases for retrieval workloads",
                "link": "https://example.com/vectors",
                "pubdate": _rfc822(_NOW - timedelta(days=1)),
                "summary": "<p>A <b>deep</b> dive into &amp; around indexes.</p>",
            },
        ])
        ctx, _ = _make_client(feed)
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await RssSource().extract(pool=None, config=dict(_CFG))
        assert len(topics) == 1
        t = topics[0]
        assert t.source == "rss"
        assert t.source_url == "https://example.com/vectors"
        # HTML flattened, entities decoded, no tags.
        assert "<" not in t.description
        assert "deep dive into & around indexes" in t.description.lower()

    async def test_parses_atom(self):
        feed = _atom([
            {
                "title": "Profiling async Python services in production",
                "link": "https://example.com/profiling",
                "updated": _iso(_NOW - timedelta(days=2)),
            },
        ])
        ctx, _ = _make_client(feed)
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await RssSource().extract(pool=None, config=dict(_CFG))
        assert len(topics) == 1
        # Atom carries only <updated>; the timestamp fallback must accept it.
        assert topics[0].source_url == "https://example.com/profiling"

    async def test_old_entries_filtered_undated_kept(self):
        feed = _rss2([
            {
                "title": "Fresh perspectives on database sharding strategies",
                "pubdate": _rfc822(_NOW - timedelta(days=2)),
            },
            {
                "title": "Ancient thoughts on legacy monolith migrations",
                "pubdate": _rfc822(_NOW - timedelta(days=90)),
            },
            {"title": "Undated notes on container image optimization"},
        ])
        ctx, _ = _make_client(feed)
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await RssSource().extract(
                pool=None, config={**_CFG, "max_age_days": 14},
            )
        titles = " | ".join(t.title.lower() for t in topics)
        assert "sharding" in titles
        assert "monolith" not in titles  # provably old → dropped
        assert "container image" in titles  # undated → kept

    async def test_max_entries_caps_output(self):
        feed = _rss2([
            {
                "title": f"Practical guide number {i} to observability pipelines",
                "pubdate": _rfc822(_NOW - timedelta(hours=i)),
            }
            for i in range(8)
        ])
        ctx, _ = _make_client(feed)
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await RssSource().extract(
                pool=None, config={**_CFG, "max_entries": 3},
            )
        assert len(topics) == 3

    async def test_relevance_score_configurable(self):
        feed = _rss2([
            {
                "title": "Measuring cache hit ratios across storage tiers",
                "pubdate": _rfc822(_NOW),
            },
        ])
        ctx, _ = _make_client(feed)
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await RssSource().extract(
                pool=None, config={**_CFG, "relevance_score": 3.5},
            )
        assert topics[0].relevance_score == 3.5

    async def test_missing_feed_url_fails_loud(self):
        with pytest.raises(ValueError, match="feed_url is required"):
            await RssSource().extract(pool=None, config={})

    async def test_http_error_propagates(self):
        """A 4xx/5xx must raise so the tap runner records a real failure
        (streak + tap_failure finding), not a healthy 0-record run."""
        ctx, _ = _make_client(b"", status=503)
        with patch("httpx.AsyncClient", return_value=ctx):
            with pytest.raises(httpx.HTTPStatusError):
                await RssSource().extract(pool=None, config=dict(_CFG))

    async def test_non_feed_content_raises(self):
        """An HTML error page / login wall served with a 200 must fail loud.

        feedparser parses HTML "cleanly" — bozo unset, zero entries — so the
        discriminator is ``version == ""`` (every real dialect, even an empty
        valid feed, gets one). Without this, a feed URL that starts returning
        an error page reports as a healthy empty feed forever.
        """
        ctx, _ = _make_client(b"<html><body>This is not a feed at all</body></html>")
        with patch("httpx.AsyncClient", return_value=ctx):
            with pytest.raises(ValueError, match="not a parseable feed"):
                await RssSource().extract(pool=None, config=dict(_CFG))

    async def test_truncated_xml_raises(self):
        """The bozo half of the same gate: syntactically broken XML with no
        recoverable entries."""
        ctx, _ = _make_client(b'<?xml version="1.0"?><rss version="2.0"><chan')
        with patch("httpx.AsyncClient", return_value=ctx):
            with pytest.raises(ValueError, match="not a parseable feed"):
                await RssSource().extract(pool=None, config=dict(_CFG))

    async def test_valid_empty_feed_returns_no_topics(self):
        ctx, _ = _make_client(_rss2([]))
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await RssSource().extract(pool=None, config=dict(_CFG))
        assert topics == []

    async def test_rewrite_filter_applied(self):
        """Junk-shaped titles die in rewrite_as_blog_topic, same funnel as
        devto/hackernews."""
        feed = _rss2([
            {"title": "Show HN: my thing", "pubdate": _rfc822(_NOW)},
            {
                "title": "Designing resilient retry policies for distributed queues",
                "pubdate": _rfc822(_NOW),
            },
        ])
        ctx, _ = _make_client(feed)
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await RssSource().extract(pool=None, config=dict(_CFG))
        assert len(topics) == 1
        assert "retry" in topics[0].title.lower()

    async def test_entries_without_title_skipped(self):
        feed = _rss2([
            {"title": "", "pubdate": _rfc822(_NOW)},
            {
                "title": "Comparing columnar formats for analytics workloads",
                "pubdate": _rfc822(_NOW),
            },
        ])
        ctx, _ = _make_client(feed)
        with patch("httpx.AsyncClient", return_value=ctx):
            topics = await RssSource().extract(pool=None, config=dict(_CFG))
        assert len(topics) == 1
