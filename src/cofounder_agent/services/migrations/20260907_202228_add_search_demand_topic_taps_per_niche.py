"""Add the search-demand topic taps (search_autocomplete, gsc_query_gap) to every niche.

Earned 2026-09-07. ``plugin.topic_source.search_autocomplete`` had been
``enabled=true`` with eight seeds for weeks, and running the source by hand
returned 25 candidates ("rtx 5090 local llm performance", "local llm vram
calculator" …) — the exact cluster whose human queries convert at 7-20% CTR.
Yet ``topic_pool`` held ZERO rows from it. The plugin row only says the
source *may* run; what actually schedules ingestion is an ``external_taps``
row (``handler_name='builtin_topic_source'``, ``tap_type=<source name>``,
one per niche), and none existed for either demand-measuring source. The
site kept publishing what HackerNews was discussing and what the corpus
already contained, while the only source that measures what people SEARCH
for sat idle. August 2026 cohort: 18 posts, 3.4 first-21-day impressions
each, zero clicks.

This inserts ``<niche>_search_autocomplete`` and ``<niche>_gsc_query_gap``
rows for every niche that already has a ``hackernews`` topic tap (the marker
of a niche provisioned for external discovery), mirroring that row's target
table. Idempotent on the ``external_taps.name`` UNIQUE key; a fresh install
with no niches inserts nothing.

Schedules: autocomplete once a day (it is an unauthenticated call to a third
party — ``feedback_stability_over_speed``; suggestions barely move intra-day);
gsc_query_gap every 6 hours (reads our own ``external_metrics``, which the
``gsc_main`` tap refreshes on that cadence).

``config.weight_pct`` mirrors the convention on the sibling rows and is read
by nothing today; the live ranking lever is ``app_settings.topic_source_rank_weights``
(same PR).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEMAND_TAPS = (
    # (tap_type, schedule, weight_pct)
    ("search_autocomplete", "every 24 hours", 30),
    ("gsc_query_gap", "every 6 hours", 20),
)

_INSERT = """
    INSERT INTO external_taps
        (name, handler_name, tap_type, target_table, schedule, config,
         state, enabled, metadata, niche_id)
    SELECT n.slug || '_' || $1,
           'builtin_topic_source',
           $1,
           t.target_table,
           $2,
           jsonb_build_object('weight_pct', $3::int),
           '{}'::jsonb,
           true,
           jsonb_build_object(
               'seeded_by', 'migration add_search_demand_topic_taps_per_niche',
               'reason', 'demand-measuring source was never scheduled'
           ),
           n.id
      FROM niches n
      JOIN external_taps t
        ON t.niche_id = n.id
       AND t.handler_name = 'builtin_topic_source'
       AND t.tap_type = 'hackernews'
    ON CONFLICT (name) DO NOTHING
"""


async def up(pool) -> None:
    """Insert one demand tap per (niche, source), skipping names that exist."""
    async with pool.acquire() as conn:
        # Both tables exist from the baseline; a throwaway smoke DB that
        # never seeded a niche simply inserts nothing.
        for tap_type, schedule, weight in _DEMAND_TAPS:
            status = await conn.execute(_INSERT, tap_type, schedule, weight)
            logger.info(
                "Migration add_search_demand_topic_taps_per_niche: %s -> %s",
                tap_type, status,
            )


async def down(pool) -> None:
    """Remove only the rows this migration seeded (by the metadata marker)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM external_taps "
            "WHERE metadata->>'seeded_by' = "
            "'migration add_search_demand_topic_taps_per_niche'"
        )
