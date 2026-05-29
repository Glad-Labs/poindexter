"""Migration 20260529_012228: add winner_variant_label to experiments.

Follow-on to PR #699 (Phase 1 PR 1 — experiments harness foundation) for
PR 3 (the ``poindexter experiments`` CLI). The CLI's ``conclude`` command
needs to persist *which* variant the operator chose as winner so the
historical record of every concluded experiment carries both:

- ``conclusion_note`` — the operator's free-text rationale (already on
  the table from PR 1).
- ``winner_variant_label`` — the structured ``A``/``B``/``control``/...
  pointer back into ``experiment_variants.label``. Lets the scorecard
  view (PR 4) badge the winner row at a glance and lets the digest
  surface (Phase 4) summarise "this week 3 experiments concluded;
  winners: A, B, A" without re-parsing the note.

The column is nullable on purpose — an in-flight experiment that's still
``draft`` / ``active`` / ``paused`` has no winner yet. Only ``concluded``
rows carry a non-NULL value, and the CLI's ``conclude`` command writes
both columns in the same UPDATE so they can't drift.

Per ``feedback_backcompat_now_required`` the column is purely additive:
``ADD COLUMN IF NOT EXISTS`` no-ops on a replay, ``DROP COLUMN IF EXISTS``
in ``down()`` reverses cleanly, and no existing reader is forced to
project the new column.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_ADD_WINNER_COLUMN_DDL = """\
ALTER TABLE experiments
  ADD COLUMN IF NOT EXISTS winner_variant_label text
"""


_DROP_WINNER_COLUMN_DDL = """\
ALTER TABLE experiments
  DROP COLUMN IF EXISTS winner_variant_label
"""


async def up(pool) -> None:
    """Add the column. Idempotent via ``IF NOT EXISTS``."""
    async with pool.acquire() as conn:
        await conn.execute(_ADD_WINNER_COLUMN_DDL)
    logger.info(
        "[migration] phase1_experiments_winner_label: applied — "
        "experiments.winner_variant_label column added",
    )


async def down(pool) -> None:
    """Reverse — drop the column. ``IF EXISTS`` makes a replay safe."""
    async with pool.acquire() as conn:
        await conn.execute(_DROP_WINNER_COLUMN_DDL)
    logger.info(
        "[migration] phase1_experiments_winner_label down: "
        "experiments.winner_variant_label column dropped",
    )
