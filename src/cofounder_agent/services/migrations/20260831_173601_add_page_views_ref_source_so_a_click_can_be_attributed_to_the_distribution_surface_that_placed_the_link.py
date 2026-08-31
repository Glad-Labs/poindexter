"""Add ``page_views.ref_source`` — which distribution surface sent this reader.

ISSUE: Glad-Labs/glad-labs-stack (distribution attribution)

Every link Poindexter places on another platform pointed at a bare
``{site_url}/posts/{slug}``, so the only attribution signal a view carried was
``document.referrer`` — which social apps and in-app browsers routinely
suppress (X collapses to ``t.co``, Dev.to sends nothing at all). Over the 90
days to 2026-08-31 the entire outbound estate — 77 posted promos across X and
Bluesky, plus 146 Dev.to crossposts — accounted for THREE identifiable
referrals, and there was no way to tell "this surface delivers nothing" from
"this surface delivers invisibly". Deciding whether to add a seventh surface
on that evidence is a coin toss.

``services/distribution_ref.py`` now tags every outbound link with the surface
that placed it; this column is where that tag lands. The chain is
ViewTracker (lifts it out of ``window.location.search``) → the page-views
Worker (blob6) → ``SyncCloudflareAnalyticsJob`` → here.

**Why a column and not the path.** The tag deliberately does NOT ride in
``page_views.path``: ``posts.view_count``, the ``lab_outcomes_v1`` windows and
every slug join key on path/slug, so folding a query string into them would
fragment each of those groupings into one row per surface. The surface is a
separate dimension and is stored as one.

``page_views_human`` is recreated to carry the new column. It is a plain view
over the same table, so this is a widening only — every existing consumer
selects by name and is untouched.

Idempotent: ``ADD COLUMN IF NOT EXISTS`` plus ``CREATE OR REPLACE VIEW``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Partial index: the overwhelming majority of views are untagged (direct and
# organic), and those rows are never the ones a yield query is looking for.
# Indexing only the tagged minority keeps it small enough to stay useful as the
# table grows, which is the same reasoning behind idx_page_views_human_*.
_INDEX_SQL = """
    CREATE INDEX IF NOT EXISTS idx_page_views_ref_source
        ON page_views (ref_source, created_at DESC)
     WHERE ref_source IS NOT NULL
"""

_VIEW_SQL = """
    CREATE OR REPLACE VIEW page_views_human AS
     SELECT id,
            path,
            slug,
            referrer,
            user_agent,
            created_at,
            ref_source
       FROM page_views
      WHERE is_bot = false
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE page_views "
            "ADD COLUMN IF NOT EXISTS ref_source varchar(32)"
        )
        await conn.execute(_INDEX_SQL)
        await conn.execute(_VIEW_SQL)
    logger.info(
        "Migration add page_views.ref_source: column + partial index + "
        "page_views_human recreated with the new column"
    )


async def down(pool) -> None:
    """Drop the index; deliberately RETAIN the column.

    lab_outcomes_v1 is built on page_views_human, which is built on this
    column. Postgres will not drop a column a view selects, and the only ways
    through are both worse than leaving it: DROP VIEW … CASCADE takes
    lab_outcomes_v1 with it, and rebuilding that view from a copy of its
    definition pasted into this file guarantees the copy rots the first time
    the real definition changes.

    So this reverts what is safely revertible — the index and the tagging
    behaviour, which lives in distribution_ref_enabled — and says plainly
    that an unused nullable column stays behind. Dropping it is a deliberate
    operator action against the live view dependency, not something a
    rollback should do on its own.
    """
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS idx_page_views_ref_source")
    logger.warning(
        "Migration add page_views.ref_source: index dropped. page_views.ref_source "
        "and its page_views_human column are RETAINED — lab_outcomes_v1 depends on "
        "the view, so dropping the column would require DROP VIEW ... CASCADE. The "
        "column is nullable and unread once distribution_ref_enabled is false."
    )
