"""Multi-keyword matching + platform tracking for affiliate_links.

Widens affiliate-link matching from one keyword per link to many (a new
child table), and adds a free-text `platform` column. See
docs/superpowers/specs/2026-07-12-affiliate-multi-keyword-platform-design.md.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS affiliate_link_keywords (
              id SERIAL PRIMARY KEY,
              link_id INTEGER NOT NULL REFERENCES affiliate_links(id) ON DELETE CASCADE,
              keyword TEXT NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE (link_id, keyword)
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_affiliate_link_keywords_link_id "
            "ON affiliate_link_keywords(link_id)"
        )
        await conn.execute(
            "ALTER TABLE affiliate_links "
            "ADD COLUMN IF NOT EXISTS platform VARCHAR(50) NOT NULL DEFAULT ''"
        )
        # Backfill: each existing row's single `keyword` becomes its first alias.
        await conn.execute(
            "INSERT INTO affiliate_link_keywords (link_id, keyword) "
            "SELECT id, keyword FROM affiliate_links "
            "ON CONFLICT (link_id, keyword) DO NOTHING"
        )
        await conn.execute("ALTER TABLE affiliate_links DROP COLUMN IF EXISTS keyword")
    logger.info("Migration multi_keyword_and_platform_for_affiliate_links: applied")


async def down(pool) -> None:
    """Revert the migration.

    Restores `keyword` as a plain (non-UNIQUE) column: post-migration data
    may legitimately have shared keywords across links, so re-adding the old
    global UNIQUE constraint here could fail against real data. Rolling back
    is a best-effort safety net, not a byte-for-byte restore.
    """
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE affiliate_links ADD COLUMN IF NOT EXISTS keyword TEXT")
        await conn.execute(
            """
            UPDATE affiliate_links al SET keyword = sub.keyword
            FROM (
              SELECT DISTINCT ON (link_id) link_id, keyword
              FROM affiliate_link_keywords
              ORDER BY link_id, id
            ) sub
            WHERE al.id = sub.link_id
            """
        )
        await conn.execute("ALTER TABLE affiliate_links DROP COLUMN IF EXISTS platform")
        await conn.execute("DROP TABLE IF EXISTS affiliate_link_keywords")
    logger.info("Migration multi_keyword_and_platform_for_affiliate_links: reverted")
