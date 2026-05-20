"""discord_ops row uses ``secret_key_ref``; relax outbound-URL constraint.

ISSUE: 2026-05-20 incident — Matt rotated the Discord webhook URL into
``app_settings.discord_ops_webhook_url`` on 2026-05-15, but the
dispatcher row at ``webhook_endpoints.discord_ops`` kept its
denormalized ``url`` field at the OLD value. Discord returned
``404 Unknown Webhook`` (code 10015) for every operator notification
from 2026-05-12 onward, even though app_settings had the live URL the
whole time.

The other two outbound rows already use the right pattern:
- ``telegram_ops``: ``url='https://api.telegram.org'`` (generic, public
  base), ``secret_key_ref='telegram_bot_token'``
- ``vercel_isr``: ``url='https://www.gladlabs.io'`` (public origin),
  ``secret_key_ref='revalidate_secret'``

``discord_ops`` was the outlier — full credential URL embedded directly,
``secret_key_ref`` left NULL. The reason it was a different shape: a
Discord webhook URL is the WHOLE credential (no public-base/secret-token
split like Telegram or Vercel ISR), so there was no natural "public base"
to put in ``url``.

Schema change: relax the
``webhook_endpoints_direction_config_chk`` constraint. The old check
required ``url IS NOT NULL`` for every outbound row, which forced
``discord_ops`` to embed the credential URL directly. The new check
accepts EITHER ``url`` OR ``secret_key_ref`` for outbound rows — the
dispatcher can resolve the destination from either source.

Data change: ``discord_ops`` row gets ``secret_key_ref =
'discord_ops_webhook_url'`` and ``url = NULL``. Nulling is intentional
— per ``feedback_no_silent_defaults`` we want the handler to fail loud
if ``secret_key_ref`` ever stops resolving, NOT to silently fall through
to whatever URL got embedded (which is exactly what caused this incident).

Pairs with the ``outbound_discord.discord_post`` handler change that
prefers ``secret_key_ref`` over ``row.url`` when set, so future
rotations propagate to the dispatcher on the next call without any
manual sync.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration.

    Idempotent: re-running this against a DB that already has the new
    constraint + row state is a no-op. The constraint swap uses
    ``DROP CONSTRAINT IF EXISTS`` so partial reruns recover cleanly.
    The row UPDATE only matches rows still on the old pattern.
    """
    async with pool.acquire() as conn:
        # Relax the outbound-URL requirement: accept secret_key_ref as
        # an equivalent source for the destination.
        await conn.execute(
            """
            ALTER TABLE webhook_endpoints
                DROP CONSTRAINT IF EXISTS webhook_endpoints_direction_config_chk
            """,
        )
        await conn.execute(
            """
            ALTER TABLE webhook_endpoints
                ADD CONSTRAINT webhook_endpoints_direction_config_chk
                CHECK (
                    (direction = 'inbound'  AND url IS NULL)
                 OR (direction = 'outbound' AND (
                        url IS NOT NULL OR secret_key_ref IS NOT NULL
                    ))
                )
            """,
        )

        # Move discord_ops onto the secret_key_ref pattern, matching
        # telegram_ops + vercel_isr. NULL the embedded URL — keeping
        # both copies in sync is exactly the bug this migration removes.
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
            "Migration discord_ops_row_use_secret_key_ref_instead_of_embedded_url: "
            "constraint relaxed; row update result=%s",
            result,
        )


async def down(pool) -> None:
    """Revert is a no-op for the row state; reverts the constraint shape only.

    Restoring the embedded URL on rollback would require knowing the
    live value from ``app_settings`` (which only the running worker
    can decrypt), and would reintroduce the divergence we just fixed.
    A deploy rollback that brings back the old handler code can either
    (a) populate ``url`` by hand from app_settings + set the constraint
    back to its strict form, or (b) leave the relaxed constraint in
    place — the strict form was a superset, so relaxed always validates
    rows that the strict form accepted.

    For safety this revert restores the strict constraint only when
    every existing outbound row has a non-NULL ``url`` (preventing the
    rollback from creating a constraint that immediately fails). If
    any outbound row has ``url IS NULL`` (the post-migration state of
    discord_ops), the constraint stays relaxed and a WARNING is logged.
    """
    async with pool.acquire() as conn:
        bad = await conn.fetchval(
            """
            SELECT COUNT(*) FROM webhook_endpoints
             WHERE direction = 'outbound' AND url IS NULL
            """,
        )
        if bad and int(bad) > 0:
            logger.warning(
                "Migration down: %s outbound rows have url IS NULL — "
                "keeping the relaxed constraint to avoid rejecting them. "
                "Re-populate row.url from app_settings before reverting.",
                bad,
            )
            return
        await conn.execute(
            """
            ALTER TABLE webhook_endpoints
                DROP CONSTRAINT IF EXISTS webhook_endpoints_direction_config_chk
            """,
        )
        await conn.execute(
            """
            ALTER TABLE webhook_endpoints
                ADD CONSTRAINT webhook_endpoints_direction_config_chk
                CHECK (
                    (direction = 'inbound'  AND url IS NULL)
                 OR (direction = 'outbound' AND url IS NOT NULL)
                )
            """,
        )
        logger.info(
            "Migration discord_ops_row_use_secret_key_ref_instead_of_embedded_url down: "
            "strict url-required constraint restored"
        )
