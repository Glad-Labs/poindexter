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
    """Return the Poindexter AI author UUID; create the row if needed.

    Dedup is by ``name`` — the live ``authors`` schema is intentionally
    minimal (id / name / bio / avatar_url / created_at). An earlier
    version of this helper referenced ``slug`` + ``email`` columns
    that never existed in the squashed baseline; every publish path
    fell back to "no author" because the SELECT raised. The lookup is
    case-sensitive on a literal name string, which is sufficient for
    the single Poindexter AI row.
    """
    try:
        async with database_service.pool.acquire() as conn:
            author_id = await conn.fetchval(
                "SELECT id FROM authors WHERE name = 'Poindexter AI' LIMIT 1"
            )
            if author_id:
                return author_id

            # First-run insert. No ON CONFLICT needed — ``authors`` has
            # no UNIQUE constraint on name, and the SELECT above already
            # handled the steady-state case. Race window is one row's
            # width, then the SELECT-fallback below resolves any
            # accidental duplicate.
            author_id = await conn.fetchval(
                """
                INSERT INTO authors (name, bio, avatar_url)
                VALUES ('Poindexter AI',
                        'AI Content Generation Engine', NULL)
                RETURNING id
                """
            )
            if author_id:
                logger.info(
                    "Created default author: Poindexter AI (%s)", author_id,
                )
                return author_id

            # Fallback for the (vanishingly rare) case where the INSERT
            # raced with a concurrent insert and lost the RETURNING.
            return await conn.fetchval(
                "SELECT id FROM authors WHERE name = 'Poindexter AI' LIMIT 1"
            )

    except Exception as e:
        logger.error(
            "Error getting/creating default author: %s", e, exc_info=True,
        )
        return None
