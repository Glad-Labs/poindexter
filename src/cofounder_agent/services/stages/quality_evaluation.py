"""QualityEvaluationStage — stage 2B of the content pipeline.

Runs a fast pattern-based QA evaluator over the freshly-generated draft
and records the scores on the context. A deeper multi-model QA can
optionally run later in the pipeline.

Ports ``_stage_quality_evaluation`` from content_router_service.py.
Preserves observable behavior:
- Same ``EvaluationMethod.PATTERN_BASED`` evaluator
- Same context shape (``topic`` / ``keywords`` / ``audience``)
- Same result keys on the shared context
- Same "no result" behavior (raise ValueError, halts the pipeline)

## Context reads

- ``topic`` (str)
- ``tags`` (list[str] — falls back to [topic] if empty, matches legacy)
- ``content`` (str — populated by generate_content)
- ``database_service`` — used to construct ``UnifiedQualityService``

## Context writes

- ``quality_score`` (float)
- ``quality_passing`` (bool)
- ``truncation_detected`` (bool)
- ``quality_details_initial`` (dict of per-dimension scores)
- ``quality_result`` (the full result object, for downstream stages)
- ``stages["2b_quality_evaluated_initial"] = True``
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.stage import StageResult

logger = logging.getLogger(__name__)


class QualityEvaluationStage:
    name = "quality_evaluation"
    description = "Pattern-based quality evaluation of the generated draft"
    # Legacy _get_stage_timeout("quality_evaluation") returned 180s.
    timeout_seconds = 180
    # Legacy raised RuntimeError on timeout (marked "critical"). Halt.
    halts_on_failure = True

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        from services.quality_service import EvaluationMethod, UnifiedQualityService

        topic = context.get("topic", "")
        tags = context.get("tags") or []
        content_text = context.get("content", "")
        database_service = context.get("database_service")

        if not content_text:
            return StageResult(
                ok=False,
                detail="context missing content (run generate_content first)",
            )

        logger.info("STAGE 2B: Early quality evaluation...")

        # Orchestrator-level constructor — cheap, each invocation gets a
        # fresh instance (matches legacy behavior). Reusing would be a
        # future optimization but not part of this migration.
        quality_service = UnifiedQualityService(database_service=database_service)

        quality_result = await quality_service.evaluate(
            content=content_text,
            context={
                "topic": topic,
                "keywords": tags or [topic],
                "audience": "General",
            },
            method=EvaluationMethod.PATTERN_BASED,
        )

        if not quality_result:
            logger.error("Quality evaluation returned None")
            raise ValueError("Quality evaluation failed: no result produced")

        stages = context.setdefault("stages", {})
        stages["2b_quality_evaluated_initial"] = True

        details = {
            "clarity": quality_result.dimensions.clarity,
            "accuracy": quality_result.dimensions.accuracy,
            "completeness": quality_result.dimensions.completeness,
            "relevance": quality_result.dimensions.relevance,
            "seo_quality": quality_result.dimensions.seo_quality,
            "readability": quality_result.dimensions.readability,
            "engagement": quality_result.dimensions.engagement,
            "truncation_detected": quality_result.truncation_detected,
        }

        logger.info("Initial quality evaluation complete:")
        logger.info("   Overall Score: %.1f/100", quality_result.overall_score)
        logger.info("   Passing: %s (threshold >=70.0)", quality_result.passing)
        if quality_result.truncation_detected:
            logger.warning("   TRUNCATION DETECTED -- content appears cut off mid-sentence")

        return StageResult(
            ok=True,
            detail=f"score={quality_result.overall_score:.1f} passing={quality_result.passing}",
            context_updates={
                "quality_score": quality_result.overall_score,
                "quality_passing": quality_result.passing,
                "truncation_detected": quality_result.truncation_detected,
                "quality_details_initial": details,
                "quality_result": quality_result,
                "stages": stages,
            },
            metrics={
                "overall_score": quality_result.overall_score,
                "passing": quality_result.passing,
                "truncation_detected": quality_result.truncation_detected,
            },
        )
