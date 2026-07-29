"""DispatchMediaPipelineJob — the Gate-1 → Stage-2 trigger (#689 Plan 7).

When a content piece clears **Gate 1** (``pipeline_tasks.status='approved'``)
or is directly auto-published (``status='published'`` — auto-publish can race
the 5-min cron and skip the ``approved`` state entirely) and has persisted
Stage-1 media scripts, this scheduled job kicks off a
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

**Render-infra health gate (2026-07-03).** Before claiming anything the job
probes wan-server + image-gen ``/health`` and a DNS canary
(``services/media_infra_health.py``); an unhealthy probe defers the whole
cycle with markers and re-dispatch counts untouched. If a run fails and the
*post-failure* probe is unhealthy (infra died mid-cycle), the piece is
un-claimed instead of left for the watchdog — an outage fast-fail must never
consume one of the task's bounded ``media_pipeline_redispatch_count``
attempts (six posts wedged permanently at the cap this way during the
wan/image-gen/DNS outage windows).

**Source-task scripts + media-scoped thread.** ``media.load_scripts`` loads the
persisted scripts by ``task_id`` from ``pipeline_versions``, so the media graph
runs with the *source* (approved) task's id. It runs under a distinct
``thread_id`` (``media-<task_id>``) so its LangGraph checkpoint never collides
with the source ``canonical_blog`` run's thread.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from plugins.job import JobResult
from services.media_infra_health import check_media_infra_health
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# Eligible = approved OR published (auto-publish races the 5-min cron),
# not yet media-dispatched, and carries a persisted Stage-1 podcast_script
# (the minimal artifact media.load_scripts needs; shot-lists are optional —
# the render nodes no-op gracefully when absent, per media_load_scripts._EMPTY).
_ELIGIBLE_SQL = """
    SELECT pt.task_id
      FROM pipeline_tasks pt
     WHERE pt.status IN ('approved', 'published')
       AND pt.media_pipeline_dispatched_at IS NULL
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
       SET media_pipeline_dispatched_at = NOW()
     WHERE task_id = $1
       AND media_pipeline_dispatched_at IS NULL
"""

# Revert a claim WE made this cycle after an unhealthy-infra fast-fail. The
# marker goes back to NULL so the next healthy cycle re-claims the piece
# directly — no media_reconciliation re-dispatch (and no
# media_pipeline_redispatch_count burn) is needed to recover from an outage.
_UNCLAIM_SQL = """
    UPDATE pipeline_tasks
       SET media_pipeline_dispatched_at = NULL
     WHERE task_id = $1
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


async def _attempt_vram_reclaim(site_config: Any) -> None:
    """Bounded, best-effort VRAM reclaim before the gate re-probe (PR 2,
    2026-07-12 desktop-lockup fix): evict Ollama (~0.3 GB) + hard-unload
    image-gen (~7 GB — exits its process so the CUDA context actually
    returns to the host; ``torch.cuda.empty_cache()`` alone doesn't under
    WSL2). Docker's ``restart: unless-stopped`` brings image-gen back; it
    lazy-loads on the next ``/generate``.

    Both callees already catch their own exceptions (best-effort by design),
    so this never raises and never blocks the cycle — a reclaim that does
    nothing just means the re-probe still fails and the cycle defers as normal.
    """
    from services.gpu_scheduler import gpu

    await gpu._unload_ollama_models()
    await gpu._unload_image_gen(hard=True)


# Wall-clock of the last reclaim that ran and left the gate still unhealthy.
# Process-local on purpose: this is cooldown STATE, not config (the duration
# itself is an app_settings key), and the job is a single non-overlapping
# instance in one worker process, so a module global is the whole scope.
_last_ineffective_reclaim: float | None = None


def _reclaim_on_cooldown(site_config: Any, *, now: float) -> float:
    """Seconds left before another reclaim is worth attempting (0 = go ahead).

    "a reclaim that does nothing just means the cycle defers as normal" was
    true per-cycle and false in aggregate: on a 5-minute cron a reclaim that
    cannot help simply runs again forever. Observed 2026-07-27 — reclaim
    fired every cycle for 2+ hours, freed nothing each time, and each one
    killed the image-gen process, opening a cold-start window that downgraded
    article images to stock. The render never happened either way, so every
    one of those exits was pure loss.

    So: once a reclaim fails to make the gate healthy, sit out
    ``media_render_reclaim_cooldown_minutes`` before trying again. A reclaim
    that DOES work clears the marker, keeping the fast path fast.
    """
    if _last_ineffective_reclaim is None:
        return 0.0
    try:
        cooldown_min = float(
            site_config.get("media_render_reclaim_cooldown_minutes", "30") or "30"
        )
    except (TypeError, ValueError):
        cooldown_min = 30.0
    if cooldown_min <= 0:
        return 0.0
    elapsed = now - _last_ineffective_reclaim
    return max(0.0, cooldown_min * 60.0 - elapsed)


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

        # Health-gate the cycle (2026-07-03): a dispatch fired into a
        # wan/image-gen/DNS outage fast-fails, and the failure path burns one of
        # the task's bounded media_reconciliation re-dispatch attempts — six
        # posts wedged permanently at the cap this way. Probing only when
        # there is work keeps idle cycles free of HTTP round-trips. Deferring
        # leaves markers + counts untouched, so the piece is picked up intact
        # by the first healthy cycle.
        if rows:
            health = await check_media_infra_health(sc)
            # Reclaim (PR 2, 2026-07-12): only when the gate failed
            # SPECIFICALLY on VRAM — a wan/image-gen/DNS outage makes a
            # reclaim pointless (restarting image-gen mid-outage doesn't fix
            # a dead wan-server). Bounded to one attempt: reclaim, settle,
            # re-probe once, then fall through to the existing defer path
            # either way.
            if (
                not health.healthy
                and health.vram_insufficient
                and sc.get_bool("media_render_reclaim_enabled", True)
            ):
                global _last_ineffective_reclaim
                now = time.monotonic()
                cooling = _reclaim_on_cooldown(sc, now=now)
                if cooling > 0:
                    logger.info(
                        "[DISPATCH_MEDIA] render-GPU VRAM insufficient, but the "
                        "last reclaim did not help — skipping for another %.0f "
                        "min rather than restarting image-gen again: %s",
                        cooling / 60.0, health.detail,
                    )
                else:
                    logger.info(
                        "[DISPATCH_MEDIA] render-GPU VRAM insufficient — "
                        "attempting a bounded reclaim (evict Ollama + hard-"
                        "unload image-gen) before deferring: %s", health.detail,
                    )
                    await _attempt_vram_reclaim(sc)
                    settle = sc.get_float("media_render_reclaim_settle_seconds", 8.0) or 8.0
                    await asyncio.sleep(settle)
                    health = await check_media_infra_health(sc)
                    if health.healthy:
                        _last_ineffective_reclaim = None
                        logger.info(
                            "[DISPATCH_MEDIA] reclaim freed enough VRAM — "
                            "proceeding with dispatch this cycle"
                        )
                    else:
                        # Start the cooldown. Retrying this every 5 min costs an
                        # image-gen restart per cycle and buys nothing.
                        _last_ineffective_reclaim = now
                        emit_finding(
                            source="dispatch_media_pipeline",
                            kind="vram_reclaim_ineffective",
                            title="VRAM reclaim freed nothing — media render still blocked",
                            body=(
                                "Hard-unloaded image-gen to make room for a media "
                                "render and the VRAM gate still failed afterwards: "
                                f"{health.detail}. Further reclaims are on cooldown "
                                "for media_render_reclaim_cooldown_minutes. If this "
                                "repeats, something outside the pipeline is holding "
                                "render-GPU VRAM."
                            ),
                            severity="warn",
                            dedup_key="vram-reclaim-ineffective",
                        )
            if not health.healthy:
                logger.warning(
                    "[DISPATCH_MEDIA] render infra unhealthy — deferring %d "
                    "eligible piece(s): %s", len(rows), health.detail,
                )
                return JobResult(
                    ok=True,
                    detail=f"deferred — render infra unhealthy: {health.detail}",
                    changes_made=0,
                )

        dispatched = 0
        deferred_mid_cycle = False
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
                # Re-probe: if the infra died mid-cycle (or the pre-dispatch
                # probe raced a crash), this failure is an outage fast-fail,
                # not a bad piece. Un-claim so the next healthy cycle retries
                # it directly — without this, the marker stays set and the
                # watchdog must burn one of the task's bounded re-dispatch
                # attempts just to recover from downtime.
                post_health = await check_media_infra_health(sc)
                if not post_health.healthy:
                    try:
                        await pool.execute(_UNCLAIM_SQL, task_id)
                        logger.warning(
                            "[DISPATCH_MEDIA] infra unhealthy after failure "
                            "(%s) — un-claimed %s and deferring the rest of "
                            "the cycle", post_health.detail, task_id,
                        )
                    except Exception as unclaim_exc:  # noqa: BLE001
                        logger.warning(
                            "[DISPATCH_MEDIA] un-claim failed for %s: %s — "
                            "the reconciliation watchdog will re-dispatch it",
                            task_id, unclaim_exc,
                        )
                    deferred_mid_cycle = True
                    break
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

        if deferred_mid_cycle:
            detail = (
                f"dispatched {dispatched}, then deferred — infra went unhealthy"
            )
        elif dispatched:
            detail = f"dispatched {dispatched}"
        else:
            detail = "no eligible pieces"
        return JobResult(ok=True, detail=detail, changes_made=dispatched)


__all__ = ["DispatchMediaPipelineJob"]
