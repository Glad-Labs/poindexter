"""Get or create the default 'Poindexter AI' author row.

Every published post needs a ``authors.id`` FK. The operator-facing
brand is intentionally "Poindexter AI" — a consistent author name makes
the RSS feed + attribution UI behave predictably even when posts are
LLM-generated.

Lifted from content_router_service.py during Phase E2. PublishService
calls this when a post is approved for publishing.
"""

from __future__ import annotations

import logging

from services.database_service import DatabaseService

logger = logging.getLogger(__name__)


async def get_or_create_default_author(
    database_service: DatabaseService,
) -> str | None:
    """Return the Poindexter AI author UUID; create the row if needed."""
    try:
        async with database_service.pool.acquire() as conn:
            author_id = await conn.fetchval(
                "SELECT id FROM authors WHERE slug = 'poindexter-ai' LIMIT 1"
            )
            if author_id:
                return author_id

            author_id = await conn.fetchval(
                """
                INSERT INTO authors (name, slug, email, bio, avatar_url)
                VALUES ('Poindexter AI', 'poindexter-ai',
                        'poindexter@glad-labs.ai',
                        'AI Content Generation Engine', NULL)
                ON CONFLICT (slug) DO NOTHING
                RETURNING id
                """
            )
            if author_id:
                logger.info("Created default author: Poindexter AI (%s)", author_id)
                return author_id

            # Fallback: return any author row if the INSERT raced with a
            # concurrent insert and we missed the RETURNING value.
            return await conn.fetchval("SELECT id FROM authors LIMIT 1")

    except Exception as e:
        logger.error("Error getting/creating default author: %s", e, exc_info=True)
        return None
