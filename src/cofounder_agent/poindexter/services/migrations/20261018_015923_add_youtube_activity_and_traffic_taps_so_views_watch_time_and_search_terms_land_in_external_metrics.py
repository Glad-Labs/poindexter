"""Add the youtube_activity and youtube_traffic taps, so views, watch time and search terms land in external_metrics.

Earned 2026-10-18. The only YouTube tap, ``youtube_reach``, subscribes to the
reach report: thumbnail impressions and click-through rate, nothing else. A
Short is mostly watched from the Shorts feed, where a swipe is no thumbnail
impression, so the one number collected missed most of the channel. Views,
watch time, retention and subscribers were read once by hand through the
Analytics API (14 Shorts: 458 views; 9 long videos: 86), and nothing in the
database could show whether a title or length change moved them.

Two more rows on the same ``tap.youtube_reporting`` handler, one per report:

- ``youtube_activity`` ← ``channel_basic_a3``: views, engaged views, watch
  minutes, likes, comments, shares, subscribers gained and lost.
- ``youtube_traffic`` ← ``channel_traffic_source_a3``: views and watch minutes
  per traffic source, with ``traffic_source_detail`` (for YouTube search, the
  search term).

Both reports split a video's day by ``live_or_on_demand``,
``subscribed_status`` and ``country_code`` (and the traffic report by source),
so those columns are dimensions: the writer's natural key keeps the split rows
apart instead of letting one overwrite the next. Only additive counts are
stored. The reports' own ``average_view_duration_*`` columns are averages of
one split row, and summing them across rows gives nonsense, so average view
duration and retention are derived by readers as watch minutes / views
(``services/youtube_performance.py``).

Each row writes its own ``source``. Both reports have a ``views`` column, and
one shared source would let anything summing ``views`` count every view twice.

Seeded ENABLED, like ``youtube_reach``: on an install without YouTube set up
the handler is a quiet 0-record run. YouTube creates the reporting job on the
first run and backfills the 30 days before it. Idempotent on the
``external_taps.name`` UNIQUE key; an operator's own row with either name is
kept.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_SEEDED_BY = "migration add_youtube_activity_and_traffic_taps"

# Dimensions both reports split a video's day by. ``medium`` is added by the
# handler from media_assets (long / short).
_SPLIT_DIMENSIONS = ["video_id", "medium", "live_or_on_demand", "subscribed_status", "country_code"]


def _config(report: str, source: str, metrics: list[str], extra_dims: list[str]) -> dict:
    return {
        "report_type_id": report,
        "job_name": f"poindexter-{report}",
        "max_reports_per_run": 60,
        "include_unmapped_videos": True,
        "metrics_mapping": {
            report: {
                "source": source,
                "date_field": "date",
                "post_field": "post_id",
                "metric_fields": metrics,
                "dimension_fields": _SPLIT_DIMENSIONS + extra_dims,
            },
        },
    }


TAPS: dict[str, dict] = {
    "youtube_activity": _config(
        "channel_basic_a3",
        "youtube_activity",
        [
            "views", "engaged_views", "watch_time_minutes", "likes", "comments",
            "shares", "subscribers_gained", "subscribers_lost",
        ],
        [],
    ),
    "youtube_traffic": _config(
        "channel_traffic_source_a3",
        "youtube_traffic",
        ["views", "engaged_views", "watch_time_minutes"],
        ["traffic_source_type", "traffic_source_detail"],
    ),
}


async def up(pool) -> None:
    """Insert both tap rows unless a row with that name already exists."""
    async with pool.acquire() as conn:
        for name, config in TAPS.items():
            status = await conn.execute(
                """
                INSERT INTO external_taps
                    (name, handler_name, tap_type, target_table, record_handler,
                     schedule, config, state, enabled, metadata)
                VALUES ($1, 'youtube_reporting', $1, 'external_metrics',
                        'external_metrics_writer', 'every 12 hours', $2::jsonb,
                        '{}'::jsonb, true, $3::jsonb)
                ON CONFLICT (name) DO NOTHING
                """,
                name,
                json.dumps(config),
                json.dumps({
                    "seeded_by": _SEEDED_BY,
                    "reason": "only thumbnail reach was collected; views, watch time and search terms were not",
                }),
            )
            logger.info("Migration add_youtube_activity_and_traffic_taps: %s %s", name, status)


async def down(pool) -> None:
    """Remove the rows only if this migration seeded them."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM external_taps WHERE name = ANY($1::text[]) AND metadata->>'seeded_by' = $2",
            list(TAPS), _SEEDED_BY,
        )
