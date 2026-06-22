"""Migration 20260622_200222: drop the retired ``pipeline_tasks.category`` column.

Final retirement of the vestigial ``pipeline_tasks.category`` column, shipped
alongside the Phase F squash. The column churned add (baseline) →
``20260622_032938`` drop (shimmed via views) → ``20260622_055500`` re-add (that
drop missed ``claim_pending_task``'s base-table SELECT and crashed the claim
path). #1867 reconciled it to base-table-only; this migration removes it for
good now that ``claim_pending_task`` no longer reads it.

**Why this migration survives the squash.** A baseline only ever
``CREATE TABLE IF NOT EXISTS``, which no-ops on installs that already have the
table — so a Phase F baseline that simply omits ``category`` would leave existing
installs (prod) with the column while fresh installs lack it. This migration is
the convergence step:

  * Fresh installs — the Phase F ``0000_baseline.schema.sql`` omits the column,
    so ``DROP COLUMN IF EXISTS`` is a no-op.
  * Existing installs (prod, which still carries the column from ``055500``) —
    this performs the real drop.

Both converge to a category-free ``pipeline_tasks``. The next squash (Phase G)
can fold this away once every install has dropped it.

The ``content_tasks`` / ``pipeline_tasks_view`` views keep their literal
``NULL::character varying AS category`` shim (they never referenced
``pt.category`` after ``032938``), so reads through them — ``SELECT *`` /
``TaskRecord.category`` / the ``GET /tasks?category=`` filter — are unaffected,
and the INSTEAD OF triggers don't write it. The DROP therefore has no view or
trigger dependency to break.

Safe: ``category`` was always NULL (0 of 1,830 rows on prod ``poindexter_brain``),
superseded by ``niche_slug`` (#796), and its last reader
(``services/flows/content_generation.py::claim_pending_task``) was removed in the
same PR.

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS category"
        )
    logger.info(
        "drop_pipeline_tasks_category up: dropped the retired "
        "pipeline_tasks.category column (no-op where already absent)"
    )


async def down(pool) -> None:
    # No-op: ``category`` is retired (superseded by niche_slug, #796) and this
    # migration ends an add/drop/re-add churn. Re-adding it would only restore an
    # all-NULL vestige with no reader. Same one-way posture as
    # 20260620_054135_retire_orphaned_ops_triage_system_prompt.
    logger.info(
        "drop_pipeline_tasks_category down: no-op (refusing to re-add a retired column)"
    )
