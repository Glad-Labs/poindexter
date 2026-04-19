"""Unified Content Router Service — centralized blog post generation pipeline."""

import asyncio
from typing import Any, Dict, List, Optional

from services.logger_config import get_logger

from .ai_content_generator import get_content_generator
from .audit_log import audit_log_bg
from .database_service import DatabaseService
from .image_service import get_image_service
from .prompt_manager import get_prompt_manager
from .quality_service import EvaluationMethod, UnifiedQualityService
from .seo_content_generator import get_seo_content_generator
from .webhook_delivery_service import emit_webhook_event

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Image-style rotation dedup (#181)
# ---------------------------------------------------------------------------
# Module-level tracker so concurrent/sequential tasks in the same worker
# process don't pick the same style.  Entries are (style_name, timestamp).
# We keep at most _STYLE_HISTORY_SIZE entries and auto-expire after
# _STYLE_HISTORY_TTL seconds so styles become available again once enough
# time passes (prevents permanent starvation when the style pool is small).
import time as _time
from collections import deque as _deque

_STYLE_HISTORY_SIZE = 10
_STYLE_HISTORY_TTL = 3600  # 1 hour
_recent_style_picks: _deque = _deque(maxlen=_STYLE_HISTORY_SIZE)


def _record_style_pick(style_name: str) -> None:
    """Record that *style_name* was just chosen."""
    _recent_style_picks.append((style_name, _time.monotonic()))


def _get_in_memory_recent_styles() -> list:
    """Return style names picked within the TTL window."""
    cutoff = _time.monotonic() - _STYLE_HISTORY_TTL
    return [name for name, ts in _recent_style_picks if ts >= cutoff]




async def _is_stage_enabled(pool, stage_key: str) -> bool:
    """Check if a pipeline stage is enabled in the database.

    Returns True if the stage is enabled or if the table doesn't exist (backwards compatible).
    """
    if pool is None:
        return True  # No DB = run everything (local dev)
    try:
        row = await pool.fetchrow(
            "SELECT enabled FROM pipeline_stages WHERE key = $1", stage_key
        )
        if row is None:
            return True  # Stage not in DB = run it (backwards compatible)
        return row["enabled"]
    except Exception:
        return True  # Table doesn't exist = run everything


# ============================================================================
# TEXT NORMALIZATION — canonical home is services/text_utils.py (Phase E2).
# Legacy names re-exported so older callers keep working.
# ============================================================================

from services.text_utils import (  # noqa: F401 — re-exported for legacy callers
    normalize_text as _normalize_text,
    scrub_fabricated_links as _scrub_fabricated_links,
)

# ============================================================================
# HELPER RE-EXPORTS (Phase E2) — the canonical modules listed below own the
# implementations now. These aliases keep the legacy underscore-prefixed
# import path (`from services.content_router_service import _X`) working for
# stage files + tests that haven't migrated yet. Drop these in a follow-up
# once every caller imports from the canonical location.
# ============================================================================

from services.model_preferences import parse_model_preferences as _parse_model_preferences  # noqa: F401, E501
from services.writing_style_context import build_writing_style_context as _build_writing_style_context  # noqa: F401, E501
from services.research_context import build_rag_context as _build_rag_context  # noqa: F401, E501
from services.title_generation import (  # noqa: F401
    check_title_originality as _check_title_originality,
    generate_canonical_title as _generate_canonical_title,
    sanitize_generated_title as _sanitize_generated_title,
)
from services.self_review import self_review_and_revise as _self_review_and_revise  # noqa: F401, E501



# ============================================================================
# TASK STORE — canonical home is services/content_task_store.py (Phase E2).
# ============================================================================

from services.content_task_store import (  # noqa: F401 — legacy alias
    ContentTaskStore,
    get_content_task_store,
)


# ============================================================================
# BACKGROUND TASK PROCESSORS
# ============================================================================
















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
    image_service = get_image_service()
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
        "models_by_phase": models_by_phase or {},
        "quality_preference": quality_preference,
        "target_audience": target_audience,
    }

    # Build the Stage runner. Stages are loaded imperatively via
    # plugins.registry.get_core_samples since the poetry entry_points
    # packaging fix is tracked separately (#78).
    from plugins.registry import get_core_samples
    from plugins.stage_runner import StageRunner
    _runner = StageRunner(database_service.pool, get_core_samples().get("stages", []))

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
        metrics = result.get("generate_metrics", {})
        title = result.get("title", "")

        audit_log_bg("generation_complete", "content_router", {
            "model": model_used, "word_count": len(content_text.split()) if content_text else 0,
        }, task_id=task_id)

        # Observability: detect silent writer fallback. If the DB configured
        # pipeline_writer_model (e.g. qwen2.5:72b) differs from the model
        # that actually produced the draft (e.g. gemma3:27b), fire a LOUD
        # audit event. Without this, a timed-out 72B silently degrades to
        # a 27B and nobody notices — which cost us task 033803c9 on 2026-04-11.
        try:
            from services.site_config import site_config as _sc_writer_check
            _configured_writer = (_sc_writer_check.get("pipeline_writer_model", "") or "").removeprefix("ollama/")
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
        _pool = database_service.pool if database_service else None
        try:
            from services.gpu_scheduler import gpu as _gpu_sched
            await _gpu_sched.prepare_mode("sdxl")
        except Exception:
            logger.debug("GPU mode switch to SDXL failed (non-fatal)")

        if await _is_stage_enabled(_pool, "featured_image"):
            await _runner.run_all(result, order=["source_featured_image"])
        else:
            logger.info("Featured image skipped (disabled in pipeline_stages)")

        featured_image = result.get("featured_image")

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


async def _select_category_for_topic(
    topic: str, database_service: DatabaseService, requested_category: str | None = None
) -> str | None:
    """Select category by requested slug, keyword matching, or default to 'technology'. Returns UUID."""
    # Priority 1: Use the requested category if valid
    if requested_category:
        try:
            async with database_service.pool.acquire() as conn:
                cat_id = await conn.fetchval(
                    "SELECT id FROM categories WHERE slug = $1 OR name ILIKE $1", requested_category
                )
            if cat_id:
                return cat_id
        except Exception:
            pass

    topic_lower = topic.lower()

    category_keywords = {
        "technology": ["ai", "tech", "software", "cloud", "machine learning", "data", "coding", "python", "javascript", "docker", "kubernetes", "api", "database"],
        "business": ["business", "strategy", "management", "entrepreneur", "growth", "revenue", "marketing", "saas"],
        "startup": ["startup", "founder", "bootstrapper", "mvp", "launch", "validate", "solo founder", "side project"],
        "security": ["security", "hack", "owasp", "zero trust", "vulnerability", "auth", "encryption", "secrets"],
        "engineering": ["engineering", "architecture", "monorepo", "git", "technical debt", "migration", "ci/cd", "testing"],
        "insights": ["trend", "landscape", "state of", "productivity", "remote work", "future of", "prediction"],
    }

    # Priority 2: Keyword matching
    matched_category = "technology"  # Default
    best_score = 0
    for category, keywords in category_keywords.items():
        score = sum(1 for kw in keywords if kw in topic_lower)
        if score > best_score:
            best_score = score
            matched_category = category

    # Get category ID
    try:
        async with database_service.pool.acquire() as conn:
            cat_id = await conn.fetchval(
                "SELECT id FROM categories WHERE slug = $1", matched_category
            )
        return cat_id
    except Exception as e:
        logger.error("Error selecting category: %s", e, exc_info=True)
        return None


async def _get_or_create_default_author(database_service: DatabaseService) -> str | None:
    """Get or create the default 'Poindexter AI' author. Returns UUID."""
    try:
        async with database_service.pool.acquire() as conn:
            # Try to get existing Poindexter AI author
            author_id = await conn.fetchval(
                "SELECT id FROM authors WHERE slug = 'poindexter-ai' LIMIT 1"
            )

            if author_id:
                return author_id

            # Create if doesn't exist
            author_id = await conn.fetchval("""
                INSERT INTO authors (name, slug, email, bio, avatar_url)
                VALUES ('Poindexter AI', 'poindexter-ai', 'poindexter@glad-labs.ai', 
                        'AI Content Generation Engine', NULL)
                ON CONFLICT (slug) DO NOTHING
                RETURNING id
                """)

            if author_id:
                logger.info("Created default author: Poindexter AI (%s)", author_id)
                return author_id

            # Fallback: return any author
            fallback_id = await conn.fetchval("SELECT id FROM authors LIMIT 1")
            return fallback_id

    except Exception as e:
        logger.error("Error getting/creating default author: %s", e, exc_info=True)
        return None
