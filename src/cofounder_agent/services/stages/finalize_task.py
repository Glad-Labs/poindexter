"""FinalizeTaskStage — stage 7 of the content pipeline.

Writes the final ``content_tasks`` row with ``status='awaiting_approval'``
and the full metadata blob the approval endpoint reads.

Ports ``_stage_finalize_task``. Preserves the important design note:
Posts rows are NOT created here — they're created when the task is
approved via ``POST /api/tasks/{task_id}/approve``. Keeps generation
and publishing cleanly separate.

## Context reads

All the fields downstream approval consumers need:
- ``task_id``, ``topic``, ``style``, ``tone``, ``content``
- ``quality_result``, ``quality_score`` (optional; falls back)
- ``seo_title``, ``seo_description``, ``seo_keywords`` / ``seo_keywords_list``
- ``category``, ``target_audience``
- ``title``, ``featured_image_url``, ``featured_image_*`` metadata
- ``podcast_script``, ``video_scenes``, ``short_summary_script``
- ``database_service``

## Context writes

- ``status = "awaiting_approval"``
- ``approval_status = "pending"``
- ``stages["5_post_created"] = False``  (legacy key — posts deferred)
- ``post_id = None``, ``post_slug = None``
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.stage import StageResult

logger = logging.getLogger(__name__)


class FinalizeTaskStage:
    name = "finalize_task"
    description = "Persist the awaiting_approval record with full task metadata"
    timeout_seconds = 60
    halts_on_failure = True  # Last stage — must succeed or task is stuck.

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        from services.text_utils import normalize_text as _normalize_text

        task_id = context.get("task_id")
        database_service = context.get("database_service")
        quality_result = context.get("quality_result")

        if not task_id or database_service is None:
            return StageResult(
                ok=False,
                detail="context missing task_id or database_service",
            )

        topic = context.get("topic", "")
        style = context.get("style", "")
        tone = context.get("tone", "")
        content_text = context.get("content", "")
        category = context.get("category", "")
        target_audience = context.get("target_audience")

        # Legacy: stage 5 (posts record creation) is INTENTIONALLY skipped here.
        logger.info("STAGE 5: Posts record creation SKIPPED")
        logger.info("   Posts will be created when task is approved by user")

        stages = context.setdefault("stages", {})
        stages["5_post_created"] = False

        # Normalize text fields before persisting.
        seo_title = context.get("seo_title") or ""
        seo_description = context.get("seo_description") or ""
        if seo_title:
            seo_title = _normalize_text(seo_title)
        if seo_description:
            seo_description = _normalize_text(seo_description)
        content_text = _normalize_text(content_text)

        # Quality score: prefer the multi-model QA score if set; fall
        # back to the early pattern-eval when QA ran nothing (or timed out).
        qa_score_from_context = context.get("quality_score")
        early_eval_score = (
            quality_result.overall_score if quality_result else 0
        )
        final_quality_score = int(round(float(
            qa_score_from_context if qa_score_from_context is not None
            else early_eval_score
        )))

        # seo_keywords: accept either a pre-built comma-joined string or
        # a list — the legacy finalize accepted both.
        seo_keywords_raw = context.get("seo_keywords")
        if isinstance(seo_keywords_raw, list):
            seo_keywords_string = ", ".join(seo_keywords_raw)
            seo_keywords_list = seo_keywords_raw
        elif isinstance(seo_keywords_raw, str):
            seo_keywords_string = seo_keywords_raw
            seo_keywords_list = context.get("seo_keywords_list") or []
        else:
            seo_keywords_string = ""
            seo_keywords_list = []

        task_metadata = {
            "featured_image_url": context.get("featured_image_url"),
            "featured_image_alt": context.get("featured_image_alt", ""),
            "featured_image_width": context.get("featured_image_width"),
            "featured_image_height": context.get("featured_image_height"),
            "featured_image_photographer": context.get("featured_image_photographer"),
            "featured_image_source": context.get("featured_image_source"),
            "content": content_text,
            "seo_title": seo_title,
            "seo_description": seo_description,
            "seo_keywords": seo_keywords_list,
            "topic": topic,
            "style": style,
            "tone": tone,
            "category": category,
            "target_audience": target_audience or "General",
            "post_id": None,
            "quality_score": final_quality_score,
            "quality_score_early_eval": early_eval_score,
            "qa_final_score": context.get("qa_final_score"),
            "content_length": len(content_text),
            "word_count": len(content_text.split()),
            "podcast_script": context.get("podcast_script", ""),
            "video_scenes": context.get("video_scenes", []),
            "short_summary_script": context.get("short_summary_script", ""),
        }

        updates = {
            "status": "awaiting_approval",
            "approval_status": "pending",
            # Clear stale error_message from any prior auto-cancel attempt.
            "error_message": None,
            "quality_score": final_quality_score,
            "title": (
                context.get("title") or seo_title or topic
            ),
            "featured_image_url": context.get("featured_image_url"),
            "seo_title": seo_title,
            "seo_description": seo_description,
            "seo_keywords": seo_keywords_string,
            "style": style,
            "tone": tone,
            "category": category,
            "target_audience": target_audience or "General",
            "task_metadata": task_metadata,
        }

        await database_service.update_task(task_id=task_id, updates=updates)

        return StageResult(
            ok=True,
            detail="task finalized → awaiting_approval",
            context_updates={
                "status": "awaiting_approval",
                "approval_status": "pending",
                "post_id": None,
                "post_slug": None,
                "stages": stages,
            },
            metrics={
                "final_quality_score": final_quality_score,
                "word_count": len(content_text.split()),
            },
        )
