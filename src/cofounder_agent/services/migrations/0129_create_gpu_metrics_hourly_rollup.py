"""Migration 0129: gpu_metrics_hourly rollup table + corrected downsample_rule.

Phase D of the memory-compression rollout. The ``gpu_metrics`` retention
policy already had a ``downsample_rule`` configured to roll 1-min raw
samples into hourly buckets, but three things were broken so enabling
the policy would have thrown on first fire:

1. **Target table missing.** ``rollup_table = "gpu_metrics_hourly"``
   was referenced but no such table exists. This migration creates it
   with a ``bucket_start`` PK so the handler's ``ON CONFLICT
   (bucket_start) DO NOTHING`` clause has something to bite on.

2. **Aggregation columns referenced names that don't exist.** The
   rule said ``utilization_pct`` / ``memory_used_mb`` / ``power_watts``
   but the actual ``gpu_metrics`` columns are ``utilization`` /
   ``memory_used`` / ``power_draw``. Three real cols renamed in the
   updated rule.

3. **age_column was wrong.** Row had ``age_column = 'sampled_at'``
   but the table uses ``timestamp``. Updated.

While here, expanded the aggregations to capture more useful signal
(min/max/avg per metric instead of just one of each).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``UPDATE …`` (re-run
leaves the table alone, re-applies the rule which is fine since the
JSONB compares equal).
"""

import json

from services.logger_config import get_logger

logger = get_logger(__name__)


_NEW_RULE = {
    "keep_raw_days": 30,
    "rollup_table": "gpu_metrics_hourly",
    "rollup_interval": "1 hour",
    "aggregations": [
        {"col": "utilization", "fn": "avg", "as": "avg_utilization"},
        {"col": "utilization", "fn": "max", "as": "peak_utilization"},
        {"col": "temperature", "fn": "avg", "as": "avg_temperature"},
        {"col": "temperature", "fn": "max", "as": "peak_temperature"},
        {"col": "power_draw", "fn": "avg", "as": "avg_power_draw"},
        {"col": "power_draw", "fn": "max", "as": "peak_power_draw"},
        {"col": "memory_used", "fn": "avg", "as": "avg_memory_used"},
        {"col": "memory_used", "fn": "max", "as": "peak_memory_used"},
        {"col": "fan_speed", "fn": "avg", "as": "avg_fan_speed"},
    ],
    "group_by": [],
}


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # (1) Create the rollup target if it doesn't exist. Column types
        # mirror the source columns from `gpu_metrics`. bucket_start is
        # the PK so the downsample handler's ON CONFLICT clause works.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gpu_metrics_hourly (
                bucket_start TIMESTAMPTZ PRIMARY KEY,
                avg_utilization DOUBLE PRECISION,
                peak_utilization DOUBLE PRECISION,
                avg_temperature DOUBLE PRECISION,
                peak_temperature DOUBLE PRECISION,
                avg_power_draw DOUBLE PRECISION,
                peak_power_draw DOUBLE PRECISION,
                avg_memory_used DOUBLE PRECISION,
                peak_memory_used DOUBLE PRECISION,
                avg_fan_speed DOUBLE PRECISION,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gpu_metrics_hourly_bucket "
            "ON gpu_metrics_hourly(bucket_start DESC)"
        )

        # (2) Fix the row config. age_column was 'sampled_at', actual
        # table column is 'timestamp'. downsample_rule had wrong column
        # names for every aggregation.
        if not await _table_exists(conn, "retention_policies"):
            logger.info(
                "Migration 0129: retention_policies missing — created "
                "rollup table only, can't seed row config"
            )
            return

        result = await conn.execute(
            """
            UPDATE retention_policies
               SET age_column = 'timestamp',
                   downsample_rule = $1::jsonb,
                   ttl_days = NULL
             WHERE name = 'gpu_metrics' AND handler_name = 'downsample'
            """,
            json.dumps(_NEW_RULE),
        )
        if result.startswith("UPDATE 1"):
            logger.info(
                "Migration 0129: created gpu_metrics_hourly + fixed "
                "gpu_metrics retention row config"
            )
        else:
            logger.info(
                "Migration 0129: created gpu_metrics_hourly; gpu_metrics "
                "retention row not present (will be seeded by a later "
                "migration if needed)"
            )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS gpu_metrics_hourly")
        # Don't try to revert the row config — it was broken before the
        # migration. Down only undoes the additive parts.
        logger.info(
            "Migration 0129 rolled back: dropped gpu_metrics_hourly "
            "(retention row config left at the corrected values)"
        )
