"""MediaDistributeJob — Stage-2 link + Gate-2-seed pass (#689 Plan 8 / 8b-2).

The ``media_pipeline`` persists task-keyed ``media_assets`` rows (``video_long`` /
``video_short``) with ``post_id=NULL`` — at render time the ``posts`` row may not
exist yet (it's created at publish). This scheduled job is the bridge from that
task-keyed render output to the post-keyed Gate-2 distribution world:

1. **Link.** For each unlinked media_pipeline asset, resolve the post via the
   canonical seam ``posts.metadata->>'pipeline_task_id'`` and back-stamp
   ``media_assets.post_id``. Assets whose task hasn't been published yet are
   left for a later cycle.
2. **Seed Gate 2.** Record a ``media_approvals`` pending row so the asset
   surfaces in the operator's Gate-2 review queue — ``video`` for the long form,
   ``video_short`` for the short (the media_approvals media vocabulary; the
   matching media_assets *types* are ``video_long`` / ``video_short``).

The actual platform dispatch (YouTube long + Shorts) is the follow-up pass
(8b-2b) — it fires only once the operator approves the Gate-2 rows this job
seeds. Keeping link/seed separate from dispatch keeps each pass small and means
a rendered asset becomes *reviewable* the moment its post publishes, without
waiting on the dispatch lane.

**Default-OFF.** Gated on ``media_pipeline_trigger_enabled`` (the Stage-2 master
switch, default ``false``) so the job is scheduled but a behaviour no-op in prod
until the operator opts the whole lane in. ``idempotent=True``: re-linking is a
no-op (already-linked assets fall out of the query; ``record_pending`` is
``ON CONFLICT DO NOTHING``).
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.media_approval_service import record_pending

logger = logging.getLogger(__name__)

# media_assets.type → media_approvals.medium. The asset table distinguishes
# long/short by ``video_long`` / ``video_short``; the approvals table uses
# ``video`` for the long form (its historical medium name) and ``video_short``
# for the short. Anything not in this map is not a media_pipeline video asset.
_TYPE_TO_MEDIUM: dict[str, str] = {
    "video_long": "video",
    "video_short": "video_short",
}

# media_pipeline assets awaiting a post link: a rendered video with a task id
# but no post yet. Newest task first so a freshly-published piece links promptly.
_UNLINKED_SQL = """
    SELECT id::text AS id, task_id, type
      FROM media_assets
     WHERE post_id IS NULL
       AND task_id IS NOT NULL
       AND type = ANY($1::text[])
     ORDER BY created_at DESC
     LIMIT $2
"""

# Resolve the post that a Stage-1 task became, via the canonical seam stamped
# by publish_service.publish_post_from_task. NULL until the piece is published.
_RESOLVE_POST_SQL = """
    SELECT id::text
      FROM posts
     WHERE metadata->>'pipeline_task_id' = $1
     ORDER BY published_at DESC NULLS LAST
     LIMIT 1
"""

# Back-stamp the resolved post id onto the asset row (idempotent — the row drops
# out of _UNLINKED_SQL once post_id is set).
_LINK_SQL = "UPDATE media_assets SET post_id = $1::uuid, updated_at = NOW() WHERE id = $2::uuid"


def _max_per_cycle(site_config: Any) -> int:
    try:
        return max(1, int(site_config.get("media_distribute_max_per_cycle", "20") or "20"))
    except (TypeError, ValueError):
        return 20


class MediaDistributeJob:
    name = "media_distribute"
    description = (
        "Link media_pipeline-rendered assets to their published post and seed "
        "Gate-2 approval rows (dormant until media_pipeline_trigger_enabled)"
    )
    schedule = "every 10 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is None:
            return JobResult(ok=True, detail="no site_config — skipping", changes_made=0)

        # Stage-2 master switch — default OFF. Checked before any DB work.
        if not sc.get_bool("media_pipeline_trigger_enabled", False):
            return JobResult(
                ok=True,
                detail="media_pipeline_trigger_enabled=false — dormant",
                changes_made=0,
            )

        if pool is None:
            return JobResult(ok=True, detail="no pool — skipping", changes_made=0)

        limit = _max_per_cycle(sc)
        try:
            rows = await pool.fetch(_UNLINKED_SQL, list(_TYPE_TO_MEDIUM.keys()), limit)
        except Exception as exc:  # noqa: BLE001 — a query failure must not crash the scheduler
            logger.warning("[MEDIA_DISTRIBUTE] unlinked-asset query failed: %s", exc)
            return JobResult(ok=False, detail=f"query failed: {exc}", changes_made=0)

        linked = 0
        for row in rows or []:
            asset_id = row["id"]
            task_id = row["task_id"]
            medium = _TYPE_TO_MEDIUM.get(row["type"])
            if medium is None:
                continue

            try:
                post_id = await pool.fetchval(_RESOLVE_POST_SQL, str(task_id))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[MEDIA_DISTRIBUTE] post resolve failed for task %s: %s",
                    task_id, exc,
                )
                continue
            if not post_id:
                # Task not published yet — leave the asset unlinked for a later
                # cycle (the post is created at publish, which may lag approval).
                continue

            try:
                await pool.execute(_LINK_SQL, post_id, asset_id)
                await record_pending(pool, post_id, medium)
                linked += 1
                logger.info(
                    "[MEDIA_DISTRIBUTE] linked asset %s (%s) → post %s + seeded "
                    "Gate-2 %s",
                    asset_id, row["type"], post_id, medium,
                )
            except Exception as exc:  # noqa: BLE001 — one asset must not halt the pass
                logger.warning(
                    "[MEDIA_DISTRIBUTE] link/seed failed for asset %s → post %s: %s",
                    asset_id, post_id, exc,
                )

        detail = f"linked {linked}" if linked else "no assets to link"
        return JobResult(ok=True, detail=detail, changes_made=linked)


__all__ = ["MediaDistributeJob"]
