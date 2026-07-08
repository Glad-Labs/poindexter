"""Migration 20260708_025428_atom_runs_output_preview_run_id_seq_unique: atom_runs output_preview + run_id seq unique

Adds two things to ``atom_runs`` for the operator console task-trace:

1. ``output_preview text`` — a bounded, human-readable snapshot of each
   node's changed output values, captured at write time (the row's
   ``output_digest`` is a hash; this is the readable preview an operator
   reads in the trace deep-dive).

2. ``UNIQUE (run_id, seq)`` — lets a run's per-node rows be **upserted**
   incrementally (one write as each node completes) instead of a single
   batch write at end-of-run. A run's ``seq`` is monotonic (rescue-loop
   re-runs get fresh seqs), so the pair is unique per record. The payoff:
   a running or killed run leaves per-node rows behind (live trace +
   partial trace when a run is cancelled mid-graph) rather than nothing.

Idempotent: ``ADD COLUMN IF NOT EXISTS`` no-ops where the column already
exists; the constraint is added once (the runner records applied
migrations by filename). A defensive de-dup runs first in case the old
batch path ever wrote a ``(run_id, seq)`` collision.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE atom_runs ADD COLUMN IF NOT EXISTS output_preview text"
        )
        # Defensively collapse any pre-existing (run_id, seq) duplicates from
        # the old batch path before the UNIQUE constraint is added, keeping the
        # physically-latest row (highest ctid).
        await conn.execute(
            """
            DELETE FROM atom_runs a
             USING atom_runs b
             WHERE a.ctid < b.ctid
               AND a.run_id = b.run_id
               AND a.seq = b.seq
            """
        )
        # ADD CONSTRAINT has no IF NOT EXISTS on this PG; guard the re-run case
        # (a manual re-apply) with a catalog check so the body stays safe.
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_constraint WHERE conname = 'atom_runs_run_id_seq_key'"
        )
        if not exists:
            await conn.execute(
                """
                ALTER TABLE atom_runs
                  ADD CONSTRAINT atom_runs_run_id_seq_key UNIQUE (run_id, seq)
                """
            )
    logger.info(
        "Migration atom_runs_output_preview_run_id_seq_unique: applied"
    )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE atom_runs DROP CONSTRAINT IF EXISTS atom_runs_run_id_seq_key"
        )
        await conn.execute(
            "ALTER TABLE atom_runs DROP COLUMN IF EXISTS output_preview"
        )
    logger.info(
        "Migration atom_runs_output_preview_run_id_seq_unique: reverted"
    )
