"""Unified Content Router Service — LangGraph TemplateRunner dispatcher.

What this file is
-----------------

The single public entry point :func:`process_content_generation_task` is
called by the Prefect flow (``services/flows/content_generation.py``) to
run one ``pipeline_tasks`` row through the content pipeline. It is a
thin dispatcher: it builds the shared pipeline context dict (image
service, settings, style tracker, site_config, models_by_phase,
experiment assignment) and hands it to
:class:`services.template_runner.TemplateRunner` keyed on the task's
``template_slug`` column.

History (why the file is now this small)
----------------------------------------

Until 2026-05-16 this module ALSO contained the legacy chunked
``StageRunner.run_all`` orchestration — five sequential calls to
``_runner.run_all([...])`` that drove the 12-stage pipeline in-process.
That path was the production default until the Lane C cutover
(``Glad-Labs/poindexter#355`` / ``#450``) shipped the
``canonical_blog`` LangGraph template and prod flipped
``app_settings.default_template_slug='canonical_blog'`` on 2026-05-10.

After 7+ clean days on TemplateRunner with zero ``template_slug IS
NULL`` tasks rolling through, the legacy chunked block was deleted in
the cleanup sweep (Stage 4 of the Lane C runbook in
``docs/architecture/langgraph-cutover.md``). What remains is the
shared-context construction + the TemplateRunner dispatch + the
post-run experiment outcome attribution. The 12-stage flow itself
lives in ``services/pipeline_templates/__init__.py:_CANONICAL_BLOG_ORDER``;
new stages go there, NOT here.

Dependencies
------------

Reads:
    - ``services.container.get_service("settings")`` — DI seam, may be None outside lifespan
    - ``services.image_style_rotation.ImageStyleTracker``
    - ``services.image_service.get_image_service``
    - ``services.site_config.site_config`` (per-module SiteConfig attr)
    - ``services.pipeline_experiment_hook`` (best-effort)
    - ``services.template_runner.TemplateRunner``
    - ``pipeline_tasks.template_slug`` (per-row, set at task creation)

Writes (via TemplateRunner → stages):
    - ``content_tasks`` (status, error_message, task_metadata) via the
      ``finalize_task`` stage and the failure branch below
    - ``audit_log`` — ``task_started`` here, plus per-stage events
      emitted from inside TemplateRunner / the stages themselves
    - ``webhook_events`` indirectly via ``emit_webhook_event`` on the
      failure path

Failure modes
-------------

- **Missing ``template_slug`` on the task row** — per
  ``feedback_no_silent_defaults`` we fail loud rather than running an
  implicit legacy path. ``tasks_db.add_task`` consults
  ``app_settings.default_template_slug`` at task creation, so a NULL
  here means either the setting was empty when the task was queued
  (stale config) or the row was inserted by a foreign writer that
  bypassed ``tasks_db``. Both deserve operator attention; we mark the
  task ``failed`` with a diagnostic ``error_message`` and return.
- **TemplateRunner raises** — caught, task marked ``failed``, partial
  context preserved in ``task_metadata`` so the operator can review
  whatever generated before the crash.

See also
--------

- ``docs/architecture/langgraph-cutover.md`` — Lane C runbook (this
  is the file that file's Stage 4 deletes from)
- ``docs/architecture/prefect-cutover.md`` — who calls this function
- ``services/template_runner.py`` — the LangGraph engine
- ``services/pipeline_templates/__init__.py`` — where new stages go
"""

from __future__ import annotations

from typing import Any

from services.logger_config import get_logger
from services.site_config import SiteConfig

from .audit_log import audit_log_bg
from .database_service import DatabaseService
from .image_service import get_image_service
from .webhook_delivery_service import emit_webhook_event

# Lifespan-bound SiteConfig; main.py wires this via set_site_config().
# Defaults to a fresh env-fallback instance until the lifespan setter
# fires. Tests can either patch this attribute directly or call
# ``set_site_config()`` for explicit wiring.
site_config: SiteConfig = SiteConfig()


def set_site_config(sc: SiteConfig) -> None:
    """Wire the lifespan-bound SiteConfig instance for this module."""
    global site_config
    site_config = sc


logger = get_logger(__name__)


async def _load_template_slug(database_service: DatabaseService, task_id: str) -> str | None:
    """Read ``pipeline_tasks.template_slug`` for ``task_id``.

    Returns the trimmed slug, or ``None`` if the row has no slug / the
    lookup fails. The caller treats ``None`` as a hard error — see the
    module docstring "Missing template_slug" note.
    """
    try:
        async with database_service.pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT template_slug FROM pipeline_tasks WHERE task_id = $1",
                str(task_id),
            )
    except Exception as exc:
        logger.warning(
            "[BG-TASK] template_slug lookup failed for task %s: %s",
            task_id, exc,
        )
        return None
    # Tight isinstance check — test fixtures bind ``db.pool`` as a
    # MagicMock that auto-generates AsyncMocks for attribute access,
    # so ``fetchval`` can return a truthy AsyncMock object rather than
    # a string. Without the isinstance gate, a non-string slug flows
    # into TemplateRunner.run and KeyErrors out.
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


async def process_content_generation_task(
    topic: str,
    style: str,
    tone: str,
    target_length: int,
    tags: list[str] | None = None,
    generate_featured_image: bool = True,
    database_service: DatabaseService | None = None,
    task_id: str | None = None,
    models_by_phase: dict[str, str] | None = None,
    quality_preference: str | None = None,
    category: str | None = None,
    target_audience: str | None = None,
) -> dict[str, Any]:
    """Dispatch one ``pipeline_tasks`` row through its LangGraph template.

    Builds the shared pipeline context (service handles + per-task
    inputs + experiment assignment) and hands it to
    :class:`services.template_runner.TemplateRunner` keyed on
    ``pipeline_tasks.template_slug``. The TemplateRunner drives the
    12-node ``canonical_blog`` graph (or whichever template the row
    declares) to completion; this function returns the final state
    dict.

    Per ``feedback_no_silent_defaults``: a row without a
    ``template_slug`` is a configuration bug, not a fallback case. We
    mark the task ``failed`` with a diagnostic message instead of
    silently running an undefined pipeline.
    """
    from uuid import uuid4

    if not task_id:
        task_id = str(uuid4())

    if not database_service:
        logger.error("DatabaseService not provided - cannot persist content")
        raise ValueError("DatabaseService is required for content_tasks persistence")

    logger.info("=" * 80)
    logger.info("CONTENT GENERATION PIPELINE")
    logger.info("=" * 80)
    logger.info("   Task ID: %s", task_id)
    logger.info("   Topic: %s", topic)
    logger.info("   Style: %s | Tone: %s", style, tone)
    logger.info("   Target Length: %s words", target_length)
    logger.info("   Tags: %s", ', '.join(tags) if tags else 'none')
    logger.info("   Image Search: %s", generate_featured_image)
    logger.info("=" * 80)

    # Build the shared pipeline context.
    #
    # TemplateRunner extracts service handles from this dict via
    # ``_KNOWN_SERVICE_KEYS`` ({database_service, image_service,
    # settings_service, image_style_tracker, site_config}). Stages
    # read inputs (topic / style / tone / target_length / tags /
    # generate_featured_image / models_by_phase / category /
    # target_audience) and accumulate outputs (content, quality_result,
    # featured_image_url, seo_*, status, ...) on the same dict.
    #
    # Pull the lifespan-loaded SiteConfig and thread it into the
    # ImageService ctor so the Pexels secret lookup goes through the
    # canonical Phase H DI seam (poindexter#381).
    image_service = get_image_service(site_config=site_config)

    # Settings + style tracker — pulled from the container/app.state
    # during DI transition (#242). Falls back to fresh instances when
    # invoked outside the lifespan-wired context (tests, ad-hoc CLI).
    try:
        from services.container import get_service as _get_service
        _settings_service = _get_service("settings")
    except Exception:
        _settings_service = None
    from services.image_style_rotation import ImageStyleTracker as _IST
    _style_tracker = _IST()

    # Mutable copy — the experiment hook may set ``models_by_phase["writer"]``
    # below if an A/B experiment is active. Always seed the dict before
    # the hook runs so the merge is in-place + observable to downstream
    # stages.
    _models_by_phase: dict[str, str] = dict(models_by_phase or {})

    # Glad-Labs/poindexter#27: assign this task to a variant of the
    # active pipeline experiment (if any). Best-effort — failure
    # returns a no-op assignment and the pipeline runs with default
    # config. The assignment dict is threaded through so finalize can
    # ``record_outcome`` on the same row.
    try:
        from services.pipeline_experiment_hook import assign_pipeline_variant
        _experiment_assignment = await assign_pipeline_variant(
            task_id=task_id,
            database_service=database_service,
            site_config=site_config,
            models_by_phase=_models_by_phase,
        )
    except Exception as _exc:
        # assign_pipeline_variant is itself wrapped in try/except, but
        # if the import fails for some bizarre reason we still want
        # the pipeline to run.
        logger.debug("[BG-TASK] experiment hook unavailable: %s", _exc)
        _experiment_assignment = {"experiment_key": None, "variant_key": None}

    result: dict[str, Any] = {
        "task_id": task_id,
        "topic": topic,
        "status": "pending",
        "stages": {},
        "category": category or "technology",
        # Orchestrator inputs — stages read these directly.
        "style": style,
        "tone": tone,
        "target_length": target_length,
        "tags": tags or [],
        "generate_featured_image": generate_featured_image,
        "database_service": database_service,
        "image_service": image_service,
        # Phase H DI seam — every stage can pull ``site_config`` from
        # context.get('site_config') and forward it into services that
        # need DB-backed settings or secrets (poindexter#381).
        "site_config": site_config,
        "models_by_phase": _models_by_phase,
        "quality_preference": quality_preference,
        "target_audience": target_audience,
        # Shared services threaded via context (replaces singletons).
        "settings_service": _settings_service,
        "image_style_tracker": _style_tracker,
        # Experiment context — present for the duration of the run so
        # finalize can ``record_outcome`` on the same assignment row.
        "experiment_assignment": _experiment_assignment,
    }

    # Resolve the template slug for this task. Per Lane C cutover
    # (poindexter#355), ``tasks_db.add_task`` reads
    # ``app_settings.default_template_slug`` at task creation and stores
    # the resolved slug on the ``pipeline_tasks`` row. Reading it back
    # here gives us per-task pipeline selection (e.g. ``dev_diary`` cron
    # tasks pass their own slug; everything else gets the operator's
    # global default).
    template_slug = await _load_template_slug(database_service, task_id)

    # Per ``feedback_no_silent_defaults``: a missing slug is a config
    # error, not a fallback. The legacy chunked StageRunner flow was
    # deleted in the 2026-05-16 sweep (see module docstring); there is
    # no implicit pipeline to run. Mark the task failed with a
    # diagnostic so the operator notices the misconfiguration instead
    # of silently dropping the task on the floor.
    if not template_slug:
        msg = (
            f"pipeline_tasks.template_slug is NULL for task {task_id} — "
            "set app_settings.default_template_slug or pass template_slug "
            "at task creation. The legacy chunked StageRunner path was "
            "deleted 2026-05-16 (see docs/architecture/langgraph-cutover.md)."
        )
        logger.error("[BG-TASK] %s", msg)
        audit_log_bg(
            "missing_template_slug", "content_router",
            {"task_id": task_id, "topic": topic[:100]},
            task_id=task_id, severity="error",
        )
        try:
            await database_service.update_task(
                task_id, {"status": "failed", "error_message": msg[:500]},
            )
        except Exception as _exc:
            logger.error("[BG-TASK] failed to mark task failed: %s", _exc)
        result["status"] = "failed"
        result["error"] = msg
        return result

    logger.info(
        "[BG-TASK] template_slug=%r — dispatching via TemplateRunner (LangGraph)",
        template_slug,
    )
    audit_log_bg(
        "task_started", "content_router",
        {"topic": topic[:100], "template_slug": template_slug},
        task_id=task_id,
    )

    try:
        from services.template_runner import TemplateRunner
        _tmpl_runner = TemplateRunner(database_service.pool)
        _tmpl_summary = await _tmpl_runner.run(
            template_slug, result, thread_id=str(task_id),
        )
        # Mirror the stage-summary shape expected by callers — task
        # routes through ``finalize_task`` inside the template, which
        # already updates the row to ``awaiting_approval`` (or auto-
        # publishes when the score clears the gate).
        result.update(_tmpl_summary.final_state)
        audit_log_bg(
            "template_completed", "content_router",
            {
                "template": template_slug,
                "ok": _tmpl_summary.ok,
                "halted_at": _tmpl_summary.halted_at,
                "records": [r.name for r in _tmpl_summary.records],
            },
            task_id=task_id,
        )

        # Glad-Labs/poindexter#27: attribute pipeline outcome to the
        # experiment assignment row (no-op when no experiment active).
        # Best-effort — failure here must not poison a successful run.
        try:
            from services.pipeline_experiment_hook import record_pipeline_outcome
            await record_pipeline_outcome(
                assignment=result.get("experiment_assignment") or {},
                task_id=task_id,
                database_service=database_service,
                site_config=site_config,
                metrics={
                    "quality_score": float(result.get("quality_score") or 0.0),
                    "qa_final_score": float(result.get("qa_final_score") or 0.0),
                    "status": str(result.get("status", "unknown")),
                    "model_used": str(result.get("model_used", "")),
                    "outcome": "success" if _tmpl_summary.ok else "halted",
                },
            )
        except Exception as _exc:
            logger.debug("[BG-TASK] experiment record_outcome failed: %s", _exc)

        logger.info("=" * 80)
        logger.info("CONTENT GENERATION PIPELINE FINISHED")
        logger.info("=" * 80)
        logger.info("   Task ID: %s", task_id)
        logger.info("   Post ID: %s", result.get('post_id', 'NOT_YET_CREATED'))
        logger.info("   Status: %s", result.get('status', 'unknown'))
        logger.info("   Template: %s", template_slug)
        logger.info("=" * 80)
        return result

    except Exception as exc:
        # TemplateRunner raised. Log loud, preserve partial context in
        # task_metadata so the operator can still review what generated
        # before the crash, and emit a task.failed webhook so OpenClaw
        # / Discord notifies downstream consumers.
        logger.exception(
            "[BG-TASK] TemplateRunner raised for task %s template=%r: %s",
            task_id, template_slug, exc,
        )

        # Per poindexter#260: when pipeline_dry_run_mode is on, the
        # writer chain short-circuits with AllModelsFailedError ("no
        # attempts recorded") because dry-run intentionally suppresses
        # model calls. That's expected behaviour, NOT a real failure —
        # logging it as severity='error' was drowning the 24h error
        # count (277/277 in one window were dry-run noise) and hiding
        # actual ollama/db errors. Demote to severity='info' with a
        # filterable event_type so dashboards/alerts can ignore it.
        _is_dry_run_halt = False
        try:
            _dry_raw = site_config.get("pipeline_dry_run_mode", "")
            _is_dry_run = str(_dry_raw).strip().lower() in ("true", "1", "yes", "on")
            _err_text = str(exc)
            _is_dry_run_halt = _is_dry_run and (
                "no attempts recorded" in _err_text
                or "AllModelsFailedError" in _err_text
            )
        except Exception as _dry_exc:
            logger.debug("[BG-TASK] dry-run severity-demote check failed: %s", _dry_exc)

        if _is_dry_run_halt:
            audit_log_bg(
                "dry_run_halt", "content_router",
                {
                    "error": str(exc)[:500],
                    "stages_completed": list(result.get("stages", {}).keys()),
                    "reason": "pipeline_dry_run_mode short-circuited the writer chain",
                },
                task_id=task_id, severity="info",
            )
        else:
            audit_log_bg(
                "error", "content_router",
                {
                    "error": str(exc)[:500],
                    "stages_completed": list(result.get("stages", {}).keys()),
                    "template": template_slug,
                },
                task_id=task_id, severity="error",
            )

        # Preserve all partially-generated data (content, image,
        # metadata) so it's available for review/approval workflow.
        try:
            failure_metadata = {
                "content": result.get("content"),
                "featured_image_url": result.get("featured_image_url"),
                "featured_image_alt": result.get("featured_image_alt"),
                "featured_image_width": result.get("featured_image_width"),
                "featured_image_height": result.get("featured_image_height"),
                "featured_image_photographer": result.get("featured_image_photographer"),
                "featured_image_source": result.get("featured_image_source"),
                "seo_title": result.get("seo_title"),
                "seo_description": result.get("seo_description"),
                "seo_keywords": result.get("seo_keywords"),
                "topic": topic,
                "style": style,
                "tone": tone,
                "quality_score": result.get("quality_score"),
                "error_stage": str(exc)[:200],
                "error_message": str(exc),
                "stages_completed": result.get("stages", {}),
                "template_slug": template_slug,
            }
            failure_metadata = {k: v for k, v in failure_metadata.items() if v is not None}

            await database_service.update_task(
                task_id=task_id,
                updates={
                    "status": "failed",
                    "error_message": str(exc),
                    "task_metadata": failure_metadata,
                },
            )

            try:
                await emit_webhook_event(database_service.pool, "task.failed", {
                    "task_id": task_id, "topic": topic, "error": str(exc)[:200],
                })
            except Exception:
                logger.warning(
                    "[WEBHOOK] Failed to emit task.failed event from pipeline",
                    exc_info=True,
                )
        except Exception as db_error:
            logger.error(
                "[BG-TASK] Failed to update task status: %s", db_error, exc_info=True,
            )

        result["status"] = "failed"
        result["error"] = str(exc)
        return result
