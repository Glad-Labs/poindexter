"""Migration 20260928_174425: pin storage_provider to s3 on installs with a configured object store

ISSUE: Glad-Labs/poindexter#1100

``storage_provider`` is new. ``settings_defaults`` seeds it ``local``, so a fresh
install publishes to a folder the worker serves at ``/site/`` instead of
skipping every upload for want of a bucket. An install that already has a
bucket must keep sending uploads to it. Flipping it to ``local`` would stop the
live site from receiving new posts, images and feeds while every publish still
reported success.

Ordering. ``StartupManager._run_migrations`` runs migrations before
``seed_all_defaults``, and ``poindexter setup`` / ``poindexter migrate`` do the
same, so on an existing install this writes ``s3`` first and the seeder's
``ON CONFLICT DO NOTHING`` leaves it alone. If a checkout seeded the key before
this file ran, the row reads ``local`` and is upgraded to ``s3``. Nothing else
can have written it: the key did not exist before this change, so a ``local``
value here can only be the seeded default, never an operator's choice.

"Configured" means any non-empty value among the object-store keys the upload
path reads, in either spelling (``storage_*`` or the legacy
``cloudflare_r2_*``). The test is broad on purpose. A false positive keeps
today's behaviour; a false negative would quietly move a live site's uploads to
a local folder.

Code reads a missing ``storage_provider`` row as ``s3`` as well, which covers a
Prefect flow run that loads new code before the worker has booted and run this.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_NAME = "pin_storage_provider_to_s3_on_installs_with_a_configured_object_store"

# Every object-store key R2UploadService reads, in both spellings. A non-empty
# value in any of them means an operator configured a bucket.
OBJECT_STORE_KEYS = (
    "storage_endpoint",
    "storage_bucket",
    "storage_access_key",
    "storage_secret_key",
    "storage_public_url",
    "cloudflare_r2_endpoint",
    "cloudflare_r2_bucket",
    "cloudflare_r2_access_key",
    "cloudflare_r2_secret_key",
    "cloudflare_r2_public_url",
)


async def up(pool) -> None:
    """Pin ``storage_provider=s3`` when an object store is configured."""
    async with pool.acquire() as conn:
        configured = await conn.fetch(
            "SELECT key FROM app_settings "
            "WHERE key = ANY($1::text[]) AND COALESCE(value, '') <> ''",
            list(OBJECT_STORE_KEYS),
        )
        if not configured:
            logger.info(
                "Migration %s: no object store configured; storage_provider "
                "keeps the seeded default (local)",
                _NAME,
            )
            return
        pinned = await conn.fetchval(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active,
                 owner, value_type, updated_at)
            VALUES
                ('storage_provider', 's3', 'integrations',
                 'Pinned to s3 because an object store was already configured '
                 '(migration 20260928_174425)',
                 FALSE, TRUE, 'local_site', 'string', NOW())
            ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = NOW()
                WHERE app_settings.value = 'local'
            RETURNING value
            """,
        )
    logger.info(
        "Migration %s: object store configured (%s); storage_provider %s",
        _NAME,
        ", ".join(sorted(r["key"] for r in configured)),
        "pinned to s3" if pinned else "already set, left alone",
    )


async def down(pool) -> None:
    """One-way: nothing to revert.

    Deleting the row would let the next boot seed ``local`` over an install
    with a working bucket, the outcome ``up`` exists to prevent. To change
    providers, set the value: ``poindexter settings set storage_provider local``.
    """
    return
