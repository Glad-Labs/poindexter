"""Migration 20260726_181025_add_gpu_scheduler_observability_tables: add gpu scheduler observability tables

ISSUE: Glad-Labs/poindexter#914
DESIGN: docs/superpowers/specs/2026-07-26-gpu-scheduler-queue-admission-design.md
PLAN:   docs/superpowers/plans/2026-07-26-gpu-scheduler-p0-p1.md (Task A1)

P0 (observe) of the GPU scheduler evolution. Two tables:

- ``gpu_lease_stats`` — rolling per-(owner, phase) lock-hold duration
  statistics, upserted on EVERY lock release (task-attributed or not — the
  existing ``gpu_task_sessions`` only records sessions with a task_id, which
  is why this is its own table). Feeds the P1 admission ETA estimator.
  ``q_state`` holds the streaming-quantile marker state (P² algorithm) as
  JSONB so the estimate continues across process restarts; ``p50_ms`` /
  ``p90_ms`` are the current estimates denormalized for cheap reads and
  Grafana panels.
- ``gpu_queue`` — a mirror of in-process lock waiters (insert on
  contended-wait start, delete on acquire/abandon) so the console and
  Grafana can show holder + waiters cross-process. Rows are ephemeral;
  crash orphans are reaped by age (the mirror piggybacks a reap on each
  write), so no retention policy is needed.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gpu_lease_stats (
                owner       TEXT NOT NULL,
                phase       TEXT NOT NULL DEFAULT '',
                samples     BIGINT NOT NULL DEFAULT 0,
                ewma_ms     DOUBLE PRECISION,
                p50_ms      DOUBLE PRECISION,
                p90_ms      DOUBLE PRECISION,
                q_state     JSONB,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (owner, phase)
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gpu_queue (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                pid          INT NOT NULL,
                owner        TEXT NOT NULL,
                model        TEXT,
                phase        TEXT,
                priority     TEXT NOT NULL DEFAULT 'pipeline',
                enqueued_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gpu_queue_enqueued "
            "ON gpu_queue (enqueued_at)"
        )
    logger.info("Migration add_gpu_scheduler_observability_tables: applied")


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS gpu_queue")
        await conn.execute("DROP TABLE IF EXISTS gpu_lease_stats")
    logger.info("Migration add_gpu_scheduler_observability_tables: reverted")
