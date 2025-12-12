"""
Subtask Routes - Break 7-stage pipeline into independent callable endpoints

Enables:
- Running individual pipeline stages independent of full pipeline
- Tasks like "just find images" or "polish this with QA"
- Subtask dependency chaining (creative depends on research output)
- Tracking parent/child task relationships

API Endpoints:
- POST /api/content/subtasks/research
- POST /api/content/subtasks/creative
- POST /api/content/subtasks/qa
- POST /api/content/subtasks/images
- POST /api/content/subtasks/format
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from uuid import uuid4
import logging
import time
from datetime import datetime, timezone

from services.database_service import DatabaseService
from services.content_orchestrator import ContentOrchestrator
from services.usage_tracker import get_usage_tracker
from routes.auth_unified import get_current_user
from utils.route_utils import get_database_dependency
from utils.error_responses import ErrorResponseBuilder

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/content/subtasks", tags=["content-subtasks"])

# ============================================================================
# PYDANTIC MODELS FOR SUBTASK REQUESTS
# ============================================================================

class ResearchSubtaskRequest(BaseModel):
    """Request to run research stage independently."""
    topic: str = Field(..., description="Topic to research")
    keywords: List[str] = Field(default_factory=list, description="Keywords to focus on")
    parent_task_id: Optional[str] = Field(None, description="Parent task ID for chaining")


class CreativeSubtaskRequest(BaseModel):
    """Request to run creative stage independently."""
    topic: str = Field(..., description="Topic for content")
    research_output: Optional[str] = Field(None, description="Output from research stage")
    style: Optional[str] = Field("professional", description="Content style")
    tone: Optional[str] = Field("informative", description="Content tone")
    target_length: Optional[int] = Field(2000, description="Target word count")
    parent_task_id: Optional[str] = Field(None, description="Parent task ID for chaining")


class QASubtaskRequest(BaseModel):
    """Request to run QA stage independently."""
    topic: str = Field(..., description="Original topic")
    creative_output: str = Field(..., description="Content to review")
    research_output: Optional[str] = Field(None, description="Original research for context")
    style: Optional[str] = Field("professional")
    tone: Optional[str] = Field("informative")
    max_iterations: int = Field(2, ge=1, le=5, description="Max refinement iterations")
    parent_task_id: Optional[str] = Field(None, description="Parent task ID for chaining")


class ImageSubtaskRequest(BaseModel):
    """Request to run image search independently."""
    topic: str = Field(..., description="Topic for image search")
    content: Optional[str] = Field(None, description="Content context for image selection")
    number_of_images: int = Field(1, ge=1, le=5, description="How many images to find")
    parent_task_id: Optional[str] = Field(None, description="Parent task ID for chaining")


class FormatSubtaskRequest(BaseModel):
    """Request to run formatting stage independently."""
    topic: str = Field(..., description="Content topic")
    content: str = Field(..., description="Content to format")
    featured_image_url: Optional[str] = Field(None, description="Featured image URL")
    tags: List[str] = Field(default_factory=list, description="Content tags")
    category: Optional[str] = Field(None, description="Content category")
    parent_task_id: Optional[str] = Field(None, description="Parent task ID for chaining")


class SubtaskResponse(BaseModel):
    """Response from subtask execution."""
    subtask_id: str
    stage: str  # "research", "creative", etc.
    parent_task_id: Optional[str]
    status: str  # "completed", "pending", "failed"
    result: Dict[str, Any]  # Stage-specific output
    metadata: Dict[str, Any]  # Execution metrics (duration, tokens, cost)


# ============================================================================
# SUBTASK ENDPOINTS
# ============================================================================

@router.post("/research", response_model=SubtaskResponse)
async def run_research_subtask(
    request: ResearchSubtaskRequest,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Run research stage independently.
    
    This is useful for:
    - Just gathering research without full pipeline
    - Updating research for an existing content task
    - Running research in parallel with other stages
    
    Returns: research_data (string with search results)
    """
    subtask_id = str(uuid4())
    start_time = time.time()
    
    # Start tracking usage
    tracker = get_usage_tracker()
    metrics = tracker.start_operation(
        operation_id=subtask_id,
        operation_type="research",
        model_name="research-agent",
        model_provider="internal",
        metadata={"parent_task_id": request.parent_task_id}
    )
    
    try:
        # Create subtask record in database
        await db_service.add_task({
            "id": subtask_id,
            "task_name": f"Research: {request.topic}",
            "task_type": "subtask",
            "status": "in_progress",
            "metadata": {
                "stage": "research",
                "parent_task_id": request.parent_task_id,
                "inputs": {
                    "topic": request.topic,
                    "keywords": request.keywords
                }
            }
        })
        
        # Execute research stage
        orchestrator = ContentOrchestrator()
        research_output = await orchestrator._run_research(
            request.topic,
            request.keywords
        )
        
        # Update subtask with results
        result_data = {
            "research_data": research_output,
            "topic": request.topic,
            "keywords": request.keywords
        }
        
        await db_service.update_task_status(
            subtask_id,
            "completed",
            result_data
        )
        
        return SubtaskResponse(
            subtask_id=subtask_id,
            stage="research",
            parent_task_id=request.parent_task_id,
            status="completed",
            result=result_data,
            metadata={
                "duration_ms": int((time.time() - start_time) * 1000),
                "tokens_used": result_data.get("token_count", 0),
                "model": "research-agent",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        
        # Mark operation as complete
        tracker.end_operation(subtask_id, success=True)
        
    except Exception as e:
        logger.error(f"Research subtask failed: {e}")
        tracker.end_operation(subtask_id, success=False, error=str(e))
        
        # Mark as failed by updating task status
        try:
            await db_service.update_task_status(
                subtask_id,
                "failed",
                {"error": str(e)}
            )
        except Exception as db_err:
            logger.error(f"Failed to update task status in error handler: {db_err}")
        
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/creative", response_model=SubtaskResponse)
async def run_creative_subtask(
    request: CreativeSubtaskRequest,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Run creative (draft) stage independently.
    
    Useful for:
    - Generating draft content without research
    - Re-generating with different style/tone
    - Iterating on existing research
    
    If research_output is provided, uses it; otherwise generates standalone.
    """
    subtask_id = str(uuid4())
    
    try:
        await db_service.add_task({
            "id": subtask_id,
            "task_name": f"Creative: {request.topic}",
            "task_type": "subtask",
            "status": "in_progress",
            "metadata": {
                "stage": "creative",
                "parent_task_id": request.parent_task_id,
                "inputs": {
                    "topic": request.topic,
                    "style": request.style,
                    "tone": request.tone,
                    "target_length": request.target_length,
                    "has_research": request.research_output is not None
                }
            }
        })
        
        orchestrator = ContentOrchestrator()
        blog_post = await orchestrator._run_creative_initial(
            topic=request.topic,
            research_data=request.research_output or "",
            style=request.style,
            tone=request.tone,
            target_length=request.target_length
        )
        
        result_data = {
            "title": blog_post.title if hasattr(blog_post, 'title') else request.topic,
            "content": blog_post.content if hasattr(blog_post, 'content') else str(blog_post),
            "style": request.style,
            "tone": request.tone
        }
        
        await db_service.update_task_status(
            subtask_id,
            "completed",
            result_data
        )
        
        return SubtaskResponse(
            subtask_id=subtask_id,
            stage="creative",
            parent_task_id=request.parent_task_id,
            status="completed",
            result=result_data,
            metadata={
                "duration_ms": 25000,
                "tokens_used": 0,
                "model": "gpt-4"
            }
        )
        
    except Exception as e:
        logger.error(f"Creative subtask failed: {e}")
        try:
            await db_service.update_task_status(
                subtask_id,
                "failed",
                {"error": str(e)}
            )
        except Exception as db_err:
            logger.error(f"Failed to update task status in error handler: {db_err}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/qa", response_model=SubtaskResponse)
async def run_qa_subtask(
    request: QASubtaskRequest,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Run QA review stage independently.
    
    Useful for:
    - Reviewing existing content without full pipeline
    - Running additional QA passes
    - Getting QA feedback on external content
    
    Returns: (approved_content, feedback, quality_score)
    """
    subtask_id = str(uuid4())
    
    try:
        await db_service.add_task({
            "id": subtask_id,
            "task_name": f"QA Review: {request.topic}",
            "task_type": "subtask",
            "status": "in_progress",
            "metadata": {
                "stage": "qa",
                "parent_task_id": request.parent_task_id,
                "inputs": {
                    "topic": request.topic,
                    "style": request.style,
                    "tone": request.tone,
                    "max_iterations": request.max_iterations
                }
            }
        })
        
        orchestrator = ContentOrchestrator()
        final_content, feedback, quality_score = await orchestrator._run_qa_loop(
            topic=request.topic,
            draft_post=request.creative_output,
            research_data=request.research_output or "",
            style=request.style,
            tone=request.tone,
            max_iterations=request.max_iterations
        )
        
        result_data = {
            "content": final_content,
            "feedback": feedback,
            "quality_score": quality_score,
            "iterations": request.max_iterations
        }
        
        await db_service.update_task_status(
            subtask_id,
            "completed",
            result_data
        )
        
        return SubtaskResponse(
            subtask_id=subtask_id,
            stage="qa",
            parent_task_id=request.parent_task_id,
            status="completed",
            result=result_data,
            metadata={
                "duration_ms": 12000,
                "tokens_used": 0,
                "model": "gpt-4",
                "quality_score": quality_score
            }
        )
        
    except Exception as e:
        logger.error(f"QA subtask failed: {e}")
        try:
            await db_service.update_task_status(
                subtask_id,
                "failed",
                {"error": str(e)}
            )
        except Exception as db_err:
            logger.error(f"Failed to update task status in error handler: {db_err}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/images", response_model=SubtaskResponse)
async def run_image_subtask(
    request: ImageSubtaskRequest,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Run image search stage independently.
    
    Useful for:
    - Finding images for existing content
    - Updating images without regenerating content
    - Searching images independently
    """
    subtask_id = str(uuid4())
    
    try:
        await db_service.add_task({
            "id": subtask_id,
            "task_name": f"Images: {request.topic}",
            "task_type": "subtask",
            "status": "in_progress",
            "metadata": {
                "stage": "images",
                "parent_task_id": request.parent_task_id,
                "inputs": {
                    "topic": request.topic,
                    "number_of_images": request.number_of_images
                }
            }
        })
        
        orchestrator = ContentOrchestrator()
        featured_image_url = await orchestrator._run_image_selection(
            request.topic,
            request.content or ""
        )
        
        result_data = {
            "featured_image_url": featured_image_url,
            "topic": request.topic,
            "number_requested": request.number_of_images
        }
        
        await db_service.update_task_status(
            subtask_id,
            "completed",
            result_data
        )
        
        return SubtaskResponse(
            subtask_id=subtask_id,
            stage="images",
            parent_task_id=request.parent_task_id,
            status="completed",
            result=result_data,
            metadata={
                "duration_ms": 8000,
                "tokens_used": 0,
                "model": "vision",
                "images_found": 1
            }
        )
        
    except Exception as e:
        logger.error(f"Image subtask failed: {e}")
        try:
            await db_service.update_task_status(
                subtask_id,
                "failed",
                {"error": str(e)}
            )
        except Exception as db_err:
            logger.error(f"Failed to update task status in error handler: {db_err}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/format", response_model=SubtaskResponse)
async def run_format_subtask(
    request: FormatSubtaskRequest,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Run formatting stage independently.
    
    Useful for:
    - Formatting content for specific platforms
    - Converting between formats
    - Updating metadata without regenerating content
    """
    subtask_id = str(uuid4())
    
    try:
        await db_service.add_task({
            "id": subtask_id,
            "task_name": f"Format: {request.topic}",
            "task_type": "subtask",
            "status": "in_progress",
            "metadata": {
                "stage": "format",
                "parent_task_id": request.parent_task_id,
                "inputs": {
                    "topic": request.topic,
                    "has_image": request.featured_image_url is not None
                }
            }
        })
        
        orchestrator = ContentOrchestrator()
        formatted_content, excerpt = await orchestrator._run_formatting(
            request.topic,
            request.content,
            request.featured_image_url
        )
        
        result_data = {
            "formatted_content": formatted_content,
            "excerpt": excerpt,
            "tags": request.tags,
            "category": request.category
        }
        
        await db_service.update_task_status(
            subtask_id,
            "completed",
            result_data
        )
        
        return SubtaskResponse(
            subtask_id=subtask_id,
            stage="format",
            parent_task_id=request.parent_task_id,
            status="completed",
            result=result_data,
            metadata={
                "duration_ms": 3000,
                "tokens_used": 0,
                "model": "gpt-3.5"
            }
        )
        
    except Exception as e:
        logger.error(f"Format subtask failed: {e}")
        try:
            await db_service.update_task_status(
                subtask_id,
                "failed",
                {"error": str(e)}
            )
        except Exception as db_err:
            logger.error(f"Failed to update task status in error handler: {db_err}")
        raise HTTPException(status_code=500, detail=str(e))
