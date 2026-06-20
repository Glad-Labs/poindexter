"""Migration: pipeline_gate_history.post_id  text -> uuid (type alignment).

DB-hygiene batch (poindexter#702 item 2). ``pipeline_gate_history.post_id``
was declared ``text`` while ``posts.id`` is ``uuid`` — a mismatch that blocks
ever adding a real FK and forces ``::text`` casts in joins. The column is
all-NULL on prod (every row carries ``task_id`` instead; the table's CHECK
constraint ``pipeline_gate_history_one_id`` enforces exactly one of
task_id/post_id), so the ``USING post_id::uuid`` cast touches zero stored
values.

Scope is the type realignment only — it does NOT add the FK. ``posts`` rows
can be hard-deleted (takedown / orphan-sweep paths), so an enforced FK would
need ``ON DELETE SET NULL`` plus a backfill audit, which is out of scope for a
hygiene type-fix.

IMPORTANT: stdlib-only (no app imports) so the migrations-smoke CI step can
apply it without a full app boot. Idempotent — guarded on the live column
type, so a re-apply (or a fresh DB whose baseline later declares uuid) no-ops.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Alter ``pipeline_gate_history.post_id`` from text to uuid.

    Idempotent: reads the live column type first and no-ops when the column
    is absent or already uuid. The cast is safe because the column is
    UUID-shaped-or-NULL (all-NULL on prod today).
    """
    async with pool.acquire() as conn:
        current_type = await conn.fetchval(
            """
            SELECT data_type
              FROM information_schema.columns
             WHERE table_name = 'pipeline_gate_history'
               AND column_name = 'post_id'
            """
        )
        if current_type is None:
            logger.info(
                "pipeline_gate_history.post_id absent — skipping uuid alignment"
            )
            return
        if current_type == "uuid":
            logger.info("pipeline_gate_history.post_id already uuid — no-op")
            return
        await conn.execute(
            """
            ALTER TABLE pipeline_gate_history
            ALTER COLUMN post_id TYPE uuid USING post_id::uuid
            """
        )
    logger.info("pipeline_gate_history.post_id altered text -> uuid")


async def down(pool) -> None:
    """Revert ``pipeline_gate_history.post_id`` back to text."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE pipeline_gate_history
            ALTER COLUMN post_id TYPE text USING post_id::text
            """
        )
    logger.warning("pipeline_gate_history.post_id reverted uuid -> text")
