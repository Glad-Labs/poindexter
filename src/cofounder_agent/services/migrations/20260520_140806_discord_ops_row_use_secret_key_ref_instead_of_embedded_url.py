"""discord_ops row uses ``secret_key_ref`` instead of an embedded URL.

ISSUE: 2026-05-20 incident — Matt rotated the Discord webhook URL into
``app_settings.discord_ops_webhook_url`` on 2026-05-15, but the
dispatcher row at ``webhook_endpoints.discord_ops`` kept its
denormalized ``url`` field at the OLD value. Discord returned
``404 Unknown Webhook`` (code 10015) for every operator notification
from 2026-05-12 onward (the URL's actual rotation moment on Discord's
side), even though app_settings had the live URL the whole time.

The other two outbound rows already use the right pattern:
- ``telegram_ops``: ``url='https://api.telegram.org'`` (generic, public
  base), ``secret_key_ref='telegram_bot_token'``
- ``vercel_isr``: ``url='https://www.gladlabs.io'`` (public origin),
  ``secret_key_ref='revalidate_secret'``

``discord_ops`` was the outlier — full credential URL embedded directly,
``secret_key_ref`` left NULL. This migration pairs with the
``outbound_discord.discord_post`` handler change that prefers
``secret_key_ref`` over ``row.url`` when set, so future rotations
propagate to the dispatcher on the next call without any manual sync.

Schema change: none. Data change: discord_ops row gets
``secret_key_ref = 'discord_ops_webhook_url'``; the ``url`` column is
nulled so the handler can't silently fall through to a stale embedded
value if ``secret_key_ref`` ever resolves to NULL. Fail-loud rather
than fail-into-stale per ``feedback_no_silent_defaults``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration.

    Idempotent: the WHERE clause only matches rows still on the old
    pattern (no secret_key_ref), so re-running this is a no-op once
    applied. A fresh DB where the row hasn't been seeded yet also
    no-ops cleanly (UPDATE on zero rows).
    """
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE webhook_endpoints
               SET secret_key_ref = 'discord_ops_webhook_url',
                   url = NULL,
                   updated_at = NOW()
             WHERE name = 'discord_ops'
               AND (secret_key_ref IS NULL OR secret_key_ref = '')
            """,
        )
        logger.info(
            "Migration discord_ops_row_use_secret_key_ref_instead_of_embedded_url: %s",
            result,
        )


async def down(pool) -> None:
    """Revert is a no-op.

    The forward migration nulls the denormalized URL because keeping
    both copies in sync is precisely the bug this migration removes.
    Restoring an embedded URL on rollback would require knowing the
    live value from ``app_settings`` (which only the running worker
    can decrypt), and reintroducing the divergence we just fixed. The
    handler change is independently rollback-safe — it still falls
    back to ``row.url`` if ``secret_key_ref`` doesn't resolve, so a
    deploy rollback that brings back the old code can populate ``url``
    by hand if needed.
    """
    logger.info(
        "Migration discord_ops_row_use_secret_key_ref_instead_of_embedded_url down: no-op"
    )
