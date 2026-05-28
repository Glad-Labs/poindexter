"""Migration 20260528_193105: seed media_approval_discord_notify_enabled toggle.

Surface the master switch for the per-medium Discord ops ping in
``app_settings`` so the operator can flip it from the dashboard without
a code change (per ``feedback_db_configurable_design``).

The setting is consumed by
``services.media_approval_service.notify_pending_for_review`` —
defaults to enabled (empty / missing → on) so the first time a
podcast/video/short lands in the gate, the operator sees the Discord
ping. Setting it to ``false`` disables the ping cleanly. Behavior
matches the existing ``site_config.get_bool`` convention.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Seed the row idempotently.

    Idempotent via ``ON CONFLICT (key) DO NOTHING`` — re-runs against
    a DB that already has the row leave the operator's chosen value
    intact.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, description, is_active, is_secret)
            VALUES (
                'media_approval_discord_notify_enabled',
                'true',
                'Master switch — when true, a Discord ops ping fires when a '
                'newly-generated podcast/video/short lands in media_approvals '
                'with status=pending and the Layer 1 quality eval completes. '
                'Defaults to enabled; set to ''false'' to silence the channel.',
                true,
                false
            )
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info(
            "Migration seed_media_approval_discord_notify_enabled_toggle: applied"
        )


async def down(pool) -> None:
    """Remove the seeded row.

    The consumer treats a missing row as enabled, so dropping the row
    reverts to the same default behavior. Operator-tuned values that
    were not the original default are lost on down — same as every
    other ``app_settings`` seed migration in this tree.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key = 'media_approval_discord_notify_enabled'
            """
        )
        logger.info(
            "Migration seed_media_approval_discord_notify_enabled_toggle down: "
            "reverted"
        )
