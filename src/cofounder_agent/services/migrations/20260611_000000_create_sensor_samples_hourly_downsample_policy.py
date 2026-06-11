"""Migration: create sensor_samples_hourly rollup table and switch
sensor_samples retention policy from ttl_prune to downsample.

The 30-day ttl_prune policy seeded in 20260610_220000 would hard-delete
all PSU/sensor telemetry older than 30 days. That's fine for memory, but
it loses the historical trend (power draw over months, thermal drift, etc.).

This migration keeps raw samples for 30 days as before, but instead of
deleting them outright, rolls older data into hourly averages/min/max in
``sensor_samples_hourly``. The rollup table grows ~75 metric_names × 24
buckets/day ≈ 1 800 rows/day (vs. ~47 000 raw rows/day), so a full year
of history is ~650 k rows rather than ~17 M.

The ``retention_policies`` row for sensor_samples is updated in-place
(same UUID ``f3a2b1c0-d4e5-6789-abcd-ef0123456789``):
- handler_name:    ttl_prune  →  downsample
- ttl_days:        30         →  NULL  (check constraint still satisfied
                                        because downsample_rule IS NOT NULL)
- downsample_rule: NULL       →  see _DOWNSAMPLE_RULE below

Idempotent: CREATE TABLE / CREATE INDEX use IF NOT EXISTS; the UPDATE
is keyed on the fixed UUID so a re-run is a no-op.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_SENSOR_SAMPLES_ID = "f3a2b1c0-d4e5-6789-abcd-ef0123456789"

_DOWNSAMPLE_RULE = json.dumps({
    "keep_raw_days": 30,
    "rollup_table": "sensor_samples_hourly",
    "rollup_interval": "1 hour",
    "group_by": ["source", "metric_name"],
    "aggregations": [
        {"col": "metric_value", "fn": "avg", "as": "avg_value"},
        {"col": "metric_value", "fn": "min", "as": "min_value"},
        {"col": "metric_value", "fn": "max", "as": "max_value"},
        {"col": "metric_value", "fn": "count", "as": "sample_count"},
    ],
})

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS sensor_samples_hourly (
    id          bigserial PRIMARY KEY,
    bucket_start timestamptz NOT NULL,
    source      text NOT NULL,
    metric_name text NOT NULL,
    avg_value   numeric(14,4),
    min_value   numeric(14,4),
    max_value   numeric(14,4),
    sample_count bigint NOT NULL DEFAULT 0,
    CONSTRAINT sensor_samples_hourly_bucket_source_metric_unique
        UNIQUE (bucket_start, source, metric_name)
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS sensor_samples_hourly_bucket_idx
    ON sensor_samples_hourly (bucket_start DESC)
"""

_UPDATE_POLICY = """
UPDATE retention_policies
   SET handler_name    = 'downsample',
       downsample_rule = $1::jsonb,
       ttl_days        = NULL
 WHERE id = $2::uuid
"""

_REVERT_POLICY = """
UPDATE retention_policies
   SET handler_name    = 'ttl_prune',
       downsample_rule = NULL,
       ttl_days        = 30
 WHERE id = $1::uuid
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE)
        await conn.execute(_CREATE_INDEX)
        await conn.execute(_UPDATE_POLICY, _DOWNSAMPLE_RULE, _SENSOR_SAMPLES_ID)
    logger.info(
        "Migration create_sensor_samples_hourly_downsample_policy: "
        "rollup table created + retention policy switched to downsample"
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_REVERT_POLICY, _SENSOR_SAMPLES_ID)
        await conn.execute("DROP TABLE IF EXISTS sensor_samples_hourly")
    logger.info(
        "Migration create_sensor_samples_hourly_downsample_policy down: "
        "reverted to ttl_prune + dropped rollup table"
    )
