"""Unified Content Router Service — centralized blog post generation pipeline."""

from typing import Any

from services.logger_config import get_logger

from .audit_log import audit_log_bg
from .database_service import DatabaseService
from .image_service import get_image_service
from .webhook_delivery_service import emit_webhook_event

logger = get_logger(__name__)




















# ============================================================================
# WRITER SELF-REVIEW PASS
# ============================================================================


























async def process_content_generation_task(
    topic: str,
    style: str,
    tone: str,
    target_length: int,
    tags: list[str] | None = None,
    generate_featured_image: bool = True,
    database_service: DatabaseService | None = None,
    task_id: str | None = None,
    # NEW: Model selection parameters (Week 1)
    models_by_phase: dict[str, str] | None = None,
    quality_preference: str | None = None,
    category: str | None = None,
    target_audience: str | None = None,
) -> dict[str, Any]:
    """Run the full content generation pipeline (verify, generate, QA, images, SEO, finalize)."""
    from uuid import uuid4

    # Generate task_id if not provided
    if not task_id:
        task_id = str(uuid4())

    if not database_service:
        logger.error("DatabaseService not provided - cannot persist content")
        raise ValueError("DatabaseService is required for content_tasks persistence")

    logger.info("=" * 80)
    logger.info("COMPLETE CONTENT GENERATION PIPELINE")
    logger.info("=" * 80)
    logger.info("   Task ID: %s", task_id)
    logger.info("   Topic: %s", topic)
    logger.info("   Style: %s | Tone: %s", style, tone)
    logger.info("   Target Length: %s words", target_length)
    logger.info("   Tags: %s", ', '.join(tags) if tags else 'none')
    logger.info("   Image Search: %s", generate_featured_image)
    logger.info("=" * 80)

    # `result` doubles as the shared pipeline context consumed by Stage
    # plugins. Stages read/write via context.get() / StageResult.context_updates.
    # Populating the orchestrator's inputs here means every stage can pull
    # what it needs without a separate adapter layer.
    #
    # Pull the lifespan-loaded SiteConfig instance and thread it into
    # the ImageService ctor so the Pexels secret lookup goes through
    # the canonical Phase H DI seam (poindexter#381). Falling back to
    # the module singleton matches what main.py rebinds, so legacy
    # paths still work.
    import services.site_config as _scm_pipeline
    image_service = get_image_service(site_config=_scm_pipeline.site_config)
    # Settings + style tracker — pulled from the container/app.state during
    # transition to full DI (#242). Falls back to fresh instances when a
    # stage is invoked outside the lifespan-wired context (e.g. tests).
    try:
        from services.container import get_service as _get_service
        _settings_service = _get_service("settings")
    except Exception:
        _settings_service = None
    from services.image_style_rotation import ImageStyleTracker as _IST
    _style_tracker = _IST()

    # Mutable copy — the experiment hook may set ``models_by_phase["writer"]``
    # below if an A/B experiment is active. Always seed the dict before the
    # hook runs so the merge is in-place + observable to downstream stages.
    _models_by_phase: dict[str, str] = dict(models_by_phase or {})

    # Glad-Labs/poindexter#27: assign this task to a variant of the active
    # pipeline experiment (if any). Best-effort — failure returns no-op
    # and the pipeline runs with default config. The assignment dict is
    # threaded through so finalize can record_outcome on the same row.
    try:
        from services.pipeline_experiment_hook import assign_pipeline_variant
        import services.site_config as _scm_exp
        _experiment_assignment = await assign_pipeline_variant(
            task_id=task_id,
            database_service=database_service,
            site_config=_scm_exp.site_config,
            models_by_phase=_models_by_phase,
        )
    except Exception as _exc:
        # Truly defensive — assign_pipeline_variant is itself wrapped in
        # try/except, but if the import fails for some bizarre reason we
        # still want the pipeline to run.
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
        # Phase H DI seam — every stage can pull `site_config` from
        # context.get('site_config') and forward it into services that
        # need DB-backed settings or secrets (poindexter#381).
        "site_config": _scm_pipeline.site_config,
        "models_by_phase": _models_by_phase,
        "quality_preference": quality_preference,
        "target_audience": target_audience,
        # Shared services threaded via context (replaces singletons).
        "settings_service": _settings_service,
        "image_style_tracker": _style_tracker,
        # Experiment context — present for the duration of the run so
        # finalize can call record_outcome on the same assignment row.
        "experiment_assignment": _experiment_assignment,
    }

    # Build the Stage runner. Stages are loaded imperatively via
    # plugins.registry.get_core_samples since the poetry entry_points
    # packaging fix is tracked separately (#78).
    from plugins.registry import get_core_samples
    from plugins.stage_runner import StageRunner
    _runner = StageRunner(database_service.pool, get_core_samples().get("stages", []))

    # ------------------------------------------------------------------
    # Dynamic-pipeline-composition dispatch (v1 POC, Glad-Labs/poindexter#359).
    # When the task carries ``template_slug``, route to the LangGraph-based
    # TemplateRunner instead of the legacy StageRunner. Tasks without a
    # template_slug continue through the canonical chunked flow below —
    # no behaviour change for the existing default path.
    # ------------------------------------------------------------------
    _template_slug: str | None = None
    try:
        async with database_service.pool.acquire() as _conn:
            _raw = await _conn.fetchval(
                "SELECT template_slug FROM pipeline_tasks WHERE task_id = $1",
                str(task_id),
            )
        # Tight isinstance check — test fixtures bind ``db.pool`` as a
        # MagicMock that auto-generates AsyncMocks for attribute access,
        # so ``fetchval`` returns a truthy AsyncMock object rather than
        # a string. Without the isinstance gate, the legacy-StageRunner
        # tests get routed into TemplateRunner.run with a non-string
        # slug and KeyError out.
        if isinstance(_raw, str) and _raw.strip():
            _template_slug = _raw.strip()
    except Exception as _exc:
        logger.debug("[BG-TASK] template_slug lookup failed: %s", _exc)

    if _template_slug:
        logger.info(
            "[BG-TASK] template_slug=%r — routing to TemplateRunner (LangGraph)",
            _template_slug,
        )
        from services.template_runner import TemplateRunner
        _tmpl_runner = TemplateRunner(database_service.pool)
        try:
            _tmpl_summary = await _tmpl_runner.run(
                _template_slug, result, thread_id=str(task_id),
            )
            # Mirror the stage-summary shape expected by callers — task
            # routes through finalize_task inside the template, which
            # already updates the row to awaiting_approval.
            result.update(_tmpl_summary.final_state)
            audit_log_bg(
                "template_completed", "content_router",
                {
                    "template": _template_slug,
                    "ok": _tmpl_summary.ok,
                    "halted_at": _tmpl_summary.halted_at,
                    "records": [r.name for r in _tmpl_summary.records],
                },
                task_id=task_id,
            )
            return result
        except Exception as _exc:
            logger.exception(
                "[BG-TASK] TemplateRunner raised for task %s template=%r: %s",
                task_id, _template_slug, _exc,
            )
            await database_service.update_task(
                task_id,
                {
                    "status": "failed",
                    "error_message": (
                        f"TemplateRunner failed for template={_template_slug}: {_exc}"
                    )[:500],
                },
            )
            result["status"] = "failed"
            return result

    try:
        logger.info("[BG-TASK] Starting content generation for task %s...", task_id[:8])
        logger.debug("[BG-TASK] database_service = %s", database_service)

        # ---------------------------------------------------------------
        # Chunk 1: verify_task → generate_content
        # ---------------------------------------------------------------
        audit_log_bg("task_started", "content_router", {"topic": topic[:100]}, task_id=task_id)
        _summary1 = await _runner.run_all(result, order=["verify_task", "generate_content"])
        if _summary1.halted_at == "generate_content":
            raise RuntimeError(
                f"Stage 'generate_content' halted — cannot continue without content "
                f"(detail: {_summary1.records[-1].detail})"
            )

        content_text = result.get("content", "")
        model_used = result.get("model_used", "")

        audit_log_bg("generation_complete", "content_router", {
            "model": model_used, "word_count": len(content_text.split()) if content_text else 0,
        }, task_id=task_id)

        # Observability: detect silent writer fallback. If the DB configured
        # pipeline_writer_model (e.g. qwen2.5:72b) differs from the model
        # that actually produced the draft (e.g. gemma3:27b), fire a LOUD
        # audit event. Without this, a timed-out 72B silently degrades to
        # a 27B and nobody notices — which cost us task 033803c9 on 2026-04-11.
        try:
            import services.site_config as _scm_writer
            _configured_writer = (_scm_writer.site_config.get("pipeline_writer_model", "") or "").removeprefix("ollama/")
            _actual_writer = (model_used or "").removeprefix("ollama/")
            if _configured_writer and _actual_writer and _configured_writer != _actual_writer:
                logger.warning(
                    "[WRITER_FALLBACK] Configured %s but actually generated with %s for task %s",
                    _configured_writer, _actual_writer, task_id[:8],
                )
                audit_log_bg(
                    "writer_fallback", "content_router",
                    {
                        "configured_writer": _configured_writer,
                        "actual_writer": _actual_writer,
                        "reason": "primary_model_failed_or_timed_out",
                        "stage": "generate_content",
                    },
                    task_id=task_id, severity="warning",
                )
        except Exception as _exc:
            logger.debug("writer_fallback check failed: %s", _exc)

        # ---------------------------------------------------------------
        # Chunk 2: writer_self_review → quality_evaluation → url_validation
        #          → replace_inline_images (image-decision PLANNING pass,
        #          still in ollama GPU mode)
        # ---------------------------------------------------------------
        _summary2 = await _runner.run_all(result, order=[
            "writer_self_review",
            "quality_evaluation",
            "url_validation",
            "replace_inline_images",
        ])
        if _summary2.halted_at == "quality_evaluation":
            raise RuntimeError(
                f"Stage 'quality_evaluation' halted — cannot continue without QA score "
                f"(detail: {_summary2.records[-1].detail})"
            )

        # Post-QA audit. The stages populate result["quality_result"] +
        # result["quality_score"]; surface the pass/fail into the audit log.
        content_text = result.get("content", "")
        quality_result = result.get("quality_result")
        if quality_result is not None:
            audit_log_bg(
                "qa_passed" if quality_result.overall_score >= 50 else "qa_failed",
                "content_router",
                {"score": quality_result.overall_score, "stage": "early_eval"},
                task_id=task_id,
            )

        # ---------------------------------------------------------------
        # Chunk 3: GPU switch → featured image → GPU switch back
        # ---------------------------------------------------------------
        try:
            from services.gpu_scheduler import gpu as _gpu_sched
            await _gpu_sched.prepare_mode("sdxl")
        except Exception:
            logger.debug("GPU mode switch to SDXL failed (non-fatal)")

        # StageRunner honors plugin.stage.source_featured_image.enabled
        # via PluginConfig; and the stage itself short-circuits when
        # context["generate_featured_image"] is False.
        await _runner.run_all(result, order=["source_featured_image"])

        try:
            await _gpu_sched.prepare_mode("ollama")
        except Exception:
            logger.debug("GPU mode switch to Ollama failed (non-fatal)")

        # ---------------------------------------------------------------
        # Chunk 4: Multi-model QA + rewrite loop → CrossModelQAStage
        # ---------------------------------------------------------------
        # The stage handles the entire rewrite loop + gate check + reject
        # short-circuit. If QA rejects the content, the stage returns
        # continue_workflow=False and sets status=rejected; we detect that
        # via the runner's halted_at and early-return.
        _summary4 = await _runner.run_all(result, order=["cross_model_qa"])
        if _summary4.halted_at == "cross_model_qa" and result.get("status") == "rejected":
            return result

        # ---------------------------------------------------------------
        # Chunk 5: SEO → media scripts → training data → finalize
        # ---------------------------------------------------------------
        # The previous inline orchestrator read stage-specific fallbacks
        # (e.g. topic[:60] for seo_title on timeout) — now lives inside
        # the stages themselves or in finalize_task's graceful defaults.
        _summary5 = await _runner.run_all(result, order=[
            "generate_seo_metadata",
            "generate_media_scripts",
            "capture_training_data",
            "finalize_task",
        ])
        if _summary5.halted_at:
            raise RuntimeError(
                f"Post-QA pipeline halted at {_summary5.halted_at} "
                f"(detail: {_summary5.records[-1].detail})"
            )

        audit_log_bg("pipeline_complete", "content_router", {
            # quality_score is the promoted score that downstream gates read
            # (matches content_tasks.quality_score). early_eval_score is kept
            # alongside for diagnostic visibility.
            "quality_score": result.get("quality_score", quality_result.overall_score),
            "qa_final_score": result.get("qa_final_score"),
            "early_eval_score": quality_result.overall_score,
            "status": result["status"],
        }, task_id=task_id)

        # Glad-Labs/poindexter#27: attribute pipeline outcome to the
        # experiment assignment row (no-op when no experiment active).
        try:
            from services.pipeline_experiment_hook import record_pipeline_outcome
            import services.site_config as _scm_outcome
            await record_pipeline_outcome(
                assignment=result.get("experiment_assignment") or {},
                task_id=task_id,
                database_service=database_service,
                site_config=_scm_outcome.site_config,
                metrics={
                    "quality_score": float(
                        result.get("quality_score", quality_result.overall_score) or 0.0
                    ),
                    "qa_final_score": float(result.get("qa_final_score") or 0.0),
                    "status": str(result["status"]),
                    "model_used": str(result.get("model_used", "")),
                    "outcome": "success",
                },
            )
        except Exception as _exc:
            logger.debug("[BG-TASK] experiment record_outcome failed: %s", _exc)

        logger.info("=" * 80)
        logger.info("COMPLETE CONTENT GENERATION PIPELINE FINISHED")
        logger.info("=" * 80)
        logger.info("   Task ID: %s", task_id)
        logger.info("   Post ID: %s", result.get('post_id', 'NOT_YET_CREATED'))
        logger.info(
            "   Featured Image: %s",
            result.get('featured_image_url', 'NONE')[:100] if result.get('featured_image_url') else 'NONE',
        )
        logger.info("   Quality Score: %.1f/100", quality_result.overall_score)
        logger.info("   Status: %s", result['status'])
        logger.info("   Next: Human review & approval")
        logger.info("=" * 80)

        return result

    except Exception as e:
        logger.error("[BG-TASK] Pipeline error for task %s...: %s", task_id[:8], e, exc_info=True)
        logger.error("[BG-TASK] Detailed traceback:", exc_info=True)

        # Glad-Labs/poindexter#260: when pipeline_dry_run_mode is on, the
        # writer chain short-circuits with AllModelsFailedError ("no
        # attempts recorded") because dry-run intentionally suppresses
        # model calls. That's expected behavior, NOT a real failure —
        # logging it as severity='error' was drowning the 24h error
        # count (277/277 in one window were dry-run noise) and hiding
        # actual ollama/db errors. Demote to severity='info' with a
        # filterable event_type so dashboards/alerts can ignore it.
        # The task's own status (set below to 'failed', or 'dry_run' by
        # finalize_task) remains the authoritative state — only the
        # audit_log row severity changes here.
        _is_dry_run_halt = False
        try:
            import services.site_config as _scm_dry
            _dry_raw = _scm_dry.site_config.get("pipeline_dry_run_mode", "")
            _is_dry_run = str(_dry_raw).strip().lower() in ("true", "1", "yes", "on")
            _err_text = str(e)
            _is_dry_run_halt = _is_dry_run and (
                "no attempts recorded" in _err_text
                or "AllModelsFailedError" in _err_text
            )
        except Exception as _dry_exc:
            logger.debug("[BG-TASK] dry-run severity-demote check failed: %s", _dry_exc)

        if _is_dry_run_halt:
            audit_log_bg("dry_run_halt", "content_router", {
                "error": str(e)[:500],
                "stages_completed": list(result.get("stages", {}).keys()),
                "reason": "pipeline_dry_run_mode short-circuited the writer chain",
            }, task_id=task_id, severity="info")
        else:
            audit_log_bg("error", "content_router", {
                "error": str(e)[:500], "stages_completed": list(result.get("stages", {}).keys()),
            }, task_id=task_id, severity="error")

        # Update content_task with failure status
        # 🔑 CRITICAL: Preserve all partially-generated data (content, image, metadata)
        # so it's available for review/approval workflow
        try:
            logger.debug("[BG-TASK] Attempting to update task status to 'failed'...")
            logger.debug("[BG-TASK] Preserving partial results: %s", list(result.keys()))

            # Build task_metadata with whatever we successfully generated
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
                "error_stage": str(e)[:200],  # Which stage failed
                "error_message": str(e),  # Full error for debugging
                "stages_completed": result.get("stages", {}),
            }

            # Remove None values from metadata
            failure_metadata = {k: v for k, v in failure_metadata.items() if v is not None}

            await database_service.update_task(
                task_id=task_id,
                updates={
                    "status": "failed",
                    "error_message": str(e),
                    "task_metadata": failure_metadata,  # ✅ Preserve all data
                },
            )
            logger.debug("[BG-TASK] Task status updated to 'failed' with preserved data")

            # Emit webhook so OpenClaw is notified of pipeline failure
            try:
                await emit_webhook_event(database_service.pool, "task.failed", {
                    "task_id": task_id, "topic": topic, "error": str(e)[:200],
                })
            except Exception:
                logger.warning("[WEBHOOK] Failed to emit task.failed event from pipeline", exc_info=True)
        except Exception as db_error:
            logger.error("[BG-TASK] Failed to update task status: %s", db_error, exc_info=True)

        result["status"] = "failed"
        result["error"] = str(e)
        return result




