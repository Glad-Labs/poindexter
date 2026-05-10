"""``content_generation_flow`` — Prefect-orchestrated content pipeline.

Wraps the existing ``services.content_router_service.process_content_generation_task``
in a Prefect flow so dispatch / retry / heartbeat / stale-task sweep
move out of ``services/task_executor.py`` and into the Prefect server
already running at ``http://localhost:4200``.

The flow body itself is a thin call into the existing pipeline — Lane C
already moved orchestration to LangGraph + the ``canonical_blog``
template. This module only changes WHO calls the pipeline (a Prefect
deployment instead of a homegrown asyncio polling daemon) and WHEN
(a Prefect schedule instead of ``_process_loop``'s 5-second poll).

Phase-0 cutover seam (Glad-Labs/poindexter#410): the flow ships behind
``app_settings.use_prefect_orchestration`` (default ``'false'``).
``TaskExecutor`` checks the flag every poll cycle and short-circuits
when Prefect is active, so both daemons can run side-by-side during
the dual-write window without double-claiming tasks.
"""

from __future__ import annotations

import logging
from typing import Any

from prefect import flow, task
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
)
async def claim_pending_task(database_service: Any) -> dict[str, Any] | None:
    """Atomically claim one pending row.

    Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so concurrent flow runs
    don't grab the same task. Returns ``None`` when the queue is empty
    so the flow can exit cleanly without raising.

    Mirrors the claim shape from
    ``services.task_executor.TaskExecutor._process_loop`` so the
    side-by-side dual-write window observes consistent semantics.
    """
    pool = getattr(database_service, "pool", None)
    if pool is None:
        return None
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT task_id, topic, style, tone, target_length,
                       category, target_audience, niche_slug,
                       template_slug, primary_keyword, site_id,
                       stage_data
                FROM pipeline_tasks
                WHERE status = 'pending'
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

    if database_service is None:
        database_service = await _build_default_database_service()

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
    )
    return {"claimed": True, "task_id": task_id, "result": result}


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

    dsn = resolve_database_url()
    db = DatabaseService(local_database_url=dsn)
    await db.initialize()
    return db


__all__ = [
    "claim_pending_task",
    "content_generation_flow",
]
