"""Migration 20260615_014648: grandfather video media_approvals for already-live videos

ISSUE: media approval gate on ALL media — 2026-05-27→06-13 podcast/video
feed freeze fix (feedback_approval_gate_all_media). Ships in the SAME PR
that gates the video RSS feed on an approved media_approvals row.

The video feed (``routes/video_routes.py::video_feed``) is moving from an
ungated disk scan to the same approval gate the podcast feed already
enforces: it now requires a ``media_approvals(medium='video',
status='approved')`` row. Flipping that gate WITHOUT first blessing the
videos that are already live would instantly freeze every currently-served
video — the exact footgun this whole change exists to prevent.

This migration grandfathers them: for every PUBLISHED post that already has
a long-form video asset (``media_assets.type IN ('video','video_long')``)
but NO ``media_approvals`` row for medium ``video``, insert an
``status='approved'`` row stamped ``decided_by='auto:grandfather'`` so its
provenance is explicit and auditable.

Deliberately INSERT-where-absent (``NOT EXISTS`` + ``ON CONFLICT DO
NOTHING``): it never flips an existing ``pending``/``rejected`` row, so
genuine operator decisions (and freshly-generated, not-yet-reviewed video)
are respected — those correctly stay off the public feed until approved.

Idempotent + safe on a fresh DB: on the baseline-seeded migrations-smoke DB
there are no posts/assets, so it inserts 0 rows. ``medium='video'`` is the
long-form medium (``media_assets`` type ``video_long`` maps to medium
``video`` per ``media_distribute._TYPE_TO_MEDIUM``); short-form
(``video_short``) is dispatched to YouTube Shorts, not the RSS feed, so it's
out of scope here.

Light imports only (migrations-smoke): logging stdlib.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_GRANDFATHER_SQL = """
INSERT INTO media_approvals (post_id, medium, status, decided_at, decided_by)
SELECT DISTINCT p.id, 'video', 'approved', NOW(), 'auto:grandfather'
  FROM posts p
  JOIN media_assets mas
    ON mas.post_id = p.id
   AND mas.type IN ('video', 'video_long')
 WHERE p.status = 'published'
   AND NOT EXISTS (
       SELECT 1 FROM media_approvals ma
        WHERE ma.post_id = p.id AND ma.medium = 'video'
   )
ON CONFLICT (post_id, medium) DO NOTHING
"""

_ROLLBACK_SQL = """
DELETE FROM media_approvals
 WHERE medium = 'video' AND decided_by = 'auto:grandfather'
"""


async def up(pool) -> None:
    """Bless already-live videos so the new feed gate doesn't freeze them."""
    async with pool.acquire() as conn:
        result = await conn.execute(_GRANDFATHER_SQL)
    logger.info(
        "Migration grandfather_video_media_approvals: %s", result,
    )


async def down(pool) -> None:
    """Remove only the auto:grandfather video approvals this migration added.

    Scoped to ``decided_by='auto:grandfather'`` so a rollback never deletes a
    real operator approval.
    """
    async with pool.acquire() as conn:
        result = await conn.execute(_ROLLBACK_SQL)
    logger.info(
        "Migration grandfather_video_media_approvals down: %s", result,
    )
