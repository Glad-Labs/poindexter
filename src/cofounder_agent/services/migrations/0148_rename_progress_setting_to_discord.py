"""Migration 0148: rename ``template_runner_progress_to_telegram`` →
``template_runner_progress_streaming`` and flip default to true.

Channel discipline: Telegram is critical alerts only (worker offline,
GPU temp, cost overrun); Discord is the spam-friendly channel for
routine progress. The original setting name from migration 0146 was
misleading — the underlying notify_operator(critical=False) path
already routes to ``discord_ops``, never to ``telegram_ops``. Renaming
the key prevents future operators from thinking the spam goes to the
phone.

Default flips from false → true because Discord is meant to be noisy.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Memory: ``feedback_telegram_vs_discord``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_OLD_KEY = "template_runner_progress_to_telegram"
_NEW_KEY = "template_runner_progress_streaming"
_NEW_DESCRIPTION = (
    "When on, TemplateRunner emits per-node progress to Discord (NOT "
    "Telegram) via notify_operator(critical=False). Default true — "
    "Discord is the spam channel by design. Telegram is reserved for "
    "critical alerts only (worker offline, GPU temp, cost overrun)."
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # If the old row exists, rename it in-place; otherwise insert
        # the new key with the default-true value.
        old_present = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM app_settings WHERE key = $1)",
            _OLD_KEY,
        )
        if old_present:
            await conn.execute(
                """
                UPDATE app_settings
                   SET key         = $2,
                       value       = 'true',
                       description = $3,
                       updated_at  = NOW()
                 WHERE key = $1
                """,
                _OLD_KEY, _NEW_KEY, _NEW_DESCRIPTION,
            )
            logger.info(
                "Migration 0148: renamed %s → %s (value flipped to true)",
                _OLD_KEY, _NEW_KEY,
            )
        else:
            await conn.execute(
                """
                INSERT INTO app_settings
                  (key, value, category, description, is_secret, is_active)
                VALUES ($1, 'true', 'pipeline', $2, false, true)
                ON CONFLICT (key) DO UPDATE
                  SET description = EXCLUDED.description,
                      updated_at  = NOW()
                """,
                _NEW_KEY, _NEW_DESCRIPTION,
            )
            logger.info(
                "Migration 0148: seeded %s = true (old key not present)",
                _NEW_KEY,
            )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET key = $2, value = 'false' WHERE key = $1",
            _NEW_KEY, _OLD_KEY,
        )
        logger.info("Migration 0148 down: reverted %s → %s", _NEW_KEY, _OLD_KEY)
