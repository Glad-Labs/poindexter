"""Add the youtube_reach tap so thumbnail impressions and CTR land in external_metrics.

Earned 2026-09-25. The channel had 13 long videos and 10 Shorts, none with a
custom thumbnail, and not one YouTube number in the database:
``external_metrics`` held Search Console and GA4 only. A thumbnail exists to
move click-through, so without the reach report no thumbnail change could be
told apart from noise.

One install-wide row (a YouTube channel is not per-niche) on the new
``tap.youtube_reporting`` handler, writing through the existing
``external_metrics_writer`` so the rows share the table, natural key and
upsert semantics every other metrics tap uses. ``source='youtube'``;
``dimensions`` carries ``video_id`` (the per-video key) and ``medium``
(``long`` / ``short``).

Seeded ENABLED. On an install that has not set up YouTube publishing the
handler is a quiet 0-record run; on one that has, a token without
``yt-analytics.readonly`` fails the run with the one command that fixes it.
Idempotent on the ``external_taps.name`` UNIQUE key; an operator who already
created a ``youtube_reach`` row keeps theirs.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_NAME = "youtube_reach"
_SEEDED_BY = "migration add_the_youtube_reach_tap"
_REPORT = "channel_reach_basic_a1"

_CONFIG = {
    "report_type_id": _REPORT,
    "job_name": f"poindexter-{_REPORT}",
    "max_reports_per_run": 60,
    "include_unmapped_videos": True,
    "metrics_mapping": {
        _REPORT: {
            "source": "youtube",
            "date_field": "date",
            "post_field": "post_id",
            "metric_fields": ["video_thumbnail_impressions", "video_thumbnail_impressions_ctr"],
            "dimension_fields": ["video_id", "medium"],
        },
    },
}


async def up(pool) -> None:
    """Insert the youtube_reach tap row unless one already exists."""
    async with pool.acquire() as conn:
        status = await conn.execute(
            """
            INSERT INTO external_taps
                (name, handler_name, tap_type, target_table, record_handler,
                 schedule, config, state, enabled, metadata)
            VALUES ($1, 'youtube_reporting', 'youtube_reach', 'external_metrics',
                    'external_metrics_writer', 'every 12 hours', $2::jsonb,
                    '{}'::jsonb, true, $3::jsonb)
            ON CONFLICT (name) DO NOTHING
            """,
            _NAME,
            json.dumps(_CONFIG),
            json.dumps({
                "seeded_by": _SEEDED_BY,
                "reason": "no YouTube reach numbers anywhere, so no thumbnail change was measurable",
            }),
        )
    logger.info("Migration add_the_youtube_reach_tap: %s", status)


async def down(pool) -> None:
    """Remove the row only if this migration seeded it."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM external_taps WHERE name = $1 AND metadata->>'seeded_by' = $2",
            _NAME, _SEEDED_BY,
        )
