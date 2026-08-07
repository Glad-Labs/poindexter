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

**Bounded un-claim (poindexter#995).** That free re-claim gets its own ceiling,
``media_pipeline_unclaim_max`` (default 3), tracked on
``pipeline_tasks.media_pipeline_unclaim_count``. The post-failure probe cannot
distinguish "infra died independently" from "this render's own VRAM footprint
is why the probe fails" — wan holds 23-27 GB mid-render — so without a ceiling
a piece that fails *because of itself* is re-claimed for free forever. The
counter resets on any successful dispatch (and whenever ``media_reconciliation``
authorises a fresh attempt), so it caps consecutive self-inflicted retries
rather than imposing a lifetime quota. When it is spent the marker stays set,
a ``media_unclaim_budget_exhausted`` finding fires, and the bounded watchdog
owns retry — leaving the full escalation ladder finite at every rung.

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
#
# Bounded since poindexter#995. The un-claim is free of the RE-DISPATCH budget
# on purpose (an outage must never wedge a piece at that cap — six posts did
# exactly that before 2026-07-03), but "free" was also "unbounded", and the
# post-failure probe cannot tell "infra died independently" from "this render's
# own VRAM footprint is why the probe fails": wan holds 23-27 GB mid-render, so
# a self-inflicted failure leaves the card under media_render_min_free_vram_gb
# and reads as an outage. Task 8faf3617 rode that loop for weeks — three full
# ~15-min GPU renders a day, 40 findings in 24h, redispatch_count still 0.
#
# So the path keeps its own separate budget. A 0-row result means the budget is
# spent: the marker stays set, the piece stops re-rendering, and the (bounded,
# self-healing) media_reconciliation watchdog owns it from there.
_UNCLAIM_SQL = """
    UPDATE pipeline_tasks
       SET media_pipeline_dispatched_at = NULL,
           media_pipeline_unclaim_count = media_pipeline_unclaim_count + 1
     WHERE task_id = $1
       AND media_pipeline_unclaim_count < $2
"""

# A piece that rendered cleanly owes nothing to the free-retry budget — reset it
# so a task that fails once a month never accumulates its way to the cap. This
# is what keeps the ceiling a loop-breaker rather than a lifetime quota.
_RESET_UNCLAIM_SQL = """
    UPDATE pipeline_tasks
       SET media_pipeline_unclaim_count = 0
     WHERE task_id = $1
       AND media_pipeline_unclaim_count > 0
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
    WSL2) + unload the chatterbox TTS model + hard-unload the wan-server.
    Docker's ``restart: unless-stopped`` brings the hard-unloaded servers
    back; all lazy-load on their next request.

    Chatterbox was added 2026-07-29 (Glad-Labs/poindexter#940): it caches its
    model after narrating an episode and is not a GPU-lock owner, so it sat
    outside every reclaim lever and squatted through the following video
    render — observed deferring on "free VRAM 24.0 GB < 25 GB required". A
    soft unload suffices; its VRAM is the model, not a wedged CUDA context.

    wan was added 2026-08-01 (Glad-Labs/poindexter#962): after a successful
    render evening its idle unloader drops the pipeline objects but the
    process keeps ~10 GB of CUDA reserved pool, which no lever here could
    touch — the gate then deferred every render overnight ("free VRAM
    18.6 GB < 25 GB required") until a human restarted the container. Its
    hard unload declines (``nothing_to_reclaim``) when the pool is below the
    ``WAN_HARD_UNLOAD_MIN_RESERVED_MB`` floor, so repeat reclaims are cheap.

    stable-audio was added 2026-08-07 (Glad-Labs/poindexter#999) — the same
    defect a third time, and the most expensive because this service had no
    hard-unload contract AND no seat here, so nothing could reach it.
    Measured: **10,952 MiB held on the render GPU with ``model_loaded:
    false``**; soft ``/unload`` freed **3 MiB**, a process restart freed
    **10.96 GiB**. wan peaks at 25.4 GiB on a 31.8 GiB card, so that ghost by
    itself made every hero render arithmetically impossible — and it is why
    ``vram_reclaim_ineffective`` kept firing while this ladder dutifully
    evicted four services that between them held almost nothing.

    Each lever is isolated: the callees are best-effort and catch internally,
    but that made "never raises" an incidental property of their current
    implementations rather than a guarantee of this one. An exception
    escaping an early lever must not skip the later ones — a stray error in
    the Ollama evict would otherwise silently cost us the wan lever this
    reclaim was extended to gain. Isolating here keeps that contract true no
    matter how the callees evolve.
    """
    from services.gpu_scheduler import gpu

    # Callables, NOT pre-built coroutines: building all four up front would
    # leave the un-awaited ones raising "coroutine was never awaited" the
    # moment anyone adds a break/continue to this loop.
    levers: tuple[tuple[str, Any], ...] = (
        ("ollama", gpu._unload_ollama_models),
        ("image-gen", lambda: gpu._unload_image_gen(hard=True)),
        ("chatterbox", gpu._unload_chatterbox),
        ("wan", lambda: gpu._unload_wan(hard=True)),
        ("stable-audio", lambda: gpu._unload_stable_audio(hard=True)),
    )
    for name, call in levers:
        try:
            await call()
        except Exception as exc:  # noqa: BLE001 — best-effort by contract
            logger.warning(
                "[DISPATCH_MEDIA] VRAM reclaim lever %r failed (continuing "
                "with the rest): %s: %s",
                name, type(exc).__name__, exc,
            )


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


def _unclaim_max(site_config: Any) -> int:
    """Free outage-retry budget per task (default 3, 0 disables the un-claim).

    Floored at 0, not 1: an operator setting this to 0 means "never un-claim",
    which is a coherent choice (leave every failure to the bounded watchdog),
    so it must not be silently promoted to 1.
    """
    try:
        return max(0, int(site_config.get("media_pipeline_unclaim_max", "3") or "3"))
    except (TypeError, ValueError):
        return 3


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
                        "unload image-gen + unload chatterbox + hard-unload "
                        "wan) before deferring: %s", health.detail,
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
                # Clean run — refund the free-retry budget (poindexter#995).
                # Best-effort: a bookkeeping failure must not turn a successful
                # render into a job failure.
                try:
                    await pool.execute(_RESET_UNCLAIM_SQL, task_id)
                except Exception as reset_exc:  # noqa: BLE001
                    logger.warning(
                        "[DISPATCH_MEDIA] un-claim budget reset failed for %s: "
                        "%s — the render itself succeeded",
                        task_id, reset_exc,
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
                    budget = _unclaim_max(sc)
                    try:
                        unclaimed = str(
                            await pool.execute(_UNCLAIM_SQL, task_id, budget)
                        ).strip().endswith(" 1")
                    except Exception as unclaim_exc:  # noqa: BLE001
                        unclaimed = False
                        logger.warning(
                            "[DISPATCH_MEDIA] un-claim failed for %s: %s — "
                            "the reconciliation watchdog will re-dispatch it",
                            task_id, unclaim_exc,
                        )
                    else:
                        if unclaimed:
                            logger.warning(
                                "[DISPATCH_MEDIA] infra unhealthy after failure "
                                "(%s) — un-claimed %s and deferring the rest of "
                                "the cycle", post_health.detail, task_id,
                            )
                        else:
                            # Budget spent (or disabled). The marker stays set,
                            # so this piece stops re-rendering — surface it, or
                            # "stopped looping" looks exactly like "silently
                            # wedged" (poindexter#995).
                            logger.warning(
                                "[DISPATCH_MEDIA] %s exhausted its un-claim "
                                "budget (%d) — leaving the marker set; the "
                                "reconciliation watchdog owns retry now",
                                task_id, budget,
                            )
                            emit_finding(
                                source="dispatch_media_pipeline",
                                kind="media_unclaim_budget_exhausted",
                                title=(
                                    f"media task {task_id} stopped free-retrying "
                                    f"(un-claim budget {budget} spent)"
                                ),
                                body=(
                                    f"The Stage-2 media_pipeline run for task "
                                    f"{task_id} failed again and the post-failure "
                                    f"infra probe was unhealthy ({post_health.detail}), "
                                    f"but its {budget} free outage retries are spent. "
                                    "The dispatch marker stays set, so the piece no "
                                    "longer re-renders every cycle. If the infra "
                                    "problem is real it will clear on its own; if the "
                                    "piece itself is bad it now needs operator triage "
                                    "— a render that only fails on ITS OWN VRAM "
                                    "footprint looks identical to an outage from here. "
                                    "media_reconciliation still owns bounded re-dispatch "
                                    "while the post is inside the regen window."
                                ),
                                severity="warn",
                                dedup_key=f"media_unclaim_budget_exhausted:{task_id}",
                                extra={
                                    "task_id": str(task_id),
                                    "unclaim_max": budget,
                                    "infra_detail": post_health.detail,
                                },
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
