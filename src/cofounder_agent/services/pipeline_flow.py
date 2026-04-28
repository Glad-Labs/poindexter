"""Prefect flow wrapper around the content generation pipeline.

#206 — Migrate the content pipeline to Prefect 3 for observability,
retry plumbing, and durable run state. APScheduler stays for the 31
periodic Jobs (different shape, different needs).

Architecture
------------

The 6-stage StageRunner pipeline (see ``content_router_service.py``)
is preserved. Each natural orchestration boundary becomes a ``@task``,
and the whole pipeline becomes a ``@flow``:

  Chunk 1: topic_decision_gate → verify_task → generate_content
  Chunk 2: writer_self_review → quality_evaluation → url_validation → replace_inline_images
  Chunk 3: source_featured_image (with GPU mode wrap)
  Chunk 4: cross_model_qa
  Chunk 5: generate_seo_metadata → generate_media_scripts → capture_training_data → finalize_task

Why per-chunk @tasks (not per-stage):
- Chunks are the natural retry boundary — retrying just verify_task
  without generate_content makes no sense; the chunk is the unit of work.
- The Prefect UI's task graph becomes readable (5 tasks vs 13).
- Each Stage already has its own halt + retry logic via StageResult;
  Prefect adds a coarser per-chunk retry on top.

Execution model
---------------

In-process. The worker imports the @flow and submits it directly via
``content_generation_flow(...)`` — Prefect server is recording the run
for observability + UI, NOT scheduling worker processes. Our existing
worker container IS the executor.

The flow does NOT serialize ``result`` between tasks (it contains
non-pickleable handles: database_service, image_service, site_config).
Tasks accept ``result`` by reference and mutate in place — the flow
holds the canonical reference.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prefect import flow, task
from prefect.logging import get_run_logger

from services.audit_log import audit_log_bg
from services.image_service import get_image_service

if TYPE_CHECKING:
    from services.database_service import DatabaseService
    from services.site_config import SiteConfig


# ---------------------------------------------------------------------------
# Per-chunk tasks
# ---------------------------------------------------------------------------
#
# Each task wraps one StageRunner.run_all(...) call. They mutate the
# shared ``result`` context dict in place; return value is the
# StageRunner summary so the flow can inspect halts without unpacking
# private fields.
#
# retries=0 on every task: each Stage already implements its own retry
# semantics (see StageResult), and most halts are *intentional* gate
# trips that retrying would just repeat. Task-level retries here are
# reserved for surprise infrastructure failures (DB blip, OOM kill of
# downstream model server, etc.) — opt in per-task with a comment.

@task(
    name="pipeline.chunk_1_verify_and_generate",
    retries=0,
    persist_result=False,
    cache_result_in_memory=False,
)
async def _run_chunk_1(result: dict[str, Any], runner: Any) -> Any:
    """Topic-decision gate → verify_task → generate_content.

    The topic-decision gate is inert by default. When enabled, it
    halts the workflow before any LLM cycles burn so the operator
    can approve/reject. generate_content is the writer LLM call —
    if it halts, the pipeline can't continue.
    """
    return await runner.run_all(
        result,
        order=["topic_decision_gate", "verify_task", "generate_content"],
    )


@task(
    name="pipeline.chunk_2_review_and_validate",
    retries=0,
    persist_result=False,
    cache_result_in_memory=False,
)
async def _run_chunk_2(result: dict[str, Any], runner: Any) -> Any:
    """Writer self-review → QA → URL validation → inline image planning.

    All Ollama-bound (no GPU mode switch needed). quality_evaluation
    halt is fatal — without a QA score the rest of the pipeline can't
    score the content for the auto-publish threshold.
    """
    return await runner.run_all(
        result,
        order=[
            "writer_self_review",
            "quality_evaluation",
            "url_validation",
            "replace_inline_images",
        ],
    )


@task(
    name="pipeline.chunk_3_featured_image",
    retries=0,
    persist_result=False,
    cache_result_in_memory=False,
)
async def _run_chunk_3(result: dict[str, Any], runner: Any) -> Any:
    """GPU mode → SDXL/Pexels featured image → GPU mode back to Ollama.

    The GPU mode switch is best-effort (caught + logged). The Stage
    itself short-circuits when context["generate_featured_image"] is
    False, so this chunk is fast for tasks that opt out of images.
    """
    logger = get_run_logger()
    try:
        from services.gpu_scheduler import gpu as _gpu_sched
        await _gpu_sched.prepare_mode("sdxl")
    except Exception as exc:
        logger.debug("GPU mode switch to SDXL failed (non-fatal): %s", exc)

    summary = await runner.run_all(result, order=["source_featured_image"])

    try:
        from services.gpu_scheduler import gpu as _gpu_sched
        await _gpu_sched.prepare_mode("ollama")
    except Exception as exc:
        logger.debug("GPU mode switch to Ollama failed (non-fatal): %s", exc)

    return summary


@task(
    name="pipeline.chunk_4_cross_model_qa",
    retries=0,
    persist_result=False,
    cache_result_in_memory=False,
)
async def _run_chunk_4(result: dict[str, Any], runner: Any) -> Any:
    """Cross-model QA + rewrite loop.

    The Stage owns the rewrite-on-reject loop and the auto-reject
    threshold. Returning a summary with halted_at='cross_model_qa'
    + status='rejected' is the clean-rejection path — flow short-
    circuits on it.
    """
    return await runner.run_all(result, order=["cross_model_qa"])


@task(
    name="pipeline.chunk_5_seo_and_finalize",
    retries=0,
    persist_result=False,
    cache_result_in_memory=False,
)
async def _run_chunk_5(result: dict[str, Any], runner: Any) -> Any:
    """SEO metadata → media scripts → training data → finalize.

    Last leg. finalize_task writes the post row + sets the final
    status (auto_publish or awaiting_approval). Any halt here is a
    bug — every prior stage produced its outputs, finalize should
    just persist.
    """
    return await runner.run_all(
        result,
        order=[
            "generate_seo_metadata",
            "generate_media_scripts",
            "capture_training_data",
            "finalize_task",
        ],
    )


# ---------------------------------------------------------------------------
# Flow entry point
# ---------------------------------------------------------------------------


@flow(
    name="content_generation_pipeline",
    persist_result=False,
    # SiteConfig + DatabaseService aren't pydantic-serializable (they
    # hold pool handles + thread-locks); turn off Prefect's parameter
    # validation. We still get full UI/run telemetry — only the
    # parameter-schema validator is skipped. The flow's type hints
    # serve as documentation for callers.
    validate_parameters=False,
)
async def content_generation_flow(
    topic: str,
    style: str,
    tone: str,
    target_length: int,
    *,
    site_config: "SiteConfig",
    tags: list[str] | None = None,
    generate_featured_image: bool = True,
    database_service: "DatabaseService | None" = None,
    task_id: str | None = None,
    models_by_phase: dict[str, str] | None = None,
    quality_preference: str | None = None,
    category: str | None = None,
    target_audience: str | None = None,
) -> dict[str, Any]:
    """Run the full content generation pipeline as a Prefect flow.

    Mirrors the previous ``process_content_generation_task`` signature
    exactly — callers pass the same kwargs and get the same ``result``
    dict back. Difference: each chunk runs as a Prefect @task, so the
    run shows up in the Prefect UI (``http://localhost:4200``) with
    timing, status, and logs per chunk.
    """
    from uuid import uuid4

    logger = get_run_logger()

    if not task_id:
        task_id = str(uuid4())

    if not database_service:
        logger.error("DatabaseService not provided — cannot persist content")
        raise ValueError("DatabaseService is required for content_tasks persistence")

    logger.info(
        "Pipeline started — task=%s topic=%r style=%s tone=%s words=%d",
        task_id[:8], topic[:80], style, tone, target_length,
    )

    image_service = get_image_service()

    try:
        from services.container import get_service as _get_service
        _settings_service = _get_service("settings")
    except Exception:
        _settings_service = None

    from services.image_style_rotation import ImageStyleTracker as _IST
    _style_tracker = _IST()

    result: dict[str, Any] = {
        "task_id": task_id,
        "topic": topic,
        "status": "pending",
        "stages": {},
        "category": category or "technology",
        "style": style,
        "tone": tone,
        "target_length": target_length,
        "tags": tags or [],
        "generate_featured_image": generate_featured_image,
        "database_service": database_service,
        "image_service": image_service,
        "models_by_phase": models_by_phase or {},
        "quality_preference": quality_preference,
        "target_audience": target_audience,
        "settings_service": _settings_service,
        "image_style_tracker": _style_tracker,
        "site_config": site_config,
    }

    from plugins.registry import get_core_samples, get_stages
    from plugins.stage_runner import StageRunner

    # Stages come from BOTH entry_points (production plugins, registered
    # via pyproject.toml's [tool.poetry.plugins."poindexter.stages"]) AND
    # the bundled core_samples loader (dev fallback). Pre-#206 the legacy
    # process_content_generation_task only consulted core_samples — which
    # always returns []  for stages since gh#152 trimmed _SAMPLES — so the
    # StageRunner registered zero stages and every pipeline run silently
    # short-circuited (no work, status=pending, no post). Caught while
    # live-verifying the Prefect migration. Dedup by stage name so a
    # core sample shipping with the same name as an entry_point doesn't
    # register twice.
    _ep_stages = get_stages()
    _sample_stages = get_core_samples().get("stages", [])
    _by_name: dict[str, Any] = {}
    for s in list(_ep_stages) + list(_sample_stages):
        name = getattr(s, "name", None)
        if name and name not in _by_name:
            _by_name[name] = s
    runner = StageRunner(database_service.pool, list(_by_name.values()))

    audit_log_bg("task_started", "content_router", {"topic": topic[:100]}, task_id=task_id)

    try:
        # ---- Chunk 1: verify + generate -----------------------------
        summary1 = await _run_chunk_1(result, runner)
        if summary1.halted_at == "topic_decision_gate":
            logger.info("topic_decision_gate paused task — operator approval pending")
            result.setdefault("status", "in_progress")
            result["awaiting_gate"] = "topic_decision"
            return result
        if summary1.halted_at == "generate_content":
            raise RuntimeError(
                "Stage 'generate_content' halted — no content to continue with "
                f"(detail: {summary1.records[-1].detail})"
            )

        content_text = result.get("content", "")
        model_used = result.get("model_used", "")
        audit_log_bg(
            "generation_complete", "content_router",
            {
                "model": model_used,
                "word_count": len(content_text.split()) if content_text else 0,
            },
            task_id=task_id,
        )

        # Writer-fallback canary (preserved from the legacy orchestrator).
        try:
            _configured_writer = (site_config.get("pipeline_writer_model", "") or "").removeprefix("ollama/")
            _actual_writer = (model_used or "").removeprefix("ollama/")
            if _configured_writer and _actual_writer and _configured_writer != _actual_writer:
                logger.warning(
                    "Writer fallback: configured %s, actually generated with %s",
                    _configured_writer, _actual_writer,
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

        # ---- Chunk 2: review + validate -----------------------------
        summary2 = await _run_chunk_2(result, runner)
        if summary2.halted_at == "quality_evaluation":
            raise RuntimeError(
                "Stage 'quality_evaluation' halted — no QA score to gate on "
                f"(detail: {summary2.records[-1].detail})"
            )

        quality_result = result.get("quality_result")
        if quality_result is not None:
            audit_log_bg(
                "qa_passed" if quality_result.overall_score >= 50 else "qa_failed",
                "content_router",
                {"score": quality_result.overall_score, "stage": "early_eval"},
                task_id=task_id,
            )

        # ---- Chunk 3: featured image --------------------------------
        await _run_chunk_3(result, runner)

        # ---- Chunk 4: cross-model QA --------------------------------
        summary4 = await _run_chunk_4(result, runner)
        if summary4.halted_at == "cross_model_qa" and result.get("status") == "rejected":
            return result

        # ---- Chunk 5: SEO + finalize --------------------------------
        summary5 = await _run_chunk_5(result, runner)
        if summary5.halted_at:
            raise RuntimeError(
                f"Post-QA pipeline halted at {summary5.halted_at} "
                f"(detail: {summary5.records[-1].detail})"
            )

        audit_log_bg(
            "pipeline_complete", "content_router",
            {
                "quality_score": result.get(
                    "quality_score",
                    quality_result.overall_score if quality_result else None,
                ),
                "qa_final_score": result.get("qa_final_score"),
                "early_eval_score": quality_result.overall_score if quality_result else None,
                "status": result["status"],
            },
            task_id=task_id,
        )

        logger.info(
            "Pipeline complete — task=%s post=%s qa=%.1f status=%s",
            task_id[:8],
            result.get("post_id", "—"),
            quality_result.overall_score if quality_result else 0.0,
            result["status"],
        )

        return result

    except Exception as e:
        logger.error("Pipeline error for task=%s: %s", task_id[:8], e, exc_info=True)
        audit_log_bg(
            "error", "content_router",
            {
                "error": str(e)[:500],
                "stages_completed": list(result.get("stages", {}).keys()),
            },
            task_id=task_id, severity="error",
        )

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
                "error_stage": str(e)[:200],
                "error_message": str(e),
                "stages_completed": result.get("stages", {}),
            }
            failure_metadata = {k: v for k, v in failure_metadata.items() if v is not None}

            await database_service.update_task(
                task_id=task_id,
                updates={
                    "status": "failed",
                    "error_message": str(e),
                    "task_metadata": failure_metadata,
                },
            )

            try:
                from services.webhook_delivery_service import emit_webhook_event
                await emit_webhook_event(
                    database_service.pool,
                    "task.failed",
                    {"task_id": task_id, "topic": topic, "error": str(e)[:200]},
                )
            except Exception:
                logger.warning("Failed to emit task.failed webhook (non-fatal)", exc_info=True)
        except Exception as db_error:
            logger.error("Failed to persist failure metadata: %s", db_error, exc_info=True)

        result["status"] = "failed"
        result["error"] = str(e)
        return result


__all__ = ["content_generation_flow"]
