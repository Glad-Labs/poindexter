"""Migration 20260712_140000: add category and description to affiliate_links

Adds the two columns the public /referrals page needs to render a name,
description, and service/product grouping from a DB-driven export instead
of a hand-maintained file. Existing rows land on the DEFAULT values
(category='product', description='') — the CLI's `poindexter affiliate add`
requires both flags explicitly for new/updated rows, so these defaults are
a schema-safety net, not the intended steady state.

See docs/superpowers/specs/2026-07-12-affiliate-referrals-page-design.md.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE affiliate_links
              ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT '',
              ADD COLUMN IF NOT EXISTS category varchar(20) NOT NULL DEFAULT 'product'
            """
        )
        await conn.execute(
            "ALTER TABLE affiliate_links "
            "DROP CONSTRAINT IF EXISTS affiliate_links_category_check"
        )
        await conn.execute(
            "ALTER TABLE affiliate_links ADD CONSTRAINT affiliate_links_category_check "
            "CHECK (category IN ('service', 'product'))"
        )
    logger.info("Migration add_category_and_description_to_affiliate_links: applied")


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE affiliate_links "
            "DROP CONSTRAINT IF EXISTS affiliate_links_category_check"
        )
        await conn.execute(
            "ALTER TABLE affiliate_links DROP COLUMN IF EXISTS category"
        )
        await conn.execute(
            "ALTER TABLE affiliate_links DROP COLUMN IF EXISTS description"
        )
    logger.info("Migration add_category_and_description_to_affiliate_links: reverted")
