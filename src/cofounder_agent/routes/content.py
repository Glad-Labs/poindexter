"""
Content Creation Routes for Cofounder Agent

Endpoints for:
- Creating blog posts with AI generation
- Publishing to Strapi
- Managing drafts
- Tracking task status
"""

import os
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
import logging

from services.strapi_client import StrapiClient, StrapiEnvironment
from services.ai_content_generator import get_content_generator
from services.pexels_client import PexelsClient
from services.serper_client import SerperClient

logger = logging.getLogger(__name__)

# Router for all content-related endpoints
content_router = APIRouter(prefix="/api/content", tags=["content"])

# In-memory task storage (use Firestore in production)
task_store: Dict[str, Dict[str, Any]] = {}


class PublishMode(str, Enum):
    """Publishing modes for blog posts"""
    DRAFT = "draft"
    PUBLISH = "publish"


class ContentStyle(str, Enum):
    """Content styles"""
    TECHNICAL = "technical"
    NARRATIVE = "narrative"
    LISTICLE = "listicle"
    EDUCATIONAL = "educational"
    THOUGHT_LEADERSHIP = "thought-leadership"


class ContentTone(str, Enum):
    """Content tones"""
    PROFESSIONAL = "professional"
    CASUAL = "casual"
    ACADEMIC = "academic"
    INSPIRATIONAL = "inspirational"


class CreateBlogPostRequest(BaseModel):
    """Request model for creating a blog post"""
    topic: str = Field(..., max_length=200, description="Blog post topic/title")
    style: ContentStyle = Field(ContentStyle.TECHNICAL, description="Content style")
    tone: ContentTone = Field(ContentTone.PROFESSIONAL, description="Content tone")
    target_length: int = Field(1500, ge=200, le=5000, description="Target word count")
    tags: List[str] = Field(default_factory=list, description="Content tags")
    categories: List[str] = Field(default_factory=list, description="Strapi categories")
    generate_featured_image: bool = Field(
        True, 
        description="Search and use Pexels image for featured image (free, no cost)"
    )
    featured_image_keywords: Optional[List[str]] = Field(
        None, 
        description="Keywords for Pexels image search (if None, uses topic)"
    )
    publish_mode: PublishMode = Field(PublishMode.DRAFT, description="Draft or publish immediately")
    target_strapi_environment: str = Field("production", description="Production or staging Strapi")


class CreateBlogPostResponse(BaseModel):
    """Response model for blog post creation"""
    task_id: str
    status: str
    topic: str
    created_at: str
    polling_url: str
    estimated_completion: str


class TaskProgressResponse(BaseModel):
    """Response for task status/progress"""
    task_id: str
    status: str
    progress: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    created_at: str


class BlogDraftResponse(BaseModel):
    """Response model for blog drafts"""
    draft_id: str
    title: str
    created_at: str
    status: str
    word_count: int
    summary: Optional[str] = None
    can_edit: bool = True
    can_publish: bool = True
    can_delete: bool = True



class DraftsListResponse(BaseModel):
    """Response model for listing drafts"""
    drafts: List[BlogDraftResponse]
    total: int
    limit: int
    offset: int


class PublishDraftRequest(BaseModel):
    """Request to publish a draft"""
    target_strapi_environment: str = "production"
    scheduled_for: Optional[str] = None


class PublishDraftResponse(BaseModel):
    """Response after publishing"""
    draft_id: str
    strapi_post_id: int
    published_url: str
    published_at: str
    status: str


# ============================================================================
# ENDPOINTS
# ============================================================================


@content_router.post(
    "/create",
    response_model=CreateBlogPostResponse,
    status_code=201,
    description="Start async blog post generation"
)
@content_router.post(
    "/create-blog-post",
    response_model=CreateBlogPostResponse,
    status_code=201,
    description="Start async blog post generation"
)
async def create_blog_post(
    request: CreateBlogPostRequest,
    background_tasks: BackgroundTasks
):
    """
    Create a blog post using AI generation.
    
    This is an async operation - returns immediately with a task_id.
    Poll the task status endpoint to check progress and get the result.
    
    Returns:
        - task_id: Use this to poll for status
        - polling_url: Endpoint to check progress
        - estimated_completion: Estimated time until done
    """
    try:
        # Validate input
        if len(request.topic.strip()) < 3:
            raise HTTPException(status_code=400, detail="Topic must be at least 3 characters")

        # Create task ID
        task_id = f"blog_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        
        # Initialize task in store
        task_data = {
            "task_id": task_id,
            "status": "pending",
            "topic": request.topic,
            "style": request.style,
            "tone": request.tone,
            "target_length": request.target_length,
            "tags": request.tags,
            "categories": request.categories,
            "publish_mode": request.publish_mode,
            "target_environment": request.target_strapi_environment,
            "created_at": datetime.now().isoformat(),
            "progress": {"stage": "queued", "percentage": 0}
        }
        
        task_store[task_id] = task_data
        
        # Start background generation task
        background_tasks.add_task(
            _generate_and_publish_blog_post,
            task_id,
            request
        )
        
        logger.info(f"Blog post task created: {task_id} - Topic: {request.topic}")
        
        return CreateBlogPostResponse(
            task_id=task_id,
            status="pending",
            topic=request.topic,
            created_at=datetime.now().isoformat(),
            polling_url=f"/api/v1/content/tasks/{task_id}",
            estimated_completion=(datetime.now().isoformat())  # Current time
        )
        
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating blog post task: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create blog post task: {str(e)}")


@content_router.get(
    "/status/{task_id}",
    response_model=TaskProgressResponse,
    description="Check blog post generation status"
)
@content_router.get(
    "/tasks/{task_id}",
    response_model=TaskProgressResponse,
    description="Check blog post generation status"
)
async def get_task_status(task_id: str):
    """
    Check the status of a blog post generation task.
    
    Poll this endpoint every 2-5 seconds until status is 'completed' or 'failed'.
    
    Returns:
        - status: 'pending', 'generating', 'publishing', 'completed', or 'failed'
        - progress: Current progress info (while generating)
        - result: Generated blog post data (when completed)
        - error: Error details (if failed)
    """
    try:
        if task_id not in task_store:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        
        task = task_store[task_id]
        
        return TaskProgressResponse(
            task_id=task_id,
            status=task["status"],
            progress=task.get("progress"),
            result=task.get("result"),
            error=task.get("error"),
            created_at=task["created_at"]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting task status: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting task status: {str(e)}")


@content_router.get(
    "/drafts",
    response_model=DraftsListResponse,
    description="List blog post drafts"
)
async def list_drafts(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str = Query("draft", regex="^(draft|scheduled|failed)$")
):
    """
    Get list of generated blog drafts (not yet published to Strapi).
    """
    try:
        # Filter tasks that are drafts
        drafts = []
        for task_id, task in task_store.items():
            if task.get("status") == "completed" and task.get("publish_mode") == "draft":
                result = task.get("result", {})
                drafts.append(BlogDraftResponse(
                    draft_id=task_id,
                    title=result.get("title", "Untitled"),
                    created_at=task["created_at"],
                    status="draft",
                    word_count=result.get("word_count", 0),
                    summary=result.get("summary", ""),
                    can_edit=True,
                    can_publish=True,
                    can_delete=True
                ))
        
        # Apply pagination
        total = len(drafts)
        paginated_drafts = drafts[offset:offset + limit]
        
        return DraftsListResponse(
            drafts=paginated_drafts,
            total=total,
            limit=limit,
            offset=offset
        )
        
    except Exception as e:
        logger.error(f"Error listing drafts: {e}")
        raise HTTPException(status_code=500, detail=f"Error listing drafts: {str(e)}")


@content_router.post(
    "/drafts/{draft_id}/publish",
    response_model=PublishDraftResponse,
    description="Publish a draft to Strapi"
)
async def publish_draft(
    draft_id: str,
    request: PublishDraftRequest
):
    """
    Publish a draft blog post to Strapi.
    """
    try:
        if draft_id not in task_store:
            raise HTTPException(status_code=404, detail=f"Draft not found: {draft_id}")
        
        task = task_store[draft_id]
        if task.get("status") != "completed":
            raise HTTPException(status_code=409, detail="Draft must be in completed status")
        
        result = task.get("result", {})
        
        # Initialize Strapi client
        environment = StrapiEnvironment.STAGING if request.target_strapi_environment == "staging" else StrapiEnvironment.PRODUCTION
        strapi = StrapiClient(environment)
        
        # Publish to Strapi
        strapi_result = await strapi.create_blog_post(
            title=result.get("title"),
            content=result.get("content"),
            summary=result.get("summary"),
            tags=task.get("tags", []),
            categories=task.get("categories", []),
            featured_image_url=result.get("featured_image_url"),
            publish=True  # Publish immediately
        )
        
        post_id = strapi_result["data"]["id"]
        
        # Update task with published info
        task["status"] = "published"
        task["publish_date"] = datetime.now().isoformat()
        task["strapi_post_id"] = post_id
        
        published_url = f"https://glad-labs-website-{request.target_strapi_environment}.up.railway.app/blog/{post_id}"
        
        logger.info(f"Draft published to Strapi: {draft_id} -> {post_id}")
        
        return PublishDraftResponse(
            draft_id=draft_id,
            strapi_post_id=post_id,
            published_url=published_url,
            published_at=datetime.now().isoformat(),
            status="published"
        )
        
    except Exception as e:
        logger.error(f"Error publishing draft: {e}")
        raise HTTPException(status_code=500, detail=f"Error publishing draft: {str(e)}")


@content_router.delete(
    "/drafts/{draft_id}",
    description="Delete a draft"
)
async def delete_draft(draft_id: str):
    """
    Delete a blog draft.
    """
    try:
        if draft_id not in task_store:
            raise HTTPException(status_code=404, detail=f"Draft not found: {draft_id}")
        
        task = task_store.pop(draft_id)
        
        logger.info(f"Draft deleted: {draft_id}")
        
        return {
            "draft_id": draft_id,
            "deleted": True,
            "message": "Draft deleted successfully"
        }
        
    except Exception as e:
        logger.error(f"Error deleting draft: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting draft: {str(e)}")


# ============================================================================
# BACKGROUND TASKS
# ============================================================================


async def _generate_and_publish_blog_post(task_id: str, request: CreateBlogPostRequest):
    """
    Background task to generate and optionally publish blog post.
    
    Steps:
    1. Generate blog content using AI
    2. Create featured image (optional)
    3. Publish to Strapi (if publish_mode is PUBLISH)
    4. Update task status
    """
    try:
        task = task_store.get(task_id)
        if not task:
            logger.error(f"Task not found: {task_id}")
            return
        
        # Mark as generating
        task["status"] = "generating"
        task["progress"] = {"stage": "content_generation", "percentage": 25, "message": "Generating content..."}
        
        # Generate content with AI (tries Ollama -> HuggingFace -> Gemini)
        generated_content, model_used, metrics = await _generate_content_with_ai(request, task_id)
        task["model_used"] = model_used
        task["generation_metrics"] = metrics
        
        logger.info(f"Content generated with: {model_used} (quality score: {metrics['final_quality_score']:.1f}/10)")
        
        task["progress"] = {"stage": "image_generation", "percentage": 50, "message": "Searching for featured image..."}
        
        # Search for featured image via Pexels (free, no cost)
        featured_image = None
        image_source = None
        if request.generate_featured_image:
            try:
                pexels = PexelsClient()
                keywords = request.featured_image_keywords or [request.topic]
                image = pexels.get_featured_image(request.topic, keywords=keywords)
                
                if image:
                    featured_image = image["url"]
                    image_source = f"Pexels - {image['photographer']}"
                    logger.info(f"Found featured image: {image_source}")
                else:
                    logger.warning(f"No Pexels image found for: {request.topic}")
            except Exception as e:
                logger.warning(f"Pexels image search failed: {e}")
                featured_image = None
        else:
            # Skip image generation
            task["progress"]["percentage"] = 60
        
        # Update task with result
        task["result"] = {
            "title": request.topic,
            "content": generated_content,
            "summary": generated_content[:200] + "...",
            "word_count": len(generated_content.split()),
            "featured_image_url": featured_image,
            "featured_image_source": image_source,
            "model_used": model_used,
            "quality_score": metrics["final_quality_score"],
            "generation_attempts": metrics["generation_attempts"],
            "validation_results": metrics["validation_results"]
        }
        
        # Publish if requested
        if request.publish_mode == PublishMode.PUBLISH:
            task["progress"] = {"stage": "publishing", "percentage": 75, "message": "Publishing to Strapi..."}
            
            strapi_env = StrapiEnvironment.STAGING if request.target_strapi_environment == "staging" else StrapiEnvironment.PRODUCTION
            strapi = StrapiClient(strapi_env)
            
            strapi_result = await strapi.create_blog_post(
                title=request.topic,
                content=generated_content,
                summary=generated_content[:200],
                tags=request.tags,
                categories=request.categories,
                featured_image_url=featured_image,
                publish=True
            )
            
            task["strapi_post_id"] = strapi_result["data"]["id"]
            task["published_at"] = datetime.now().isoformat()
        
        # Mark as completed
        task["status"] = "completed"
        task["progress"] = {"stage": "complete", "percentage": 100, "message": "Done!"}
        task["completed_at"] = datetime.now().isoformat()
        
        logger.info(f"Blog post generation completed: {task_id}")
        
    except Exception as e:
        logger.error(f"Error in background task {task_id}: {e}")
        task = task_store.get(task_id)
        if task:
            task["status"] = "failed"
            task["error"] = {
                "code": "generation_failed",
                "message": str(e),
                "details": "Check logs for more info"
            }


async def _generate_content_with_ai(request: CreateBlogPostRequest, task_id: str) -> tuple:
    """
    Generate content using available AI models with self-checking.
    
    Tries models in this order:
    1. Local Ollama (if available, free, uses RTX 5070)
    2. HuggingFace (if token available, free tier)
    3. Google Gemini (fallback, costs money)
    
    Features:
    - Self-validation and quality checking
    - Refinement loops for rejected content
    - Full metrics tracking
    
    Returns:
        Tuple of (content, model_used, metrics)
    """
    generator = get_content_generator()
    
    logger.info(f"Generating content for topic: {request.topic}")
    
    content, model_used, metrics = await generator.generate_blog_post(
        topic=request.topic,
        style=request.style.value,
        tone=request.tone.value,
        target_length=request.target_length,
        tags=request.tags,
    )
    
    # Log metrics for debugging/analytics
    logger.info(f"Generation metrics: {metrics}")
    
    return content, model_used, metrics
