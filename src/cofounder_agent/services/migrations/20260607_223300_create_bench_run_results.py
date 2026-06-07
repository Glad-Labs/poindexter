"""Migration: create bench_run_results + seed cost/energy eval-harness settings.

Backs the cost/energy eval harness (glad-labs-stack#530). The harness
(`scripts/bench/eval_cost_tiers.py`) sweeps cost tiers / models against a
fixed prompt set and persists one measured row per (model × prompt) run so
the Grafana cost dashboard can validate cost-tier routing with real
intelligence-per-watt data instead of the static-TDP estimate.

Adds:
  - bench_run_results table (+ 3 indexes on model / tier / created_at)
  - 2 app_settings keys the harness reads (Prometheus URL + default
    prompt-repeat count). Value '' is the unset sentinel — never NULL
    (app_settings.value is NOT NULL).

Idempotent — CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, and
ON CONFLICT DO NOTHING for app_settings. Imports only stdlib.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS bench_run_results (
    id                 SERIAL PRIMARY KEY,
    run_id             UUID NOT NULL DEFAULT gen_random_uuid(),
    model              TEXT NOT NULL,
    tier               TEXT,
    provider           TEXT NOT NULL,
    prompt_label       TEXT,
    prompt_tokens      INTEGER NOT NULL DEFAULT 0,
    completion_tokens  INTEGER NOT NULL DEFAULT 0,
    total_tokens       INTEGER NOT NULL DEFAULT 0,
    duration_ms        INTEGER NOT NULL DEFAULT 0,
    gpu_watts_avg      NUMERIC(8,2),
    electricity_kwh    NUMERIC(12,8),
    cost_usd           NUMERIC(10,6),
    quality_score      NUMERIC(5,2),
    joules_per_token   NUMERIC(12,4),
    tokens_per_second  NUMERIC(10,2),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_bench_run_results_model ON bench_run_results (model);",
    "CREATE INDEX IF NOT EXISTS idx_bench_run_results_tier ON bench_run_results (tier);",
    "CREATE INDEX IF NOT EXISTS idx_bench_run_results_created ON bench_run_results (created_at);",
]

_SETTINGS = [
    (
        "bench_prometheus_url",
        "http://localhost:9091",
        "bench",
        "Prometheus HTTP API base URL the cost/energy eval harness queries "
        "for avg_over_time(nvidia_gpu_power_draw_watts) during each LLM call. "
        "Empty = harness falls back to the static-TDP estimate.",
    ),
    (
        "bench_default_prompt_count",
        "3",
        "bench",
        "Default --repeat count for the cost/energy eval harness "
        "(scripts/bench/eval_cost_tiers.py): how many times each prompt is "
        "run per model when --repeat is not passed.",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE)
        for idx_sql in _CREATE_INDEXES:
            await conn.execute(idx_sql)
        for key, value, category, description in _SETTINGS:
            await conn.execute(
                """
                INSERT INTO app_settings
                  (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, false, true)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
    logger.info(
        "Migration create_bench_run_results: table + %d indexes + %d settings",
        len(_CREATE_INDEXES), len(_SETTINGS),
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS bench_run_results")
        keys = [k for k, *_ in _SETTINGS]
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])", keys,
        )
    logger.info("Migration create_bench_run_results down: table + settings removed")
