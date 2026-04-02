"""
Topic Discovery — autonomous trend scraping and topic generation.

Scrapes trending topics from tech news sources, cross-references against
published posts to avoid duplicates, and queues fresh content tasks.

Sources:
1. Hacker News (top/best stories)
2. Dev.to (trending articles)
3. DuckDuckGo trend search per category
4. Reddit r/programming, r/webdev (top posts)

All free, no API keys. Runs as part of the idle worker or on a cron.

Usage:
    from services.topic_discovery import TopicDiscovery
    discovery = TopicDiscovery(pool)
    topics = await discovery.discover(max_topics=5)
    await discovery.queue_topics(topics)
"""

import asyncio
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)


@dataclass
class DiscoveredTopic:
    """A topic discovered from web sources."""
    title: str
    category: str
    source: str
    source_url: str
    relevance_score: float = 0.0
    is_duplicate: bool = False


# Category-specific search queries for DuckDuckGo
CATEGORY_SEARCHES = {
    "technology": [
        "latest AI developer tools 2026",
        "new programming frameworks 2026",
        "cloud infrastructure trends",
    ],
    "startup": [
        "solo founder success stories 2026",
        "bootstrapped SaaS launch tips",
        "indie hacker revenue milestones",
    ],
    "security": [
        "latest cybersecurity threats developers",
        "API security best practices 2026",
        "zero trust architecture practical guide",
    ],
    "engineering": [
        "software architecture patterns 2026",
        "developer productivity engineering",
        "CI/CD pipeline best practices",
    ],
    "insights": [
        "state of software development 2026",
        "developer survey results latest",
        "tech industry trends predictions",
    ],
    "business": [
        "AI business automation 2026",
        "content marketing for developers",
        "SaaS metrics that matter",
    ],
}


class TopicDiscovery:
    """Discover trending topics from free web sources."""

    def __init__(self, pool):
        self.pool = pool

    async def discover(self, max_topics: int = 10, categories: Optional[List[str]] = None) -> List[DiscoveredTopic]:
        """Discover fresh topics from multiple sources.

        Returns deduplicated, scored topics ready for queuing.
        """
        all_topics: List[DiscoveredTopic] = []

        # Scrape from multiple sources concurrently
        sources = await asyncio.gather(
            self._scrape_hackernews(),
            self._scrape_devto(),
            self._search_by_category(categories),
            return_exceptions=True,
        )

        for result in sources:
            if isinstance(result, list):
                all_topics.extend(result)
            elif isinstance(result, Exception):
                logger.warning("[TOPIC_DISCOVERY] Source failed: %s", result)

        # Deduplicate against published posts
        all_topics = await self._deduplicate(all_topics)

        # Score and rank
        all_topics.sort(key=lambda t: t.relevance_score, reverse=True)

        # Filter to requested categories
        if categories:
            all_topics = [t for t in all_topics if t.category in categories]

        result = [t for t in all_topics if not t.is_duplicate][:max_topics]
        logger.info("[TOPIC_DISCOVERY] Discovered %d topics (%d before dedup)", len(result), len(all_topics))
        return result

    async def queue_topics(self, topics: List[DiscoveredTopic]) -> int:
        """Queue discovered topics as content tasks."""
        queued = 0
        for topic in topics:
            try:
                await self.pool.execute("""
                    INSERT INTO content_tasks (task_id, task_type, topic, status, task_metadata)
                    VALUES (gen_random_uuid()::text, 'blog_post', $1, 'pending',
                            jsonb_build_object('category', $2, 'source', $3, 'source_url', $4,
                                              'discovered_by', 'topic_discovery'))
                """, topic.title, topic.category, topic.source, topic.source_url)
                queued += 1
                logger.info("[TOPIC_DISCOVERY] Queued: %s [%s]", topic.title[:50], topic.category)
            except Exception as e:
                logger.warning("[TOPIC_DISCOVERY] Failed to queue '%s': %s", topic.title[:40], e)
        return queued

    async def _scrape_hackernews(self) -> List[DiscoveredTopic]:
        """Scrape top stories from Hacker News API (free, no auth)."""
        topics = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # Get top story IDs
                resp = await client.get("https://hacker-news.firebaseio.com/v0/topstories.json")
                story_ids = resp.json()[:20]  # Top 20

                # Fetch story details (concurrent, limited)
                sem = asyncio.Semaphore(5)
                async def fetch_story(sid):
                    async with sem:
                        r = await client.get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json")
                        return r.json()

                stories = await asyncio.gather(*[fetch_story(sid) for sid in story_ids], return_exceptions=True)

                for story in stories:
                    if isinstance(story, Exception) or not isinstance(story, dict):
                        continue
                    title = story.get("title", "")
                    url = story.get("url", "")
                    score = story.get("score", 0)

                    if not title or score < 50:
                        continue

                    # Classify category
                    category = self._classify_category(title)

                    topics.append(DiscoveredTopic(
                        title=self._rewrite_as_blog_topic(title),
                        category=category,
                        source="hackernews",
                        source_url=url or f"https://news.ycombinator.com/item?id={story.get('id')}",
                        relevance_score=min(score / 100, 5.0),  # Normalize HN score
                    ))

            logger.info("[TOPIC_DISCOVERY] HackerNews: %d topics", len(topics))
        except Exception as e:
            logger.warning("[TOPIC_DISCOVERY] HackerNews scrape failed: %s", e)
        return topics

    async def _scrape_devto(self) -> List[DiscoveredTopic]:
        """Scrape trending articles from Dev.to API (free, no auth)."""
        topics = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get("https://dev.to/api/articles?per_page=15&top=7")
                articles = resp.json()

                for article in articles:
                    title = article.get("title", "")
                    url = article.get("url", "")
                    reactions = article.get("positive_reactions_count", 0)

                    if not title or reactions < 20:
                        continue

                    category = self._classify_category(title)
                    topics.append(DiscoveredTopic(
                        title=self._rewrite_as_blog_topic(title),
                        category=category,
                        source="devto",
                        source_url=url,
                        relevance_score=min(reactions / 50, 5.0),
                    ))

            logger.info("[TOPIC_DISCOVERY] Dev.to: %d topics", len(topics))
        except Exception as e:
            logger.warning("[TOPIC_DISCOVERY] Dev.to scrape failed: %s", e)
        return topics

    async def _search_by_category(self, categories: Optional[List[str]] = None) -> List[DiscoveredTopic]:
        """Search DuckDuckGo for trending topics per category."""
        topics = []
        target_categories = categories or list(CATEGORY_SEARCHES.keys())

        try:
            from services.web_research import WebResearcher
            researcher = WebResearcher()

            for cat in target_categories[:3]:  # Limit to 3 categories per run
                queries = CATEGORY_SEARCHES.get(cat, [])
                if not queries:
                    continue

                # Pick one random query
                import random
                query = random.choice(queries)

                results = await researcher.search_simple(query, num_results=3)
                for r in results:
                    title = r.get("title", "")
                    if not title:
                        continue
                    topics.append(DiscoveredTopic(
                        title=self._rewrite_as_blog_topic(title),
                        category=cat,
                        source="ddg_search",
                        source_url=r.get("url", ""),
                        relevance_score=2.0,  # Medium baseline for search results
                    ))

            logger.info("[TOPIC_DISCOVERY] DuckDuckGo: %d topics", len(topics))
        except Exception as e:
            logger.warning("[TOPIC_DISCOVERY] DuckDuckGo search failed: %s", e)
        return topics

    async def _deduplicate(self, topics: List[DiscoveredTopic]) -> List[DiscoveredTopic]:
        """Mark topics that duplicate existing published posts."""
        if not self.pool:
            return topics

        try:
            # Get all published post titles for fuzzy matching
            rows = await self.pool.fetch(
                "SELECT title FROM posts WHERE status = 'published'"
            )
            published_titles = {r["title"].lower() for r in rows}

            # Also check pending/in-progress tasks
            task_rows = await self.pool.fetch(
                "SELECT topic FROM content_tasks WHERE status IN ('pending', 'approved', 'in_progress', 'awaiting_approval')"
            )
            pending_topics = {r["topic"].lower() for r in task_rows}

            for topic in topics:
                title_lower = topic.title.lower()
                # Exact or near-exact match
                if title_lower in published_titles or title_lower in pending_topics:
                    topic.is_duplicate = True
                    continue
                # Substring match (catches rephrased versions)
                for pub_title in published_titles:
                    # Check if >60% of words overlap
                    topic_words = set(title_lower.split())
                    pub_words = set(pub_title.split())
                    if len(topic_words) > 3 and len(topic_words & pub_words) / len(topic_words) > 0.6:
                        topic.is_duplicate = True
                        break

        except Exception as e:
            logger.warning("[TOPIC_DISCOVERY] Dedup failed: %s", e)

        return topics

    def _classify_category(self, title: str) -> str:
        """Classify a title into a category."""
        title_lower = title.lower()
        scores = {}
        for cat, searches in CATEGORY_SEARCHES.items():
            keywords = " ".join(searches).lower().split()
            score = sum(1 for kw in keywords if kw in title_lower)
            scores[cat] = score
        best = max(scores, key=scores.get) if scores else "technology"
        return best if scores.get(best, 0) > 0 else "technology"

    def _rewrite_as_blog_topic(self, title: str) -> str:
        """Clean up a scraped title into a good blog topic."""
        # Remove common prefixes/suffixes
        title = re.sub(r'^\[.*?\]\s*', '', title)  # [Show HN] etc
        title = re.sub(r'\s*\|.*$', '', title)  # | Site Name
        title = re.sub(r'\s*[-–—]\s*\w+\.?\w*$', '', title)  # - Site
        return title.strip()
