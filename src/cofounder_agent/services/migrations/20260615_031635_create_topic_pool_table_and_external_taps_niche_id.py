"""Migration 20260615_031635_create_topic_pool: niche-tagged candidate pool + external_taps.niche_id

ISSUE: Topic sourcing taps b1 (docs/superpowers/specs/2026-06-14-topic-sourcing-taps-design.md)

topic_pool is the decoupling seam between ingestion (taps) and orchestration
(TopicBatchService). One row per (niche, candidate); status walks
pooled -> batched -> expired. external_taps gains a nullable niche_id so a tap
row can bind to a niche (NULL for non-topic taps like corsair_csv).

Light imports only (migrations-smoke): logging stdlib.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS topic_pool (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    niche_id     UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
    source       TEXT NOT NULL,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL DEFAULT '',
    category     TEXT NOT NULL DEFAULT '',
    score        DOUBLE PRECISION NOT NULL DEFAULT 0,
    dedup_key    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pooled',
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batched_at   TIMESTAMPTZ
);
"""

# One explicit string per item — no adjacent-literal concatenation (CodeQL
# py/implicit-string-concatenation-in-list hides missing commas).
_CREATE_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_topic_pool_niche_dedup ON topic_pool (niche_id, dedup_key);",
    "CREATE INDEX IF NOT EXISTS idx_topic_pool_niche_status ON topic_pool (niche_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_topic_pool_ingested_at ON topic_pool (ingested_at);",
]

_ADD_NICHE_ID = (
    "ALTER TABLE external_taps "
    "ADD COLUMN IF NOT EXISTS niche_id UUID REFERENCES niches(id) ON DELETE CASCADE;"
)

_CREATE_TAPS_NICHE_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_external_taps_niche_id "
    "ON external_taps (niche_id) WHERE niche_id IS NOT NULL;"
)


async def up(pool) -> None:
    """Create topic_pool + indexes and add external_taps.niche_id."""
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE)
        for idx_sql in _CREATE_INDEXES:
            await conn.execute(idx_sql)
        await conn.execute(_ADD_NICHE_ID)
        await conn.execute(_CREATE_TAPS_NICHE_IDX)
    logger.info(
        "Migration create_topic_pool: table + %d indexes + external_taps.niche_id",
        len(_CREATE_INDEXES),
    )


async def down(pool) -> None:
    """Drop topic_pool and the external_taps.niche_id column."""
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE external_taps DROP COLUMN IF EXISTS niche_id")
        await conn.execute("DROP TABLE IF EXISTS topic_pool")
    logger.info("Migration create_topic_pool down: reverted")
