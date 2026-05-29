"""Migration 20260529_050524: backfill storage_public_url and media_upload_delay_seconds from legacy r2 keys

ISSUE: Glad-Labs/poindexter#731

Finishes the object-store config cutover from the legacy ``r2_*`` keys
to the provider-agnostic ``storage_*`` namespace. Production code stopped
reading ``r2_public_url`` and ``media_r2_upload_delay_seconds`` in the same
PR; this migration guarantees the replacement keys exist and carry the
operator's already-configured values so nothing breaks at cutover.

What it does (all idempotent):

1. Ensure the ``storage_public_url`` row exists (seed empty if missing).
2. Backfill ``storage_public_url`` from the legacy ``r2_public_url`` value
   ONLY when ``storage_public_url`` is currently empty — never clobber an
   already-configured storage value.
3. Ensure ``media_upload_delay_seconds`` exists, seeded from the legacy
   ``media_r2_upload_delay_seconds`` value when present, else the default
   ``'240'``.

The old ``r2_public_url`` / ``media_r2_upload_delay_seconds`` rows are
intentionally left in place — a later cleanup PR removes them from the
seeds + DB once this cutover has shipped.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration.

    Safe to re-run: row creation uses ``ON CONFLICT (key) DO NOTHING`` and
    the public-url backfill only fills an empty target, so a second run is
    a no-op.
    """
    async with pool.acquire() as conn:
        # 1. Ensure storage_public_url exists so the key is always present.
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_active, is_secret)
            VALUES (
                'storage_public_url', '', 'general',
                'Public object-store base URL (S3-compatible: R2/S3/B2/MinIO) — set via poindexter set-setting',
                true, false
            )
            ON CONFLICT (key) DO NOTHING
            """
        )

        # 2. Backfill storage_public_url from the legacy r2_public_url value,
        #    but only when storage_public_url is still empty so we never
        #    overwrite a value the operator already set on the new key.
        await conn.execute(
            """
            UPDATE app_settings
               SET value = r2.value
              FROM (
                    SELECT value FROM app_settings WHERE key = 'r2_public_url'
                   ) AS r2
             WHERE app_settings.key = 'storage_public_url'
               AND COALESCE(NULLIF(app_settings.value, ''), '') = ''
            """
        )

        # 3. Ensure media_upload_delay_seconds exists, seeded from the legacy
        #    media_r2_upload_delay_seconds value when present, else '240'.
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_active, is_secret)
            VALUES (
                'media_upload_delay_seconds',
                COALESCE(
                    NULLIF(
                        (SELECT value FROM app_settings
                          WHERE key = 'media_r2_upload_delay_seconds'),
                        ''
                    ),
                    '240'
                ),
                'general',
                'Wait this many seconds after a post publishes before uploading podcast/video/short to the object-store CDN',
                true, false
            )
            ON CONFLICT (key) DO NOTHING
            """
        )

        logger.info(
            "Migration backfill_storage_public_url_and_media_upload_delay_seconds_from_legacy_r2_keys: applied"
        )


async def down(pool) -> None:
    """Revert the migration.

    Removes only the two rows this migration may have introduced. The
    backfilled value on a pre-existing ``storage_public_url`` row is not
    restored — same one-way posture as every other backfill migration in
    this tree. The legacy ``r2_*`` rows were never touched by ``up()``.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
             WHERE key IN ('storage_public_url', 'media_upload_delay_seconds')
            """
        )
        logger.info(
            "Migration backfill_storage_public_url_and_media_upload_delay_seconds_from_legacy_r2_keys down: reverted"
        )
