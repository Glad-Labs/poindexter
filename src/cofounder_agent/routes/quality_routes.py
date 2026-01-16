"""
Quality Assessment Routes

Endpoints for evaluating content quality using UnifiedQualityService.

Provides:
- Content evaluation with 7-criteria framework
- Quality statistics and reporting
- Batch evaluation support
- Quality improvement suggestions
"""

import logging
from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Optional, Dict, Any, List

from services.quality_service import UnifiedQualityService, EvaluationMethod, QualityAssessment
from services.qa_style_evaluator import get_style_consistency_validator
from schemas.quality_schemas import (
    QualityEvaluationRequest,
    QualityDimensionsResponse,
    QualityEvaluationResponse,
    BatchQualityRequest,
)
from utils.service_dependencies import get_quality_service

logger = logging.getLogger(__name__)

quality_router = APIRouter(prefix="/api/quality", tags=["quality-assessment"])


# ============================================================================
# ENDPOINTS
# ============================================================================


@quality_router.post(
    "/evaluate",
    response_model=QualityEvaluationResponse,
    summary="Evaluate content quality",
    description="""
    Evaluate content quality using 7-criteria framework:
    1. Clarity - Is content clear and easy to understand?
    2. Accuracy - Is information correct and fact-checked?
    3. Completeness - Does it cover the topic thoroughly?
    4. Relevance - Is all content relevant to the topic?
    5. SEO Quality - Keywords, meta, structure optimization?
    6. Readability - Grammar, flow, formatting?
    7. Engagement - Is content compelling and interesting?
    
    Overall Score Interpretation:
    - 8.5+ : Excellent - Publication ready
    - 7.5-8.4 : Good - Minor improvements recommended
    - 7.0-7.4 : Acceptable - Some improvements suggested
    - 6.0-6.9 : Fair - Significant improvements needed
    - <6.0 : Poor - Major revisions required
    """,
)
async def evaluate_content_quality(
    request: QualityEvaluationRequest,
    quality_service: UnifiedQualityService = Depends(get_quality_service),
) -> QualityEvaluationResponse:
    """Evaluate content quality"""
    try:
        logger.info(f"Evaluating content ({request.method}): {len(request.content)} chars")

        # Map method string to enum
        method_map = {
            "pattern-based": EvaluationMethod.PATTERN_BASED,
            "llm-based": EvaluationMethod.LLM_BASED,
            "hybrid": EvaluationMethod.HYBRID,
        }
        method = method_map.get(request.method.lower(), EvaluationMethod.PATTERN_BASED)

        # Run evaluation
        assessment = await quality_service.evaluate(
            content=request.content,
            context={"topic": request.topic, "keywords": request.keywords or []},
            method=method,
            store_result=request.store_result,
        )

        return QualityEvaluationResponse(
            overall_score=assessment.overall_score,
            passing=assessment.passing,
            dimensions=QualityDimensionsResponse(**assessment.dimensions.to_dict()),
            feedback=assessment.feedback,
            suggestions=assessment.suggestions,
            evaluation_method=assessment.evaluation_method.value,
            content_length=assessment.content_length or len(request.content),
            word_count=assessment.word_count or len(request.content.split()),
            evaluated_at=assessment.evaluation_timestamp,
        )

    except Exception as e:
        logger.error(f"Quality evaluation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {str(e)}")


@quality_router.post(
    "/batch-evaluate",
    summary="Batch evaluate multiple content items",
    description="Evaluate multiple content items in a single request",
)
async def batch_evaluate_quality(
    request: BatchQualityRequest,
    quality_service: UnifiedQualityService = Depends(get_quality_service),
) -> Dict[str, Any]:
    """Evaluate multiple content items"""
    try:
        logger.info(f"Batch evaluating {len(request.items)} items")

        results = []
        passed_count = 0

        for i, item in enumerate(request.items):
            try:
                content = item.get("content", "")
                if not content:
                    logger.warning(f"Item {i} has no content, skipping")
                    results.append(
                        {"index": i, "status": "skipped", "reason": "No content provided"}
                    )
                    continue

                assessment = await quality_service.evaluate(
                    content=content,
                    context={"topic": item.get("topic"), "keywords": item.get("keywords", [])},
                    method=EvaluationMethod.PATTERN_BASED,
                    store_result=False,
                )

                if assessment.passing:
                    passed_count += 1

                results.append(
                    {
                        "index": i,
                        "status": "evaluated",
                        "overall_score": assessment.overall_score,
                        "passing": assessment.passing,
                        "feedback": assessment.feedback,
                    }
                )

            except Exception as e:
                logger.error(f"Failed to evaluate item {i}: {e}")
                results.append({"index": i, "status": "error", "error": str(e)})

        return {
            "total_items": len(request.items),
            "evaluated": sum(1 for r in results if r.get("status") == "evaluated"),
            "passed": passed_count,
            "failed": sum(
                1 for r in results if r.get("status") == "evaluated" and not r.get("passing")
            ),
            "pass_rate": (
                (passed_count / sum(1 for r in results if r.get("status") == "evaluated") * 100)
                if any(r.get("status") == "evaluated" for r in results)
                else 0
            ),
            "results": results,
        }

    except Exception as e:
        logger.error(f"Batch evaluation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Batch evaluation failed: {str(e)}")


@quality_router.get(
    "/statistics",
    summary="Get quality service statistics",
    description="Retrieve aggregate statistics about quality evaluations",
)
async def get_quality_statistics(
    quality_service: UnifiedQualityService = Depends(get_quality_service),
) -> Dict[str, Any]:
    """Get quality service statistics"""
    try:
        stats = quality_service.get_statistics()
        return {
            "statistics": stats,
            "retrieved_at": datetime.utcnow().isoformat(),
            "message": "Quality service statistics retrieved successfully",
        }
    except Exception as e:
        logger.error(f"Failed to retrieve statistics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve statistics: {str(e)}")


@quality_router.post(
    "/quick-check",
    summary="Quick quality check",
    description="Perform a quick quality check on content without full evaluation",
)
async def quick_quality_check(
    content: str = Field(..., min_length=10, max_length=5000, description="Content to check"),
    quality_service: UnifiedQualityService = Depends(get_quality_service),
) -> Dict[str, Any]:
    """Quick quality check"""
    try:
        assessment = await quality_service.evaluate(
            content=content, method=EvaluationMethod.PATTERN_BASED, store_result=False
        )

        return {
            "overall_score": assessment.overall_score,
            "passing": assessment.passing,
            "status": "pass" if assessment.passing else "fail",
            "message": assessment.feedback,
        }

    except Exception as e:
        logger.error(f"Quick check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Quick check failed: {str(e)}")


# ============================================================================
# STYLE EVALUATION ENDPOINTS (Phase 3.5)
# ============================================================================


@quality_router.post(
    "/evaluate-style-consistency",
    summary="Evaluate style consistency",
    description="""
    Evaluate if generated content matches a reference writing style.
    
    Checks:
    - Tone consistency (formal, casual, authoritative, conversational)
    - Vocabulary diversity match
    - Sentence structure similarity
    - Formatting element usage
    
    Returns detailed breakdown with pass/fail determination (>= 0.75).
    """,
)
async def evaluate_style_consistency(
    generated_content: str,
    reference_metrics: Optional[Dict[str, Any]] = None,
    reference_style: Optional[str] = None,
    reference_tone: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Evaluate style consistency of generated content against reference metrics.

    Args:
        generated_content: The content to evaluate
        reference_metrics: Metrics from reference writing sample (Phase 3.3)
        reference_style: Expected style (technical, narrative, listicle, etc.)
        reference_tone: Expected tone (formal, casual, authoritative, conversational)

    Returns:
        Dict with style consistency evaluation results
    """
    try:
        validator = get_style_consistency_validator()

        result = await validator.validate_style_consistency(
            generated_content=generated_content,
            reference_metrics=reference_metrics,
            reference_style=reference_style,
            reference_tone=reference_tone,
        )

        logger.info(f"✅ Style consistency evaluation: {result.style_consistency_score:.2f}/1.0")

        return {
            "style_consistency_score": round(result.style_consistency_score, 3),
            "component_scores": {
                "tone_consistency": round(result.tone_consistency_score, 3),
                "vocabulary": round(result.vocabulary_score, 3),
                "sentence_structure": round(result.sentence_structure_score, 3),
                "formatting": round(result.formatting_score, 3),
            },
            "style_analysis": {
                "detected_style": result.detected_style,
                "detected_tone": result.detected_tone,
                "reference_style": result.reference_style,
                "reference_tone": result.reference_tone,
            },
            "metrics": {
                "avg_sentence_length": round(result.avg_sentence_length, 2),
                "avg_word_length": round(result.avg_word_length, 2),
                "vocabulary_diversity": round(result.vocabulary_diversity, 3),
            },
            "passing": result.passing,
            "issues": result.issues,
            "suggestions": result.suggestions,
        }

    except Exception as e:
        logger.error(f"Style consistency evaluation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {str(e)}")


@quality_router.post(
    "/verify-tone-consistency",
    summary="Verify tone consistency throughout content",
    description="""
    Verify that the generated content maintains consistent tone throughout.
    
    Analyzes:
    - Tone markers presence
    - Formality level consistency
    - Language register maintenance
    - Detected tone vs. expected tone
    
    Returns tone consistency score and specific feedback.
    """,
)
async def verify_tone_consistency(
    content: str,
    expected_tone: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Verify tone consistency in content.

    Args:
        content: Content to analyze
        expected_tone: Expected tone (formal, casual, authoritative, conversational)

    Returns:
        Dict with tone analysis results
    """
    try:
        validator = get_style_consistency_validator()

        # Analyze content
        metrics = validator._analyze_content(content)
        detected_tone = validator._detect_tone(content)

        # Calculate consistency
        tone_score = validator._calculate_tone_consistency(detected_tone, expected_tone, metrics)

        # Identify tone-specific issues
        issues = []
        if expected_tone and detected_tone != expected_tone:
            issues.append(
                f"Detected tone '{detected_tone}' differs from expected '{expected_tone}'"
            )

        logger.info(f"✅ Tone consistency verification: {tone_score:.2f}/1.0")

        return {
            "detected_tone": detected_tone,
            "expected_tone": expected_tone,
            "consistency_score": round(tone_score, 3),
            "passing": tone_score >= 0.75,
            "issues": issues,
            "metrics": {
                "word_count": metrics["word_count"],
                "sentence_count": metrics["sentence_count"],
                "avg_sentence_length": round(metrics["avg_sentence_length"], 2),
                "vocabulary_diversity": round(metrics["vocabulary_diversity"], 3),
            },
        }

    except Exception as e:
        logger.error(f"Tone consistency verification failed: {e}")
        raise HTTPException(status_code=500, detail=f"Verification failed: {str(e)}")


@quality_router.post(
    "/evaluate-style-metrics",
    summary="Evaluate style-specific metrics",
    description="""
    Evaluate style-specific scoring metrics for the generated content.
    
    Calculates:
    - Style match percentage
    - Tone match percentage
    - Vocabulary alignment
    - Structure similarity
    - Overall style score
    
    Useful for detailed content analysis before QA verification.
    """,
)
async def evaluate_style_metrics(
    content: str,
    content_style: Optional[str] = None,
    reference_metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Evaluate detailed style metrics for content.

    Args:
        content: Content to evaluate
        content_style: The intended style of the content
        reference_metrics: Optional reference sample metrics for comparison

    Returns:
        Dict with detailed style metrics
    """
    try:
        validator = get_style_consistency_validator()

        # Analyze content
        metrics = validator._analyze_content(content)
        detected_style = validator._detect_style(content)
        detected_tone = validator._detect_tone(content)

        # Calculate style-specific scores
        vocab_score = validator._calculate_vocabulary_consistency(metrics, reference_metrics)

        sentence_score = validator._calculate_sentence_structure_consistency(
            metrics, reference_metrics
        )

        format_score = validator._calculate_formatting_consistency(content, reference_metrics)

        # Overall style score
        overall_style_score = vocab_score * 0.4 + sentence_score * 0.35 + format_score * 0.25

        logger.info(f"✅ Style metrics evaluation: {overall_style_score:.2f}/1.0")

        return {
            "detected_style": detected_style,
            "detected_tone": detected_tone,
            "intended_style": content_style,
            "style_match": content_style == detected_style if content_style else None,
            "overall_style_score": round(overall_style_score, 3),
            "component_scores": {
                "vocabulary_alignment": round(vocab_score, 3),
                "structure_similarity": round(sentence_score, 3),
                "formatting_consistency": round(format_score, 3),
            },
            "content_characteristics": {
                "word_count": metrics["word_count"],
                "sentence_count": metrics["sentence_count"],
                "paragraph_count": metrics["paragraph_count"],
                "avg_word_length": round(metrics["avg_word_length"], 2),
                "avg_sentence_length": round(metrics["avg_sentence_length"], 2),
                "avg_paragraph_length": round(metrics["avg_paragraph_length"], 2),
                "vocabulary_diversity": round(metrics["vocabulary_diversity"], 3),
            },
            "formatting_elements": {
                "has_lists": metrics["has_lists"],
                "has_code_blocks": metrics["has_code_blocks"],
                "has_headings": metrics["has_headings"],
                "has_quotes": metrics["has_quotes"],
            },
        }

    except Exception as e:
        logger.error(f"Style metrics evaluation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {str(e)}")


def register_quality_routes(app):
    """Register quality assessment routes with the FastAPI app"""
    app.include_router(quality_router)
    logger.info("✅ Quality assessment routes registered")
