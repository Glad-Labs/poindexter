"""content.persist_task — guarded DB write for the finalized task.

Extracted from FinalizeTaskStage. Runs update_task_status_guarded +
update_task + log_revision + upsert_version. Single responsibility:
persist the awaiting_approval record.

Produces: status, approval_status, post_id, post_slug,
          stages["5_post_created"].

Issue: Glad-Labs/poindexter#362.
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.persist_task",
    type="atom",
    version="1.0.0",
    description=(
        "Guarded DB write: update_task_status_guarded + update_task + "
        "log_revision + pipeline_versions upsert. Sets task status to "
        "awaiting_approval."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(name="content", type="str", description="finalized body"),
        FieldSpec(name="title", type="str", description="canonical title"),
        FieldSpec(name="excerpt", type="str", description="short excerpt"),
        FieldSpec(name="quality_score", type="float", description="final quality score"),
        FieldSpec(name="database_service", type="object", description="DB service"),
        FieldSpec(name="platform", type="object", description="capability handle", required=False),
    ),
    outputs=(
        FieldSpec(name="status", type="str", description="awaiting_approval"),
        FieldSpec(name="approval_status", type="str", description="pending"),
        FieldSpec(name="post_id", type="str", description="None — posts created on approve"),
        FieldSpec(name="post_slug", type="str", description="None — posts created on approve"),
        FieldSpec(name="task_metadata", type="dict", description="assembled finalize metadata for content.record_pipeline_version (#693)"),
    ),
    requires=("task_id", "content"),
    produces=("status", "approval_status", "post_id", "post_slug", "task_metadata"),
    capability_tier=None,
    cost_class="free",
    idempotent=False,
    side_effects=("db_write",),
    retry=RetryPolicy(
        max_attempts=3,
        backoff_s=1.0,
        retry_on=("asyncpg.PostgresConnectionError",),
    ),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Write the awaiting_approval record to content_tasks."""
    from services.quality_models import ensure_quality_assessment
    from services.text_utils import normalize_text as _normalize_text

    task_id = state.get("task_id")
    database_service = state.get("database_service")
    if not task_id or database_service is None:
        raise ValueError("content.persist_task requires task_id and database_service")

    topic = state.get("topic", "")
    style = state.get("style", "")
    tone = state.get("tone", "")
    content_text = state.get("content") or ""
    seo_title = _normalize_text(state.get("seo_title") or "")
    seo_description = _normalize_text(state.get("seo_description") or "")
    category = state.get("category", "")
    target_audience = state.get("target_audience")
    excerpt_text = state.get("excerpt") or ""
    qa_feedback_text = state.get("qa_feedback_formatted") or state.get("qa_feedback") or ""
    preview_token = state.get("preview_token") or ""

    quality_result = ensure_quality_assessment(state.get("quality_result"))
    final_quality_score = state.get("quality_score") or (
        round(float(quality_result.overall_score)) if quality_result else 0
    )
    early_eval_score = quality_result.overall_score if quality_result else 0

    # seo_keywords handling.
    seo_keywords_raw = state.get("seo_keywords")
    if isinstance(seo_keywords_raw, list):
        seo_keywords_string = ", ".join(seo_keywords_raw)
        seo_keywords_list = seo_keywords_raw
    elif isinstance(seo_keywords_raw, str):
        seo_keywords_string = seo_keywords_raw
        seo_keywords_list = state.get("seo_keywords_list") or []
    else:
        seo_keywords_string = ""
        seo_keywords_list = []

    # Canonical title.
    from services.title_generation import strip_qa_batch_suffix
    final_title = (
        state.get("title") or seo_title or strip_qa_batch_suffix(topic)
    )

    # Shared assembly with FinalizeTaskStage — see
    # modules/content/task_metadata.py. Derived fields (preview_token,
    # normalized content/seo, scores) are computed above and passed in;
    # passthrough fields are read from `state` by the helper. Keeps the
    # two finalize paths' key sets identical (Glad-Labs/poindexter#693).
    from modules.content.task_metadata import build_task_metadata
    task_metadata = build_task_metadata(
        state,
        preview_token=preview_token,
        content_text=content_text,
        seo_title=seo_title,
        seo_description=seo_description,
        seo_keywords_list=seo_keywords_list,
        final_quality_score=final_quality_score,
        early_eval_score=early_eval_score,
    )

    updates = {
        "status": "awaiting_approval",
        "approval_status": "pending",
        "error_message": None,
        "quality_score": final_quality_score,
        "title": final_title,
        "featured_image_url": state.get("featured_image_url"),
        "seo_title": seo_title,
        "seo_description": seo_description,
        "seo_keywords": seo_keywords_string,
        "style": style,
        "tone": tone,
        "category": category,
        "target_audience": target_audience or "General",
        "excerpt": excerpt_text,
        "qa_feedback": qa_feedback_text,
        "task_metadata": task_metadata,
    }

    # Status-guarded write (GH-90).
    guard_result = None
    if hasattr(database_service, "update_task_status_guarded"):
        try:
            guard_result = await database_service.update_task_status_guarded(
                task_id=task_id,
                new_status="awaiting_approval",
                allowed_from=("in_progress", "pending"),
            )
        except Exception as _guard_err:
            logger.warning("[content.persist_task] status-guard raised — fallback: %s", _guard_err)
            guard_result = "fallback"
    else:
        guard_result = "fallback"

    if guard_result is None:
        logger.error(
            "[content.persist_task] ABORTED: task %s no longer in_progress/pending", task_id
        )
        raise RuntimeError(
            f"content.persist_task aborted: task {task_id} race with stale-task sweeper (GH-90)"
        )

    await database_service.update_task(task_id=task_id, updates=updates)

    # pipeline_versions upsert (poindexter#473).
    try:
        from services.pipeline_db import PipelineDB
        await PipelineDB(database_service.pool).upsert_version(
            task_id,
            {
                "title": final_title,
                "content": content_text,
                "excerpt": excerpt_text,
                "featured_image_url": state.get("featured_image_url"),
                "seo_title": seo_title,
                "seo_description": seo_description,
                "seo_keywords": seo_keywords_string,
                "quality_score": final_quality_score,
                "qa_feedback": qa_feedback_text,
                "models_used_by_phase": state.get("models_used_by_phase", {}),
                "metadata": task_metadata,
                "task_metadata": task_metadata,
                "featured_image_prompt": state.get("featured_image_prompt"),
                "tags": state.get("tags"),
            },
        )
    except Exception as ver_err:
        logger.warning(
            "[content.persist_task] pipeline_versions write failed for %s: %s",
            task_id, ver_err,
        )

    # Revision snapshot.
    try:
        from services.content_revisions_logger import log_revision
        await log_revision(
            database_service.pool,
            task_id=task_id,
            content=content_text,
            title=seo_title or topic,
            change_type="finalized",
            change_summary=(
                f"Final revision at quality score {final_quality_score} "
                f"({'passed' if state.get('quality_passing') else 'below threshold'})"
            ),
            model_used=state.get("model_used"),
            quality_score=final_quality_score,
        )
    except Exception as rev_err:
        logger.debug("[content.persist_task] final snapshot failed: %s", rev_err)

    stages = state.get("stages") or {}
    stages["5_post_created"] = False

    return {
        "status": "awaiting_approval",
        "approval_status": "pending",
        "post_id": None,
        "post_slug": None,
        "stages": stages,
        # Publish the assembled metadata onto the pipeline state so the
        # downstream content.record_pipeline_version atom re-asserts the SAME
        # full blob. Without this, record reads an empty task_metadata channel
        # and its upsert shallow-merge-clobbers metadata->>'preview_token'
        # (and pre_approve_content / video_shot_list / featured-image meta)
        # back to {} on the canonical_blog approval-queue row
        # (Glad-Labs/poindexter#693).
        "task_metadata": task_metadata,
    }


__all__ = ["ATOM_META", "run"]
