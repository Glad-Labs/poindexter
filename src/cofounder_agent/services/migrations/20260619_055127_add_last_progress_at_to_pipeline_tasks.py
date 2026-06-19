"""Migration 20260619_055127: add pipeline_tasks.last_progress_at (per-node heartbeat).

ISSUE: Glad-Labs/poindexter (progress-aware stuck-flow probe — see
docs/superpowers/specs/2026-06-19-progress-aware-stuck-flow-probe-design.md)

The brain's prefect_stuck_flow_probe needs a durable signal to tell a
*progressing* content run from a *wedged* one. The content pipeline stamps
this column on every graph node start (and at claim time); the probe reads
``now() - last_progress_at`` to gate the queue-backlog page and to redefine
RUNNING-stuck as "no node progress for N minutes" rather than a flat age.

Nullable, no default, no backfill — existing in_progress rows stay NULL and
ride the probe's legacy flat-threshold fallback until they finish. Metadata-
only on PG11+ (no table rewrite). Idempotent (IF NOT EXISTS); a no-op on a
fresh baseline DB other than adding the column.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_UP = """
ALTER TABLE pipeline_tasks
    ADD COLUMN IF NOT EXISTS last_progress_at timestamptz
"""

_DOWN = """
ALTER TABLE pipeline_tasks
    DROP COLUMN IF EXISTS last_progress_at
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_UP)
    logger.info("Migration 20260619_055127: added pipeline_tasks.last_progress_at")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DOWN)
    logger.info("Migration 20260619_055127: dropped pipeline_tasks.last_progress_at")
