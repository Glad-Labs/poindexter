"""CaptureTrainingDataStage — stage 6 of the content pipeline.

Writes the finished draft + quality score + context into two tables
that feed the learning pipeline: ``quality_evaluations`` (per-draft QA
record) and ``orchestrator_training_data`` (aggregate intent → outcome
rows for later model distillation).

Ports ``_stage_capture_training_data``. Entirely non-critical — both
writes are wrapped in try/except so a DB error can't crash the pipeline.

## Context reads

- ``task_id``, ``topic``, ``style``, ``tone``, ``target_length`` (int),
  ``tags`` (list), ``content``, ``quality_result``, ``featured_image``,
  ``database_service``

## Context writes

- ``stages["6_training_data_captured"] = True``
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.stage import StageResult

logger = logging.getLogger(__name__)


class CaptureTrainingDataStage:
    name = "capture_training_data"
    description = "Persist QA scores + training rows for the learning pipeline"
    timeout_seconds = 60
    halts_on_failure = False  # Legacy: "entire stage is non-critical".

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        task_id = context.get("task_id")
        topic = context.get("topic", "")
        style = context.get("style", "")
        tone = context.get("tone", "")
        target_length = int(context.get("target_length", 0))
        tags = context.get("tags") or []
        content_text = context.get("content", "")
        quality_result = context.get("quality_result")
        featured_image = context.get("featured_image")
        database_service = context.get("database_service")

        if not task_id or database_service is None or quality_result is None:
            return StageResult(
                ok=False,
                detail="context missing task_id / database_service / quality_result",
            )

        logger.info("STAGE 6: Capturing training data...")

        word_count = len(content_text.split())
        paragraph_count = len([p for p in content_text.split("\n\n") if p.strip()])
        sentences = [s.strip() for s in content_text.split(".") if s.strip()]
        avg_sentence_length = (
            len(sentences) / word_count if word_count > 0 else 0
        )

        qa_payload = {
            "content_id": task_id,
            "task_id": task_id,
            "overall_score": quality_result.overall_score,
            "clarity": quality_result.dimensions.clarity,
            "accuracy": quality_result.dimensions.accuracy,
            "completeness": quality_result.dimensions.completeness,
            "relevance": quality_result.dimensions.relevance,
            "seo_quality": quality_result.dimensions.seo_quality,
            "readability": quality_result.dimensions.readability,
            "engagement": quality_result.dimensions.engagement,
            "passing": quality_result.passing,
            "feedback": getattr(quality_result, "feedback", None),
            "suggestions": getattr(quality_result, "suggestions", None),
            "evaluated_by": "ContentQualityService",
            "evaluation_method": getattr(quality_result, "evaluation_method", None),
            "content_length": len(content_text),
            "content": content_text,
            "context_data": {
                "topic": topic,
                "style": style,
                "tone": tone,
                "target_length": target_length,
                "has_featured_image": featured_image is not None,
                "readability_metrics": {
                    "word_count": word_count,
                    "paragraph_count": paragraph_count,
                    "average_sentence_length": round(avg_sentence_length, 2),
                    "sentence_count": len(sentences),
                },
            },
        }

        try:
            await database_service.create_quality_evaluation(qa_payload)
        except Exception as qe_err:
            logger.warning(
                "Quality evaluation insert failed (non-critical): %s", qe_err,
            )

        # create_orchestrator_training_data expects a 0.0-1.0 normalized score
        # (quality_result.overall_score is 0-100 from the QA evaluator).
        normalized_score = min(quality_result.overall_score / 100, 1.0)
        training_payload = {
            "execution_id": task_id,
            "user_request": f"Generate blog post on: {topic}",
            "intent": "content_generation",
            "business_state": {
                "topic": topic,
                "style": style,
                "tone": tone,
                "featured_image": featured_image is not None,
            },
            "execution_result": "success",
            "quality_score": normalized_score,
            "success": quality_result.passing,
            "tags": tags,
            "source_agent": "content_router_service",
        }

        try:
            await database_service.create_orchestrator_training_data(training_payload)
        except Exception as td_err:
            logger.warning(
                "Training data insert skipped (likely re-processed task): %s",
                td_err,
            )

        stages = context.setdefault("stages", {})
        stages["6_training_data_captured"] = True

        logger.info("Training data captured for learning pipeline")

        return StageResult(
            ok=True,
            detail="training data captured",
            context_updates={"stages": stages},
            metrics={
                "word_count": word_count,
                "quality_score": quality_result.overall_score,
            },
        )
