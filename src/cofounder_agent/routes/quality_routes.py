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
from schemas.quality_schemas import (
    QualityEvaluationRequest,
    QualityDimensionsResponse,
    QualityEvaluationResponse,
    BatchQualityRequest,
)
from utils.service_dependencies import get_quality_service

logger = logging.getLogger(__name__)

quality_router = APIRouter(
    prefix="/api/quality",
    tags=["quality-assessment"]
)


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
    """
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
            context={
                "topic": request.topic,
                "keywords": request.keywords or []
            },
            method=method,
            store_result=request.store_result
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
            evaluated_at=assessment.evaluation_timestamp
        )
        
    except Exception as e:
        logger.error(f"Quality evaluation failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Evaluation failed: {str(e)}"
        )


@quality_router.post(
    "/batch-evaluate",
    summary="Batch evaluate multiple content items",
    description="Evaluate multiple content items in a single request"
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
                    results.append({
                        "index": i,
                        "status": "skipped",
                        "reason": "No content provided"
                    })
                    continue
                
                assessment = await quality_service.evaluate(
                    content=content,
                    context={
                        "topic": item.get("topic"),
                        "keywords": item.get("keywords", [])
                    },
                    method=EvaluationMethod.PATTERN_BASED,
                    store_result=False
                )
                
                if assessment.passing:
                    passed_count += 1
                
                results.append({
                    "index": i,
                    "status": "evaluated",
                    "overall_score": assessment.overall_score,
                    "passing": assessment.passing,
                    "feedback": assessment.feedback,
                })
                
            except Exception as e:
                logger.error(f"Failed to evaluate item {i}: {e}")
                results.append({
                    "index": i,
                    "status": "error",
                    "error": str(e)
                })
        
        return {
            "total_items": len(request.items),
            "evaluated": sum(1 for r in results if r.get("status") == "evaluated"),
            "passed": passed_count,
            "failed": sum(1 for r in results if r.get("status") == "evaluated" and not r.get("passing")),
            "pass_rate": (passed_count / sum(1 for r in results if r.get("status") == "evaluated") * 100) if any(r.get("status") == "evaluated" for r in results) else 0,
            "results": results
        }
        
    except Exception as e:
        logger.error(f"Batch evaluation failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Batch evaluation failed: {str(e)}"
        )


@quality_router.get(
    "/statistics",
    summary="Get quality service statistics",
    description="Retrieve aggregate statistics about quality evaluations"
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
            "message": "Quality service statistics retrieved successfully"
        }
    except Exception as e:
        logger.error(f"Failed to retrieve statistics: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve statistics: {str(e)}"
        )


@quality_router.post(
    "/quick-check",
    summary="Quick quality check",
    description="Perform a quick quality check on content without full evaluation"
)
async def quick_quality_check(
    content: str = Field(
        ...,
        min_length=10,
        max_length=5000,
        description="Content to check"
    ),
    quality_service: UnifiedQualityService = Depends(get_quality_service),
) -> Dict[str, Any]:
    """Quick quality check"""
    try:
        assessment = await quality_service.evaluate(
            content=content,
            method=EvaluationMethod.PATTERN_BASED,
            store_result=False
        )
        
        return {
            "overall_score": assessment.overall_score,
            "passing": assessment.passing,
            "status": "pass" if assessment.passing else "fail",
            "message": assessment.feedback
        }
        
    except Exception as e:
        logger.error(f"Quick check failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Quick check failed: {str(e)}"
        )


# ============================================================================
# REGISTRATION
# ============================================================================

def register_quality_routes(app):
    """Register quality assessment routes with the FastAPI app"""
    app.include_router(quality_router)
    logger.info("✅ Quality assessment routes registered")
