"""
Workflow REST API Endpoints (Phase 4)

Provides HTTP endpoints for executing workflows via the UnifiedWorkflowRouter
and NLPIntentRecognizer. Supports both structured and natural language requests.

Endpoints:
  POST   /api/workflows/execute           - Execute structured workflow request
  POST   /api/workflows/execute-from-nl   - Execute natural language workflow
  POST   /api/intent/recognize            - Recognize intent from natural language
  GET    /api/workflows/list              - List available workflows
  GET    /api/workflows/{workflow_id}     - Get workflow execution status

Type Hints: 100% coverage
Error Handling: Comprehensive with proper HTTP status codes
Async: Full async/await support
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, Query, Path, Body
from pydantic import BaseModel, Field, validator

from src.cofounder_agent.services.workflow_router import UnifiedWorkflowRouter
from src.cofounder_agent.services.nlp_intent_recognizer import NLPIntentRecognizer, IntentMatch
from src.cofounder_agent.services.pipeline_executor import WorkflowResponse, WorkflowRequest
from src.cofounder_agent.tasks import TaskStatus

# Initialize router and services
router = APIRouter(prefix="/api", tags=["workflows"])
workflow_router = UnifiedWorkflowRouter()
nlp_recognizer = NLPIntentRecognizer()
logger = logging.getLogger(__name__)


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class WorkflowExecutionRequest(BaseModel):
    """Request model for structured workflow execution."""
    
    workflow_type: str = Field(
        ...,
        description="Type of workflow: content_generation, social_media, financial_analysis, market_analysis, compliance_check, performance_review"
    )
    input_data: Dict[str, Any] = Field(
        ...,
        description="Input parameters for the workflow"
    )
    user_id: str = Field(
        ...,
        description="User ID for tracking and context"
    )
    custom_pipeline: Optional[List[str]] = Field(
        default=None,
        description="Optional custom task pipeline to override default"
    )
    execution_options: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional execution options (timeouts, retries, etc.)"
    )

    @validator("workflow_type")
    def validate_workflow_type(cls, v: str) -> str:
        """Validate workflow type is supported."""
        valid_types = {
            "content_generation",
            "social_media",
            "financial_analysis",
            "market_analysis",
            "compliance_check",
            "performance_review"
        }
        if v not in valid_types:
            raise ValueError(f"Invalid workflow_type: {v}. Must be one of {valid_types}")
        return v

    class Config:
        schema_extra = {
            "example": {
                "workflow_type": "content_generation",
                "input_data": {
                    "topic": "AI trends",
                    "style": "professional",
                    "length": "2000 words"
                },
                "user_id": "user123",
                "custom_pipeline": None,
                "execution_options": {"timeout": 300}
            }
        }


class NaturalLanguageRequest(BaseModel):
    """Request model for natural language workflow execution."""
    
    message: str = Field(
        ...,
        description="Natural language message describing the desired workflow"
    )
    user_id: str = Field(
        ...,
        description="User ID for tracking and context"
    )
    context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional context for intent recognition"
    )

    class Config:
        schema_extra = {
            "example": {
                "message": "Write a professional blog post about AI trends for 2000 words",
                "user_id": "user123",
                "context": {"department": "marketing"}
            }
        }


class IntentRecognitionRequest(BaseModel):
    """Request model for intent recognition without execution."""
    
    message: str = Field(
        ...,
        description="Natural language message to analyze"
    )
    context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional context for intent recognition"
    )

    class Config:
        schema_extra = {
            "example": {
                "message": "Create social media posts on Twitter and LinkedIn",
                "context": None
            }
        }


class IntentRecognitionResponse(BaseModel):
    """Response model for intent recognition."""
    
    success: bool
    intent_type: str
    confidence: float
    workflow_type: str
    parameters: Dict[str, Any]
    raw_message: str
    timestamp: str

    class Config:
        schema_extra = {
            "example": {
                "success": True,
                "intent_type": "social_media",
                "confidence": 0.95,
                "workflow_type": "social_media",
                "parameters": {
                    "platforms": ["twitter", "linkedin"],
                    "topic": None,
                    "tone": None
                },
                "raw_message": "Create social media posts on Twitter and LinkedIn",
                "timestamp": "2025-11-23T10:30:00Z"
            }
        }


class WorkflowListResponse(BaseModel):
    """Response model for listing available workflows."""
    
    workflows: List[Dict[str, Any]] = Field(
        ...,
        description="List of available workflows with descriptions"
    )
    count: int = Field(
        ...,
        description="Total number of available workflows"
    )

    class Config:
        schema_extra = {
            "example": {
                "workflows": [
                    {
                        "workflow_type": "content_generation",
                        "description": "Generate blog posts and articles",
                        "default_pipeline": ["research", "creative", "qa", "image", "publish"]
                    }
                ],
                "count": 6
            }
        }


class WorkflowStatusResponse(BaseModel):
    """Response model for workflow execution status."""
    
    workflow_id: str
    status: str
    workflow_type: str
    user_id: str
    created_at: str
    updated_at: str
    progress: int = Field(
        default=0,
        description="Progress percentage (0-100)"
    )
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    class Config:
        schema_extra = {
            "example": {
                "workflow_id": "wf_abc123",
                "status": "COMPLETED",
                "workflow_type": "content_generation",
                "user_id": "user123",
                "created_at": "2025-11-23T10:00:00Z",
                "updated_at": "2025-11-23T10:15:00Z",
                "progress": 100,
                "result": {"blog_post": "...content..."},
                "error": None
            }
        }


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.post(
    "/workflows/execute",
    response_model=Dict[str, Any],
    summary="Execute Structured Workflow",
    description="Execute a workflow with structured input data. Supports 6 workflow types with parameter validation."
)
async def execute_workflow(
    request: WorkflowExecutionRequest = Body(...),
) -> Dict[str, Any]:
    """
    Execute a workflow with structured input.

    Args:
        request: WorkflowExecutionRequest with workflow_type, input_data, user_id

    Returns:
        Dict containing workflow execution results

    Raises:
        HTTPException: 400 for invalid parameters, 500 for execution errors
    """
    try:
        logger.info(
            f"Executing workflow: {request.workflow_type} for user {request.user_id}"
        )

        # Execute workflow
        response = await workflow_router.execute_workflow(
            workflow_type=request.workflow_type,
            input_data=request.input_data,
            user_id=request.user_id,
            custom_pipeline=request.custom_pipeline,
            execution_options=request.execution_options or {}
        )

        logger.info(
            f"Workflow {request.workflow_type} completed with status: {response.status}"
        )

        return {
            "success": response.status == "COMPLETED",
            "workflow_id": response.workflow_id,
            "workflow_type": request.workflow_type,
            "status": response.status,
            "result": response.output,
            "metadata": {
                "execution_time_ms": int(response.duration_seconds * 1000),
                "tasks_executed": len(response.task_results),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
        }

    except ValueError as e:
        logger.error(f"Validation error: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Validation error: {str(e)}")
    except Exception as e:
        logger.error(f"Workflow execution failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Workflow execution failed: {str(e)}")


@router.post(
    "/workflows/execute-from-nl",
    response_model=Dict[str, Any],
    summary="Execute Workflow from Natural Language",
    description="Execute a workflow by parsing natural language message. Automatically detects workflow type and extracts parameters."
)
async def execute_from_natural_language(
    request: NaturalLanguageRequest = Body(...),
) -> Dict[str, Any]:
    """
    Execute a workflow from natural language request.

    Args:
        request: NaturalLanguageRequest with message and user_id

    Returns:
        Dict containing workflow execution results

    Raises:
        HTTPException: 400 for invalid input, 500 for execution errors
    """
    try:
        logger.info(
            f"Processing natural language request from user {request.user_id}: {request.message[:100]}"
        )

        # Execute workflow from natural language
        response = await workflow_router.execute_from_natural_language(
            user_message=request.message,
            user_id=request.user_id,
            context=request.context
        )

        logger.info(
            f"NL workflow completed with status: {response.status}"
        )

        return {
            "success": response.status == "COMPLETED",
            "workflow_id": response.workflow_id,
            "status": response.status,
            "result": response.output,
            "metadata": {
                "execution_time_ms": int(response.duration_seconds * 1000),
                "tasks_executed": len(response.task_results),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
        }

    except ValueError as e:
        logger.error(f"Natural language parsing failed: {str(e)}")
        raise HTTPException(
            status_code=400,
            detail=f"Could not parse natural language request: {str(e)}"
        )
    except Exception as e:
        logger.error(f"NL workflow execution failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Workflow execution failed: {str(e)}")


@router.post(
    "/intent/recognize",
    response_model=IntentRecognitionResponse,
    summary="Recognize Intent from Natural Language",
    description="Recognize workflow intent from natural language without executing. Useful for intent preview and debugging."
)
async def recognize_intent(
    request: IntentRecognitionRequest = Body(...),
) -> IntentRecognitionResponse:
    """
    Recognize workflow intent from natural language message.

    Args:
        request: IntentRecognitionRequest with message

    Returns:
        IntentRecognitionResponse with intent details and confidence

    Raises:
        HTTPException: 400 for invalid input, 404 if no intent matched
    """
    try:
        logger.info(f"Recognizing intent from: {request.message[:100]}")

        # Recognize intent
        intent_match: Optional[IntentMatch] = await nlp_recognizer.recognize_intent(
            message=request.message,
            context=request.context
        )

        if not intent_match:
            logger.warning(f"No intent matched for: {request.message}")
            raise HTTPException(
                status_code=404,
                detail="Could not recognize workflow intent from message"
            )

        logger.info(
            f"Intent recognized: {intent_match.intent_type} "
            f"(confidence: {intent_match.confidence:.2f})"
        )

        return IntentRecognitionResponse(
            success=True,
            intent_type=intent_match.intent_type,
            confidence=intent_match.confidence,
            workflow_type=intent_match.workflow_type,
            parameters=intent_match.parameters,
            raw_message=intent_match.raw_message,
            timestamp=datetime.utcnow().isoformat() + "Z"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Intent recognition failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Intent recognition failed: {str(e)}"
        )


@router.get(
    "/workflows/list",
    response_model=WorkflowListResponse,
    summary="List Available Workflows",
    description="Get list of all available workflow types with descriptions."
)
async def list_workflows() -> WorkflowListResponse:
    """
    List all available workflows.

    Returns:
        WorkflowListResponse with workflow catalog

    Raises:
        HTTPException: 500 if listing fails
    """
    try:
        logger.info("Listing available workflows")

        # Get workflow list
        result = await workflow_router.list_available_workflows()
        workflows = result.get("workflows", [])

        logger.info(f"Found {len(workflows)} available workflows")

        return WorkflowListResponse(
            workflows=workflows,
            count=len(workflows)
        )

    except Exception as e:
        logger.error(f"Failed to list workflows: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list workflows: {str(e)}"
        )


@router.get(
    "/workflows/{workflow_id}",
    response_model=WorkflowStatusResponse,
    summary="Get Workflow Execution Status",
    description="Get current status and results of a workflow execution."
)
async def get_workflow_status(
    workflow_id: str = Path(
        ...,
        description="Workflow execution ID"
    ),
) -> WorkflowStatusResponse:
    """
    Get workflow execution status.

    Args:
        workflow_id: ID of workflow execution to check

    Returns:
        WorkflowStatusResponse with current status and results

    Raises:
        HTTPException: 404 if workflow not found, 500 if retrieval fails
    """
    try:
        logger.info(f"Retrieving status for workflow: {workflow_id}")

        # Get workflow status (placeholder - would query database in Phase 5)
        # For now, return not implemented until database persistence is added
        raise HTTPException(
            status_code=501,
            detail="Workflow status tracking requires Phase 5 (Database Persistence). "
                   "This endpoint will be fully implemented in Phase 5."
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get workflow status: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get workflow status: {str(e)}"
        )


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@router.get(
    "/health/workflows",
    response_model=Dict[str, Any],
    summary="Workflow Service Health Check",
    description="Check health of workflow services (router, NLP recognizer)."
)
async def health_check() -> Dict[str, Any]:
    """
    Health check for workflow services.

    Returns:
        Dict with service health status

    Raises:
        HTTPException: 503 if services unhealthy
    """
    try:
        logger.debug("Performing workflow services health check")

        # Check if services are initialized
        if not workflow_router or not nlp_recognizer:
            raise HTTPException(
                status_code=503,
                detail="Workflow services not properly initialized"
            )

        return {
            "status": "healthy",
            "service": "workflows",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "components": {
                "workflow_router": "ready",
                "nlp_recognizer": "ready",
                "supported_workflows": 6,
                "intent_patterns": "96+"
            }
        }

    except Exception as e:
        logger.error(f"Health check failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=f"Workflow services health check failed: {str(e)}"
        )
