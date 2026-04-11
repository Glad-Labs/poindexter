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

    async def discover(self, max_topics: int = 10, categories: list[str] | None = None) -> list[DiscoveredTopic]:
        """Discover fresh topics from multiple sources.

        Brain knowledge is the primary source (works offline).
        Web scraping enriches with trending signals when available.

        Returns deduplicated, scored topics ready for queuing.
        """
        all_topics: list[DiscoveredTopic] = []

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

    async def queue_topics(self, topics: list[DiscoveredTopic]) -> int:
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

    async def _discover_from_knowledge(self, categories: list[str] | None = None) -> list[DiscoveredTopic]:
        """Generate topics from the brain's own knowledge graph and gap analysis.

        Works completely offline — no internet required. Combines:
        1. brain_knowledge entities related to content/tech
        2. Published post titles (to find category gaps)
        3. topic_gaps analysis from idle_worker
        """
        topics: list[DiscoveredTopic] = []
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
            underserved: dict[str, int] = {}
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
                # Apply same quality filters as scraped topics
                rewritten = self._rewrite_as_blog_topic(gap_value)
                if not rewritten or not self._is_brand_relevant(rewritten):
                    continue
                category = self._classify_category(rewritten)
                if categories and category not in categories:
                    continue
                topics.append(DiscoveredTopic(
                    title=rewritten,
                    category=category,
                    source="brain_knowledge_gap",
                    source_url="",
                    relevance_score=4.0,
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

                # Apply quality filters before queuing
                rewritten = self._rewrite_as_blog_topic(candidate)
                if not rewritten or not self._is_brand_relevant(rewritten):
                    continue

                category = self._classify_category(rewritten)
                if categories and category not in categories:
                    continue

                # Boost score for underserved categories
                cat_count = underserved.get(category, 0)
                gap_boost = max(0, (avg_posts - cat_count) / max(avg_posts, 1)) * 2.0
                base_score = 2.5 + gap_boost

                topics.append(DiscoveredTopic(
                    title=rewritten,
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

    async def _scrape_hackernews(self) -> list[DiscoveredTopic]:
        """Scrape top stories from Hacker News API (free, no auth)."""
        topics = []
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=3.0)
            ) as client:
                # Get top story IDs
                resp = await client.get(
                    "https://hacker-news.firebaseio.com/v0/topstories.json",
                    timeout=10,
                )
                story_ids = resp.json()[:20]  # Top 20

                # Fetch story details (concurrent, limited)
                sem = asyncio.Semaphore(5)
                async def fetch_story(sid):
                    async with sem:
                        r = await client.get(
                            f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                            timeout=10,
                        )
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

                    rewritten = self._rewrite_as_blog_topic(title)
                    if not rewritten:
                        continue  # Skip product launches (Show HN, Launch HN, etc.)
                    topics.append(DiscoveredTopic(
                        title=rewritten,
                        category=category,
                        source="hackernews",
                        source_url=url or f"https://news.ycombinator.com/item?id={story.get('id')}",
                        relevance_score=min(score / 100, 5.0),  # Normalize HN score
                    ))

            logger.info("[TOPIC_DISCOVERY] HackerNews: %d topics", len(topics))
        except Exception as e:
            logger.warning("[TOPIC_DISCOVERY] HackerNews scrape failed: %s", e)
        return topics

    async def _scrape_devto(self) -> list[DiscoveredTopic]:
        """Scrape trending articles from Dev.to API (free, no auth)."""
        topics = []
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=3.0)
            ) as client:
                resp = await client.get(
                    "https://dev.to/api/articles?per_page=15&top=7",
                    timeout=10,
                )
                articles = resp.json()

                for article in articles:
                    title = article.get("title", "")
                    url = article.get("url", "")
                    reactions = article.get("positive_reactions_count", 0)

                    if not title or reactions < 20:
                        continue

                    rewritten = self._rewrite_as_blog_topic(title)
                    if not rewritten:
                        continue
                    category = self._classify_category(rewritten)
                    topics.append(DiscoveredTopic(
                        title=rewritten,
                        category=category,
                        source="devto",
                        source_url=url,
                        relevance_score=min(reactions / 50, 5.0),
                    ))

            logger.info("[TOPIC_DISCOVERY] Dev.to: %d topics", len(topics))
        except Exception as e:
            logger.warning("[TOPIC_DISCOVERY] Dev.to scrape failed: %s", e)
        return topics

    async def _search_by_category(self, categories: list[str] | None = None) -> list[DiscoveredTopic]:
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
                    rewritten = self._rewrite_as_blog_topic(title)
                    if not rewritten:
                        continue
                    topics.append(DiscoveredTopic(
                        title=rewritten,
                        category=cat,
                        source="ddg_search",
                        source_url=r.get("url", ""),
                        relevance_score=2.0,
                    ))

            logger.info("[TOPIC_DISCOVERY] DuckDuckGo: %d topics", len(topics))
        except Exception as e:
            logger.warning("[TOPIC_DISCOVERY] DuckDuckGo search failed: %s", e)
        return topics

    async def _deduplicate(self, topics: list[DiscoveredTopic]) -> list[DiscoveredTopic]:
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
                "SELECT topic, title FROM content_tasks WHERE created_at > NOW() - ($1 || ' hours')::interval",
                str(dedup_hours),
            )
            pending_topics = set()
            for r in task_rows:
                if r.get("topic"):
                    pending_topics.add(r["topic"].lower())
                if r.get("title"):
                    pending_topics.add(r["title"].lower())

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
                    if fwd >= 0.5 or rev >= 0.5:
                        topic.is_duplicate = True
                        logger.debug(
                            "[DEDUP] '%s' matches '%s' (fwd=%.0f%% rev=%.0f%%)",
                            topic.title[:40], existing_title[:40], fwd * 100, rev * 100,
                        )
                        break

        except Exception as e:
            logger.warning("[TOPIC_DISCOVERY] Dedup failed: %s", e)

        return topics

    # Keywords that indicate a topic is relevant to this site's niche.
    # KEEP THIS TIGHT — overly broad terms like "software", "code", "data",
    # "model", "cloud", "server" match nearly everything from HN and produce
    # off-brand content. Use multi-word phrases when possible.
    # Override via app_settings key "brand_keywords" (comma-separated).
    _BRAND_KEYWORDS = {
        # AI / ML (core niche)
        "ai", "artificial intelligence", "llm", "language model", "gpt", "claude",
        "local inference", "machine learning", "deep learning", "transformer",
        "ollama", "hugging face", "huggingface", "stable diffusion", "sdxl",
        "fine-tun", "rag", "embeddings", "vector database",
        "generative ai", "diffusion", "lora",
        "ai agent", "autonomous agent", "copilot", "chatbot", "prompt engineering",
        # Self-hosted / local-first (brand identity)
        "self-host", "self host", "local-first", "homelab", "home server",
        "own your data", "data sovereignty", "vendor lock-in",
        # Content automation (what we sell)
        "content pipeline", "content automation", "headless cms",
        "blog automation", "ai writing", "ai content",
        "podcast", "text-to-speech",
        # Dev tools & infrastructure (supporting)
        "docker", "kubernetes", "ci/cd", "devops", "terraform",
        "fastapi", "next.js", "nextjs", "postgresql", "postgres",
        "redis", "mongodb", "supabase", "cloudflare",
        "grafana", "prometheus", "monitoring",
        "open source", "open-source",
        "python", "javascript", "typescript", "rust", "golang",
        # Solo founder / indie (target audience)
        "solo founder", "solo developer", "indie hacker",
        "bootstrapped", "one-person", "side project",
        "saas", "startup tech stack",
        # Security
        "zero trust", "cybersecurity", "encryption", "quantum computing",
        # PC Hardware (core niche)
        "gpu", "nvidia", "amd", "radeon", "rtx", "geforce",
        "cpu", "ryzen", "threadripper", "intel",
        "vram", "ddr5", "nvme", "pcie",
        "pc build", "custom build", "workstation",
        "benchmark", "overclock", "cooling",
        "homelab", "home server",
        # Gaming (core niche)
        "gaming", "game dev", "game engine",
        "steam", "xbox", "playstation", "nintendo",
        "unreal engine", "unity", "godot",
        "indie game", "esports",
        "ray tracing", "dlss", "fsr", "frame generation",
        "vr", "virtual reality",
    }

    @staticmethod
    def _is_brand_relevant(title: str) -> bool:
        """Check if a topic is relevant to the site's niche.

        Uses word-boundary matching to avoid false positives like
        'farmer' matching 'arm' or 'autocross' matching 'solo'.
        Multi-word keywords use substring match (they're specific enough).
        """
        title_lower = title.lower()
        for kw in TopicDiscovery._BRAND_KEYWORDS:
            if " " in kw or "-" in kw:
                # Multi-word keywords: substring match is fine
                if kw in title_lower:
                    return True
            else:
                # Single-word keywords: require word boundary
                if re.search(rf"\b{re.escape(kw)}\b", title_lower):
                    return True
        return False

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

    # Patterns that indicate news/current events (not evergreen editorial content)
    _NEWS_PATTERNS = [
        r"\b(?:police|arrest|charged|sentenced|indicted|convicted|alleged)\b",
        r"\b(?:lawsuit|sued|court|judge|ruling|verdict)\b",
        r"\b(?:killed|dead|dies|died|shooting|crash)\b",
        r"\b(?:election|voted|senator|congress|parliament|president)\b",
        r"\b(?:earthquake|hurricane|flood|wildfire|tornado)\b",
        r"\b(?:shirt|merch|sticker|swag|coupon|discount|sale|buy now)\b",
        r"\b(?:my experience|my journey|i tried|i built|dear diary)\b",
    ]
    _NEWS_RE = [re.compile(p, re.IGNORECASE) for p in _NEWS_PATTERNS]

    @staticmethod
    def _is_news_or_junk(title: str) -> bool:
        """Reject breaking news, current events, personal anecdotes, and merch."""
        for pattern in TopicDiscovery._NEWS_RE:
            if pattern.search(title):
                return True
        # Too short to be a real topic
        if len(title.split()) < 4:
            return True
        return False

    def _rewrite_as_blog_topic(self, title: str) -> str:
        """Clean up a scraped title into a good blog topic.

        Returns empty string for titles that should be filtered out.
        """
        # Reject product launches/announcements
        if re.match(r"^(?:Launch|Show|Ask|Tell)\s+HN\b", title, re.IGNORECASE):
            return ""
        # Reject news/current events/junk
        if self._is_news_or_junk(title):
            return ""
        # Remove bracket prefixes: [Show HN], [OC], etc.
        title = re.sub(r'^\[.*?\]\s*', '', title)
        # Remove site name suffixes: | Site Name, - Blog Name
        title = re.sub(r'\s*\|.*$', '', title)
        title = re.sub(r'\s*[-–—]\s*\w+\.?\w*$', '', title)
        # Remove leading product name + colon ("Freestyle: Sandboxes..." → "Sandboxes...")
        title = re.sub(r'^[A-Z][\w]*(?:\s+[A-Z][\w]*)?\s*[:–—]\s*', '', title)
        return title.strip()
