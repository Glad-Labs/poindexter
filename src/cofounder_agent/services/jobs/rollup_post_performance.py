"""RollupPostPerformanceJob — aggregate page_views + external_metrics into post_performance.

Part of internal tracker Phase 3 Wave B. The external-tap half (Google Search
Console, GA4, Cloudflare Analytics) ships via the Singer-tap framework
in #103; the LOCAL half — rolling up our own page_views table into
post_performance — is pure SQL. As of Glad-Labs/poindexter#27 the job
also LEFT JOINs external_metrics (filtered by source='google_search_console')
to enrich each snapshot row with google_impressions / google_clicks /
google_avg_position, fixing the audit gap where 322 post_performance
rows had impressions=0 despite 2,322 GSC rows in external_metrics.

As of Glad-Labs/poindexter#672 the job also LEFT JOINs external_metrics
(filtered by source='ga4') to populate ``avg_time_on_page_seconds``
from the GA4 ``user_engagement_duration`` / ``screen_page_views`` metrics.

## What it does

Once per cycle, for every published post:
  - Count page_views.slug matches over 1d / 7d / 30d / total windows.
  - Aggregate GSC impressions/clicks (sum) and position (avg) over the
    last 30 days from external_metrics.
  - Aggregate GA4 engagement duration / page-views over the same window
    to derive ``avg_time_on_page_seconds``.
  - Insert a new post_performance snapshot row with ``period='snapshot'``
    and ``measured_at=NOW()``.

The view counts are per SLUG (not per post_id) because page_views only
stores the request path — we join slug → posts.id so the FK is valid.
Snapshots accumulate over time, so the table doubles as the history.
Retention is bounded by the retention_janitor (defaults to 180d).

## Config (``plugin.job.rollup_post_performance``)

- ``config.interval_hours`` (default 24) — how often to snapshot
- ``config.min_published_days_ago`` (default 0) — skip posts too new to
  have meaningful view counts yet (set to 1 if you only want "yesterday's
  views" patterns)
- ``config.gsc_window_days`` (default 30) — how far back to aggregate
  GSC impressions/clicks. 30d matches the post_performance.views_30d
  column convention so the per-row "30 day window" is consistent. The
  same window is reused for GA4 engagement metrics.

## GSC-join semantics

We aggregate by ``slug`` rather than ``post_id`` because GSC data lands
with ``post_id`` NULL for any URL the tap couldn't resolve at write time
(the tap stores the path in the ``slug`` column either way). Joining on
slug means we capture all the impressions for a post regardless of
when the tap was wired up. ``COALESCE(SUM(...), 0)`` keeps the columns
non-NULL for posts with no GSC traffic yet.

## GA4-join semantics

The GA4 Singer tap writes ``user_engagement_duration`` (total seconds
users were active on the page) and ``screen_page_views`` (total page
renders) per slug per day. We aggregate both over the same rolling
window as GSC and compute:

    avg_time_on_page_seconds = SUM(user_engagement_duration)
                               / GREATEST(SUM(screen_page_views), 1)

A post with 0 GA4 screen_page_views in the window gets NULL (not 0) so
NULL-means-no-data semantics are preserved downstream.

## Idempotency

Multiple runs in a day produce multiple snapshot rows — by design. The
``measured_at`` timestamp disambiguates and the caller (Grafana panel /
experiments evaluator) picks the latest per post.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


class RollupPostPerformanceJob:
    name = "rollup_post_performance"
    description = (
        "Snapshot views_1d/7d/30d/total from page_views into post_performance"
    )
    schedule = "every 24 hours"
    idempotent = False  # multiple runs = multiple snapshots, by design

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        min_published_days_ago = int(config.get("min_published_days_ago", 0) or 0)
        gsc_window_days = int(config.get("gsc_window_days", 30) or 30)
        try:
            async with pool.acquire() as conn:
                # One SQL statement rolls up every published post.
                # Using FILTER + a join against posts so we only produce
                # rows for posts that still exist in the CMS (FK satisfied).
                # slug match handles both "/posts/<slug>" path form and
                # the raw slug form (some page_view writers use each).
                #
                # Glad-Labs/poindexter#27: also LEFT JOIN external_metrics
                # for GSC enrichment. The CTE shape is per-(slug, metric_name)
                # so we collapse with FILTER + SUM/AVG. Joining on slug
                # (not post_id) captures GSC rows where the tap wrote
                # ``post_id=NULL`` because the URL didn't resolve at write
                # time — the slug column is always populated.
                #
                # Glad-Labs/poindexter#672: also LEFT JOIN external_metrics
                # for GA4 enrichment using the same $2 rolling window.
                # ``user_engagement_duration`` (seconds on page) /
                # ``screen_page_views`` (page renders) → avg_time_on_page_seconds.
                rows = await conn.fetch(
                    """
                    WITH windows AS (
                      SELECT
                        p.id AS post_id,
                        p.slug,
                        COUNT(pv.id) FILTER (
                          WHERE pv.created_at > NOW() - INTERVAL '1 day'
                        ) AS views_1d,
                        COUNT(pv.id) FILTER (
                          WHERE pv.created_at > NOW() - INTERVAL '7 days'
                        ) AS views_7d,
                        COUNT(pv.id) FILTER (
                          WHERE pv.created_at > NOW() - INTERVAL '30 days'
                        ) AS views_30d,
                        COUNT(pv.id) AS views_total
                      FROM posts p
                      LEFT JOIN page_views pv
                        ON pv.slug = p.slug
                        OR pv.path = '/posts/' || p.slug
                        OR pv.path = '/posts/' || p.slug || '/'
                      WHERE p.status = 'published'
                        AND p.published_at <= NOW() - ($1::int || ' days')::interval
                      GROUP BY p.id, p.slug
                    ),
                    gsc AS (
                      SELECT
                        em.slug,
                        COALESCE(SUM(em.metric_value) FILTER (
                          WHERE em.metric_name = 'impressions'
                        ), 0)::int AS google_impressions,
                        COALESCE(SUM(em.metric_value) FILTER (
                          WHERE em.metric_name = 'clicks'
                        ), 0)::int AS google_clicks,
                        AVG(em.metric_value) FILTER (
                          WHERE em.metric_name = 'position' AND em.metric_value > 0
                        ) AS google_avg_position
                      FROM external_metrics em
                      WHERE em.source = 'google_search_console'
                        AND em.date > (NOW() - ($2::int || ' days')::interval)::date
                        AND em.slug IS NOT NULL
                      GROUP BY em.slug
                    ),
                    ga4 AS (
                      SELECT
                        em.slug,
                        COALESCE(SUM(em.metric_value) FILTER (
                          WHERE em.metric_name = 'user_engagement_duration'
                        ), 0) AS total_engagement_seconds,
                        COALESCE(SUM(em.metric_value) FILTER (
                          WHERE em.metric_name = 'screen_page_views'
                        ), 0) AS total_screen_views
                      FROM external_metrics em
                      WHERE em.source = 'ga4'
                        AND em.date > (NOW() - ($2::int || ' days')::interval)::date
                        AND em.slug IS NOT NULL
                      GROUP BY em.slug
                    )
                    INSERT INTO post_performance (
                      post_id, slug,
                      views_1d, views_7d, views_30d, views_total,
                      google_impressions, google_clicks, google_avg_position,
                      avg_time_on_page_seconds,
                      measured_at, period
                    )
                    SELECT
                      w.post_id, w.slug,
                      w.views_1d, w.views_7d, w.views_30d, w.views_total,
                      COALESCE(g.google_impressions, 0),
                      COALESCE(g.google_clicks, 0),
                      g.google_avg_position,
                      CASE WHEN COALESCE(g4.total_screen_views, 0) > 0
                           THEN (g4.total_engagement_seconds
                                 / g4.total_screen_views)::numeric(8,2)
                           ELSE NULL
                      END,
                      NOW(), 'snapshot'
                    FROM windows w
                    LEFT JOIN gsc g ON g.slug = w.slug
                    LEFT JOIN ga4 g4 ON g4.slug = w.slug
                    RETURNING id
                    """,
                    min_published_days_ago,
                    gsc_window_days,
                )
                inserted = len(rows)
                # Also log a summary for the daily top-5 — operators like
                # to see "what was hot yesterday" in the Grafana job panel.
                top5 = await conn.fetch(
                    """
                    SELECT slug, views_1d FROM post_performance
                    WHERE measured_at > NOW() - INTERVAL '1 hour'
                    ORDER BY views_1d DESC LIMIT 5
                    """,
                )
                if top5:
                    logger.info(
                        "[rollup_post_performance] Top views_1d in this snapshot: %s",
                        "; ".join(
                            f"{r['slug'][:50]}={r['views_1d']}" for r in top5
                        ),
                    )

            return JobResult(
                ok=True,
                detail=f"snapshotted {inserted} posts",
                changes_made=inserted,
            )
        except Exception as e:
            logger.warning(
                "[rollup_post_performance] rollup failed (non-fatal): %s",
                e, exc_info=True,
            )
            return JobResult(
                ok=False, detail=f"rollup failed: {type(e).__name__}: {e}",
            )
