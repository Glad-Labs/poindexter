"""media_asset_recorder — single source of truth for ``media_assets`` writes.

Every Stage, service, or job that produces an audio / video / image
file the pipeline keeps must call :func:`record_media_asset` after the
file lands. The function is best-effort (never raises out into a
caller) and idempotent-friendly (callers may pass a ``url_or_path``
that already exists; the helper does NOT dedupe on its own — see
``ensure_media_asset`` for the dedupe variant used by the backfill
script).

Closes Glad-Labs/poindexter#161 — before this module, only the V0
video stitch Stages wrote rows; the legacy podcast/video paths and
the featured/inline image producers wrote files but skipped the DB
record, so cleanup/retention/cost-attribution missed them.

Schema notes (matching migrations 0057 + 0096):

- ``type`` is the asset family. Conventions in use across the codebase:
  ``video_long``, ``video_short``, ``podcast``, ``featured_image``,
  ``inline_image``.
- ``source`` is the human-facing pipeline label
  (``"pipeline"``, ``"backfill"``, ``"manual"``).
- ``post_id`` ties the row back to ``posts(id)``. May be ``NULL`` for
  orphan files but every producer in the live pipeline has a post_id
  in scope, so the FK should always be set.
- ``mime_type`` is conventional: ``audio/mpeg`` / ``video/mp4`` /
  ``image/png`` / ``image/jpeg``.

The function silently returns ``None`` when the pool is missing or the
INSERT fails — this is by design. A failed media_assets write must
NEVER break the pipeline that produced the file. The operator sees
the failure in logs.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _json_dumps(payload: dict[str, Any] | None) -> str:
    """JSON-serialize ``payload`` defensively.

    asyncpg accepts a Python dict for ``jsonb`` columns, but the
    helper accepts a ``str`` so callers can stash arbitrary objects
    in metadata without first proving they're JSON-clean. Falls back
    to ``str()``-coerced values when serialization fails so the row
    insert still succeeds.
    """
    if payload is None:
        return "{}"
    try:
        return json.dumps(payload)
    except (TypeError, ValueError):
        return json.dumps({k: str(v) for k, v in payload.items()})


async def record_media_asset(
    *,
    pool: Any,
    post_id: Any,
    asset_type: str,
    storage_path: str = "",
    public_url: str = "",
    mime_type: str = "",
    file_size_bytes: int = 0,
    width: int | None = None,
    height: int | None = None,
    duration_ms: int | None = None,
    cost_usd: float = 0.0,
    electricity_kwh: float = 0.0,
    provider_plugin: str = "",
    source: str = "pipeline",
    storage_provider: str = "local",
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Insert a row into ``media_assets`` for a freshly-produced file.

    Best-effort: returns ``None`` on any failure (no pool, asyncpg
    error, schema mismatch). The pipeline that produced the file
    must keep going — see Glad-Labs/poindexter#161.

    Args:
        pool: asyncpg connection pool (e.g. ``site_config._pool``).
        post_id: Post UUID this asset belongs to. May be ``None``.
        asset_type: One of ``video_long``, ``video_short``, ``podcast``,
            ``featured_image``, ``inline_image``. Stored verbatim in
            ``media_assets.type``.
        storage_path: Absolute local path to the file on disk. Empty
            string when the file lives only in object storage.
        public_url: Public URL (R2 / S3 / CDN). Empty string when the
            upload failed but the local file is still usable.
        mime_type: MIME type. Auto-derived from ``asset_type`` if empty.
        file_size_bytes: File size on disk. Pass ``0`` if unknown.
        width / height: Pixel dimensions for image / video assets.
        duration_ms: Duration in milliseconds for audio / video assets.
        cost_usd: Generation cost (LLM / TTS API). ``0.0`` for free local.
        electricity_kwh: GPU energy cost from the cost guard.
        provider_plugin: Registered plugin name that produced the file
            (e.g. ``image.sdxl``, ``tts.edge_tts``,
            ``compositor.ffmpeg_local``).
        source: Pipeline phase label — defaults to ``"pipeline"``.
            Backfill scripts pass ``"backfill"`` to keep rows distinct.
        storage_provider: ``"local"`` / ``"cloudflare_r2"`` / ``"s3"`` etc.
        metadata: Free-form JSON; coerced to ``str`` if not serializable.

    Returns:
        The new row's UUID as a string, or ``None`` on any failure.
    """
    if pool is None:
        logger.debug(
            "[media_assets] no DB pool — row not persisted (asset_type=%s post_id=%s)",
            asset_type, post_id,
        )
        return None

    # Auto-derive MIME type when the caller didn't supply one. Keeps
    # producers concise — they don't need to repeat the ext→type map.
    if not mime_type:
        mime_type = _DEFAULT_MIME_TYPES.get(asset_type, "application/octet-stream")

    try:
        async with pool.acquire() as conn:
            row_id = await conn.fetchval(
                """
                INSERT INTO media_assets (
                    type, source, storage_provider, url, storage_path,
                    metadata, post_id, provider_plugin,
                    width, height, duration_ms, file_size_bytes,
                    mime_type, cost_usd, electricity_kwh
                ) VALUES (
                    $1, $2, $3, $4, $5,
                    $6::jsonb, $7, $8,
                    $9, $10, $11, $12,
                    $13, $14, $15
                )
                RETURNING id
                """,
                asset_type,
                source,
                storage_provider,
                public_url or "",
                storage_path or "",
                _json_dumps(metadata),
                post_id,
                provider_plugin or "",
                width,
                height,
                duration_ms,
                file_size_bytes,
                mime_type,
                cost_usd,
                electricity_kwh,
            )
            return str(row_id) if row_id else None
    except Exception as exc:
        logger.warning(
            "[media_assets] INSERT failed (asset_type=%s post_id=%s): %s",
            asset_type, post_id, exc,
        )
        return None


_DEFAULT_MIME_TYPES = {
    "video_long": "video/mp4",
    "video_short": "video/mp4",
    "video": "video/mp4",
    "podcast": "audio/mpeg",
    "audio": "audio/mpeg",
    "featured_image": "image/jpeg",
    "inline_image": "image/png",
    "image_featured": "image/jpeg",
    "image": "image/jpeg",
}


# ---------------------------------------------------------------------------
# Idempotent helpers — used by the backfill script
# ---------------------------------------------------------------------------


async def media_asset_exists(
    *,
    pool: Any,
    post_id: Any,
    asset_type: str,
    storage_path: str = "",
    public_url: str = "",
) -> bool:
    """Return True if a media_assets row already exists for this asset.

    Match strategy (any of the predicates below is sufficient):

    1. Same post_id + asset_type + storage_path
    2. Same post_id + asset_type + public_url

    Falls back to ``False`` if the pool is missing or the query fails —
    callers treat that as "not found, safe to insert" and the worst
    case is a duplicate row, not data loss.
    """
    if pool is None or post_id is None:
        return False
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchval(
                """
                SELECT 1
                FROM media_assets
                WHERE post_id = $1
                  AND type = $2
                  AND (
                        ($3 <> '' AND storage_path = $3)
                     OR ($4 <> '' AND url = $4)
                  )
                LIMIT 1
                """,
                post_id, asset_type,
                storage_path or "",
                public_url or "",
            )
            return bool(row)
    except Exception as exc:
        logger.debug(
            "[media_assets] dedupe check failed (post_id=%s type=%s): %s",
            post_id, asset_type, exc,
        )
        return False


async def file_size_safe(path: str) -> int:
    """Return file size in bytes, or 0 if the file is missing/unreadable.

    Tiny convenience wrapper that callers can use without sprinkling
    ``try``/``OSError`` blocks at every producer site.
    """
    if not path:
        return 0
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
