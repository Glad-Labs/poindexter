"""BackfillPostPerformanceGscJob — retro-populate google_* columns on existing snapshots.

Glad-Labs/poindexter#27 follow-up. Pre-existing post_performance rows
(322 of them at the time of this fix) were written before the GSC
LEFT JOIN was added to ``rollup_post_performance.py``, so they all
have ``google_impressions = 0`` / ``google_clicks = 0`` /
``google_avg_position = NULL`` despite ``external_metrics`` carrying
2,500+ rows of GSC data.

This one-shot UPDATE pass joins ``post_performance`` against
``external_metrics`` (per-slug, summed over the metric_value field
filtered by metric_name) and patches the columns in place. After it
runs once, the regular rollup job keeps new rows accurate.

## Config (``plugin.job.backfill_post_performance_gsc``)

- ``config.gsc_window_days`` (default 30) — how far back of GSC data
  to attribute to each snapshot. 30 matches the rollup job's window.
  This is intentionally a snapshot of the recent window, not historical
  per-day attribution — that would require windowing each snapshot's
  ``measured_at`` against the GSC date column, which is more SQL than
  the audit's "small fix" intent.
- ``config.dry_run`` (default ``false``) — log what would change without
  writing.

## Runs once

The job is safe to re-run (idempotent — same SQL produces same result)
but operationally it's a one-shot. Default schedule is monthly so a
re-run happens automatically if/when GSC backfills land — and the
operator can disable the job entirely via ``plugin.job.backfill_post_performance_gsc.enabled``
in app_settings once they've verified the rollup is steady-state.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


class BackfillPostPerformanceGscJob:
    name = "backfill_post_performance_gsc"
    description = (
        "Retro-populate google_impressions/clicks/position on existing "
        "post_performance snapshots from external_metrics."
    )
    # Monthly cadence — the job is essentially a maintenance pass for
    # historical rows. The hot path (new snapshots) is handled by
    # rollup_post_performance directly.
    schedule = "every 30 days"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        gsc_window_days = int(config.get("gsc_window_days", 30) or 30)
        dry_run = bool(config.get("dry_run", False))

        try:
            async with pool.acquire() as conn:
                # Build the per-slug GSC aggregate first so we can both
                # report counts (dry_run) and apply the UPDATE. UPDATE FROM
                # against a CTE is the canonical Postgres pattern for
                # "patch many rows from a derived table".
                aggregate_sql = """
                    WITH gsc AS (
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
                        AND em.date > (NOW() - ($1::int || ' days')::interval)::date
                        AND em.slug IS NOT NULL
                      GROUP BY em.slug
                    )
                """
                aggregate_count = await conn.fetchval(
                    aggregate_sql + " SELECT COUNT(*) FROM gsc",
                    gsc_window_days,
                )
                aggregate_count = int(aggregate_count or 0)

                if dry_run:
                    candidate_count = await conn.fetchval(
                        aggregate_sql
                        + """
                        SELECT COUNT(*)
                          FROM post_performance pp
                          JOIN gsc g ON g.slug = pp.slug
                         WHERE COALESCE(pp.google_impressions, 0) = 0
                           AND g.google_impressions > 0
                        """,
                        gsc_window_days,
                    )
                    candidate_count = int(candidate_count or 0)
                    return JobResult(
                        ok=True,
                        detail=(
                            f"DRY-RUN: would patch {candidate_count} post_performance "
                            f"rows from {aggregate_count} GSC slug aggregates"
                        ),
                        changes_made=0,
                    )

                result = await conn.execute(
                    aggregate_sql
                    + """
                    UPDATE post_performance pp
                       SET google_impressions = g.google_impressions,
                           google_clicks      = g.google_clicks,
                           google_avg_position = g.google_avg_position
                      FROM gsc g
                     WHERE pp.slug = g.slug
                       AND COALESCE(pp.google_impressions, 0) = 0
                       AND g.google_impressions > 0
                    """,
                    gsc_window_days,
                )

            # asyncpg returns "UPDATE <n>"
            try:
                updated = int(result.split()[-1])
            except (AttributeError, ValueError, IndexError):
                updated = 0

            logger.info(
                "[backfill_post_performance_gsc] patched %d post_performance "
                "rows from %d GSC slug aggregates (window=%dd)",
                updated, aggregate_count, gsc_window_days,
            )
            return JobResult(
                ok=True,
                detail=(
                    f"patched {updated} post_performance rows from "
                    f"{aggregate_count} GSC slug aggregates"
                ),
                changes_made=updated,
            )
        except Exception as e:
            logger.warning(
                "[backfill_post_performance_gsc] backfill failed (non-fatal): %s",
                e, exc_info=True,
            )
            return JobResult(
                ok=False,
                detail=f"backfill failed: {type(e).__name__}: {e}",
                changes_made=0,
            )
