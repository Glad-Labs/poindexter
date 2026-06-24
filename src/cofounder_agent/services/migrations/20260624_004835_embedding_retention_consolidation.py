"""Migration 20260624_004835: embedding retention consolidation.

Prod convergence for folding prune_stale_embeddings / prune_orphan_embeddings /
collapse_old_embeddings plugin jobs into the retention_policies declarative
framework.

Changes on prod (all idempotent — fresh installs already have the correct state
from the updated 0000_baseline):

1. Drop the parameter_required check constraint — it requires at least one of
   ttl_days / downsample_rule / summarize_handler to be non-NULL, but the new
   handler types (embeddings_orphan_prune, embeddings_collapse) store all config
   in the config jsonb column and leave those three NULL.

2. Add min_interval_hours (REAL, nullable) so the runner can skip policies that
   aren't due yet. NULL = run every cycle (backward-compatible). 168 = weekly.

3. Rename + fix the 3 existing TTL rows:
   - embeddings.audit  → embeddings.ttl_prune.audit  (ttl_days=90, fix JSON)
   - embeddings.brain  → embeddings.ttl_prune.brain  (ttl_days=365 was 180, fix JSON)
   - embeddings.claude_sessions → embeddings.ttl_prune.claude_sessions (ttl_days=30, fix JSON)
   Also adds COALESCE(is_summary, FALSE) = FALSE to filter_sql so summary rows
   are never hard-deleted by TTL prune.

4. Insert 6 new rows (orphan_prune × 3, collapse × 3), all enabled=FALSE.
   min_interval_hours=168 for collapse rows (weekly cadence).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    # 1. Drop the check constraint — incompatible with config-based handlers.
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE retention_policies "
            "DROP CONSTRAINT IF EXISTS retention_policies_parameter_required_chk"
        )

    # 2. Add min_interval_hours column (idempotent).
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE retention_policies "
            "ADD COLUMN IF NOT EXISTS min_interval_hours REAL"
        )

    # 3. Rename + fix the 3 existing TTL rows.
    #    WHERE matches old name → no-op on fresh installs (baseline already
    #    inserts with new names via ON CONFLICT DO NOTHING).
    renames = [
        (
            "embeddings.ttl_prune.audit",
            "source_table = 'audit' AND COALESCE(is_summary, FALSE) = FALSE",
            90,
            '{"description": "Audit event embeddings — long enough for quarterly reviews"}',
            "embeddings.audit",
        ),
        (
            "embeddings.ttl_prune.brain",
            "source_table = 'brain' AND COALESCE(is_summary, FALSE) = FALSE",
            365,
            '{"description": "Brain decision embeddings — system reasoning, slower decay"}',
            "embeddings.brain",
        ),
        (
            "embeddings.ttl_prune.claude_sessions",
            "source_table = 'claude_sessions' AND COALESCE(is_summary, FALSE) = FALSE",
            30,
            '{"description": "Claude session chunks — ephemeral working notes, recent context matters"}',
            "embeddings.claude_sessions",
        ),
    ]
    async with pool.acquire() as conn:
        for new_name, filter_sql, ttl_days, metadata_json, old_name in renames:
            await conn.execute(
                """
                UPDATE retention_policies
                   SET name        = $1,
                       filter_sql  = $2,
                       ttl_days    = $3,
                       config      = '{}'::jsonb,
                       metadata    = $4::jsonb
                 WHERE name = $5
                """,
                new_name, filter_sql, ttl_days, metadata_json, old_name,
            )

    # 4. Insert 6 new rows.
    #    ON CONFLICT (id) DO NOTHING — baseline already inserted them on fresh
    #    installs. A subsequent UPDATE sets min_interval_hours for collapse rows
    #    in case the baseline INSERT already ran without the column.
    new_rows = [
        (
            "7a000001-0000-0000-0000-000000000001",
            "embeddings.orphan_prune.posts",
            "embeddings_orphan_prune",
            '{"source_table": "posts", "batch_size": 1000}',
            None,
        ),
        (
            "7a000001-0000-0000-0000-000000000002",
            "embeddings.orphan_prune.audit",
            "embeddings_orphan_prune",
            '{"source_table": "audit", "batch_size": 1000}',
            None,
        ),
        (
            "7a000001-0000-0000-0000-000000000003",
            "embeddings.orphan_prune.brain",
            "embeddings_orphan_prune",
            '{"source_table": "brain", "batch_size": 1000}',
            None,
        ),
        (
            "7a000001-0000-0000-0000-000000000004",
            "embeddings.collapse.claude_sessions",
            "embeddings_collapse",
            '{"source_table": "claude_sessions", "age_days": 90, "cluster_size": 8, "summary_provider": "ollama", "summary_model": "phi4:14b"}',
            168,
        ),
        (
            "7a000001-0000-0000-0000-000000000005",
            "embeddings.collapse.brain",
            "embeddings_collapse",
            '{"source_table": "brain", "age_days": 90, "cluster_size": 8, "summary_provider": "ollama", "summary_model": "phi4:14b"}',
            168,
        ),
        (
            "7a000001-0000-0000-0000-000000000006",
            "embeddings.collapse.audit",
            "embeddings_collapse",
            '{"source_table": "audit", "age_days": 90, "cluster_size": 8, "summary_provider": "ollama", "summary_model": "phi4:14b"}',
            168,
        ),
    ]
    async with pool.acquire() as conn:
        for row_id, name, handler, config_json, min_interval_hours in new_rows:
            await conn.execute(
                """
                INSERT INTO retention_policies
                    (id, name, handler_name, table_name, age_column, enabled,
                     config, metadata, min_interval_hours)
                VALUES ($1, $2, $3, 'embeddings', 'created_at', false,
                        $4::jsonb, '{}'::jsonb, $5)
                ON CONFLICT (id) DO NOTHING
                """,
                row_id, name, handler, config_json, min_interval_hours,
            )

    # 5. Ensure collapse rows have min_interval_hours=168 even if they were
    #    inserted via baseline before the column existed (covers edge cases
    #    where the baseline and migration ran in rapid succession on the same DB).
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE retention_policies SET min_interval_hours = 168 "
            "WHERE name LIKE 'embeddings.collapse.%' AND min_interval_hours IS NULL"
        )

    logger.info(
        "embedding_retention_consolidation up: constraint dropped, "
        "min_interval_hours added, 3 TTL rows renamed, 6 new rows inserted"
    )


async def down(_pool) -> None:
    # One-way migration: the constraint is dropped, rows are renamed, new rows
    # added. Reversing would rename rows back (losing the fix) and delete the 6
    # new rows — losing operator-configured state. Not worth the risk.
    logger.info(
        "embedding_retention_consolidation down: no-op "
        "(one-way consolidation — re-run baseline to restore original seed state)"
    )
