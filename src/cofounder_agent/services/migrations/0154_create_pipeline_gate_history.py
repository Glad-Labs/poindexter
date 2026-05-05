"""Migration 0154: pipeline_gate_history side-table for gate approvals + regens.

Phase 1 of pipeline_events split-migration (poindexter#366). The
2026-05-04 audit found pipeline_events serving four unrelated
purposes — tracing, gate state, retry counters, restart-safe metric
counter — that have to be unwound separately before the table can be
dropped (#48).

This migration handles the gate-state slice. Adds a typed side-table
that approval_service writes to and approval_gate.py + rejection_handlers
read from. Backfills from existing pipeline_events rows so in-flight
tasks don't lose their idempotency record (in practice the live DB
has zero rows for these event types as of 2026-05-04, but the
backfill stays for any other env that's been running longer).

Why a side-table and not columns on pipeline_tasks: each task can
hit multiple gates and each gate can fire multiple regens, so the
relationship is one-to-many. A flat column would either lose history
or require a JSONB blob per row.

Why nullable task_id + post_id with a CHECK constraint: regen_media
is keyed by post_id (final-publish gate runs after the task has
finalized into a post), all other rows are keyed by task_id. The
constraint enforces exactly-one-of, indexes are partial on the
NOT-NULL side so lookups stay fast.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_gate_history (
                id BIGSERIAL PRIMARY KEY,
                task_id TEXT,
                post_id TEXT,
                gate_name TEXT NOT NULL,
                event_kind TEXT NOT NULL,
                feedback TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT pipeline_gate_history_one_id
                    CHECK ((task_id IS NOT NULL) <> (post_id IS NOT NULL))
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pipeline_gate_history_task_lookup
                ON pipeline_gate_history (task_id, gate_name, event_kind)
                WHERE task_id IS NOT NULL
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pipeline_gate_history_post_lookup
                ON pipeline_gate_history (post_id, gate_name, event_kind)
                WHERE post_id IS NOT NULL
            """
        )

        approvals = await conn.execute(
            """
            INSERT INTO pipeline_gate_history
                (task_id, gate_name, event_kind, feedback, metadata, created_at)
            SELECT
                payload->>'task_id',
                payload->>'gate_name',
                'approved',
                payload->>'feedback',
                payload,
                created_at
            FROM pipeline_events
            WHERE event_type = 'task.gate_approved'
              AND payload->>'task_id' IS NOT NULL
              AND payload->>'gate_name' IS NOT NULL
            """
        )
        logger.info("Migration 0154: backfilled approvals: %s", approvals)

        regen_draft = await conn.execute(
            """
            INSERT INTO pipeline_gate_history
                (task_id, gate_name, event_kind, feedback, metadata, created_at)
            SELECT
                payload->>'primary_id',
                COALESCE(payload->>'gate_name', 'preview_approval'),
                'regen_draft',
                payload->>'feedback',
                payload,
                created_at
            FROM pipeline_events
            WHERE event_type = 'task.regen_draft'
              AND payload->>'primary_id' IS NOT NULL
            """
        )
        logger.info("Migration 0154: backfilled regen_draft: %s", regen_draft)

        regen_media = await conn.execute(
            """
            INSERT INTO pipeline_gate_history
                (post_id, gate_name, event_kind, feedback, metadata, created_at)
            SELECT
                payload->>'primary_id',
                COALESCE(payload->>'gate_name', 'final_publish_approval'),
                'regen_media',
                payload->>'feedback',
                payload,
                created_at
            FROM pipeline_events
            WHERE event_type = 'task.regen_media'
              AND payload->>'primary_id' IS NOT NULL
            """
        )
        logger.info("Migration 0154: backfilled regen_media: %s", regen_media)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS pipeline_gate_history CASCADE")
        logger.info("Migration 0154 down: dropped pipeline_gate_history")
