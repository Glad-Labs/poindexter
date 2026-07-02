"""Migration 20260702_064544_add_topic_pool_retention_policy: add topic_pool retention policy

ISSUE: Glad-Labs/poindexter#812 (b2 — orchestrator pool-reader cutover)

With run_sweep now reading topic_pool and flipping only the batch winners
to 'batched', unchosen 'pooled' rows would accumulate forever without a
pruner (the pool already held ~4.7k rows at cutover). This seeds a
ttl_prune retention policy that removes 'pooled' rows no sweep batched
within ttl_days (operator-tunable on the row itself, per the declarative
retention framework). 'batched' rows are kept — they're the provenance
record behind topic_candidates.

Idempotent (ON CONFLICT (id) DO NOTHING); the same row is seeded in
0000_baseline.seeds.sql for fresh installs — this migration is the prod
convergence step, mirroring 20260624_004835's pattern.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_POLICY_ID = "4d1be84b-4dfc-41db-9bfa-4d919011c372"

_INSERT = f"""
INSERT INTO retention_policies
    (id, name, handler_name, table_name, filter_sql, age_column, ttl_days,
     downsample_rule, summarize_handler, enabled, config, metadata)
VALUES
    ('{_POLICY_ID}', 'topic_pool', 'ttl_prune',
     'topic_pool', 'status = ''pooled''', 'ingested_at', 14,
     NULL, NULL, true, '{{}}'::jsonb,
     '{{"description": "Prune pooled topic candidates that no sweep batched within 14 days — stale headlines are not worth ranking"}}'::jsonb)
ON CONFLICT (id) DO NOTHING
"""


async def up(pool) -> None:
    """Seed the topic_pool ttl_prune retention policy (idempotent)."""
    async with pool.acquire() as conn:
        await conn.execute(_INSERT)
    logger.info("Migration add_topic_pool_retention_policy: seeded ttl_prune row")


async def down(pool) -> None:
    """Remove the topic_pool retention policy row."""
    async with pool.acquire() as conn:
        await conn.execute(
            f"DELETE FROM retention_policies WHERE id = '{_POLICY_ID}'"
        )
    logger.info("Migration add_topic_pool_retention_policy down: reverted")
