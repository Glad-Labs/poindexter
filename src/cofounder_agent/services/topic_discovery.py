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
    from services.site_config import site_config

    discovery = TopicDiscovery(pool, site_config=site_config)
    topics = await discovery.discover(max_topics=5)
    await discovery.queue_topics(topics)
"""

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from services.logger_config import get_logger

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


# Stop-word + content-word + overlap helpers moved to services.topic_dedup
# in #151 so other ingest paths (manual topic injection, RSS, future
# topic sources) can reuse them without importing the full dispatcher.
# Re-exported here so existing imports of these names from
# ``services.topic_discovery`` keep working — public API surface
# preserved, implementation moved.
from services.topic_dedup import (  # noqa: F401  (re-exports for back-compat)
    _STOP_WORDS,
    _content_words,
    _word_overlap_match,
)


class TopicDiscovery:
    """Discover trending topics from free web sources."""

    def __init__(self, pool, *, site_config: Any):
        """Initialize the topic discovery dispatcher.

        Args:
            pool: asyncpg pool for reading published posts / app_settings.
            site_config: SiteConfig instance — required (keyword-only).
                Tests should pass an explicit instance via
                ``SiteConfig(initial_config={...})``.
        """
        if site_config is None:
            raise ValueError(
                "TopicDiscovery: site_config is required "
                "(no module singleton fallback)"
            )
        self.pool = pool
        self._site_config = site_config

    async def discover(self, max_topics: int = 10, categories: list[str] | None = None) -> list[DiscoveredTopic]:
        """Discover fresh topics from multiple sources.

        Brain knowledge is the primary source (works offline).
        Web scraping enriches with trending signals when available.

        Sources can be toggled via the `enabled_topic_sources` app_setting
        (comma-separated).  Default: all 5 enabled.  Valid source names:
        knowledge, codebase, hackernews, devto, web_search.

        Returns deduplicated, scored topics ready for queuing.
        """
        all_topics: list[DiscoveredTopic] = []
        enabled = await self._get_enabled_sources()

        # Primary: generate topics from brain knowledge (always available)
        if "knowledge" in enabled:
            knowledge_topics = await self._discover_from_knowledge(categories)
            all_topics.extend(knowledge_topics)

        # Codebase-driven topics (Poindexter's differentiator — #213)
        if "codebase" in enabled:
            codebase_topics = await self._discover_from_codebase()
            all_topics.extend(codebase_topics)

        # Enrichment: scrape from web sources concurrently
        web_tasks = []
        web_source_names: list[str] = []
        if "hackernews" in enabled:
            web_tasks.append(self._scrape_hackernews())
            web_source_names.append("hackernews")
        if "devto" in enabled:
            web_tasks.append(self._scrape_devto())
            web_source_names.append("devto")
        if "web_search" in enabled:
            web_tasks.append(self._search_by_category(categories))
            web_source_names.append("web_search")

        if web_tasks:
            sources = await asyncio.gather(*web_tasks, return_exceptions=True)
            for name, result in zip(web_source_names, sources, strict=True):
                if isinstance(result, list):
                    all_topics.extend(result)
                elif isinstance(result, Exception):
                    logger.warning("[TOPIC_DISCOVERY] Source %s failed: %s", name, result)

        logger.info(
            "[TOPIC_DISCOVERY] Enabled sources: %s — %d raw topics before dedup",
            ",".join(sorted(enabled)), len(all_topics),
        )

        # Deduplicate against published posts
        all_topics = await self._deduplicate(all_topics)

        # Filter to brand-relevant topics
        all_topics = [t for t in all_topics if self._is_brand_relevant(t.title)]

        # Reject news / merch / personal-anecdote / truncated / emoji-led junk.
        # Brand-relevance alone passes single-word "Cybersecurity" (matches
        # brand keyword) and emoji-led devto clickbait (matches "AI") — the
        # junk filter catches what brand-relevance can't. Without this, the
        # gitea#279 dedup loosening lets noise through to the approval queue.
        before_junk = len(all_topics)
        all_topics = [t for t in all_topics if not self._is_news_or_junk(t.title)]
        if before_junk != len(all_topics):
            logger.info(
                "[TOPIC_DISCOVERY] Junk filter rejected %d titles (%d -> %d)",
                before_junk - len(all_topics), before_junk, len(all_topics),
            )

        # Score and rank
        all_topics.sort(key=lambda t: t.relevance_score, reverse=True)

        # Filter to requested categories
        if categories:
            all_topics = [t for t in all_topics if t.category in categories]

        result = [t for t in all_topics if not t.is_duplicate][:max_topics]
        logger.info("[TOPIC_DISCOVERY] Discovered %d topics (%d before dedup)", len(result), len(all_topics))
        return result

    async def _get_str_setting(self, key: str, default: str = "") -> str:
        """Read a string value from app_settings with a default."""
        if not self.pool:
            return default
        try:
            row = await self.pool.fetchrow(
                "SELECT value FROM app_settings WHERE key = $1", key
            )
            return str(row["value"]) if row and row["value"] is not None else default
        except Exception:
            return default

    async def _get_int_setting(self, key: str, default: int) -> int:
        """Read an integer value from app_settings with a default."""
        raw = await self._get_str_setting(key, str(default))
        try:
            return int(raw)
        except (ValueError, TypeError):
            return default

    async def _get_enabled_sources(self) -> set[str]:
        """Read enabled_topic_sources from app_settings.

        Returns a set of source names. Defaults to all 5 enabled.
        Customers can toggle sources for their niche via:
          UPDATE app_settings SET value = 'knowledge,codebase,hackernews'
          WHERE key = 'enabled_topic_sources';
        """
        default = "knowledge,codebase,hackernews,devto,web_search"
        if not self.pool:
            return set(default.split(","))
        try:
            row = await self.pool.fetchrow(
                "SELECT value FROM app_settings WHERE key = 'enabled_topic_sources'"
            )
            raw = str(row["value"]).strip() if row and row["value"] else default
            return {s.strip().lower() for s in raw.split(",") if s.strip()}
        except Exception as e:
            logger.warning("[TOPIC_DISCOVERY] Failed to read enabled_topic_sources: %s", e)
            return set(default.split(","))

    async def queue_topics(self, topics: list[DiscoveredTopic]) -> int:
        """Queue discovered topics as content tasks.

        Respects the topic-decision queue cap (#146): when the
        ``topic_decision`` gate is enabled and the awaiting-approval
        queue is at ``topic_discovery_max_pending``, this skips the
        propose call so the operator can drain before more lands.
        """
        import json as _json
        import random

        # Queue-cap check — respected only when the topic-decision gate
        # is on (default off). Keeps anticipation_engine from filling
        # Telegram with hundreds of pending topics. When the cap can't
        # be read (DB outage, malformed value), helper logs WARNING and
        # falls back to the default — never silently lets the queue
        # grow unbounded.
        try:
            from services.topic_proposal_service import queue_at_capacity
            if await queue_at_capacity(pool=self.pool, site_config=self._site_config):
                logger.info(
                    "[TOPIC_DISCOVERY] topic_decision queue at capacity; "
                    "skipping queue_topics for %d candidates",
                    len(topics),
                )
                return 0
        except Exception as _exc:
            # The cap helper bails loudly itself; fall through here so a
            # broken cap check doesn't block the legacy auto-queue path.
            logger.warning(
                "[TOPIC_DISCOVERY] queue_at_capacity check failed: %s", _exc,
            )

        # Vary post lengths: default 60% short / 30% medium / 10% deep dive.
        # Customers tune the mix via app_settings.topic_discovery_length_distribution
        # as JSON: [[800, 1200, 0.6], [1500, 2000, 0.3], [2500, 3500, 0.1]]. (#198)
        _LENGTH_WEIGHTS = [
            (800, 1200, 0.6),    # Short reads (3-5 min)
            (1500, 2000, 0.3),   # Medium reads (6-8 min)
            (2500, 3500, 0.1),   # Deep dives (10-15 min)
        ]
        _raw_lengths = self._site_config.get("topic_discovery_length_distribution", "")
        if _raw_lengths:
            try:
                _parsed = _json.loads(_raw_lengths)
                if isinstance(_parsed, list) and _parsed:
                    _LENGTH_WEIGHTS = [
                        (int(lo), int(hi), float(w)) for lo, hi, w in _parsed
                    ]
            except (ValueError, TypeError) as _e:
                logger.warning(
                    "[TOPIC_DISCOVERY] topic_discovery_length_distribution "
                    "invalid JSON, using defaults: %s", _e,
                )

        # Vary writing styles to mimic a multi-writer newsroom.
        # Customers tune via app_settings.topic_discovery_style_distribution
        # as JSON: [["technical","professional"], ["narrative","casual"], ...]. (#198)
        _STYLES = [
            ("technical", "professional"),    # Deep technical analysis
            ("narrative", "professional"),    # Story-driven reporting
            ("listicle", "casual"),           # "5 things..." quick reads
            ("educational", "professional"),  # How-to / explainer
            ("narrative", "casual"),          # Conversational analysis
        ]
        _raw_styles = self._site_config.get("topic_discovery_style_distribution", "")
        if _raw_styles:
            try:
                _parsed_styles = _json.loads(_raw_styles)
                if isinstance(_parsed_styles, list) and _parsed_styles:
                    _STYLES = [(str(s), str(t)) for s, t in _parsed_styles]
            except (ValueError, TypeError) as _e:
                logger.warning(
                    "[TOPIC_DISCOVERY] topic_discovery_style_distribution "
                    "invalid JSON, using defaults: %s", _e,
                )

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
        """Delegate to ``services.topic_sources.knowledge.KnowledgeSource``.

        Phase F slice 5 moved the implementation. This wrapper:
        - Preserves the legacy signature (``categories=[...]`` positional)
        - Applies the category filter here after the source returns
          (the source itself doesn't filter by category; keeping that
          responsibility on the dispatcher matches how the other legacy
          methods have been migrated)
        - Applies the brand-relevance filter here too, since the source
          doesn't own _BRAND_KEYWORDS (that's a TopicDiscovery-scoped
          concept tied to the site's niche)
        """
        from services.topic_sources.knowledge import KnowledgeSource
        if not self.pool:
            return []
        source = KnowledgeSource()
        try:
            raw = await source.extract(self.pool, {})
        except Exception as e:
            logger.warning("[TOPIC_DISCOVERY] Brain knowledge discovery failed: %s", e)
            return []

        filtered: list[DiscoveredTopic] = []
        for topic in raw:
            if not self._is_brand_relevant(topic.title):
                continue
            if categories and topic.category not in categories:
                continue
            filtered.append(topic)

        logger.info(
            "[TOPIC_DISCOVERY] Brain knowledge: %d topics (filtered from %d raw)",
            len(filtered), len(raw),
        )
        return filtered

    async def _scrape_hackernews(self) -> list[DiscoveredTopic]:
        """Delegate to ``services.topic_sources.hackernews.HackerNewsSource``.

        Phase F moved the implementation into its own plugin-style module.
        This method is a thin back-compat wrapper so existing callers
        and tests that mock ``self._scrape_hackernews`` continue to work.
        Legacy app_settings keys (``hn_top_stories`` / ``hn_min_score``)
        are read here so operators don't have to migrate their config.
        """
        from services.topic_sources.hackernews import HackerNewsSource
        # Translate the legacy per-source app_setting keys into the
        # config dict the new source expects.
        hn_top = await self._get_int_setting("hn_top_stories", 20)
        hn_min_score = await self._get_int_setting("hn_min_score", 50)
        source = HackerNewsSource()
        try:
            topics = await source.extract(
                self.pool,
                {"top_stories": hn_top, "min_score": hn_min_score},
            )
            logger.info("[TOPIC_DISCOVERY] HackerNews: %d topics", len(topics))
            return topics
        except Exception as e:
            logger.warning("[TOPIC_DISCOVERY] HackerNews scrape failed: %s", e)
            return []

    async def _scrape_devto(self) -> list[DiscoveredTopic]:
        """Delegate to ``services.topic_sources.devto.DevtoSource``.

        Phase F moved the implementation into its own module. This
        wrapper reads the legacy app_settings keys so operator config
        stays compatible.
        """
        from services.topic_sources.devto import DevtoSource
        per_page = await self._get_int_setting("devto_per_page", 15)
        top_days = await self._get_int_setting("devto_top_days", 7)
        min_reactions = await self._get_int_setting("devto_min_reactions", 20)
        tag = (await self._get_str_setting("devto_tag", "")).strip()
        api_base = self._site_config.get("devto_api_base", "https://dev.to/api")
        source = DevtoSource()
        try:
            topics = await source.extract(
                self.pool,
                {
                    "per_page": per_page,
                    "top_days": top_days,
                    "min_reactions": min_reactions,
                    "tag": tag,
                    "api_base": api_base,
                },
            )
            logger.info("[TOPIC_DISCOVERY] Dev.to: %d topics", len(topics))
            return topics
        except Exception as e:
            logger.warning("[TOPIC_DISCOVERY] Dev.to scrape failed: %s", e)
            return []

    async def _search_by_category(self, categories: list[str] | None = None) -> list[DiscoveredTopic]:
        """Delegate to ``services.topic_sources.web_search.WebSearchSource``.

        Phase F slice 4 moved the implementation. This wrapper preserves
        the legacy signature (``categories=[...]`` positional arg) and
        log message so callers + tests that still go through
        ``self._search_by_category`` keep working.
        """
        from services.topic_sources.web_search import WebSearchSource
        source = WebSearchSource()
        cfg: dict[str, Any] = {"max_categories_per_run": 3, "results_per_query": 3}
        if categories:
            cfg["categories"] = list(categories)
        try:
            topics = await source.extract(self.pool, cfg)
            logger.info("[TOPIC_DISCOVERY] DuckDuckGo: %d topics", len(topics))
            return topics
        except Exception as e:
            logger.warning("[TOPIC_DISCOVERY] DuckDuckGo search failed: %s", e)
            return []

    async def _discover_from_codebase(self) -> list[DiscoveredTopic]:
        """Delegate to ``services.topic_sources.codebase.CodebaseSource``.

        Phase F slice 6 moved the implementation. The wrapper reads the
        legacy ``topic_discovery_ideation_lookback_days`` app_setting
        and threads it through so operators who've tuned the window
        don't have to migrate their config.
        """
        from services.topic_sources.codebase import CodebaseSource
        if not self.pool:
            return []
        lookback = self._site_config.get_int("topic_discovery_ideation_lookback_days", 30)
        source = CodebaseSource()
        try:
            topics = await source.extract(
                self.pool, {"lookback_days": lookback},
            )
            logger.info("[TOPIC_DISCOVERY] Vector DB: %d topics", len(topics))
            return topics
        except Exception as e:
            logger.warning("[TOPIC_DISCOVERY] Vector DB scan failed: %s", e)
            return []

    async def _deduplicate(self, topics: list[DiscoveredTopic]) -> list[DiscoveredTopic]:
        """Mark topics that duplicate existing published posts.

        Two thresholds, both tunable via app_settings (gitea#279):

        - ``topic_dedup_existing_threshold`` (default 0.7) — match against
          published posts + in-flight tasks. Should be permissive because
          coincidental keyword overlap with the ~hundreds-of-titles corpus
          is common (e.g. "Challenge" and "Week" recur across distinct
          events with different sponsors / dollar amounts / platforms).

        - ``topic_dedup_intra_batch_threshold`` (default 0.65) — match
          between candidates in the same scrape. Slightly tighter, since
          a single source dropping two near-identical headlines usually
          *is* a duplicate.

        Both replace the previous hardcoded 0.4, which was filtering
        14/14 candidates against the corpus (gitea#279, "topic intra-batch
        dedup is too aggressive — distinct-event titles flagged as
        duplicates"). Lowering either knob will block more topics; raising
        will let more through.
        """
        # Implementation moved to services.topic_dedup.TopicDeduplicator
        # in #151. This method stays as the public surface so existing
        # callers don't break; it just delegates to the helper now.
        from services.topic_dedup import TopicDeduplicator

        deduper = TopicDeduplicator(self.pool, site_config=self._site_config)
        return await deduper.mark_duplicates(topics)

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
        "quantiz", "gguf", "ggml", "awq", "gptq", "token throughput",
        "inference speed", "context window", "context length",
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
            # Single-word keywords: require word boundary
            elif re.search(rf"\b{re.escape(kw)}\b", title_lower):
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
        """Reject breaking news, current events, personal anecdotes, merch,
        truncated mid-phrase titles, and emoji-led clickbait.

        Delegates to the canonical implementation in
        ``services.topic_sources._filters`` so the dispatcher and the
        per-source plugins stay in sync. The local class-level
        ``_NEWS_RE`` constants are kept around for back-compat with any
        code that still references them directly, but new patterns go
        in ``_filters.py``.
        """
        from services.topic_sources._filters import is_news_or_junk
        return is_news_or_junk(title)

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
        # Reject academic papers / government publications
        if re.search(r"(?:Special Publication|NIST|RFC \d{3,}|arXiv|doi\.org|ISBN)", title, re.IGNORECASE):
            return ""
        # Reject titles that are mostly ALLCAPS (academic/government docs)
        words = title.split()
        if len(words) >= 3:
            caps_words = sum(1 for w in words if w.isupper() and len(w) > 2)
            if caps_words / len(words) > 0.4:
                return ""
        # Remove bracket prefixes: [Show HN], [OC], etc.
        title = re.sub(r'^\[.*?\]\s*', '', title)
        # Remove site name suffixes: | Site Name, - Blog Name
        title = re.sub(r'\s*\|.*$', '', title)
        title = re.sub(r'\s*[-–—]\s*\w+\.?\w*$', '', title)
        # Remove leading product name + colon ("Freestyle: Sandboxes..." → "Sandboxes...")
        title = re.sub(r'^[A-Z][\w]*(?:\s+[A-Z][\w]*)?\s*[:–—]\s*', '', title)
        # Strip trailing author names ("... Scott Rose", "... Alper Kerman")
        # Only match when preceded by lowercase text (not title-case content words)
        # and the title is long enough that stripping won't gut it.
        if len(title.split()) >= 6:
            title = re.sub(r'(?<=[a-z.,;:)])\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}\s*$', '', title)
        # Reject if too short after cleanup (gibberish fragments)
        title = title.strip()
        if len(title) < 10:
            return ""
        return title
