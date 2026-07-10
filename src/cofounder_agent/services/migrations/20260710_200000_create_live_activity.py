"""Create the live_activity ledger — mutable in-flight rows carrying live
progress + heartbeat across every activity kind (jobs / content / media / brain).
The console 'what is the system doing now' pulse reads from this table.
stdlib-only so migrations-smoke applies it without a full app boot."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS live_activity (
                id            BIGSERIAL PRIMARY KEY,
                kind          TEXT NOT NULL,
                ref_id        TEXT,
                title         TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'running',
                step          TEXT,
                progress_pct  SMALLINT,
                detail        JSONB NOT NULL DEFAULT '{}'::jsonb,
                started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                finished_at   TIMESTAMPTZ
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_live_activity_running "
            "ON live_activity (started_at DESC) WHERE finished_at IS NULL"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_live_activity_recent "
            "ON live_activity (finished_at DESC) WHERE finished_at IS NOT NULL"
        )
        # Prod-convergence seed for the live_activity retention policy. The
        # canonical copy lives in 0000_baseline.seeds.sql (fresh installs);
        # prod never re-runs baseline, so this idempotent INSERT is how the
        # row reaches an existing DB — same pattern as
        # 20260702_064544_add_topic_pool_retention_policy. ttl_prune on
        # finished_at only touches completed rows; live rows (finished_at
        # NULL) are never pruned.
        await conn.execute(
            """
            INSERT INTO retention_policies
                (id, name, handler_name, table_name, filter_sql, age_column,
                 ttl_days, downsample_rule, summarize_handler, enabled,
                 config, metadata)
            VALUES
                ('a1b2c3d4-0002-4000-8000-000000000024', 'live_activity',
                 'ttl_prune', 'live_activity', NULL, 'finished_at', 2, NULL,
                 NULL, true, '{}'::jsonb,
                 '{"description": "Console live-activity ledger — prune finished rows after 2d; running rows (finished_at NULL) are never pruned"}'::jsonb)
            ON CONFLICT (id) DO NOTHING
            """
        )
    logger.info(
        "create_live_activity up: live_activity ledger + retention policy ready"
    )
