"""DispatchMediaPipelineJob — the Gate-1 → Stage-2 trigger (#689 Plan 7).

When a content piece clears **Gate 1** (``pipeline_tasks.status='approved'``)
and has persisted Stage-1 media scripts, this scheduled job kicks off a
``media_pipeline`` run that renders the long/short video + podcast from those
scripts (epic poindexter#689). It is the *primary* Stage-2 producer; the
``media_reconciliation`` watchdog (Plan 8 — the demoted backfill jobs) is the
safety net that re-enqueues failures.

**Default-OFF.** The job is registered + scheduled, but gated on
``media_pipeline_trigger_enabled`` (default ``false``) — so it is a behaviour
no-op in prod until the operator flips the flag. This keeps flipping
``media_pipeline`` from dormant to LIVE an explicit, reversible operator action.

**Idempotency — claim-before-run.** The job stamps
``pipeline_tasks.media_pipeline_dispatched_at = NOW()`` for a piece *before*
running ``media_pipeline``. The stamp is a conditional UPDATE
(``WHERE … media_pipeline_dispatched_at IS NULL``); if it affects 0 rows another
worker already claimed the piece, so this cycle skips it. This makes
re-dispatch impossible across concurrent cycles and worker restarts. On a
dispatch *failure* the marker stays set (no auto-retry here) — retries/backoff
are the Plan-8 reconciliation watchdog's job (#677).

**Source-task scripts + media-scoped thread.** ``media.load_scripts`` loads the
persisted scripts by ``task_id`` from ``pipeline_versions``, so the media graph
runs with the *source* (approved) task's id. It runs under a distinct
``thread_id`` (``media-<task_id>``) so its LangGraph checkpoint never collides
with the source ``canonical_blog`` run's thread.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# Eligible = approved at Gate 1, not yet media-dispatched, and carries a
# persisted director shot-list (only pieces that went through Stage-1 video
# shot-list generation — which is itself niche-gated upstream — qualify).
_ELIGIBLE_SQL = """
    SELECT pt.task_id
      FROM pipeline_tasks pt
     WHERE pt.status = 'approved'
       AND pt.media_pipeline_dispatched_at IS NULL
       AND EXISTS (
           SELECT 1
             FROM pipeline_versions pv
            WHERE pv.task_id = pt.task_id
              AND pv.stage_data -> 'task_metadata' -> 'video_shot_list' IS NOT NULL
       )
     ORDER BY pt.updated_at ASC
     LIMIT $1
"""

# Conditional claim — only one worker wins. Affects 0 rows if already claimed.
_CLAIM_SQL = """
    UPDATE pipeline_tasks
       SET media_pipeline_dispatched_at = NOW()
     WHERE task_id = $1
       AND media_pipeline_dispatched_at IS NULL
"""


class _PoolDS:
    """Minimal ``database_service`` shim — media atoms read ``.pool`` off it
    (``media.load_scripts`` / ``media.qa`` resolve the pool via
    ``getattr(database_service, 'pool', None)``)."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool


async def _run_media_pipeline(pool: Any, site_config: Any, task_id: str) -> None:
    """Run the ``media_pipeline`` graph for one source task.

    Loads scripts by the SOURCE ``task_id`` and checkpoints under a media-scoped
    ``thread_id`` so it never collides with the source ``canonical_blog`` run.
    Awaiting the run inline serialises media renders (the job is
    ``idempotent=False`` so the scheduler won't overlap instances).
    """
    from services.template_runner import TemplateRunner

    runner = TemplateRunner(pool, site_config=site_config)
    await runner.run(
        "media_pipeline",
        {
            "task_id": task_id,
            "site_config": site_config,
            "database_service": _PoolDS(pool),
            "pool": pool,
        },
        thread_id=f"media-{task_id}",
    )


def _max_per_cycle(site_config: Any) -> int:
    """GPU-bound cap on media renders kicked off per cycle (default 1)."""
    try:
        return max(1, int(site_config.get("media_pipeline_max_per_cycle", "1") or "1"))
    except (TypeError, ValueError):
        return 1


class DispatchMediaPipelineJob:
    name = "dispatch_media_pipeline"
    description = (
        "Stage-2 trigger: run media_pipeline for Gate-1-approved pieces that "
        "have persisted scripts but no media yet (gated on "
        "media_pipeline_trigger_enabled, default off)"
    )
    schedule = "every 5 minutes"
    # GPU-bound render that takes minutes — never let two instances overlap.
    idempotent = False

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is None:
            return JobResult(ok=True, detail="no site_config — skipping", changes_made=0)

        # Master feature flag — default OFF. The trigger stays dormant in prod
        # (a behaviour no-op) until the operator opts in. Checked first so a
        # disabled trigger costs one settings read, no DB query.
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
            rows = await pool.fetch(_ELIGIBLE_SQL, limit)
        except Exception as exc:  # noqa: BLE001 — a query failure must not crash the scheduler
            logger.warning("[DISPATCH_MEDIA] eligible-task query failed: %s", exc)
            return JobResult(ok=False, detail=f"query failed: {exc}", changes_made=0)

        dispatched = 0
        for row in rows or []:
            task_id = row["task_id"]
            # Claim-before-run: stamp the marker first. A 0-row update means a
            # concurrent cycle already claimed it — skip rather than double-run.
            try:
                claim = await pool.execute(_CLAIM_SQL, task_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[DISPATCH_MEDIA] claim failed for %s: %s", task_id, exc
                )
                continue
            if not str(claim).strip().endswith(" 1"):
                continue

            try:
                await _run_media_pipeline(pool, sc, str(task_id))
                dispatched += 1
                logger.info(
                    "[DISPATCH_MEDIA] media_pipeline dispatched for task %s",
                    task_id,
                )
            except Exception as exc:  # noqa: BLE001 — one failure must not halt the job
                logger.warning(
                    "[DISPATCH_MEDIA] media_pipeline run failed for %s: %s",
                    task_id, exc,
                )
                emit_finding(
                    source="dispatch_media_pipeline",
                    kind="media_dispatch_failed",
                    title=f"media_pipeline run failed for task {task_id}",
                    body=(
                        f"The Stage-2 media_pipeline run raised for approved task "
                        f"{task_id}: {exc}. The piece is marked dispatched; the "
                        "media_reconciliation watchdog (Plan 8) owns retry."
                    ),
                    severity="warn",
                    dedup_key=f"media_dispatch_failed:{task_id}",
                    extra={"task_id": str(task_id), "error": str(exc)},
                )

        detail = f"dispatched {dispatched}" if dispatched else "no eligible pieces"
        return JobResult(ok=True, detail=detail, changes_made=dispatched)


__all__ = ["DispatchMediaPipelineJob"]
