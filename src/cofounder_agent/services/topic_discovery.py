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
import json
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
    "hardware": [
        "best GPU for AI inference 2026",
        "AMD vs NVIDIA gaming benchmarks",
        "PC hardware news reviews 2026",
    ],
    "gaming": [
        "upcoming PC games 2026",
        "indie game development news",
        "game engine updates Unreal Unity Godot",
    ],
}


class TopicDiscovery:
    """Discover trending topics from free web sources."""

    def __init__(self, pool):
        self.pool = pool

    async def discover(self, max_topics: int = 10, categories: Optional[List[str]] = None) -> List[DiscoveredTopic]:
        """Discover fresh topics from multiple sources.

        Brain knowledge is the primary source (works offline).
        Web scraping enriches with trending signals when available.

        Returns deduplicated, scored topics ready for queuing.
        """
        all_topics: List[DiscoveredTopic] = []

        # Primary: generate topics from brain knowledge (always available)
        knowledge_topics = await self._discover_from_knowledge(categories)
        all_topics.extend(knowledge_topics)

        # Enrichment: scrape from web sources concurrently
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

        # Filter to brand-relevant topics
        all_topics = [t for t in all_topics if self._is_brand_relevant(t.title)]

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
        import random
        # Vary post lengths: 60% short (800-1200), 30% medium (1500-2000), 10% deep dive (2500-3500)
        _LENGTH_WEIGHTS = [
            (800, 1200, 0.6),    # Short reads (3-5 min)
            (1500, 2000, 0.3),   # Medium reads (6-8 min)
            (2500, 3500, 0.1),   # Deep dives (10-15 min)
        ]

        # Vary writing styles to mimic a multi-writer newsroom
        _STYLES = [
            ("technical", "professional"),    # Deep technical analysis
            ("narrative", "professional"),    # Story-driven reporting
            ("listicle", "casual"),           # "5 things..." quick reads
            ("educational", "professional"),  # How-to / explainer
            ("narrative", "casual"),          # Conversational analysis
        ]

        def _pick_length() -> int:
            r = random.random()
            cumulative = 0.0
            for lo, hi, weight in _LENGTH_WEIGHTS:
                cumulative += weight
                if r <= cumulative:
                    return random.randint(lo, hi)
            return 1200  # fallback

        queued = 0
        for topic in topics:
            try:
                target_length = _pick_length()
                style, tone = random.choice(_STYLES)
                metadata = json.dumps({
                    "category": str(topic.category or "technology"),
                    "source": str(topic.source or "unknown"),
                    "source_url": str(topic.source_url or ""),
                    "discovered_by": "topic_discovery",
                    "target_length": target_length,
                    "style": style,
                    "tone": tone,
                })
                await self.pool.execute("""
                    INSERT INTO content_tasks (task_id, task_type, content_type, topic, status, metadata)
                    VALUES (gen_random_uuid()::text, 'blog_post', 'blog_post', $1::text, 'pending', $2::jsonb)
                """, str(topic.title), metadata)
                queued += 1
                logger.info("[TOPIC_DISCOVERY] Queued: %s [%s]", topic.title[:50], topic.category)
            except Exception as e:
                logger.warning("[TOPIC_DISCOVERY] Failed to queue '%s': %s", topic.title[:40], e)
        return queued

    async def _discover_from_knowledge(self, categories: Optional[List[str]] = None) -> List[DiscoveredTopic]:
        """Generate topics from the brain's own knowledge graph and gap analysis.

        Works completely offline — no internet required. Combines:
        1. brain_knowledge entities related to content/tech
        2. Published post titles (to find category gaps)
        3. topic_gaps analysis from idle_worker
        """
        topics: List[DiscoveredTopic] = []
        if not self.pool:
            return topics

        try:
            # 1. Get knowledge entities (tech/content related)
            knowledge_rows = await self.pool.fetch("""
                SELECT DISTINCT ON (entity, attribute) entity, attribute, value, updated_at
                FROM brain_knowledge
                WHERE attribute IN ('topic', 'trend', 'technology', 'category', 'gap', 'opportunity')
                   OR entity ILIKE '%content%' OR entity ILIKE '%topic%' OR entity ILIKE '%tech%'
                ORDER BY entity, attribute, updated_at DESC NULLS LAST
                LIMIT 50
            """)

            # 2. Count published posts per category to find gaps
            category_counts = await self.pool.fetch("""
                SELECT c.name AS category, COUNT(p.id) AS post_count
                FROM categories c
                LEFT JOIN posts p ON p.category_id = c.id AND p.status = 'published'
                GROUP BY c.name
                ORDER BY post_count ASC
            """)

            # Build a map of underserved categories
            underserved: Dict[str, int] = {}
            for row in category_counts:
                cat = row["category"].lower() if row["category"] else "technology"
                count = row["post_count"]
                # Categories with fewer posts are underserved
                underserved[cat] = count

            avg_posts = sum(underserved.values()) / max(len(underserved), 1)

            # 3. Check for topic_gaps from idle_worker analysis
            gap_rows = await self.pool.fetch("""
                SELECT value
                FROM brain_knowledge
                WHERE entity = 'content_strategy' AND attribute = 'topic_gap'
                ORDER BY updated_at DESC NULLS LAST
                LIMIT 20
            """)

            # Generate topics from gaps first (highest value)
            for row in gap_rows:
                gap_value = row["value"]
                if not gap_value or len(gap_value) < 10:
                    continue
                category = self._classify_category(gap_value)
                if categories and category not in categories:
                    continue
                topics.append(DiscoveredTopic(
                    title=gap_value,
                    category=category,
                    source="brain_knowledge_gap",
                    source_url="",
                    relevance_score=4.0,  # High score — gaps are high value
                ))

            # Generate topics from knowledge entities + underserved categories
            for row in knowledge_rows:
                entity = row["entity"]
                value = row["value"]
                if not value or len(value) < 10:
                    continue

                # Skip JSON blobs and operational metrics — not real topic seeds
                if value.strip().startswith("{") or value.strip().startswith("["):
                    continue
                if any(skip in entity for skip in ("probe.", "trend.", "freshness.", "health_status")):
                    continue

                # Use the value as a topic seed
                candidate = value if len(value) < 120 else entity
                if len(candidate) < 10:
                    continue

                category = self._classify_category(candidate)
                if categories and category not in categories:
                    continue

                # Boost score for underserved categories
                cat_count = underserved.get(category, 0)
                gap_boost = max(0, (avg_posts - cat_count) / max(avg_posts, 1)) * 2.0
                base_score = 2.5 + gap_boost

                topics.append(DiscoveredTopic(
                    title=self._rewrite_as_blog_topic(candidate),
                    category=category,
                    source="brain_knowledge",
                    source_url="",
                    relevance_score=base_score,
                ))

            logger.info("[TOPIC_DISCOVERY] Brain knowledge: %d topics (%d gaps, %d entities)",
                        len(topics), len(gap_rows), len(knowledge_rows))
        except Exception as e:
            logger.warning("[TOPIC_DISCOVERY] Brain knowledge discovery failed: %s", e)

        return topics

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

            # Also check all recent tasks regardless of status — prevents
            # re-generating the same topic after rejection or completion.
            # Window is tunable via app_settings key: qa_topic_dedup_hours (default 48).
            try:
                from services.site_config import site_config
                dedup_hours = site_config.get_int("qa_topic_dedup_hours", 48)
            except Exception:
                dedup_hours = 48
            task_rows = await self.pool.fetch(
                f"SELECT topic FROM content_tasks WHERE created_at > NOW() - INTERVAL '{dedup_hours} hours'"
            )
            pending_topics = {r["topic"].lower() for r in task_rows if r.get("topic")}

            all_existing = published_titles | pending_topics

            for topic in topics:
                title_lower = topic.title.lower()
                # Exact match
                if title_lower in all_existing:
                    topic.is_duplicate = True
                    continue
                # Fuzzy word overlap (catches rephrased versions)
                topic_words = set(title_lower.split())
                if len(topic_words) <= 3:
                    continue
                for existing_title in all_existing:
                    existing_words = set(existing_title.split())
                    # Check overlap in both directions — either title
                    # sharing >50% of meaningful words is a duplicate
                    if len(existing_words) <= 3:
                        continue
                    overlap = len(topic_words & existing_words)
                    fwd = overlap / len(topic_words)
                    rev = overlap / len(existing_words)
                    if fwd > 0.5 or rev > 0.5:
                        topic.is_duplicate = True
                        logger.debug(
                            "[DEDUP] '%s' matches '%s' (fwd=%.0f%% rev=%.0f%%)",
                            topic.title[:40], existing_title[:40], fwd * 100, rev * 100,
                        )
                        break

        except Exception as e:
            logger.warning("[TOPIC_DISCOVERY] Dedup failed: %s", e)

        return topics

    # Keywords that indicate a topic is relevant to Glad Labs' niche
    _BRAND_KEYWORDS = {
        # AI / ML
        "ai", "artificial intelligence", "llm", "language model", "gpt", "claude",
        "inference", "machine learning", "deep learning", "neural", "transformer",
        "ollama", "hugging face", "huggingface", "stable diffusion", "sdxl",
        "fine-tun", "rag", "embeddings", "vector", "model", "training",
        "computer vision", "nlp", "generative", "diffusion", "lora",
        "agent", "autonomous", "copilot", "chatbot", "prompt",
        # Hardware
        "gpu", "cuda", "nvidia", "amd", "radeon", "vram", "cpu", "ryzen",
        "geforce", "rtx", "3d v-cache", "x3d", "threadripper", "epyc",
        "pcie", "nvme", "ddr5", "ram", "overclock", "benchmark", "cooling",
        "custom build", "pc build", "workstation", "server", "homelab",
        "raspberry pi", "arm", "risc-v", "fpga", "asic",
        # Gaming
        "gaming", "game", "esports", "steam", "xbox", "playstation", "nintendo",
        "unreal engine", "unity", "godot", "game dev", "game engine",
        "fps", "mmo", "rpg", "indie game", "retro", "emulat",
        "controller", "peripheral", "monitor", "display", "refresh rate",
        "ray tracing", "dlss", "fsr", "frame generation", "upscaling",
        "vr", "virtual reality", "ar", "mixed reality",
        "streaming", "twitch", "obs", "capture card",
        # Dev / Infra (supporting)
        "developer", "dev tool", "devops", "cicd", "ci/cd", "pipeline",
        "automation", "content", "blog", "podcast",
        "open source", "open-source", "linux", "docker", "kubernetes",
        "api", "cloud", "edge computing", "serverless", "infrastructure",
        "coding", "programming", "software", "engineering", "code",
        "privacy", "security", "cryptography", "encryption", "quantum",
        "cyber", "vulnerability", "exploit", "ransomware", "zero-day",
        "self-healing", "monitoring", "grafana",
        "productivity", "workflow", "database", "postgres", "data",
        "startup", "indie", "solo", "founder", "saas", "business",
        "local", "self-host", "self host",
    }

    @staticmethod
    def _is_brand_relevant(title: str) -> bool:
        """Check if a topic is relevant to Glad Labs' niche."""
        title_lower = title.lower()
        return any(kw in title_lower for kw in TopicDiscovery._BRAND_KEYWORDS)

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
