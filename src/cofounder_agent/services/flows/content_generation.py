"""``content_generation_flow`` — Prefect-orchestrated content pipeline.

Wraps the existing ``services.content_router_service.process_content_generation_task``
in a Prefect flow so dispatch / retry / heartbeat / stale-task sweep
run on the Prefect server at ``http://localhost:4200`` instead of a
homegrown asyncio polling daemon.

The flow body itself is a thin call into the existing pipeline — Lane C
already moved orchestration to LangGraph + the ``canonical_blog``
template. This module only changes WHO calls the pipeline (a Prefect
deployment) and WHEN (a Prefect schedule instead of a 5-second poll).

Cutover history (Glad-Labs/poindexter#410):

- Phase 0 (2026-05-10): flow shipped behind
  ``app_settings.use_prefect_orchestration`` (default ``'false'``);
  the legacy ``TaskExecutor`` short-circuited when the flag was true.
- Stage 3 (2026-05-13): default flipped to ``'true'`` for fresh installs.
- Stage 4 (2026-05-16): ``services/task_executor.py`` deleted entirely.
  This module is now the only path through which the content pipeline
  is dispatched.
"""

from __future__ import annotations

import logging
from typing import Any

from prefect import flow, task
from prefect.cache_policies import NO_CACHE
from prefect.context import get_run_context

logger = logging.getLogger(__name__)


@task(
    name="claim_pending_task",
    description=(
        "Claim a single ``status='pending'`` row from ``pipeline_tasks`` "
        "and transition it to ``status='in_progress'``. Returns the "
        "claimed row dict, or None when the queue is empty."
    ),
    retries=1,
    retry_delay_seconds=10,
    # Disable Prefect's task-result caching: the ``database_service``
    # argument wraps an asyncpg pool whose internals are cython-
    # extension objects that the cache layer can't serialise.
    # Caching a CLAIM operation would also be a correctness bug —
    # every call MUST hit the DB so concurrent flow runs see the
    # FOR UPDATE SKIP LOCKED dance, not a cached "you already won."
    cache_policy=NO_CACHE,
)
async def claim_pending_task(database_service: Any) -> dict[str, Any] | None:
    """Atomically claim one pending row.

    Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so concurrent flow runs
    don't grab the same task. Returns ``None`` when the queue is empty
    so the flow can exit cleanly without raising.

    The claim shape was originally mirrored from the legacy
    ``TaskExecutor._process_loop`` so the dual-write cutover window
    observed consistent semantics; that path was retired in
    Glad-Labs/poindexter#410 Stage 4 (2026-05-16).
    """
    pool = getattr(database_service, "pool", None)
    if pool is None:
        return None
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Column list mirrors what the flow body actually consumes
            # below. The earlier draft listed a ``stage_data`` column
            # that doesn't exist on this table — see the post-mortem
            # in #410: the unit tests covered the dict-consumer shape
            # but mocked ``fetchrow`` instead of running the real SQL.
            # Claim fresh ``pending`` work AND operator ``rejected_retry``
            # tasks (#541): `tasks reject --retry` sets rejected_retry meaning
            # "regenerate", but nothing transitioned it back to pending, so
            # those tasks were stranded and never re-ran. ``rejected_final``
            # stays terminal (excluded). The audit_log rejection event keeps
            # the learning signal that distinguishes a retry from fresh work.
            row = await conn.fetchrow(
                """
                SELECT task_id, topic, style, tone, target_length,
                       category, target_audience, niche_slug,
                       template_slug, primary_keyword, site_id
                FROM pipeline_tasks
                WHERE status IN ('pending', 'rejected_retry')
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            if row is None:
                return None
            await conn.execute(
                "UPDATE pipeline_tasks SET status = 'in_progress', "
                "updated_at = NOW() WHERE task_id = $1",
                row["task_id"],
            )
    return dict(row)


@flow(
    name="content_generation",
    description=(
        "Drive one content task through the full pipeline. Wraps "
        "``content_router_service.process_content_generation_task``; "
        "retries on transient pipeline errors, heartbeats via Prefect's "
        "run-state machine, surfaces in the Prefect UI at :4200."
    ),
    retries=2,
    retry_delay_seconds=60,
    log_prints=True,
)
async def content_generation_flow(
    *,
    task_id: str | None = None,
    topic: str | None = None,
    style: str = "technical",
    tone: str = "professional",
    target_length: int = 1500,
    tags: list[str] | None = None,
    generate_featured_image: bool = True,
    category: str | None = None,
    target_audience: str | None = None,
    database_service: Any | None = None,
) -> dict[str, Any]:
    """Prefect-flow wrapper over the content-generation pipeline.

    Two ways to call:

    1. **Schedule-driven** — the deployment runs the flow with no args
       on its cron schedule. The flow calls ``claim_pending_task``,
       and if a row was claimed, runs the pipeline against it. When
       the queue is empty, returns ``{"claimed": False}`` and the
       run completes cleanly. Prefect's deployment + work-pool
       handles the polling cadence; the flow itself doesn't loop.

    2. **Operator-triggered** — pass ``task_id`` / ``topic`` etc.
       directly to run a specific task on demand (parity with
       calling ``process_content_generation_task`` from a CLI or
       FastAPI endpoint).

    Database connection: when ``database_service`` is None (the
    schedule-driven case), the flow constructs a fresh
    ``DatabaseService`` from the bootstrap-resolved DSN. Operator-
    triggered calls can inject their own pool to share connections
    with the calling context.
    """
    # Lazy-import to keep flow-module import cheap (Prefect imports
    # the module to register flows during deployment-time discovery
    # but doesn't need the heavy database/services tree).
    from services.content_router_service import process_content_generation_task
    from services.di_wiring import build_and_wire_subprocess_with_container

    if database_service is None:
        database_service = await _build_default_database_service()

    # poindexter#477: Prefect spawns this flow inside a fresh Python
    # subprocess that never runs ``main.py``'s lifespan. Without the
    # explicit wire here, every wired module's ``site_config`` stays
    # the empty default — ``site_config.get("preferred_ollama_model")``
    # returns ``""``, the ``auto`` resolver in ollama_client falls
    # through to "pick largest installed model by file size", and the
    # 70-150B parameter local models that ship for testing get loaded
    # into 32 GB VRAM + 63 GB host RAM and thrash the system.
    # ``build_and_wire_subprocess_with_container`` (DI migration PR 2,
    # design doc 2026-05-28-site-config-di-migration.md) loads
    # SiteConfig from the database_service's pool via
    # ``services.bootstrap.build_container``, rebinds the same instance
    # across every wired module, and returns the AppContainer so
    # downstream migrated services can construct through it.
    _wired_site_config: Any = None
    _app_container: Any = None
    _pool = getattr(database_service, "pool", None)
    if _pool is not None:
        _wired_site_config, _app_container = (
            await build_and_wire_subprocess_with_container(_pool)
        )
    else:
        logger.warning(
            "[CONTENT_FLOW] database_service has no .pool — skipping "
            "subprocess SiteConfig wiring. Stage code that relies on "
            "site_config will fall through to env defaults.",
        )

    # poindexter Phase-5 stress-test finding #6: this prefect-worker
    # subprocess never runs main.py's lifespan, so neither the OTel
    # tracer provider nor the Langfuse LiteLLM callback gets wired.
    # Result: every pipeline stage was invisible to Tempo (only the
    # FastAPI worker's /health + /metrics spans appeared) and zero
    # LLM calls landed in Langfuse despite the env vars being set.
    # Wire both here (idempotent — telemetry.py uses
    # trace.set_tracer_provider with RuntimeError-swallow on re-set,
    # configure_langfuse_callback has its own module-level guard).
    if _wired_site_config is not None:
        try:
            from services.telemetry import setup_telemetry as _setup_telemetry
            _setup_telemetry(
                app=None,
                site_config=_wired_site_config,
                service_name="cofounder-agent-prefect",
            )
        except Exception:  # noqa: BLE001 — telemetry must never block work
            logger.warning(
                "[CONTENT_FLOW] OpenTelemetry setup failed — "
                "pipeline spans will not export to Tempo this run",
                exc_info=True,
            )
        try:
            from services.llm_providers.litellm_provider import (
                configure_langfuse_callback,
            )
            await configure_langfuse_callback(_wired_site_config)
        except Exception:  # noqa: BLE001 — same fail-soft
            logger.warning(
                "[CONTENT_FLOW] Langfuse callback registration failed — "
                "LLM traces will not land in Langfuse this run",
                exc_info=True,
            )

    # Seam 1 Wave 3c (Glad-Labs/poindexter#667) — build content's
    # capability-scoped Platform handle for THIS subprocess. The handle bound
    # in main.py's lifespan (Wave 3b) lives in the FastAPI worker, not here, so
    # the pipeline rebuilds its own — exactly as it rebuilt site_config above.
    # Best-effort: ``None`` when deps are missing (the migrated audit sites then
    # quietly drop their telemetry, never breaking generation).
    _platform: Any = None
    if _wired_site_config is not None and _pool is not None:
        from services.di_wiring import build_platform_for_subprocess
        _platform = build_platform_for_subprocess(_pool, _wired_site_config)

    # Schedule-driven: no task_id → claim from queue.
    if task_id is None and topic is None:
        claimed = await claim_pending_task(database_service)
        if claimed is None:
            logger.info("[CONTENT_FLOW] queue empty — flow run exits cleanly")
            return {"claimed": False, "task_id": None}
        task_id = claimed["task_id"]
        topic = claimed["topic"]
        style = claimed.get("style") or style
        tone = claimed.get("tone") or tone
        target_length = int(claimed.get("target_length") or target_length)
        category = claimed.get("category")
        target_audience = claimed.get("target_audience")

    # Surface trace_id correlation. Prefect run_id is the canonical
    # identifier; pipeline tasks log their own task_id; stitching
    # them in audit_log lets operators jump from Prefect UI → audit
    # trail in Grafana. ``getattr`` because ``get_run_context`` returns
    # a FlowRunContext or TaskRunContext depending on call site, and
    # only the flow context exposes the flow_run handle.
    prefect_run_id: str | None = None
    try:
        run_ctx = get_run_context()
        flow_run = getattr(run_ctx, "flow_run", None)
        if flow_run is not None:
            prefect_run_id = str(flow_run.id)
    except Exception:
        pass
    if prefect_run_id:
        logger.info(
            "[CONTENT_FLOW] task_id=%s prefect_run_id=%s",
            task_id, prefect_run_id,
        )

    if not topic:
        raise ValueError("content_generation_flow requires a topic")

    # Cycle-5 #253: wrap the pipeline call so a crash here doesn't
    # strand the claimed row in ``status='in_progress'`` forever.
    # Without this, an unhandled exception inside
    # ``process_content_generation_task`` propagates straight to
    # Prefect's retry machinery, which leaves the task in_progress
    # while the flow run gets marked failed in the Prefect UI — the
    # operator sees the flow failure but the next sweep is the only
    # thing that can recover the task (and the sweep itself was
    # broken pre-#253, so in practice rows stayed in_progress until
    # manual intervention).
    #
    # We mark the task ``failed`` first, then re-raise so:
    # 1. Prefect's @flow(retries=2) machinery still runs (transient
    #    errors get retried — schedule-driven retry will claim the
    #    next pending task, not this one, since it's no longer
    #    pending; operator-triggered retry can be observed in the
    #    Prefect UI as a separate failure event).
    # 2. The Prefect UI / Grafana flow-run dashboard still records
    #    the failure for observability.
    # 3. The pipeline_tasks row reflects the real terminal state,
    #    so the brain daemon / approval queue / cost dashboards
    #    don't show it as still-running.
    try:
        result = await process_content_generation_task(
            topic=topic,
            style=style,
            tone=tone,
            target_length=target_length,
            tags=tags,
            generate_featured_image=generate_featured_image,
            database_service=database_service,
            task_id=task_id,
            category=category,
            target_audience=target_audience,
            # #272 Phase-2f: site_config is now a required kwarg. Thread
            # the subprocess-wired instance from
            # build_and_wire_subprocess_with_container above (None only in
            # the degenerate no-pool branch, which already logged a
            # warning and is a real misconfiguration we surface loudly).
            site_config=_wired_site_config,
            # Seam 1 Wave 3c: content's scoped handle for this run (None-tolerant
            # — best-effort audit telemetry only).
            platform=_platform,
        )
    except Exception as exc:
        await _mark_task_failed_on_flow_crash(
            database_service=database_service,
            task_id=task_id,
            error=exc,
        )
        # Re-raise so Prefect knows the flow failed and the operator
        # sees it in the UI. The task row is already correctly marked
        # failed in the DB — Prefect's retry won't re-pick this task
        # (it's no longer status='pending').
        raise

    # Glad-Labs/poindexter#478: post-pipeline-success side-effects
    # (webhook + auto-curator + auto-publish + operator notification)
    # delegate to ``services.post_pipeline_actions.run_post_pipeline_actions``.
    # The original inline block lived in ``task_executor._process_loop``;
    # that file was deleted in Stage 4 (poindexter#410) so this is now
    # the sole driver. Failure-path webhooks fire via the existing
    # FailedTaskHandler stage where it matters; we only run the helper
    # on successful pipeline completions (status != 'failed').
    try:
        final_status = (
            result.get("status", "awaiting_approval")
            if isinstance(result, dict)
            else "awaiting_approval"
        )
        if final_status != "failed":
            from services.post_pipeline_actions import run_post_pipeline_actions

            await run_post_pipeline_actions(
                database_service=database_service,
                task_id=task_id,
                topic=topic,
                result=result if isinstance(result, dict) else None,
                site_config=_wired_site_config,
            )
    except Exception:
        logger.warning(
            "[CONTENT_FLOW] post-pipeline actions raised for task %s — "
            "the pipeline result is still committed, but downstream "
            "webhook/notification side-effects may not have fired",
            task_id, exc_info=True,
        )

    return {"claimed": True, "task_id": task_id, "result": result}


async def _mark_task_failed_on_flow_crash(
    *,
    database_service: Any,
    task_id: str | None,
    error: BaseException,
) -> None:
    """Mark a stranded ``in_progress`` task as ``failed`` after a flow crash.

    Cycle-5 #253: belt-and-braces companion to the rewritten
    ``sweep_stale_tasks``. The sweep is the safety net (catches flow
    runs that died ungracefully — OOM, container kill, prefect-worker
    crash); this helper is the eager-cleanup path for the case where
    the flow's own Python frame is still alive when the exception
    fires.

    Best-effort: catches its own exceptions and logs rather than
    re-raising. The caller's ``raise`` (which propagates the original
    pipeline error to Prefect) is what matters for observability — a
    failure to flip the DB row to 'failed' is a degraded but not
    catastrophic state, because the sweep will catch it after the
    stale threshold.

    Truncates the error message to 2KB to bound the column size and
    avoid logging an attacker-controlled message verbatim into the DB.
    """
    if task_id is None:
        return
    pool = getattr(database_service, "pool", None)
    if pool is None:
        logger.warning(
            "[CONTENT_FLOW] cannot mark task=%s failed — no DB pool on "
            "database_service; rely on sweep_stale_tasks to recover",
            task_id,
        )
        return
    error_message = f"flow crashed: {type(error).__name__}: {error!s}"[:2048]
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE pipeline_tasks
                SET status = 'failed',
                    error_message = $1,
                    updated_at = NOW()
                WHERE task_id = $2 AND status = 'in_progress'
                """,
                error_message,
                task_id,
            )
        logger.warning(
            "[CONTENT_FLOW] task=%s marked failed after flow crash: %s",
            task_id, error_message,
        )
    except Exception:  # noqa: BLE001 — best-effort cleanup
        logger.exception(
            "[CONTENT_FLOW] failed to mark task=%s failed after flow "
            "crash; the stale-sweep will retry recovery on next fire",
            task_id,
        )


async def _build_default_database_service() -> Any:
    """Construct a ``DatabaseService`` from the bootstrap-resolved DSN.

    Used when the flow runs schedule-driven (no caller-injected pool).
    The schedule-driven path is single-flow-per-run so opening a fresh
    DatabaseService per invocation is fine; Prefect's work-pool
    concurrency limits the parallelism.

    Matches the worker's ``utils.startup_manager`` init pattern —
    ``local_database_url=`` kwarg, then ``await initialize()`` to open
    the pool. The DSN comes from ``brain.bootstrap`` so behavior is
    identical regardless of whether the flow runs from inside a
    container, the host shell, or the Prefect worker pool.
    """
    from brain.bootstrap import resolve_database_url

    from services.database_service import DatabaseService
    from services.site_config import SiteConfig

    dsn = resolve_database_url()
    # #272 Phase-2g: DatabaseService takes a REQUIRED site_config. This path
    # opens the pool BEFORE the subprocess SiteConfig is loaded from it (the
    # flow body calls ``build_and_wire_subprocess_with_container`` on this
    # pool afterwards), so a fresh env-fallback SiteConfig() is correct here —
    # the pool-size reads in ``initialize()`` use defaults, matching the
    # empty module global this path resolved before.
    db = DatabaseService(local_database_url=dsn, site_config=SiteConfig())
    await db.initialize()
    return db


__all__ = [
    "claim_pending_task",
    "content_generation_flow",
    "_mark_task_failed_on_flow_crash",
]
