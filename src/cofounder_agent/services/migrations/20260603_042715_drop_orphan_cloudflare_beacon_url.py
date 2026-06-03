"""Migration: drop the orphan cloudflare_beacon_url app_setting (poindexter#567).

The real beacon URL the public site reads is Vercel's
``NEXT_PUBLIC_BEACON_URL`` env var; ``cloudflare_beacon_url`` had ZERO
production readers — only the baseline seed + a parity test referenced it.
Idempotent (DELETE no-ops when the key is already absent).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = 'cloudflare_beacon_url'"
        )
    logger.info("Migration drop_orphan_cloudflare_beacon_url: applied")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
              (key, value, category, description, is_secret, is_active)
            VALUES ('cloudflare_beacon_url', '', 'cloudflare',
                    'Public URL of the deployed page-views-beacon Cloudflare Worker.',
                    false, true)
            ON CONFLICT (key) DO NOTHING
            """
        )
    logger.info("Migration drop_orphan_cloudflare_beacon_url down: re-seeded")
