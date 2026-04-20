"""HackerNewsSource — scrape topics from Hacker News top stories.

Free, no auth, rate-limited by common sense. Returns only stories
above a configurable score threshold (default 50) from the top N
(default 20). Each title runs through the shared blog-topic rewriter
+ news/junk rejection before being yielded.

Config (``plugin.topic_source.hackernews`` in app_settings):

- ``enabled`` (default true)
- ``config.top_stories`` (default 20) — how many top-story IDs to pull
- ``config.min_score`` (default 50) — drop stories below this point count
- ``config.concurrency`` (default 5) — parallel HTTP fetches for story details
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from plugins.topic_source import DiscoveredTopic
from services.topic_sources._filters import classify_category, rewrite_as_blog_topic

logger = logging.getLogger(__name__)


_HN_BASE = "https://hacker-news.firebaseio.com/v0"


class HackerNewsSource:
    """Pull trending programmer-adjacent topics from HN's public API."""

    name = "hackernews"

    async def extract(
        self,
        pool: Any,  # unused — HN is an HTTP-only source
        config: dict[str, Any],
    ) -> list[DiscoveredTopic]:
        del pool

        top_n = int(config.get("top_stories", 20) or 20)
        min_score = int(config.get("min_score", 50) or 50)
        concurrency = int(config.get("concurrency", 5) or 5)

        topics: list[DiscoveredTopic] = []
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0)
        ) as client:
            ids_resp = await client.get(f"{_HN_BASE}/topstories.json", timeout=10)
            ids_resp.raise_for_status()
            story_ids = ids_resp.json()[:top_n]

            sem = asyncio.Semaphore(concurrency)

            async def _fetch(sid: int) -> dict[str, Any] | None:
                async with sem:
                    r = await client.get(f"{_HN_BASE}/item/{sid}.json", timeout=10)
                    if r.status_code != 200:
                        return None
                    return r.json()

            stories = await asyncio.gather(
                *[_fetch(sid) for sid in story_ids],
                return_exceptions=True,
            )

        for story in stories:
            if isinstance(story, Exception) or not isinstance(story, dict):
                continue
            title = story.get("title", "")
            url = story.get("url", "")
            score = int(story.get("score", 0))

            if not title or score < min_score:
                continue

            rewritten = rewrite_as_blog_topic(title)
            if not rewritten:
                continue  # filtered (Show HN, news/merch, too short, etc.)

            category = classify_category(rewritten)
            topics.append(
                DiscoveredTopic(
                    title=rewritten,
                    category=category,
                    source=self.name,
                    source_url=url or f"https://news.ycombinator.com/item?id={story.get('id')}",
                    # HN scores run 50-2000+; normalize to 0-5 for ranking.
                    relevance_score=min(score / 100, 5.0),
                )
            )

        logger.info("HackerNewsSource: %d topics from %d stories", len(topics), len(story_ids))
        return topics
