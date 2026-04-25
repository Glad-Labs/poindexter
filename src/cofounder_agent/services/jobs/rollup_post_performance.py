"""RollupPostPerformanceJob — aggregate page_views into post_performance.

Part of gitea#272 Phase 3 Wave B. The external-tap half (Google Search
Console, GA4, Cloudflare Analytics) needs OAuth that isn't provisioned
yet, but the LOCAL half — rolling up our own page_views table into
post_performance — is pure SQL and ships now. Unblocks "views per post"
panels in Grafana and starts building the historical dataset that the
experiments agent (gitea#273) will consume.

## What it does

Once per cycle, for every published post:
  - Count page_views.slug matches over 1d / 7d / 30d / total windows.
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
    # Wall-clock cron at 01:30 UTC daily, NOT "every 24 hours". The
    # interval-based schedule restarts the clock on every worker boot, so
    # any worker process restarted more often than once a day never
    # actually fires the job (root cause of the 2-day post_performance
    # gap that surfaced 2026-04-24). Cron triggers absolute wall-clock
    # time so frequent restarts don't matter.
    schedule = "30 1 * * *"
    idempotent = False  # multiple runs = multiple snapshots, by design

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        min_published_days_ago = int(config.get("min_published_days_ago", 0) or 0)
        try:
            async with pool.acquire() as conn:
                # One SQL statement rolls up every published post.
                # Using FILTER + a join against posts so we only produce
                # rows for posts that still exist in the CMS (FK satisfied).
                # slug match handles both "/posts/<slug>" path form and
                # the raw slug form (some page_view writers use each).
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
                    )
                    INSERT INTO post_performance (
                      post_id, slug,
                      views_1d, views_7d, views_30d, views_total,
                      measured_at, period
                    )
                    SELECT
                      post_id, slug,
                      views_1d, views_7d, views_30d, views_total,
                      NOW(), 'snapshot'
                    FROM windows
                    RETURNING id
                    """,
                    min_published_days_ago,
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
