"""Add ``entity_demand_cache`` — Wikipedia pageview lookups for topic ranking.

Backs ``services/entity_demand.py`` (2026-09-08): the batch pre-rank now asks
Wikimedia how many people read the article for the entity a candidate topic
names last month, and multiplies the score by a bounded demand factor. The
sweep runs every 30 minutes over ~60 candidates, so results are cached here
for ``topic_demand_wiki_cache_days`` (default 7). A confirmed "no article
matched" is cached too (``wiki_title`` NULL, ``monthly_views`` NULL); a
transport failure is not cached, so the next sweep retries.

Small by construction — one row per distinct (lang, normalised title) ever
ranked — so no retention policy is registered; ``fetched_at`` is the TTL.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CREATE = """
    CREATE TABLE IF NOT EXISTS entity_demand_cache (
        query_key      text PRIMARY KEY,
        lang           text NOT NULL DEFAULT 'en',
        wiki_title     text,
        monthly_views  bigint,
        fetched_at     timestamptz NOT NULL DEFAULT now()
    )
"""
_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_entity_demand_cache_fetched_at "
    "ON entity_demand_cache (fetched_at)"
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CREATE)
        await conn.execute(_INDEX)
    logger.info("Migration add_entity_demand_cache_for_wikipedia_pageviews: applied")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS entity_demand_cache")
