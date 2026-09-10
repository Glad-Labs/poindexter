"""Migration 20260826_032540: add benchmark_results table for CI endpoint latency ingest

ISSUE: Glad-Labs/glad-labs-stack#3337 (follow-up)

The nightly ``benchmarks`` GitHub Actions workflow uploads pytest-benchmark
results (endpoint latency, in-process TestClient) as a run artifact that
nothing consumed — no Grafana panel, violating the every-metric-gets-a-panel
rule. The workflow runs on a HOSTED runner with no Tailnet access, so it
cannot write to this DB itself; ``IngestBenchmarkResultsJob`` pulls the
artifacts down via the GitHub API instead and lands one row per benchmark
per workflow run here. The Observability board's "CI Endpoint Benchmarks"
panel charts mean latency per benchmark over time from this table.

``UNIQUE (workflow_run_id, benchmark_name)`` is the ingest's idempotency
key — re-polling a run is an ON CONFLICT no-op. Volume is tiny (one run
per night x ~5 benchmarks), but a retention policy row (730d) keeps it
bounded on principle.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Create benchmark_results + its retention policy (idempotent)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS benchmark_results (
                id BIGSERIAL PRIMARY KEY,
                workflow_run_id BIGINT NOT NULL,
                run_started_at TIMESTAMP WITH TIME ZONE NOT NULL,
                commit_sha TEXT,
                benchmark_name TEXT NOT NULL,
                group_name TEXT,
                mean_seconds DOUBLE PRECISION NOT NULL,
                min_seconds DOUBLE PRECISION,
                max_seconds DOUBLE PRECISION,
                stddev_seconds DOUBLE PRECISION,
                median_seconds DOUBLE PRECISION,
                rounds INTEGER,
                captured_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                UNIQUE (workflow_run_id, benchmark_name)
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO retention_policies
                (name, handler_name, table_name, filter_sql, age_column,
                 ttl_days, enabled, metadata)
            SELECT
                'benchmark_results.aged', 'ttl_prune', 'benchmark_results',
                NULL, 'captured_at', 730, true,
                '{"issue": "glad-labs-stack#3337"}'::jsonb
            WHERE NOT EXISTS (
                SELECT 1 FROM retention_policies
                WHERE name = 'benchmark_results.aged'
            )
            """
        )
    logger.info(
        "Migration add_benchmark_results_table_for_ci_endpoint_latency_ingest: applied"
    )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM retention_policies WHERE name = 'benchmark_results.aged'"
        )
        await conn.execute("DROP TABLE IF EXISTS benchmark_results")
    logger.info(
        "Migration add_benchmark_results_table_for_ci_endpoint_latency_ingest: reverted"
    )
