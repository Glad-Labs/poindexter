"""Migration: create sensor_samples_unified view for continuous sensor history.

After PR #1393, sensor_samples is pruned at 30 days and older data is
summarised into sensor_samples_hourly (bucket_start, source, metric_name,
avg_value, min_value, max_value, sample_count).

This view unions them so Grafana dashboards — which filter on $__timeFilter
over a single table — see unbroken history regardless of the selected time
range:

  - < 30 days  → raw rows from sensor_samples
  - >= 30 days → hourly-bucket rows from sensor_samples_hourly

The hourly side maps avg_value → metric_value and bucket_start →
sampled_at, so existing queries that reference sampled_at / metric_value
continue to work without modification.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CREATE = """
CREATE OR REPLACE VIEW sensor_samples_unified AS
    SELECT
        sampled_at,
        source,
        metric_name,
        metric_value,
        unit,
        dimensions
    FROM sensor_samples
UNION ALL
    SELECT
        bucket_start        AS sampled_at,
        source,
        metric_name,
        avg_value           AS metric_value,
        NULL::text          AS unit,
        '{}'::jsonb         AS dimensions
    FROM sensor_samples_hourly
    WHERE bucket_start < now() - INTERVAL '30 days'
"""

_DROP = "DROP VIEW IF EXISTS sensor_samples_unified"


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CREATE)
    logger.info("Migration create_sensor_samples_unified_view: view created")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DROP)
    logger.info("Migration create_sensor_samples_unified_view down: view dropped")
