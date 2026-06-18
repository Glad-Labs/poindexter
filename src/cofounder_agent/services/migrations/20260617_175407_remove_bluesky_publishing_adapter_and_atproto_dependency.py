"""Migration 20260617_175407: remove bluesky publishing adapter and atproto dependency

ISSUE: Glad-Labs/poindexter — Trivy HIGH GHSA-537c-gmf6-5ccf
       ("Vulnerable OpenSSL included in cryptography wheels")

The ``cryptography`` wheels bundled a vulnerable OpenSSL through 46.x; the
fix lands in cryptography 48.0.1. The ``atproto`` (Bluesky AT Protocol) SDK
hard-caps ``cryptography<47`` in every published release, which pinned both
Poetry lockfiles at the vulnerable 46.0.7 and made the security bump
unresolvable. Rather than suppress a real HIGH, the Bluesky cross-posting
capability — the only ``atproto`` consumer — was retired so cryptography can
move to 48.0.1.

This migration removes the live data the retired feature left behind on
existing databases: the seeded ``bluesky_main`` row in ``publishing_adapters``
(whose ``publishing.bluesky`` handler no longer exists), the now-dead
``bluesky`` token in the ``social_distribution_platforms`` setting, and any
dormant Bluesky credential rows. The baseline seed file was updated in
lockstep, so fresh databases never create these rows in the first place
(making every statement below a no-op on a clean install).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration.

    Idempotent — each statement is guarded so a re-run is harmless (the
    runner records each migration after a successful apply, but this is
    defense-in-depth per ``docs/operations/migrations.md``).
    """
    async with pool.acquire() as conn:
        # 1. Drop the seeded bluesky publishing-adapter row. Its handler
        #    (publishing.bluesky) was deleted alongside the adapter, so an
        #    enabled row would dispatch to a handler that no longer exists.
        adapters = await conn.execute(
            "DELETE FROM publishing_adapters "
            "WHERE platform = 'bluesky' OR handler_name = 'bluesky'"
        )

        # 2. Strip the now-dead 'bluesky' token from the CSV
        #    social_distribution_platforms setting. unnest/string_agg keeps
        #    any sibling platforms intact: 'bluesky' -> '',
        #    'bluesky,mastodon' -> 'mastodon', 'a,bluesky,b' -> 'a,b'.
        await conn.execute(
            """
            UPDATE app_settings
               SET value = (
                    SELECT COALESCE(string_agg(tok, ','), '')
                      FROM unnest(string_to_array(value, ',')) AS tok
                     WHERE tok <> 'bluesky'
               )
             WHERE key = 'social_distribution_platforms'
               AND 'bluesky' = ANY(string_to_array(value, ','))
            """
        )

        # 3. Remove dormant Bluesky credential rows — unreachable now that
        #    the adapter is gone. No-op when they were never configured.
        await conn.execute(
            "DELETE FROM app_settings "
            "WHERE key IN ('bluesky_identifier', 'bluesky_app_password')"
        )

        logger.info(
            "Migration 20260617_175407: bluesky publishing adapter retired (%s)",
            adapters,
        )


async def down(pool) -> None:  # noqa: ARG001
    """Revert — intentionally a no-op.

    This is a one-way feature retirement: the ``atproto`` dependency, the
    Bluesky adapter, and the ``publishing.bluesky`` handler were all removed
    from the tree in the same change. Re-inserting the ``publishing_adapters``
    row here would recreate a row pointing at a handler that no longer
    exists, so there is nothing safe to restore. To bring Bluesky back,
    re-add the dependency + adapter code and re-seed the row.
    """
    logger.info(
        "Migration 20260617_175407 down: no-op (one-way Bluesky retirement)"
    )
