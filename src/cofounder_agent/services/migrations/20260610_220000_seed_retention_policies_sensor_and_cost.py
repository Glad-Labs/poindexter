"""Migration: seed ttl_prune retention policies for sensor_samples and cost_logs.

Both tables were growing without bounds:
- ``sensor_samples``: 1.49M rows (30 days, ~47k/day from Corsair HX1500i tap).
  No retention existed. At this rate it would reach ~14M rows by year-end.
  A 30-day TTL keeps the last month of raw PSU/sensor telemetry — enough for
  operational monitoring and the Grafana Hardware & Power dashboard.
- ``cost_logs``: 21k rows (74 days, ~280/day). Small but growing indefinitely.
  365 days retained for business cost analysis; beyond that the cost dashboard
  aggregates are the right view.

Both use the ``ttl_prune`` handler (hard-delete rows older than ``ttl_days``),
matching the ``embeddings.*`` policy pattern. No summary table needed.

Idempotent via ``ON CONFLICT (id) DO NOTHING``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SENSOR_SAMPLES_ID = "f3a2b1c0-d4e5-6789-abcd-ef0123456789"
_COST_LOGS_ID = "c0d1e2f3-a4b5-6789-cdef-012345678901"

_INSERT = """
INSERT INTO retention_policies (
    id, name, handler_name, table_name, filter_sql,
    age_column, ttl_days, downsample_rule, summarize_handler,
    enabled, config, metadata
) VALUES (
    $1, $2, 'ttl_prune', $3, NULL,
    $4, $5, NULL, NULL,
    true, '{}'::jsonb, $6::jsonb
) ON CONFLICT (id) DO NOTHING
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            _INSERT,
            _SENSOR_SAMPLES_ID,
            "sensor_samples",
            "sensor_samples",
            "sampled_at",
            30,
            '{"description": "Corsair PSU/sensor raw telemetry — 30 days raw; ~47k rows/day, hardware metrics"}',
        )
        await conn.execute(
            _INSERT,
            _COST_LOGS_ID,
            "cost_logs",
            "cost_logs",
            "created_at",
            365,
            '{"description": "LLM cost tracking — full year retained for business analysis"}',
        )
    logger.info(
        "Migration seed_retention_policies_sensor_and_cost: 2 ttl_prune policies seeded"
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM retention_policies WHERE id = ANY($1::uuid[])",
            [_SENSOR_SAMPLES_ID, _COST_LOGS_ID],
        )
    logger.info(
        "Migration seed_retention_policies_sensor_and_cost down: 2 policies removed"
    )
