"""Migration 0096: extend ``media_assets`` for the video pipeline.

Closes Glad-Labs/poindexter#108. Grounds the video pipeline build
(Glad-Labs/poindexter#143) — every Stage that produces or uploads
media writes a row here instead of relying on filesystem scans of
``~/.poindexter/{video,podcast,images}/<post_id>.<ext>``.

The ``media_assets`` table already exists with a multi-tenant/
site-aware shape (``tenant_id``, ``site_id``, ``type``, ``url``,
``storage_path``, ``ai_metadata``). This migration adds the
columns the new video stages need without disturbing existing
callers:

- ``post_id`` — FK to ``posts(id)``. Lets the dashboard ask
  "every asset for this post."
- ``provider_plugin`` — which plugin produced the asset
  (e.g. ``video.wan2_1``, ``image.sdxl``). Useful for filtering
  the gallery view by producer.
- ``width``, ``height``, ``duration_ms``, ``file_size_bytes``,
  ``mime_type`` — physical attributes the existing schema didn't
  carry. Needed for "is this video the right size for Shorts."
- ``cost_usd`` and ``electricity_kwh`` — same axes the unified
  cost-guard records on ``cost_logs``. Lets the eco-comparison
  dashboard roll up total spend per asset family.
- ``platform_video_ids`` — JSONB map of platform → external ID
  (e.g. ``{"youtube": "abc123", "tiktok": "xyz"}``). Stable
  schema as we add platforms.

Existing columns kept as-is. The video stages will set
``type='video_long'`` / ``'video_short'`` / ``'image_featured'`` /
etc. matching the conventions already in the table.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_NEW_COLUMNS = [
    ("post_id", "UUID NULL"),
    ("provider_plugin", "VARCHAR(128) NULL"),
    ("width", "INTEGER NULL"),
    ("height", "INTEGER NULL"),
    ("duration_ms", "INTEGER NULL"),
    ("file_size_bytes", "BIGINT NULL"),
    ("mime_type", "VARCHAR(64) NULL"),
    ("cost_usd", "NUMERIC(10, 6) NULL"),
    ("electricity_kwh", "NUMERIC(12, 8) NULL"),
    ("platform_video_ids", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for column, ddl in _NEW_COLUMNS:
            await conn.execute(
                f"ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS {column} {ddl}"
            )

        # Foreign key on post_id — added separately so retries don't fail
        # if it already exists. Postgres doesn't have IF NOT EXISTS for
        # constraints, so we check pg_constraint first.
        already = await conn.fetchval(
            "SELECT 1 FROM pg_constraint WHERE conname = 'media_assets_post_id_fkey'"
        )
        if not already:
            await conn.execute(
                """
                ALTER TABLE media_assets
                ADD CONSTRAINT media_assets_post_id_fkey
                FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE SET NULL
                """
            )

        # Indexes — idempotent.
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_assets_post_id "
            "ON media_assets (post_id) WHERE post_id IS NOT NULL"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_assets_kind_created "
            "ON media_assets (type, created_at DESC)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_assets_platform_video_ids "
            "ON media_assets USING GIN (platform_video_ids)"
        )

        logger.info(
            "0096: extended media_assets with %d new columns + FK + 3 indexes",
            len(_NEW_COLUMNS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS idx_media_assets_platform_video_ids")
        await conn.execute("DROP INDEX IF EXISTS idx_media_assets_kind_created")
        await conn.execute("DROP INDEX IF EXISTS idx_media_assets_post_id")
        await conn.execute(
            "ALTER TABLE media_assets DROP CONSTRAINT IF EXISTS media_assets_post_id_fkey"
        )
        for column, _ddl in reversed(_NEW_COLUMNS):
            await conn.execute(
                f"ALTER TABLE media_assets DROP COLUMN IF EXISTS {column}"
            )
        logger.info("0096: rolled back media_assets extensions")
