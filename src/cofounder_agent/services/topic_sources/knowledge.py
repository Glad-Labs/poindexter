"""KnowledgeSource — offline topic ideation from brain_knowledge.

Queries the local ``brain_knowledge`` table (populated by brain daemon
probes + operator curation) to surface high-value blog-post topics
without a network round-trip. Three seed paths:

1. **Explicit topic gaps** — rows under ``entity='content_strategy'``,
   ``attribute='topic_gap'``. These are operator-flagged or
   idle-worker-discovered gaps in published coverage. Highest score
   (4.0 base) because they've already been triaged.
2. **Knowledge entities with topic-adjacent attributes** — anything
   under ``topic``, ``trend``, ``technology``, ``category``, ``gap``,
   or ``opportunity`` attributes. Scored 2.5 base with a gap-boost
   (up to +2.0) for underserved categories.
3. **Category balancing** — published-post counts per category drive
   the gap-boost: categories with fewer posts get higher scores so
   new content naturally balances the publication mix.

Fully offline — only depends on the shared asyncpg pool. No HTTP,
no LLM calls, no external APIs.

Config (``plugin.topic_source.knowledge`` in app_settings):

- ``enabled`` (default true)
- ``config.max_gap_topics`` (default 20) — cap on topic_gap rows read
- ``config.max_entity_topics`` (default 50) — cap on knowledge entity rows
- ``config.min_title_chars`` (default 10) — reject gibberish fragments
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.topic_source import DiscoveredTopic
from services.topic_sources._filters import classify_category, rewrite_as_blog_topic

logger = logging.getLogger(__name__)


class KnowledgeSource:
    """Generate topic candidates from brain_knowledge + category gap analysis."""

    name = "knowledge"

    async def extract(
        self,
        pool: Any,  # asyncpg.Pool — required
        config: dict[str, Any],
    ) -> list[DiscoveredTopic]:
        if pool is None:
            logger.warning("KnowledgeSource: no pool, returning empty")
            return []

        max_gap = int(config.get("max_gap_topics", 20) or 20)
        max_entity = int(config.get("max_entity_topics", 50) or 50)
        min_title_chars = int(config.get("min_title_chars", 10) or 10)

        # 1. Knowledge entities whose attribute/name suggests topic-adjacent content
        knowledge_rows = await pool.fetch(
            """
            SELECT DISTINCT ON (entity, attribute) entity, attribute, value, updated_at
            FROM brain_knowledge
            WHERE attribute IN ('topic', 'trend', 'technology', 'category', 'gap', 'opportunity')
               OR entity ILIKE '%content%' OR entity ILIKE '%topic%' OR entity ILIKE '%tech%'
            ORDER BY entity, attribute, updated_at DESC NULLS LAST
            LIMIT $1
            """,
            max_entity,
        )

        # 2. Post counts per category — drives the gap-boost scoring
        category_counts = await pool.fetch(
            """
            SELECT c.name AS category, COUNT(p.id) AS post_count
            FROM categories c
            LEFT JOIN posts p ON p.category_id = c.id AND p.status = 'published'
            GROUP BY c.name
            ORDER BY post_count ASC
            """
        )
        underserved: dict[str, int] = {}
        for row in category_counts:
            cat = row["category"].lower() if row["category"] else "technology"
            underserved[cat] = row["post_count"]
        avg_posts = sum(underserved.values()) / max(len(underserved), 1)

        # 3. Explicit topic gaps (idle-worker or operator-curated)
        gap_rows = await pool.fetch(
            """
            SELECT value
            FROM brain_knowledge
            WHERE entity = 'content_strategy' AND attribute = 'topic_gap'
            ORDER BY updated_at DESC NULLS LAST
            LIMIT $1
            """,
            max_gap,
        )

        topics: list[DiscoveredTopic] = []

        # Gap topics first — highest relevance (triaged signal)
        for row in gap_rows:
            value = row["value"]
            if not value or len(value) < min_title_chars:
                continue
            rewritten = rewrite_as_blog_topic(value)
            if not rewritten:
                continue
            category = classify_category(rewritten)
            topics.append(
                DiscoveredTopic(
                    title=rewritten,
                    category=category,
                    source="brain_knowledge_gap",
                    source_url="",
                    relevance_score=4.0,
                )
            )

        # Knowledge entities with gap-boosted scoring
        for row in knowledge_rows:
            entity = row["entity"]
            value = row["value"]
            if not value or len(value) < min_title_chars:
                continue
            # Skip JSON blobs + operational metrics (not topic seeds)
            stripped = value.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                continue
            if any(skip in entity for skip in ("probe.", "trend.", "freshness.", "health_status")):
                continue

            # Use the value when it's short enough, otherwise fall back to
            # the entity name (e.g. 'content.strategy.gamified_onboarding').
            candidate = value if len(value) < 120 else entity
            if len(candidate) < min_title_chars:
                continue

            rewritten = rewrite_as_blog_topic(candidate)
            if not rewritten:
                continue

            category = classify_category(rewritten)
            # Boost score for underserved categories (gap-driven scoring)
            cat_count = underserved.get(category, 0)
            gap_boost = max(0.0, (avg_posts - cat_count) / max(avg_posts, 1.0)) * 2.0
            base_score = 2.5 + gap_boost

            topics.append(
                DiscoveredTopic(
                    title=rewritten,
                    category=category,
                    source="brain_knowledge",
                    source_url="",
                    relevance_score=base_score,
                )
            )

        logger.info(
            "KnowledgeSource: %d topics (%d from gaps, %d from entities, "
            "%d categories known, avg=%.1f posts/cat)",
            len(topics), len(gap_rows), len(knowledge_rows),
            len(underserved), avg_posts,
        )
        return topics
