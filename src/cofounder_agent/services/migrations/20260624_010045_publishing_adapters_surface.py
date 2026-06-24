"""Migration 20260624_010045: add ``surface`` column to ``publishing_adapters``.

Social posts (text + URL) and media uploads (MP4 + title) are two
separate dispatch surfaces that should never cross.  Before this
migration, ``social_poster._distribute_to_adapters`` called
``load_enabled_publishers`` which returned ALL enabled rows — including
the YouTube row, which expects a ``{"media_path", "title"}`` payload.
This caused a ``TypeError`` on every social-post distribution run because
the YouTube handler received ``{"text", "url"}`` instead.

The ``surface`` column distinguishes social adapters (mastodon, twitter,
bluesky, …) from media/video adapters (youtube, vimeo, …):

* ``'social'`` — called by ``social_poster._distribute_to_adapters``.
  Default for new rows so existing adapters (mastodon) keep working
  without a data edit.
* ``'media'``  — called by ``media_distribute._dispatch_asset``.
  Applied here to the youtube row.

``load_enabled_publishers`` gains an optional ``surface`` filter so each
caller fetches only the adapters that belong to its payload contract.

stdlib-only — the migrations-smoke CI step applies it without a full app
boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE publishing_adapters
              ADD COLUMN IF NOT EXISTS surface TEXT NOT NULL DEFAULT 'social'
            """
        )
        await conn.execute(
            """
            UPDATE publishing_adapters
               SET surface = 'media'
             WHERE platform = 'youtube'
            """
        )
    logger.info(
        "publishing_adapters_surface up: added surface column, "
        "set youtube rows to 'media'"
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE publishing_adapters DROP COLUMN IF EXISTS surface"
        )
    logger.info("publishing_adapters_surface down: dropped surface column")
