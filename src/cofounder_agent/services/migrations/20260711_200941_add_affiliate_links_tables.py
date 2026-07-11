"""Migration 20260711_200941_add_affiliate_links_tables: add affiliate links tables

ISSUE: affiliate-link injection rebuild (design + plan under docs/superpowers/).

Background — the affiliate-link feature (an ``affiliate_links`` table + an
injection service) shipped in early 2026 and was dropped as "dead" in the #686
cleanup sweep because it had never been rewired into the atom/graph_def
pipeline. This migration restores the schema — the ``affiliate_links`` catalog
(keyword → /go/<code> → real URL) plus an ``affiliate_link_clicks`` event table
(one row per /go redirect, synced from Cloudflare Analytics Engine). Both ship
EMPTY: real referral URLs are DB-only, added via ``poindexter affiliate add``
(never seeded in source), per the March-2026 fabricated-code-leak rule.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Create ``affiliate_links`` + ``affiliate_link_clicks`` (empty, idempotent)."""
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS affiliate_links (
                id            serial PRIMARY KEY,
                code          varchar(64)  NOT NULL UNIQUE,
                keyword       varchar(100) NOT NULL UNIQUE,
                url           text         NOT NULL,
                display_text  varchar(200),
                program       varchar(100),
                is_active     boolean      NOT NULL DEFAULT true,
                clicks        integer      NOT NULL DEFAULT 0,
                created_at    timestamptz  NOT NULL DEFAULT now(),
                updated_at    timestamptz  NOT NULL DEFAULT now()
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_affiliate_links_active "
            "ON affiliate_links (is_active)"
        )
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS affiliate_link_clicks (
                id          bigserial PRIMARY KEY,
                code        varchar(64) NOT NULL,
                post_slug   text,
                referrer    text,
                country     varchar(8),
                user_agent  text,
                created_at  timestamptz NOT NULL DEFAULT now()
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_affiliate_link_clicks_code "
            "ON affiliate_link_clicks (code)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_affiliate_link_clicks_created "
            "ON affiliate_link_clicks (created_at)"
        )
    logger.info(
        "add_affiliate_links_tables up: affiliate_links + affiliate_link_clicks ready"
    )


async def down(pool) -> None:
    """Drop both tables (reverse dependency order)."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS affiliate_link_clicks")
        await conn.execute("DROP TABLE IF EXISTS affiliate_links")
    logger.info("add_affiliate_links_tables down: tables dropped")
