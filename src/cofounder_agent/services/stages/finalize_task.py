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

        # Phase 6 / GH#54: if the writer cited external URLs inline but
        # didn't append a Sources section, add one automatically. Readers
        # + search engines both benefit from an explicit list, and it's
        # a low-risk idempotent transform (existing sections left alone).
        try:
            from services.citation_verifier import (
                append_sources_section,
                extract_urls,
            )
            # Phase H step 4.3 (GH#95): read site_config from the pipeline
            # context instead of reaching for the module-level singleton.
            _sc_sources = context.get("site_config")
            if _sc_sources is None:
                # Transitional fallback — removed in Phase H step 5 when
                # the singleton is deleted.
                from services.site_config import site_config as _sc_sources
            if (_sc_sources.get("auto_append_sources_section", "true") or "true").lower() not in ("false", "0", "no"):
                _site_url = _sc_sources.get("site_url") or None
                _urls = extract_urls(content_text, site_url=_site_url)
                if _urls:
                    content_text = append_sources_section(content_text, _urls)
        except Exception as _sources_err:
            logger.debug(
                "[finalize_task] Sources-section auto-append skipped (non-fatal): %s",
                _sources_err,
            )

        # GH-86: derive an excerpt from the finalized content. Frontend was
        # falling back to content[:N] which rendered the opening "What
        # You'll Learn" bullet list as the social-card snippet.
        #
        # The title argument lets generate_excerpt reject excerpts that are
        # just the title repeated — a common degenerate case when the
        # opening paragraph starts with "# <title>". Pass seo_title (what
        # the page actually shows) with topic as the fallback seed.
        from services.excerpt_generator import generate_excerpt
        excerpt_text = generate_excerpt(
            title=seo_title or topic,
            content=content_text,
        )

        # GH-86: format the multi-model QA reviewers' feedback into human-readable
        # text so approvers can see *why* a post scored Q85 vs Q88. Looks at both
        # the quality_result (MultiModelResult) and any serialized qa_reviews list.
        from services.multi_model_qa import format_qa_feedback_from_reviews
        qa_reviews = context.get("qa_reviews") or []
        qa_feedback_text = ""
        if quality_result is not None and hasattr(quality_result, "format_feedback_text"):
            qa_feedback_text = quality_result.format_feedback_text()
        elif qa_reviews:
            qa_feedback_text = format_qa_feedback_from_reviews(
                qa_reviews,
                final_score=context.get("qa_final_score"),
                approved=context.get("qa_approved"),
            )

        # Quality score: prefer the multi-model QA score if set; fall
        # back to the early pattern-eval when QA ran nothing (or timed out).
        qa_score_from_context = context.get("quality_score")
        early_eval_score = (
            quality_result.overall_score if quality_result else 0
        )
        final_quality_score = round(float(
            qa_score_from_context if qa_score_from_context is not None
            else early_eval_score
        ))

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
            # GH-86: persist excerpt + qa_feedback on the base row.
            "excerpt": excerpt_text,
            "qa_feedback": qa_feedback_text,
            "task_metadata": task_metadata,
        }

        # GH-90 AC #3: before writing the terminal ``awaiting_approval`` status
        # use a status-guarded update to verify the row is still in a live
        # state. If the sweeper (or a manual cancel) already flipped it to
        # ``failed``/``cancelled``/``rejected`` the guard returns None and
        # we abort — don't resurrect a cancelled task with generated
        # content, don't emit downstream webhooks that would publish a
        # ghost post. The stage result is logged as ``ok=False`` so the
        # runner halts (halts_on_failure=True).
        guard_result = None
        if hasattr(database_service, "update_task_status_guarded"):
            try:
                guard_result = await database_service.update_task_status_guarded(
                    task_id=task_id,
                    new_status="awaiting_approval",
                    allowed_from=("in_progress", "pending"),
                )
            except Exception as _guard_err:
                logger.warning(
                    "[GH-90] finalize_task status-guard raised — falling back "
                    "to update_task: %s", _guard_err,
                )
                guard_result = "fallback"  # proceed with legacy update
        else:
            guard_result = "fallback"  # database_service doesn't expose the guard

        if guard_result is None:
            logger.error(
                "[GH-90] finalize_task ABORTED: task %s is no longer in "
                "in_progress/pending — sweeper or operator cancelled it "
                "mid-stage. Content + image + QA results will NOT be "
                "persisted to the approval queue.",
                task_id,
            )
            return StageResult(
                ok=False,
                detail=(
                    "aborted: task is no longer in pending/in_progress — "
                    "race with stale-task sweeper (GH-90)"
                ),
                continue_workflow=False,
                metrics={
                    "final_quality_score": final_quality_score,
                    "word_count": len(content_text.split()),
                    "aborted_by_status_guard": True,
                },
            )

        await database_service.update_task(task_id=task_id, updates=updates)

        # Snapshot the finalized draft so the feedback loop has a clear
        # terminal row per task (gitea#271 Phase 3.A2). The initial draft
        # + any QA rewrite iterations precede this row, so the diff chain
        # tells the full story.
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
                    f"({'passed' if context.get('quality_passing') else 'below threshold'})"
                ),
                model_used=context.get("model_used"),
                quality_score=final_quality_score,
            )
        except Exception as rev_err:
            logger.debug("[content_revisions] final snapshot failed: %s", rev_err)

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
