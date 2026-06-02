"""Migration: drop #355 atom-cutover residue.

After the atom-cutover went live (``pipeline_use_graph_def=true``;
``canonical_blog`` runs the seeded graph_def with the ``qa.*`` rail atoms
instead of the monolithic ``cross_model_qa`` stage), two artifacts of the
now-deleted stage are left dangling in the DB:

  - ``pipeline_atoms`` row ``name='stage.cross_model_qa'`` — the worker on
    main no longer registers this atom (``services/stages/cross_model_qa.py``
    is deleted), so ``atom_registry`` will never re-create it. The row is a
    frozen orphan (its ``last_seen_at`` stopped updating at the cutover).
  - ``app_settings`` key ``pipeline.stages.order`` — the legacy static stage
    order that listed ``cross_model_qa`` and predates the ``qa.*`` atoms. The
    graph_def path resolves its order from ``canonical_blog_spec`` /
    ``pipeline_templates.graph_def``, not this key; grep confirms no live
    reader (only the baseline seed inserted it, plus docstring mentions in
    ``services/atoms/*``).

Both verified to have no live code readers. ``down()`` is a no-op: these are
dead artifacts of a deleted stage with no meaningful prior state to restore
(the atom row would only reappear if the stage were re-added to code, which it
will not be).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        atoms_result = await conn.execute(
            "DELETE FROM pipeline_atoms WHERE name = 'stage.cross_model_qa'"
        )
        order_result = await conn.execute(
            "DELETE FROM app_settings WHERE key = 'pipeline.stages.order'"
        )
    logger.info(
        "drop_cross_model_qa_cutover_residue: removed pipeline_atoms "
        "stage.cross_model_qa (%s); app_settings pipeline.stages.order (%s)",
        atoms_result,
        order_result,
    )


async def down(pool) -> None:
    # No-op: dead artifacts of the deleted cross_model_qa stage. Nothing
    # meaningful to restore — the atom is only (re)created by registering the
    # stage in code (deleted), and pipeline.stages.order is superseded by the
    # graph_def. Kept as a callable so the runner's down() interface holds.
    logger.info(
        "drop_cross_model_qa_cutover_residue down: no-op (dead cutover residue)"
    )
