"""Migration 20260703_192329: add media_pipeline_cap_reset_at + re-arm outage-wedged video tasks

Two related changes for the Stage-2 render-infra health-gate work
(glad-labs-stack, 2026-07-03):

1. **Schema**: add ``pipeline_tasks.media_pipeline_cap_reset_at timestamptz``
   — the per-task cooldown stamp for the bounded re-dispatch-cap reset in
   ``services/jobs/media_reconciliation.py`` (``_CAP_RESET_SQL``). When a
   missing-video task is wedged at ``media_pipeline_redispatch_max`` but the
   render infra probes healthy again, the watchdog resets the counter — at
   most once per ``media_redispatch_cap_reset_cooldown_hours`` per task,
   enforced against this column.

2. **One-off data fix**: re-arm the six published posts whose tasks hit the
   re-dispatch cap purely because their dispatches fired during the
   wan-server / image-gen / DNS outage windows (each fast-fail burned one of
   the three bounded attempts; render servers verified healthy 2026-07-02).
   Resets ``media_pipeline_redispatch_count`` to 0 and clears
   ``media_pipeline_dispatched_at`` so ``dispatch_media_pipeline`` — now
   health-gated — re-claims them directly. Resolved via the canonical
   ``posts.metadata->>'pipeline_task_id'`` seam; a no-op on fresh installs
   (no matching posts) and on installs where the tasks were already re-armed.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Published posts with no video whose source tasks were wedged at
# media_pipeline_redispatch_max by outage-window fast-fails. Post ids are the
# public ``posts.id`` UUIDs (they already appear in public media URLs).
_WEDGED_POST_IDS: tuple[str, ...] = (
    "954d4e18-5ca3-4560-8b55-7a1e39882d95",
    "511b64be-a7e2-4c6f-b76f-4ddc48773223",
    "85537936-a41f-40ec-8065-e2e9414cd570",
    "4fca6592-ed79-4365-b86e-ed5e954afcef",
    "c2bd5a25-cd6e-4acb-b005-af7744aaba21",
    "8af685ce-7f55-41f2-8cf1-629ca75965a5",
)

_RE_ARM_SQL = """
    UPDATE pipeline_tasks pt
       SET media_pipeline_redispatch_count = 0,
           media_pipeline_dispatched_at = NULL
      FROM posts p
     WHERE p.metadata->>'pipeline_task_id' = pt.task_id
       AND p.id = ANY($1::uuid[])
       AND pt.media_pipeline_redispatch_count > 0
"""


async def up(pool) -> None:
    """Apply — add the cooldown column, then re-arm the six wedged tasks.

    Idempotent: ``ADD COLUMN IF NOT EXISTS`` no-ops on re-run; the re-arm
    UPDATE matches only rows still carrying a burned count (0 rows on fresh
    installs and on already-healed prods).
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks "
            "ADD COLUMN IF NOT EXISTS media_pipeline_cap_reset_at timestamptz"
        )
        result = await conn.execute(_RE_ARM_SQL, list(_WEDGED_POST_IDS))
        logger.info(
            "Migration add_media_pipeline_cap_reset_at: applied (re-armed %s "
            "outage-wedged video tasks)", str(result).replace("UPDATE ", ""),
        )


async def down(pool) -> None:
    """Revert — drop the column added by ``up()``.

    The one-off re-arm is one-way by design: restoring burned re-dispatch
    counts would just re-wedge tasks the fix exists to free, so ``down()``
    deliberately leaves the counters alone.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS media_pipeline_cap_reset_at"
        )
    logger.info("Migration add_media_pipeline_cap_reset_at: reverted")
