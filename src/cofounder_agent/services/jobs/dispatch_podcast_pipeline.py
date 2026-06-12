"""DispatchPodcastPipelineJob — the Stage-3 podcast trigger (#689 deviation).

The podcast twin of ``dispatch_media_pipeline``. When a content piece clears
Gate 1 (``pipeline_tasks.status='approved'``) or is auto-published
(``status='published'``) and has a persisted Stage-1 ``podcast_script``, this
scheduled job runs the isolated ``podcast_pipeline`` graph to render + persist
the podcast episode — fully independent of the video ``media_pipeline`` so a
video-render failure can never block podcast production.

**Per-medium claim marker.** Eligibility + the claim-before-run guard ride a
dedicated ``pipeline_tasks.podcast_dispatched_at`` column (NOT the shared video
marker), so podcast and video dispatch, fail, and re-dispatch independently.
Re-render = clear that one column (``media_reconciliation`` watchdog).

**Gated** on ``podcast_pipeline_trigger_enabled`` (default off) — a behaviour
no-op until the operator opts the Stage-3 lane in.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# Eligible = approved OR published (auto-publish races the cron), not yet
# podcast-dispatched, carrying a persisted Stage-1 podcast_script.
_ELIGIBLE_SQL = """
    SELECT pt.task_id
      FROM pipeline_tasks pt
     WHERE pt.status IN ('approved', 'published')
       AND pt.podcast_dispatched_at IS NULL
       AND EXISTS (
           SELECT 1
             FROM pipeline_versions pv
            WHERE pv.task_id = pt.task_id
              AND pv.stage_data -> 'task_metadata' ->> 'podcast_script' IS NOT NULL
              AND pv.stage_data -> 'task_metadata' ->> 'podcast_script' != ''
       )
     ORDER BY pt.updated_at ASC
     LIMIT $1
"""

# Conditional claim — only one worker wins. Affects 0 rows if already claimed.
_CLAIM_SQL = """
    UPDATE pipeline_tasks
       SET podcast_dispatched_at = NOW()
     WHERE task_id = $1
       AND podcast_dispatched_at IS NULL
"""


class _PoolDS:
    """Minimal ``database_service`` shim — atoms read ``.pool`` off it."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool


async def _run_podcast_pipeline(pool: Any, site_config: Any, task_id: str) -> None:
    """Run the ``podcast_pipeline`` graph for one source task under a
    podcast-scoped ``thread_id`` so its checkpoint never collides with the
    source ``canonical_blog`` or the video ``media_pipeline`` run."""
    from services.template_runner import TemplateRunner

    runner = TemplateRunner(pool, site_config=site_config)
    await runner.run(
        "podcast_pipeline",
        {
            "task_id": task_id,
            "site_config": site_config,
            "database_service": _PoolDS(pool),
            "pool": pool,
        },
        thread_id=f"podcast-{task_id}",
    )


def _max_per_cycle(site_config: Any) -> int:
    """Cap on podcast renders kicked off per cycle (default 2 — lighter than video)."""
    try:
        return max(1, int(site_config.get("podcast_pipeline_max_per_cycle", "2") or "2"))
    except (TypeError, ValueError):
        return 2


class DispatchPodcastPipelineJob:
    name = "dispatch_podcast_pipeline"
    description = (
        "Stage-3 trigger: run podcast_pipeline for Gate-1-approved pieces that "
        "have a persisted podcast_script but no podcast yet (gated on "
        "podcast_pipeline_trigger_enabled, default off)"
    )
    schedule = "every 5 minutes"
    # GPU/CPU-bound TTS render — never let two instances overlap.
    idempotent = False

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is None:
            return JobResult(ok=True, detail="no site_config — skipping", changes_made=0)

        if not sc.get_bool("podcast_pipeline_trigger_enabled", False):
            return JobResult(
                ok=True,
                detail="podcast_pipeline_trigger_enabled=false — dormant",
                changes_made=0,
            )

        if pool is None:
            return JobResult(ok=True, detail="no pool — skipping", changes_made=0)

        limit = _max_per_cycle(sc)
        try:
            rows = await pool.fetch(_ELIGIBLE_SQL, limit)
        except Exception as exc:  # noqa: BLE001 — a query failure must not crash the scheduler
            logger.warning("[DISPATCH_PODCAST] eligible-task query failed: %s", exc)
            return JobResult(ok=False, detail=f"query failed: {exc}", changes_made=0)

        dispatched = 0
        for row in rows or []:
            task_id = row["task_id"]
            try:
                claim = await pool.execute(_CLAIM_SQL, task_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[DISPATCH_PODCAST] claim failed for %s: %s", task_id, exc)
                continue
            if not str(claim).strip().endswith(" 1"):
                continue

            try:
                await _run_podcast_pipeline(pool, sc, str(task_id))
                dispatched += 1
                logger.info("[DISPATCH_PODCAST] podcast_pipeline dispatched for task %s", task_id)
            except Exception as exc:  # noqa: BLE001 — one failure must not halt the job
                logger.warning("[DISPATCH_PODCAST] podcast_pipeline run failed for %s: %s", task_id, exc)
                emit_finding(
                    source="dispatch_podcast_pipeline",
                    kind="podcast_dispatch_failed",
                    title=f"podcast_pipeline run failed for task {task_id}",
                    body=(
                        f"The Stage-3 podcast_pipeline run raised for task {task_id}: "
                        f"{exc}. The piece is marked dispatched; the "
                        "media_reconciliation watchdog re-dispatches."
                    ),
                    severity="warn",
                    dedup_key=f"podcast_dispatch_failed:{task_id}",
                    extra={"task_id": str(task_id), "error": str(exc)},
                )

        detail = f"dispatched {dispatched}" if dispatched else "no eligible pieces"
        return JobResult(ok=True, detail=detail, changes_made=dispatched)


__all__ = ["DispatchPodcastPipelineJob"]
