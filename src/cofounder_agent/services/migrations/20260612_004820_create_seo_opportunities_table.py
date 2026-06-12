"""Migration 20260612_004820_create_seo_opportunities_table: create seo_opportunities

ISSUE: SEO Harvest Loop — Phase 1 (docs/superpowers/specs/2026-06-11-seo-harvest-loop-design.md)

The harvest analyzer's output table. One row per (post_id, target_query)
holding the current SEO opportunity for a published post: which tier it's in
(page1_push / striking_distance / low_ctr), its live GSC metrics, a gap_score
(estimated clicks left on the table), and baseline/outcome columns reserved
for the Phase 2 refresh-outcome tracking. ``target_query`` is '' (empty string,
not NULL) when no specific query is known yet (page-level data) so the
UNIQUE(post_id, target_query) constraint behaves. Read-only producer — nothing
mutates content.

Light imports only (migrations-smoke): logging stdlib.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS seo_opportunities (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id             UUID,
    slug                TEXT NOT NULL,
    target_query        TEXT NOT NULL DEFAULT '',
    tier                TEXT NOT NULL,
    current_position    NUMERIC(6,2),
    impressions         INTEGER NOT NULL DEFAULT 0,
    ctr                 NUMERIC(8,5),
    gap_score           NUMERIC(12,2) NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'open',
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    baseline_position   NUMERIC(6,2),
    baseline_ctr        NUMERIC(8,5),
    outcome_position    NUMERIC(6,2),
    outcome_ctr         NUMERIC(8,5),
    outcome_measured_at TIMESTAMPTZ
);
"""

# One explicit string per item — no adjacent-literal concatenation (CodeQL
# py/implicit-string-concatenation-in-list: the pattern hides missing commas).
_CREATE_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_seo_opportunities_post_query ON seo_opportunities (post_id, target_query);",
    "CREATE INDEX IF NOT EXISTS idx_seo_opportunities_tier ON seo_opportunities (tier);",
    "CREATE INDEX IF NOT EXISTS idx_seo_opportunities_status ON seo_opportunities (status);",
    "CREATE INDEX IF NOT EXISTS idx_seo_opportunities_gap_score ON seo_opportunities (gap_score DESC);",
]

# Retention: opportunities are a recomputed current-state view; prune rows not
# re-detected in 90 days (stale posts that fell out of every tier). detected_at
# is bumped on every upsert, so a row only ages out if the analyzer stops
# emitting it. retention_policies has no UNIQUE(name), so guard with NOT EXISTS
# rather than ON CONFLICT. The CHECK constraint is satisfied by ttl_days.
_RETENTION = """
INSERT INTO retention_policies (
    name, handler_name, table_name, filter_sql,
    age_column, ttl_days, downsample_rule, summarize_handler,
    enabled, config, metadata
)
SELECT
    'seo_opportunities', 'ttl_prune', 'seo_opportunities', NULL,
    'detected_at', 90, NULL, NULL,
    true, '{}'::jsonb,
    '{"description": "Prune SEO opportunity rows not re-detected in 90 days"}'::jsonb
WHERE NOT EXISTS (
    SELECT 1 FROM retention_policies WHERE name = 'seo_opportunities'
)
"""


async def up(pool) -> None:
    """Create seo_opportunities + indexes, and seed its retention policy."""
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE)
        for idx_sql in _CREATE_INDEXES:
            await conn.execute(idx_sql)
        await conn.execute(_RETENTION)
    logger.info(
        "Migration create_seo_opportunities_table: table + %d indexes + retention policy",
        len(_CREATE_INDEXES),
    )


async def down(pool) -> None:
    """Drop the table and its retention policy."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM retention_policies WHERE name = 'seo_opportunities'"
        )
        await conn.execute("DROP TABLE IF EXISTS seo_opportunities")
    logger.info("Migration create_seo_opportunities_table down: reverted")
