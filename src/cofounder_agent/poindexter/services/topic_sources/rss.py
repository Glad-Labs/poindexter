"""RssSource — generic RSS/Atom feed ingestion (poindexter#1017).

The declarative-data-plane case: **one plugin, N feeds as rows**. Every feed
the operator wants to watch is just an ``external_taps`` row —

.. code:: sql

    INSERT INTO external_taps (name, handler_name, tap_type, target_table,
                               schedule, enabled, niche_id, config)
    VALUES ('glad-labs_rss_ollama', 'builtin_topic_source', 'rss', 'topic_pool',
            'every 6 hours', TRUE, '<niche uuid>',
            '{"feed_url": "https://ollama.com/blog/rss.xml"}');

— and it inherits the whole tap frame for free: niche binding, semantic
dedup, the topic-sanity + self-reference ingest gates, per-tap health with
failure streaks, and per-tap ``tap_failure`` findings (poindexter#1015).

Unlike the engagement-ranked sources (hackernews scores, devto reactions),
a feed carries no popularity signal — its signal IS curation: the operator
chose this publication. So every candidate gets a flat, configurable
``relevance_score`` and the ranking layer differentiates from there.

All feeds share ``source="rss"`` in ``topic_pool`` (per-feed identity lives
on the tap row and in each topic's ``source_url``). That is deliberate: the
pool's read-side balance guard partitions by source, so N feeds compete in
one "rss" lane rather than each feed claiming its own per-source quota.

Parsing is feedparser — the mature answer to RSS 0.9x/1.0/2.0 + Atom + the
long tail of malformed feeds (hand-rolled ElementTree parsing is the classic
wheel reinvention that dies on CDATA, namespaces, and encodings). Imported
lazily inside ``extract`` (same pattern as web_search's ``ddgs``) so a
worker image that predates the dependency degrades to a per-tap failure
with a remediation message instead of poisoning registry import.

Config (tap-row ``config`` overlaid on ``plugin.topic_source.rss``):

- ``feed_url`` (**required** — no default; a feed tap without a feed is a
  misconfigured row and fails loud per feedback_no_silent_defaults)
- ``max_entries`` (default 10) — cap per run, applied feed-order (feeds are
  newest-first by convention)
- ``max_age_days`` (default 14) — entries with a parseable timestamp older
  than this are skipped. Entries with NO timestamp pass (can't prove them
  old; the pool's dedup key makes repeats free).
- ``relevance_score`` (default 2.0) — flat score on every candidate
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from plugins.topic_source import DiscoveredTopic
from services.topic_sources._filters import classify_category, rewrite_as_blog_topic

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ENTRIES = 10
_DEFAULT_MAX_AGE_DAYS = 14
_DEFAULT_RELEVANCE = 2.0
# topic_pool.summary is display context for the ranking layer, not a corpus
# document — keep it a sentence or two.
_SUMMARY_MAX_CHARS = 300


def _entry_timestamp(entry: Any) -> datetime | None:
    """Best-available entry timestamp as aware UTC, or None when absent.

    feedparser normalizes ``published_parsed`` / ``updated_parsed`` to UTC
    ``time.struct_time``; prefer published (Atom feeds often carry only
    ``updated``).
    """
    for attr in ("published_parsed", "updated_parsed"):
        parsed = entry.get(attr)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def _strip_html(text: str) -> str:
    """Flatten a feed summary (frequently HTML) to plain text."""
    if not text:
        return ""
    if "<" not in text:
        return " ".join(text.split())
    from bs4 import BeautifulSoup

    return " ".join(BeautifulSoup(text, "html.parser").get_text(" ").split())


class RssSource:
    """Pull recent entries from one RSS/Atom feed into the topic funnel."""

    name = "rss"

    async def extract(
        self,
        pool: Any,  # unused — HTTP-only source
        config: dict[str, Any],
    ) -> list[DiscoveredTopic]:
        del pool

        feed_url = str(config.get("feed_url", "") or "").strip()
        if not feed_url:
            raise ValueError(
                "RssSource: config.feed_url is required — set it on the "
                "external_taps row, e.g. poindexter taps set-config <name> "
                '\'{"feed_url": "https://example.com/feed.xml"}\''
            )

        max_entries = int(config.get("max_entries", _DEFAULT_MAX_ENTRIES) or _DEFAULT_MAX_ENTRIES)
        max_age_days = int(config.get("max_age_days", _DEFAULT_MAX_AGE_DAYS) or _DEFAULT_MAX_AGE_DAYS)
        relevance = float(config.get("relevance_score", _DEFAULT_RELEVANCE) or _DEFAULT_RELEVANCE)

        try:
            import feedparser
        except ImportError as e:
            raise RuntimeError(
                "RssSource: the 'feedparser' package is not installed in "
                "this environment — rebuild the worker image (it is a "
                "declared dependency as of poindexter#1017)"
            ) from e

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0), follow_redirects=True
        ) as client:
            resp = await client.get(feed_url)
            resp.raise_for_status()
            raw = resp.content

        # feedparser is sync + CPU-bound-ish; keep the event loop clean.
        parsed = await asyncio.to_thread(feedparser.parse, raw)

        entries = list(parsed.get("entries") or [])
        if not entries:
            # `version` is the discriminator, not `bozo`: feedparser labels
            # every recognized dialect (even an EMPTY valid feed) with a
            # version like "rss20"/"atom10", while non-feed content (an HTML
            # error page, a login wall served with a 200) parses "cleanly"
            # to version="" with bozo unset. Either signal + zero entries =
            # a broken feed, and it must take the per-tap failure path
            # (streak + tap_failure finding), not report a healthy
            # 0-record run forever.
            if parsed.get("bozo") or not parsed.get("version"):
                raise ValueError(
                    f"RssSource: content at {feed_url} is not a parseable "
                    f"feed and yielded no entries "
                    f"(version={parsed.get('version')!r}, "
                    f"bozo_exception={parsed.get('bozo_exception')!r})"
                )
            logger.info("RssSource: feed %s is valid but empty", feed_url)
            return []
        if parsed.get("bozo"):
            # Malformed but recoverable — feedparser's leniency is the point.
            logger.warning(
                "RssSource: feed %s set bozo (%s) but yielded %d entries — "
                "continuing",
                feed_url, parsed.get("bozo_exception"), len(entries),
            )

        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        topics: list[DiscoveredTopic] = []
        for entry in entries:
            if len(topics) >= max_entries:
                break
            title = str(entry.get("title", "") or "").strip()
            link = str(entry.get("link", "") or "").strip()
            if not title:
                continue

            stamp = _entry_timestamp(entry)
            if stamp is not None and stamp < cutoff:
                continue

            rewritten = rewrite_as_blog_topic(title)
            if not rewritten:
                continue

            topics.append(
                DiscoveredTopic(
                    title=rewritten,
                    category=classify_category(rewritten),
                    source=self.name,
                    source_url=link,
                    relevance_score=relevance,
                    description=_strip_html(
                        str(entry.get("summary", "") or "")
                    )[:_SUMMARY_MAX_CHARS],
                )
            )

        logger.info(
            "RssSource: %d topics from %d entries (%s, max_age_days=%d)",
            len(topics), len(entries), feed_url, max_age_days,
        )
        return topics
