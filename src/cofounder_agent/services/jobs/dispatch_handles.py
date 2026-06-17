"""Shared YouTube external-handle capture+persist for video dispatch.

``media_distribute`` (asset-keyed) delivers Gate-2-approved videos to the enabled
``publishing_adapters`` and, on a successful upload, captures the platform's
external handles: the video id is merged into ``media_assets.platform_video_ids``
(a non-clobbering jsonb ``||`` merge) and a ``pipeline_distributions`` row is
upserted (``status='published'``) for observability + re-upload dedupe. This
module single-sources those pieces: the :class:`PlatformDispatchResult` value
type, the two SQL statements, and the per-platform merge+insert loop
(:func:`persist_platform_handles`).

(Originally extracted after #1584 / #1601 to de-duplicate byte-identical copies
shared with the post-keyed ``backfill_videos`` disk-scan job; ``backfill_videos``
was retired in #1460, leaving ``media_distribute`` the sole caller.)

The caller keeps its own thin ``_persist_dispatch_result`` wrapper because it
stamps ``record_dispatched`` in its own transaction. :func:`persist_platform_handles`
therefore does the merge+insert loop ONLY (no ``record_dispatched``); the caller
owns the transaction and the dispatch stamp.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlatformDispatchResult:
    """Outcome of delivering one asset to one video platform.

    A dispatch helper returns one of these per enabled adapter so the caller can
    both decide the aggregate dispatch outcome (``record_dispatched``) AND persist
    the platform's external handles (``external_id`` / ``url``). The upload handler
    returns the external video id under its ``post_id`` key and the public watch
    URL under ``url`` (see
    ``services/integrations/handlers/publishing_youtube.py``); they're captured
    here under clearer names. ``external_id`` is ``None`` on failure.
    """

    platform: str
    success: bool
    external_id: str | None = None
    url: str | None = None


# Merge one platform's external video id into the asset's platform_video_ids
# jsonb. The ``||`` concat operator is a shallow merge, so {"youtube": "<id>"}
# replaces only the youtube key and leaves any other platform's id intact —
# the asset can be cross-posted without one delivery clobbering another's id.
_MERGE_PLATFORM_VIDEO_ID_SQL = """
    UPDATE media_assets
       SET platform_video_ids = platform_video_ids || $2::jsonb,
           updated_at = NOW()
     WHERE id = $1::uuid
"""

# Record the external delivery for observability + dedupe. ``task_id`` is the
# media_assets task key (== posts.metadata->>'pipeline_task_id', the FK target
# for pipeline_distributions). Upserted on the (task_id, target) unique key so a
# re-dispatch refreshes the same row rather than duplicating it.
_RECORD_DISTRIBUTION_SQL = """
    INSERT INTO pipeline_distributions
        (task_id, target, status, external_id, external_url, post_id, published_at)
    VALUES ($1, $2, 'published', $3, $4, $5::uuid, NOW())
    ON CONFLICT (task_id, target) DO UPDATE SET
        status       = EXCLUDED.status,
        external_id  = COALESCE(EXCLUDED.external_id, pipeline_distributions.external_id),
        external_url = COALESCE(EXCLUDED.external_url, pipeline_distributions.external_url),
        post_id      = COALESCE(EXCLUDED.post_id, pipeline_distributions.post_id),
        published_at = COALESCE(EXCLUDED.published_at, pipeline_distributions.published_at)
"""


async def persist_platform_handles(
    conn: Any,
    *,
    post_id: str,
    asset_id: str | None,
    task_id: str | None,
    results: list[PlatformDispatchResult],
) -> None:
    """Persist each successful platform's external handles on an open connection.

    The per-platform merge+insert loop shared by both video-dispatch jobs. For
    each successful result carrying an ``external_id`` we:

    1. merge ``{platform: external_id}`` into ``media_assets.platform_video_ids``
       (shallow ``||`` merge — never clobbers another platform's id) when an
       ``asset_id`` is known, and
    2. upsert a ``pipeline_distributions`` row (``status='published'``) carrying
       ``external_id`` / ``external_url`` when a ``task_id`` is known (it's the
       NOT NULL FK key); the row is logged-and-skipped otherwise.

    Results that failed or carry no ``external_id`` are skipped. This is the loop
    ONLY — the caller owns the transaction and the ``record_dispatched`` stamp,
    because the two jobs stamp it differently and source ``(asset_id, task_id)``
    differently. ``conn`` is an already-acquired connection so all writes share
    the caller's transaction.
    """
    for r in results:
        if not (r.success and r.external_id):
            continue
        if asset_id:
            await conn.execute(
                _MERGE_PLATFORM_VIDEO_ID_SQL,
                asset_id,
                json.dumps({r.platform: r.external_id}),
            )
        if task_id:
            await conn.execute(
                _RECORD_DISTRIBUTION_SQL,
                task_id, r.platform, r.external_id, r.url, post_id,
            )
        else:
            logger.warning(
                "%s delivered post %s (external_id=%s) but no task_id — "
                "skipping pipeline_distributions row",
                r.platform, post_id, r.external_id,
            )


__all__ = [
    "PlatformDispatchResult",
    "persist_platform_handles",
    "_MERGE_PLATFORM_VIDEO_ID_SQL",
    "_RECORD_DISTRIBUTION_SQL",
]
