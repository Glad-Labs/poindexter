"""Migration 0117: object_stores table (GH-113).

Renumbered from 0093 on 2026-04-30 to resolve a number collision with
``0093_create_qa_gates_table.py``. The qa_gates migration kept 0093
because the immediately-following ``0094_seed_qa_gates_default_chain.py``
depends on it; object_stores has no in-tree dependency ordering against
0094-0116, so the safe move was to slot it after the current max.

Phase D of the Declarative Data Plane RFC; implements GH-113.

Today, S3-compatible storage targets (Cloudflare R2, AWS S3, Backblaze B2,
MinIO, Wasabi) are configured through a flat ``storage_*`` namespace in
``app_settings``. Functionally that works for the single-bucket case the
pipeline started with, but it cannot express:

  - Multiple buckets (e.g. podcast files in one R2 bucket, image
    backups in a second, archive in a third).
  - Per-store cache-busting strategies (the podcast pipeline currently
    hardcodes a ``podcast_cdn_version`` prefix; that should be a row
    attribute, not service-specific code).
  - Per-store credential references that an operator can rotate via
    ``poindexter stores set-secret <name>`` without restart.

This migration creates the ``object_stores`` table and seeds a single
``primary`` row from the current ``storage_*`` settings (with a
``cloudflare_r2_*`` fallback to handle in-flight #198 deployments).

The seed reads its values from app_settings at migration time, NOT
from hardcoded defaults — empty rows are still inserted so the
operator only has to fill in the gaps, not the whole shape.

### Standard handler

A single S3-compatible boto3 client (``services.r2_upload_service``)
handles every row regardless of provider — the ``endpoint_url`` field
is what swaps R2 for S3 vs. B2 vs. MinIO. ``provider`` is metadata
only (used by Grafana for per-store dashboards).

### Back-compat

The old ``storage_*`` keys keep working through ``upload_to_r2``,
which is now a thin shim over ``upload_to_store("primary", ...)``.
Consumers migrate to ``upload_to_store(...)`` in follow-up tickets.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


SQL_UP = """
CREATE TABLE IF NOT EXISTS object_stores (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Stable slug, e.g. "primary", "podcast_cdn", "backup_archive".
    name                     text NOT NULL UNIQUE,
    -- Provider tag for dashboards / metrics. Functionally all rows are
    -- handled by the same S3-compatible boto3 client; this is metadata.
    -- Allowed values (soft, not enforced via CHECK so new providers
    -- can be added without a migration):
    --   cloudflare_r2, aws_s3, backblaze_b2, minio, wasabi
    provider                 text NOT NULL,
    -- S3-compatible endpoint URL. Empty for AWS S3 (boto3 default).
    endpoint_url             text,
    -- Bucket name (case-sensitive).
    bucket                   text NOT NULL,
    -- Public CDN base URL used to construct the post-upload public link.
    -- Optional — some buckets are private and the URL is built by the
    -- consumer (e.g. signed URLs for premium asset downloads).
    public_url               text,
    -- app_settings key holding encrypted ``{access_key, secret_key}``
    -- JSON. Defaults to ``storage_credentials`` for the bootstrap
    -- ``primary`` row; per-store rows pick their own ref.
    credentials_ref          text NOT NULL DEFAULT 'storage_credentials',
    -- How the upload key is munged for cache-busting. Recognized values:
    --   none           — write the key verbatim
    --   version_prefix — prepend a versioned segment (cache_busting_config.version)
    cache_busting_strategy   text NOT NULL DEFAULT 'none',
    -- Strategy-specific config. For ``version_prefix``: {"version": "v2"}.
    -- Free-form JSON so future strategies can add fields without a migration.
    cache_busting_config     jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Every row ships disabled. Activation is always deliberate so
    -- operators can drop in a row, fill in credentials, then flip the
    -- flag once they're confident.
    enabled                  boolean NOT NULL DEFAULT false,
    metadata                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Observability counters updated by the upload dispatcher after
    -- every call. Mirrors the external_taps / webhook_endpoints
    -- conventions so the same Grafana panels work across all three.
    last_upload_at           timestamptz,
    last_upload_status       text,           -- 'success' | 'failed'
    last_error               text,
    total_uploads            bigint NOT NULL DEFAULT 0,
    total_failures           bigint NOT NULL DEFAULT 0,
    total_bytes_uploaded     bigint NOT NULL DEFAULT 0,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_object_stores_name
    ON object_stores (name);

CREATE INDEX IF NOT EXISTS idx_object_stores_enabled
    ON object_stores (enabled);

CREATE OR REPLACE FUNCTION object_stores_touch_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS object_stores_touch_updated_at_trg ON object_stores;
CREATE TRIGGER object_stores_touch_updated_at_trg
    BEFORE UPDATE ON object_stores
    FOR EACH ROW EXECUTE FUNCTION object_stores_touch_updated_at();
"""


SQL_DOWN = """
DROP TRIGGER IF EXISTS object_stores_touch_updated_at_trg ON object_stores;
DROP FUNCTION IF EXISTS object_stores_touch_updated_at();
DROP TABLE IF EXISTS object_stores;
"""


# Seed for the one row that's guaranteed to exist post-migration. We
# read values from current app_settings (storage_* preferred, legacy
# cloudflare_r2_* fallback) — that way an operator who has already
# wired up the old flat namespace gets a working ``primary`` row
# without re-entering anything. Missing values land as NULL/empty,
# which the upload code already tolerates (the old codepath fell back
# to logging a warning in that case).
async def _seed_primary_from_storage_settings(conn) -> None:
    """Pull current storage_* / cloudflare_r2_* settings and insert a
    ``primary`` row into ``object_stores`` if it doesn't exist yet.

    Idempotent — ON CONFLICT DO NOTHING. If an operator already has a
    ``primary`` row (e.g. from a manual insert), we leave it alone.
    """
    async def _setting(key: str, fallback_key: str = "") -> str | None:
        # Read non-secret values directly. Secrets (access/secret key)
        # are NOT seeded here — they stay in app_settings under the
        # ``credentials_ref`` pointer and are resolved at upload time.
        for k in (key, fallback_key) if fallback_key else (key,):
            if not k:
                continue
            row = await conn.fetchrow(
                "SELECT value FROM app_settings WHERE key = $1 AND is_active = true",
                k,
            )
            if row and row["value"]:
                return row["value"]
        return None

    # Provider — defaults to cloudflare_r2 because that's what every
    # historical deployment used. Operators with S3/B2/MinIO will have
    # set storage_provider explicitly, so the lookup picks that up.
    provider = await _setting("storage_provider") or "cloudflare_r2"
    endpoint_url = await _setting("storage_endpoint", "cloudflare_r2_endpoint")
    bucket = await _setting("storage_bucket", "cloudflare_r2_bucket") or ""
    public_url = await _setting("storage_public_url", "r2_public_url")

    # Whether to enable the row depends on whether we have enough
    # config to actually upload. If we have a bucket, we have something
    # — credentials live behind credentials_ref and can be set later
    # without flipping enabled back off.
    enabled = bool(bucket)

    await conn.execute(
        """
        INSERT INTO object_stores (
            name, provider, endpoint_url, bucket, public_url,
            credentials_ref, cache_busting_strategy, enabled, metadata
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
        ON CONFLICT (name) DO NOTHING
        """,
        "primary",
        provider,
        endpoint_url,
        bucket,
        public_url,
        "storage_credentials",
        "none",
        enabled,
        '{"seeded_from": "storage_settings", "migration": "0117"}',
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_UP)
        await _seed_primary_from_storage_settings(conn)
        count = await conn.fetchval("SELECT count(*) FROM object_stores")
        logger.info(
            "0117: object_stores table created + seeded; %d total row(s)", count
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_DOWN)
        logger.info("0117: dropped object_stores table")
