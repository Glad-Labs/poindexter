"""
Natural Language Content Routes

Endpoints for processing content requests via natural language using UnifiedOrchestrator.

These endpoints complement the existing structured content_routes.py by allowing:
1. Natural language input (e.g., "Create a blog post about AI marketing")
2. Automatic request type detection
3. Intelligent routing to appropriate handlers
4. Quality assessment integration

Usage:
- POST /api/content/natural-language  - Process natural language request
- GET /api/content/natural-language/{task_id} - Get task status
- POST /api/content/natural-language/{task_id}/refine - Refine content
"""

import logging
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime

from services.unified_orchestrator import UnifiedOrchestrator, RequestType
from services.quality_service import UnifiedQualityService, EvaluationMethod
from services.database_service import DatabaseService
from utils.service_dependencies import (
    get_unified_orchestrator,
    get_quality_service,
    get_database_service,
)
from schemas.natural_language_schemas import (
    NaturalLanguageRequest,
    RefineContentRequest,
    NaturalLanguageResponse,
)

logger = logging.getLogger(__name__)

nl_content_router = APIRouter(
    prefix="/api/content/natural-language", tags=["content-natural-language"]
)


# ============================================================================
# ENDPOINTS
# ============================================================================


@nl_content_router.post(
    "",
    response_model=NaturalLanguageResponse,
    summary="Process natural language content request",
    description="""
    Process a natural language request for content operations.
    
    The system automatically:
    1. Understands what you're asking for
    2. Determines the type of request (create content, refine, etc.)
    3. Routes to the appropriate handler
    4. Evaluates quality if requested
    5. Returns the result
    
    Examples:
    - "Create a blog post about AI marketing trends"
    - "Write a LinkedIn article on remote work challenges"
    - "Research benefits of machine learning in healthcare"
    """,
)
async def process_natural_language_request(
    request: NaturalLanguageRequest,
    orchestrator: UnifiedOrchestrator = Depends(get_unified_orchestrator),
    quality_service: UnifiedQualityService = Depends(get_quality_service),
) -> NaturalLanguageResponse:
    """
    Process natural language content request using unified orchestrator
    """
    try:
        logger.info(f"Processing natural language request: {request.prompt[:100]}")

        # Process through unified orchestrator
        result = await orchestrator.process_request(
            user_input=request.prompt, context=request.context or {}
        )

        # Extract result components
        status = result.get("status", "unknown")
        output = result.get("output", "")
        task_id = result.get("task_id")
        request_type = result.get("request_type", "unknown")

        quality_assessment = None

        # Run quality assessment if requested and content was generated
        if request.auto_quality_check and output and isinstance(output, str):
            try:
                assessment = await quality_service.evaluate(
                    content=output,
                    context=request.context or {"topic": request.prompt},
                    method=EvaluationMethod.PATTERN_BASED,
                )
                quality_assessment = assessment.to_dict()
                logger.info(f"Quality assessment complete: {assessment.overall_score:.1f}/10")
            except Exception as e:
                logger.warning(f"Quality assessment failed: {e}")

        return NaturalLanguageResponse(
            request_id=result.get("request_id", "unknown"),
            status=status,
            request_type=request_type,
            task_id=task_id,
            output=output if isinstance(output, str) else str(output)[:5000],
            quality=quality_assessment,
            message=f"Request processed successfully as {request_type}",
            created_at=datetime.utcnow(),
        )

    except Exception as e:
        logger.error(f"Natural language processing failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to process request: {str(e)}")


@nl_content_router.get(
    "/{task_id}",
    response_model=Dict[str, Any],
    summary="Get natural language task status",
    description="Retrieve the status and results of a natural language content request",
)
async def get_natural_language_task(
    task_id: str,
    db_service: DatabaseService = Depends(get_database_service),
) -> Dict[str, Any]:
    """
    Get status of a natural language content generation task
    """
    try:
        logger.info(f"Retrieving task status: {task_id}")

        # Get task from database
        task = await db_service.get_task(task_id)

        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        return {
            "task_id": task_id,
            "status": task.get("status", "unknown"),
            "type": task.get("type", "unknown"),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "result": task.get("result"),
            "metadata": task.get("metadata", {}),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to retrieve task: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve task: {str(e)}")


@nl_content_router.post(
    "/{task_id}/refine",
    response_model=NaturalLanguageResponse,
    summary="Refine generated content",
    description="Submit feedback to refine previously generated content",
)
async def refine_natural_language_content(
    task_id: str,
    refinement: RefineContentRequest,
    orchestrator: UnifiedOrchestrator = Depends(get_unified_orchestrator),
    db_service: DatabaseService = Depends(get_database_service),
) -> NaturalLanguageResponse:
    """
    Refine content with feedback
    """
    try:
        logger.info(f"Refining task {task_id}: {refinement.feedback[:100]}")

        # Get original task
        task = await db_service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Generate refinement request
        original_prompt = task.get("metadata", {}).get("original_prompt", "")
        refinement_prompt = f"""
        Original request: {original_prompt}
        
        Feedback: {refinement.feedback}
        
        {"Focus area: " + refinement.focus_area if refinement.focus_area else ""}
        
        Please refine the content based on this feedback.
        """

        # Process refinement through orchestrator
        result = await orchestrator.process_request(
            user_input=refinement_prompt,
            context={
                "refinement": True,
                "original_task_id": task_id,
            },
        )

        return NaturalLanguageResponse(
            request_id=result.get("request_id", "unknown"),
            status=result.get("status", "unknown"),
            request_type=result.get("request_type", "unknown"),
            task_id=result.get("task_id"),
            output=result.get("output"),
            quality=result.get("quality"),
            message="Content refinement completed",
            created_at=datetime.utcnow(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Content refinement failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to refine content: {str(e)}")


# ============================================================================
# REGISTRATION
# ============================================================================


def register_nl_content_routes(app):
    """Register natural language content routes with the FastAPI app"""
    app.include_router(nl_content_router)
    logger.info("✅ Natural language content routes registered")
