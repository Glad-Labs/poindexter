"""Migration 20260704_033500: dedup external_metrics + add natural-key unique index.

The GA4 / GSC Singer taps re-emit their whole backfill window on every run
(every 6h), and ``tap.external_metrics_writer`` used a bare ``INSERT`` against a
table whose only unique constraint was the ``id`` primary key. So every run
re-appended the same logical rows — ``external_metrics`` grew ~382k rows that
collapse to ~11k unique (GA4 was 99.8% duplicates, GSC 94%).

Anything that ``SUM(metric_value)`` over the table read inflated by the per-row
run count: ``rollup_post_performance`` sums GSC impressions/clicks and GA4
screen_page_views into ``post_performance`` (SEO Harvest dashboard + the
experiments evaluator consume those columns). Duplication silently multiplied
them by ~40-80x.

This migration is the convergence step:

1. **One-time dedup** — collapse existing duplicates to one row per natural key
   ``(source, metric_name, date, slug, dimensions)``, keeping the row with the
   latest ``fetched_at`` (last-write-wins — for GSC, whose values mature over a
   few days, that keeps the finalised value, not an early ``0``).
2. **Natural-key unique index** (``NULLS NOT DISTINCT`` so a NULL ``slug`` /
   ``post_id`` still dedups). This is the arbiter for the writer's new
   ``ON CONFLICT ... DO UPDATE`` upsert (same commit) — future re-emits update
   in place instead of appending.

Both steps are idempotent:

  * Fresh installs — ``external_metrics`` is empty, the DELETE removes nothing,
    the index builds on an empty table.
  * Existing installs (prod) — the DELETE removes the ~370k duplicate rows, then
    the unique index builds over what remains.

``dimensions`` (jsonb) participates in the key directly — GSC's ``search_type``
web/image split is a legitimately distinct row, so it must be part of the
uniqueness. Current tap dimensions are tiny ({site_url, search_type} or {}), so
they sit comfortably under the btree index-tuple size limit.

The live taps aggregate one row per natural key per run (verified against prod):

  * GA4 ``page_path_and_screen_class_report`` — per-page, empty dimensions.
  * GSC ``performance_report_date`` — site-wide daily, ``slug`` NULL (hence
    ``NULLS NOT DISTINCT``, so these dedup across runs).
  * GSC ``performance_report_page`` — per-page daily.

Neither GSC stream requests a ``query`` dimension, so the ~382k→~11k collapse is
pure hourly re-emission, not lost per-query granularity. For the rare (~0.2%)
key carrying >1 row within a single run, the latest-``fetched_at`` row is kept
(consistent with the writer's last-write-wins upsert), not summed. If per-query
GSC granularity is ever wanted, add ``query`` to the stream's
``dimension_fields`` so each query becomes its own natural-key row.

NOTE: ``post_performance`` snapshots written before this migration retain their
inflated GSC/GA4 sums (they are append-only history rows). Re-running
``rollup_post_performance`` after this migration produces a corrected latest
snapshot; consumers already pick the latest per post.

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_INDEX_NAME = "ux_external_metrics_natural_key"


async def up(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Collapse duplicates, keeping the freshest row per natural key.
            #    row_number() over the same tuple the unique index will enforce;
            #    NULL slug/dimensions group together (matches NULLS NOT DISTINCT).
            deleted = await conn.execute(
                """
                DELETE FROM external_metrics em
                USING (
                    SELECT id FROM (
                        SELECT id,
                               row_number() OVER (
                                   PARTITION BY source, metric_name, date, slug, dimensions
                                   ORDER BY fetched_at DESC NULLS LAST, id DESC
                               ) AS rn
                        FROM external_metrics
                    ) ranked
                    WHERE ranked.rn > 1
                ) dups
                WHERE em.id = dups.id
                """
            )

            # 2. The arbiter index for the writer's ON CONFLICT upsert.
            await conn.execute(
                f"""
                CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_NAME}
                ON external_metrics (source, metric_name, date, slug, dimensions)
                NULLS NOT DISTINCT
                """
            )

    logger.info(
        "dedup_external_metrics_natural_key up: %s duplicate rows removed, "
        "unique index %s ensured",
        deleted.split()[-1] if isinstance(deleted, str) else deleted,
        _INDEX_NAME,
    )


async def down(pool) -> None:
    # Drop only the index. The one-time dedup is not reversible (the deleted
    # duplicates carried no information the surviving row lacks), and re-adding
    # duplicates would just restore the inflation this migration removed.
    async with pool.acquire() as conn:
        await conn.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
    logger.info(
        "dedup_external_metrics_natural_key down: dropped %s "
        "(dedup itself is one-way, not restored)",
        _INDEX_NAME,
    )
