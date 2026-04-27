"""Resolve the right ``categories.id`` UUID for a post about a given topic.

Priority order (legacy behavior preserved from content_router_service.py):

1. If the caller requested a specific category (slug or name), look that
   up and return it.
2. Otherwise, keyword-match the topic against a hardcoded taxonomy and
   fetch the best-matching category's UUID.
3. On any DB error, return ``None`` and let the caller decide.

The taxonomy is a developer-niche default. Operators in other niches
can override via a future ``category_keywords`` app_setting — tracked
as a follow-up; the hardcoded defaults are preserved exactly for now.
"""

from __future__ import annotations

import logging

from services.database_service import DatabaseService

logger = logging.getLogger(__name__)


CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "technology": [
        "ai", "tech", "software", "cloud", "machine learning", "data",
        "coding", "python", "javascript", "docker", "kubernetes", "api",
        "database",
    ],
    "business": [
        "business", "strategy", "management", "entrepreneur", "growth",
        "revenue", "marketing", "saas",
    ],
    "startup": [
        "startup", "founder", "bootstrapper", "mvp", "launch", "validate",
        "solo founder", "side project",
    ],
    "security": [
        "security", "hack", "owasp", "zero trust", "vulnerability", "auth",
        "encryption", "secrets",
    ],
    "engineering": [
        "engineering", "architecture", "monorepo", "git", "technical debt",
        "migration", "ci/cd", "testing",
    ],
    "insights": [
        "trend", "landscape", "state of", "productivity", "remote work",
        "future of", "prediction",
    ],
}

DEFAULT_CATEGORY = "technology"


async def select_category_for_topic(
    topic: str,
    database_service: DatabaseService,
    requested_category: str | None = None,
) -> str | None:
    """Select category ID by requested slug, keyword match, or default."""
    # Priority 1: explicit request.
    if requested_category:
        try:
            async with database_service.pool.acquire() as conn:
                cat_id = await conn.fetchval(
                    "SELECT id FROM categories WHERE slug = $1 OR name ILIKE $1",
                    requested_category,
                )
            if cat_id:
                return cat_id
        except Exception as e:
            # DB unreachable / categories table missing / similar — fall
            # through to keyword matching so the post still routes
            # somewhere, but log so a real outage isn't invisible.
            logger.warning(
                "[category_resolver] DB lookup for slug=%r failed: %s",
                requested_category, e,
            )

    # Priority 2: keyword matching.
    topic_lower = topic.lower()
    matched_category = DEFAULT_CATEGORY
    best_score = 0
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in topic_lower)
        if score > best_score:
            best_score = score
            matched_category = category

    try:
        async with database_service.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT id FROM categories WHERE slug = $1", matched_category,
            )
    except Exception as e:
        logger.error("Error selecting category: %s", e, exc_info=True)
        return None
