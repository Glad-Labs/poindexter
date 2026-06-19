"""Migration 20260619_120000_repoint_ops_webhooks_to_apprise: route ops notify via apprise

Re-points the two operator-notification webhook_endpoints rows from the
per-channel ``discord_post`` / ``telegram_post`` handlers to the generic
``apprise_notify`` handler, adding an ``apprise_url`` template to each row's
``config``. Live config is a JSONB object, so the ``||`` merge is safe.

- discord_ops : apprise_url = "{secret}" (native webhook URL passthrough)
- telegram_ops: apprise_url = "tgram://{secret}/{chat_id}/" (chat_id from config)

Idempotent: the WHERE clause matches only rows still on the legacy handler.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE webhook_endpoints
               SET handler_name = 'apprise_notify',
                   config = config || '{"apprise_url": "{secret}"}'::jsonb
             WHERE name = 'discord_ops'
               AND handler_name = 'discord_post'
            """
        )
        await conn.execute(
            """
            UPDATE webhook_endpoints
               SET handler_name = 'apprise_notify',
                   config = config || '{"apprise_url": "tgram://{secret}/{chat_id}/"}'::jsonb
             WHERE name = 'telegram_ops'
               AND handler_name = 'telegram_post'
            """
        )
    logger.info("re-pointed discord_ops + telegram_ops to apprise_notify")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE webhook_endpoints
               SET handler_name = 'discord_post',
                   config = config - 'apprise_url'
             WHERE name = 'discord_ops'
               AND handler_name = 'apprise_notify'
            """
        )
        await conn.execute(
            """
            UPDATE webhook_endpoints
               SET handler_name = 'telegram_post',
                   config = config - 'apprise_url'
             WHERE name = 'telegram_ops'
               AND handler_name = 'apprise_notify'
            """
        )
    logger.info("rolled back discord_ops + telegram_ops to legacy handlers")
