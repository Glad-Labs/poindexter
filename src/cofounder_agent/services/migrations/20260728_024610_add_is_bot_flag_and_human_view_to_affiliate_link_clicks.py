"""Migration 20260728_024610: add is_bot flag + human view to affiliate_link_clicks

ISSUE: Glad-Labs/poindexter#930

Affiliate click metrics were counting crawlers. Of the 403 rows present when
this was written, 210 (52%) carried a self-identifying bot user agent
(LinkupBot, AhrefsBot, SemrushBot, jscrawler, curl), and ``affiliate_links.clicks``
rolls up every row — so per-link totals shown to the operator were roughly
double the real figure.

This mirrors the split already used for beacon traffic (``page_views`` +
``page_views_human``, see docs/architecture/page-views-bot-flag.md): raw rows
are preserved for liveness and forensics, while reader surfaces select from
the ``_human`` view. Classification happens at ingest in
``services/jobs/sync_affiliate_clicks.py``; this migration adds the columns,
the view, and a one-time backfill of existing rows.

The backfill pattern is inlined rather than read from
``app_settings.affiliate_click_bot_ua_pattern`` because a migration runs once
against historical rows, while the setting governs rows arriving from now on.
The two are seeded with the same expression; drift only affects how old rows
are labelled, never new ones.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Kept in sync with settings_defaults.affiliate_click_bot_ua_pattern.
_BOT_UA_PATTERN = (
    r"(bot|crawler|spider|slurp|bingpreview|facebookexternalhit|headless|"
    r"python-requests|python-urllib|curl|wget|scrapy|monitor|uptime|"
    r"preview|fetch|axios|okhttp|go-http-client|java/|libwww)"
)


async def up(pool) -> None:
    """Add is_bot/bot_reason, backfill historical rows, create the human view."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE affiliate_link_clicks "
            "ADD COLUMN IF NOT EXISTS is_bot BOOLEAN NOT NULL DEFAULT FALSE"
        )
        await conn.execute(
            "ALTER TABLE affiliate_link_clicks "
            "ADD COLUMN IF NOT EXISTS bot_reason TEXT"
        )
        # Partial index: reader surfaces filter is_bot = false, and human rows
        # are the minority today, so the partial index stays small.
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_affiliate_link_clicks_human "
            "ON affiliate_link_clicks (created_at) WHERE is_bot = FALSE"
        )

        # One-time backfill. Monotonic like the page_views sweep: only ever
        # sets is_bot true, so re-running can add labels but never clears one.
        status = await conn.execute(
            "UPDATE affiliate_link_clicks "
            "SET is_bot = TRUE, bot_reason = 'ua:backfill' "
            "WHERE is_bot = FALSE AND user_agent IS NOT NULL "
            "AND user_agent ~* $1",
            _BOT_UA_PATTERN,
        )
        logger.info(
            "Migration 20260728_024610: backfilled bot flags (%s)", status
        )

        # A NULL user agent on a redirect endpoint is not a browser either;
        # label it separately so the two causes stay distinguishable.
        await conn.execute(
            "UPDATE affiliate_link_clicks "
            "SET is_bot = TRUE, bot_reason = 'ua:missing' "
            "WHERE is_bot = FALSE AND (user_agent IS NULL OR user_agent = '')"
        )

        await conn.execute(
            "CREATE OR REPLACE VIEW affiliate_link_clicks_human AS "
            "SELECT id, code, post_slug, referrer, country, user_agent, created_at "
            "FROM affiliate_link_clicks WHERE is_bot = FALSE"
        )

        # Re-roll affiliate_links.clicks from human rows only, so the catalog
        # totals stop double-counting crawlers the moment this lands.
        await conn.execute(
            "UPDATE affiliate_links al SET clicks = COALESCE(c.n, 0), updated_at = now() "
            "FROM (SELECT code, COUNT(*) AS n FROM affiliate_link_clicks_human "
            "GROUP BY code) c WHERE c.code = al.code"
        )
        await conn.execute(
            "UPDATE affiliate_links al SET clicks = 0, updated_at = now() "
            "WHERE NOT EXISTS (SELECT 1 FROM affiliate_link_clicks_human h "
            "WHERE h.code = al.code) AND al.clicks <> 0"
        )
    logger.info("Migration 20260728_024610: applied")


async def down(pool) -> None:
    """Drop the view, the index and both columns.

    The click roll-up is left as-is: reverting cannot know which totals were
    pre-existing, and the next sync_affiliate_clicks run recomputes it anyway.
    """
    async with pool.acquire() as conn:
        await conn.execute("DROP VIEW IF EXISTS affiliate_link_clicks_human")
        await conn.execute("DROP INDEX IF EXISTS idx_affiliate_link_clicks_human")
        await conn.execute(
            "ALTER TABLE affiliate_link_clicks DROP COLUMN IF EXISTS bot_reason"
        )
        await conn.execute(
            "ALTER TABLE affiliate_link_clicks DROP COLUMN IF EXISTS is_bot"
        )
    logger.info("Migration 20260728_024610: reverted")
