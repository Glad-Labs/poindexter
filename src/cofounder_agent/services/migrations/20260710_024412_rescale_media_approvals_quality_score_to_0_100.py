"""Migration 20260710_024412: rescale ``media_approvals.quality_score`` to 0-100.

Media Quality Layer 2 (spec 2026-07-09) moved ``media_approvals.quality_score``
off the old binary Layer-1 scale (``0.0`` = hard-fail, ``1.0`` = passed) onto a
0-100 semantic scale unified with the text pipeline. Historical rows still carry
the binary values, so an old ``1.0`` ("passed") would read as ``1/100`` on the
new scale — visually indistinguishable from near-garbage. This migration
rescales those rows ``×100`` so a passed podcast reads as ``100``, not ``1``.

Convergence / re-run safety: every pre-migration row is on the 0/1 scale, so
``quality_score <= 1.0`` selects exactly the un-rescaled rows. New-scale scores
are ``> 1`` (the evaluator writes ``100`` for a clean pass and 0-100 composites
otherwise; the only ``<= 1`` value it can emit is a genuine ``0.0`` hard-fail,
which ``* 100`` leaves at ``0.0``). So a second run is a no-op, and a row written
after this migration is never touched.

One-way backfill: ``down()`` is a deliberate no-op — post-migration a rescaled
old ``100`` and a natively-scored new ``100`` are indistinguishable, so dividing
back by 100 would corrupt legitimately-scored rows.

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE media_approvals
            SET quality_score = quality_score * 100
            WHERE quality_score IS NOT NULL AND quality_score <= 1.0
            """
        )
    logger.info(
        "rescale_media_approvals_quality_score_to_0_100 up: rescaled binary "
        "quality_score rows to 0-100 (%s); no-op where already on the new scale",
        result,
    )


async def down(pool) -> None:
    # One-way backfill: after the rescale, a rescaled old 100 and a natively
    # scored new 100 are indistinguishable, so dividing by 100 would corrupt
    # legitimately-scored rows. Same one-way posture as
    # 20260622_200222_drop_pipeline_tasks_category.
    logger.info(
        "rescale_media_approvals_quality_score_to_0_100 down: no-op (one-way "
        "backfill; cannot safely un-rescale)"
    )
