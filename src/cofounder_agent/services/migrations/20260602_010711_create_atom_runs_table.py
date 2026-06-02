"""Migration: create atom_runs table + seed atom_runs_capture_enabled

ISSUE: Glad-Labs/poindexter#355 (atom-cutover Plan 2)

Per-atom run + outcome capture for composed (build_graph_from_spec)
pipelines — the (composition -> outcome) substrate for #361
(outcome->router feedback) and a future composition-learning architect.

Complementary to capability_outcomes (which scores (atom, tier, model)
for the router): atom_runs adds a per-invocation run_id, input/output
state-key digests (the composition shape), cost/retries, and the full
outcome join (post_id / approval decision / edit_distance) backfilled
after the human-approval gate resolves.

Additive + dormant: no production code writes to this table yet. Plan 4
wires persist_atom_runs into the runner and record_atom_run_outcome into
the approval path. The capture is gated by app_settings.atom_runs_capture_enabled
(seeded 'true'). post_id is UUID because posts.id is a uuid.

Idempotent via IF NOT EXISTS / ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration. Idempotent."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS atom_runs (
                id              BIGSERIAL PRIMARY KEY,
                run_id          TEXT NOT NULL,
                task_id         TEXT,
                template_slug   TEXT,
                seq             INTEGER NOT NULL DEFAULT 0,
                atom            TEXT NOT NULL,
                node_id         TEXT,
                tier            TEXT,
                model           TEXT,
                latency_ms      INTEGER NOT NULL DEFAULT 0,
                cost            NUMERIC(12, 6),
                retries         INTEGER NOT NULL DEFAULT 0,
                status          TEXT NOT NULL,
                input_digest    TEXT,
                output_digest   TEXT,
                input_keys      TEXT[],
                output_keys     TEXT[],
                metrics         JSONB NOT NULL DEFAULT '{}'::jsonb,
                -- Outcome join (backfilled by record_atom_run_outcome
                -- after the human-approval gate resolves):
                post_id         UUID,
                decision        TEXT,
                quality_score   NUMERIC(5, 2),
                edit_distance   INTEGER,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_atom_runs_run_id ON atom_runs (run_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_atom_runs_task_id ON atom_runs (task_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_atom_runs_atom ON atom_runs (atom)"
        )
        # Seed the capture toggle (DB-config enable flag, D6). 'true' so
        # capture is on the moment Plan 4 wires the call; operators can
        # flip it without a deploy. ON CONFLICT keeps an operator-tuned value.
        await conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO NOTHING",
            "atom_runs_capture_enabled", "true",
        )
        logger.info("Migration create_atom_runs_table: applied")


async def down(pool) -> None:
    """Revert: drop the table + remove the seeded flag (only if untouched)."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS atom_runs")
        await conn.execute(
            "DELETE FROM app_settings "
            "WHERE key = 'atom_runs_capture_enabled' AND value = 'true'"
        )
        logger.info("Migration create_atom_runs_table down: reverted")
