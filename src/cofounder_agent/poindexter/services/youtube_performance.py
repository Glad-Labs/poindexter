"""How each YouTube video is doing: views, watch time, retention, reach, sources.

The reader for the three YouTube reporting taps (``external_metrics``):

- ``youtube_activity`` (``channel_basic_a3``): views, engaged views, watch
  minutes, likes, comments, shares, subscribers gained and lost;
- ``youtube_traffic`` (``channel_traffic_source_a3``): views and watch minutes
  per traffic source, with the search term for YouTube search;
- ``youtube`` (``channel_reach_basic_a1``, the ``youtube_reach`` tap):
  thumbnail impressions and click-through rate.

Every row the activity and traffic taps write is one split of a video's day
(by subscribed status, country, live or on demand, and for traffic the
source). Counts are summed across splits; averages are never summed. Average
view duration is watch minutes / views, and retention divides that by the
video's length from ``media_assets.duration_ms``. A video the pipeline did not
render (uploaded by hand) has no length there, so its retention is ``None``,
never a guess.

Click-through rate is a per-day fraction (0.04 = 4%), so it is weighted by
that day's impressions before it is summed.

Earned 2026-10-18: the reach tap had collected thumbnail numbers since
September and nothing read them, and nothing collected views at all.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

#: ``traffic_source_type`` codes from the Reporting API
#: (developers.google.com/youtube/reporting/v1/reports/dimensions).
TRAFFIC_SOURCE_NAMES: dict[str, str] = {
    "0": "direct or unknown",
    "1": "YouTube advertising",
    "3": "browse features",
    "4": "channel pages",
    "5": "YouTube search",
    "7": "suggested videos",
    "8": "other YouTube features",
    "9": "external websites",
    "11": "cards and annotations",
    "14": "playlists",
    "17": "notifications",
    "18": "playlist pages",
    "19": "claimed content promotion",
    "20": "end screens",
    "23": "stories",
    "24": "Shorts feed",
    "25": "product pages",
    "26": "hashtag pages",
    "27": "sound pages",
    "28": "live redirects",
    "29": "podcasts",
    "30": "remixed videos",
    "31": "vertical live feed",
    "32": "related in the Shorts player",
}

#: ``traffic_source_type`` for YouTube search; its ``traffic_source_detail`` is
#: the search term.
SEARCH_SOURCE = "5"


@dataclass
class VideoPerformance:
    video_id: str
    medium: str
    title: str
    duration_seconds: float | None
    views: float = 0.0
    engaged_views: float = 0.0
    watch_minutes: float = 0.0
    likes: float = 0.0
    comments: float = 0.0
    shares: float = 0.0
    subscribers_gained: float = 0.0
    subscribers_lost: float = 0.0
    impressions: float = 0.0
    impression_clicks: float = 0.0
    sources: dict[str, float] = field(default_factory=dict)

    @property
    def average_view_seconds(self) -> float | None:
        return self.watch_minutes * 60 / self.views if self.views else None

    @property
    def retention(self) -> float | None:
        """Average share of the video watched (0.4 = 40%); ``None`` without a length."""
        avg = self.average_view_seconds
        if avg is None or not self.duration_seconds:
            return None
        return avg / self.duration_seconds

    @property
    def click_through_rate(self) -> float | None:
        return self.impression_clicks / self.impressions if self.impressions else None

    @property
    def net_subscribers(self) -> float:
        return self.subscribers_gained - self.subscribers_lost


@dataclass
class ChannelPerformance:
    start: _dt.date
    end: _dt.date
    videos: list[VideoPerformance]
    sources: dict[str, float]
    search_terms: list[tuple[str, float]]

    @property
    def has_activity(self) -> bool:
        return any(v.views for v in self.videos)


# Per video: summed activity counts, impressions-weighted reach, and the
# video's length and title from media_assets (falling back to the post title).
_VIDEOS_SQL = """
WITH activity AS (
    SELECT dimensions->>'video_id' AS video_id,
           max(dimensions->>'medium') AS medium,
           sum(metric_value) FILTER (WHERE metric_name = 'views') AS views,
           sum(metric_value) FILTER (WHERE metric_name = 'engaged_views') AS engaged_views,
           sum(metric_value) FILTER (WHERE metric_name = 'watch_time_minutes') AS watch_minutes,
           sum(metric_value) FILTER (WHERE metric_name = 'likes') AS likes,
           sum(metric_value) FILTER (WHERE metric_name = 'comments') AS comments,
           sum(metric_value) FILTER (WHERE metric_name = 'shares') AS shares,
           sum(metric_value) FILTER (WHERE metric_name = 'subscribers_gained') AS subscribers_gained,
           sum(metric_value) FILTER (WHERE metric_name = 'subscribers_lost') AS subscribers_lost
      FROM external_metrics
     WHERE source = 'youtube_activity' AND date BETWEEN $1 AND $2
     GROUP BY 1
),
reach_day AS (
    SELECT dimensions->>'video_id' AS video_id, date,
           max(dimensions->>'medium') AS medium,
           max(metric_value) FILTER (WHERE metric_name = 'video_thumbnail_impressions') AS impressions,
           max(metric_value) FILTER (WHERE metric_name = 'video_thumbnail_impressions_ctr') AS ctr
      FROM external_metrics
     WHERE source = 'youtube' AND date BETWEEN $1 AND $2
     GROUP BY 1, 2
),
reach AS (
    SELECT video_id, max(medium) AS medium,
           sum(coalesce(impressions, 0)) AS impressions,
           sum(coalesce(impressions, 0) * coalesce(ctr, 0)) AS impression_clicks
      FROM reach_day
     GROUP BY 1
),
asset AS (
    SELECT DISTINCT ON (ma.platform_video_ids->>'youtube')
           ma.platform_video_ids->>'youtube' AS video_id,
           ma.duration_ms,
           coalesce(nullif(ma.metadata->'youtube_package'->>'title', ''), p.title, ma.title) AS title
      FROM media_assets ma
      LEFT JOIN posts p ON p.id = ma.post_id
     WHERE ma.platform_video_ids ? 'youtube'
     ORDER BY ma.platform_video_ids->>'youtube', ma.updated_at DESC NULLS LAST
)
SELECT coalesce(a.video_id, r.video_id) AS video_id,
       coalesce(a.medium, r.medium, 'unknown') AS medium,
       coalesce(asset.title, '') AS title,
       asset.duration_ms,
       a.views, a.engaged_views, a.watch_minutes, a.likes, a.comments, a.shares,
       a.subscribers_gained, a.subscribers_lost,
       r.impressions, r.impression_clicks
  FROM activity a
  FULL OUTER JOIN reach r ON r.video_id = a.video_id
  LEFT JOIN asset ON asset.video_id = coalesce(a.video_id, r.video_id)
 WHERE coalesce(a.video_id, r.video_id) IS NOT NULL
"""

_SOURCES_SQL = """
SELECT dimensions->>'video_id' AS video_id,
       dimensions->>'traffic_source_type' AS source_type,
       sum(metric_value) AS views
  FROM external_metrics
 WHERE source = 'youtube_traffic' AND metric_name = 'views' AND date BETWEEN $1 AND $2
 GROUP BY 1, 2
"""

_SEARCH_TERMS_SQL = """
SELECT dimensions->>'traffic_source_detail' AS term, sum(metric_value) AS views
  FROM external_metrics
 WHERE source = 'youtube_traffic' AND metric_name = 'views' AND date BETWEEN $1 AND $2
   AND dimensions->>'traffic_source_type' = $3
   AND coalesce(dimensions->>'traffic_source_detail', '') <> ''
 GROUP BY 1
 ORDER BY 2 DESC, 1
 LIMIT $4
"""


def _num(value: Any) -> float:
    return float(value) if value is not None else 0.0


def source_name(code: str | None) -> str:
    """Readable name for a ``traffic_source_type`` code (unknown codes kept as-is)."""
    code = (code or "").strip()
    return TRAFFIC_SOURCE_NAMES.get(code, f"source {code}" if code else "unknown")


def build_videos(rows: list[dict[str, Any]], source_rows: list[dict[str, Any]]) -> list[VideoPerformance]:
    """Query rows → one :class:`VideoPerformance` per video, most viewed first."""
    videos: dict[str, VideoPerformance] = {}
    for r in rows:
        duration_ms = r.get("duration_ms")
        videos[r["video_id"]] = VideoPerformance(
            video_id=r["video_id"],
            medium=r.get("medium") or "unknown",
            title=r.get("title") or "",
            duration_seconds=float(duration_ms) / 1000 if duration_ms else None,
            views=_num(r.get("views")),
            engaged_views=_num(r.get("engaged_views")),
            watch_minutes=_num(r.get("watch_minutes")),
            likes=_num(r.get("likes")),
            comments=_num(r.get("comments")),
            shares=_num(r.get("shares")),
            subscribers_gained=_num(r.get("subscribers_gained")),
            subscribers_lost=_num(r.get("subscribers_lost")),
            impressions=_num(r.get("impressions")),
            impression_clicks=_num(r.get("impression_clicks")),
        )
    for s in source_rows:
        video = videos.get(s.get("video_id") or "")
        if video is not None:
            name = source_name(s.get("source_type"))
            video.sources[name] = video.sources.get(name, 0.0) + _num(s.get("views"))
    return sorted(videos.values(), key=lambda v: (-v.views, -v.impressions, v.video_id))


async def channel_performance(
    pool: Any, *, days: int, end: _dt.date | None = None, search_terms: int = 15,
) -> ChannelPerformance:
    """Per-video performance over the ``days`` ending at ``end`` (default today)."""
    if days < 1:
        raise ValueError("days must be at least 1")
    end = end or _dt.date.today()
    start = end - _dt.timedelta(days=days - 1)
    async with pool.acquire() as conn:
        rows = [dict(r) for r in await conn.fetch(_VIDEOS_SQL, start, end)]
        source_rows = [dict(r) for r in await conn.fetch(_SOURCES_SQL, start, end)]
        term_rows = await conn.fetch(_SEARCH_TERMS_SQL, start, end, SEARCH_SOURCE, search_terms)
    videos = build_videos(rows, source_rows)
    sources: dict[str, float] = {}
    for s in source_rows:
        name = source_name(s.get("source_type"))
        sources[name] = sources.get(name, 0.0) + _num(s.get("views"))
    return ChannelPerformance(
        start=start,
        end=end,
        videos=videos,
        sources=dict(sorted(sources.items(), key=lambda kv: -kv[1])),
        search_terms=[(r["term"], _num(r["views"])) for r in term_rows],
    )


__all__ = [
    "SEARCH_SOURCE",
    "TRAFFIC_SOURCE_NAMES",
    "ChannelPerformance",
    "VideoPerformance",
    "build_videos",
    "channel_performance",
    "source_name",
]
