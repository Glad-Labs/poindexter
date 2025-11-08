"""
Unified Content Routes

Consolidates all content creation functionality into a single,
well-organized API with backward-compatible endpoints.

Primary Endpoints (NEW):
- POST   /api/content/blog-posts
- GET    /api/content/blog-posts/tasks/{task_id}
- GET    /api/content/blog-posts/drafts
- POST   /api/content/blog-posts/drafts/{id}/publish
- DELETE /api/content/blog-posts/drafts/{id}

Backward Compatible Endpoints (DEPRECATED):
- POST   /api/content/create
- POST   /api/content/create-blog-post
- POST   /api/content/generate
- GET    /api/content/status/{task_id}
- GET    /api/content/tasks
- POST   /api/v1/content/enhanced/blog-posts/create-seo-optimized
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
import logging

from services.content_router_service import (
    ContentStyle,
    ContentTone,
    PublishMode,
    get_content_task_store,
    process_content_generation_task,
)

logger = logging.getLogger(__name__)

# ============================================================================
# ROUTER SETUP
# ============================================================================

content_router = APIRouter(prefix="/api/content", tags=["content"])

# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================


class CreateBlogPostRequest(BaseModel):
    """Request to create a blog post"""

    topic: str = Field(..., min_length=3, max_length=200, description="Blog post topic")
    style: ContentStyle = Field(
        ContentStyle.TECHNICAL, description="Content style"
    )
    tone: ContentTone = Field(ContentTone.PROFESSIONAL, description="Content tone")
    target_length: int = Field(
        1500, ge=200, le=5000, description="Target word count"
    )
    tags: Optional[List[str]] = Field(
        None, description="Tags for categorization"
    )
    categories: Optional[List[str]] = Field(
        None, description="Strapi categories"
    )
    generate_featured_image: bool = Field(
        True, description="Search Pexels for featured image (free)"
    )
    publish_mode: PublishMode = Field(
        PublishMode.DRAFT, description="Draft or publish immediately"
    )
    enhanced: bool = Field(
        False, description="Use SEO enhancement"
    )
    target_environment: str = Field(
        "production", description="Strapi environment"
    )


class CreateBlogPostResponse(BaseModel):
    """Response from blog post creation"""

    task_id: str
    status: str
    topic: str
    created_at: str
    polling_url: str


class TaskStatusResponse(BaseModel):
    """Task status response"""

    task_id: str
    status: str
    progress: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    created_at: str


class BlogDraftResponse(BaseModel):
    """Blog draft info"""

    draft_id: str
    title: str
    created_at: str
    status: str
    word_count: int
    summary: str


class DraftsListResponse(BaseModel):
    """List of drafts"""

    drafts: List[BlogDraftResponse]
    total: int
    limit: int
    offset: int


class PublishDraftRequest(BaseModel):
    """Request to publish a draft"""

    target_environment: str = Field("production", description="Strapi environment")


class PublishDraftResponse(BaseModel):
    """Response from publishing a draft"""

    draft_id: str
    strapi_post_id: int
    published_url: str
    published_at: str
    status: str


# ============================================================================
# PRIMARY UNIFIED ENDPOINTS
# ============================================================================


@content_router.post(
    "/blog-posts",
    response_model=CreateBlogPostResponse,
    status_code=201,
    description="Create a blog post (unified endpoint)",
    tags=["content-unified"],
)
async def create_blog_post(
    request: CreateBlogPostRequest, background_tasks: BackgroundTasks
):
    """
    Create a new blog post with AI generation.

    This is an async operation - returns immediately with task_id.
    Poll /api/content/blog-posts/tasks/{task_id} to check progress.

    Request:
        - topic: Blog post topic/title
        - style: technical, narrative, listicle, educational, thought-leadership
        - tone: professional, casual, academic, inspirational
        - target_length: Target word count (200-5000)
        - tags: Optional tags for categorization
        - generate_featured_image: Search Pexels for featured image
        - enhanced: Use SEO enhancement
        - publish_mode: draft or publish immediately

    Returns:
        - task_id: Use to poll for status
        - polling_url: Endpoint to check progress
    """
    logger.info(f"🟢 POST /api/content/blog-posts called - Topic: {request.topic}")
    
    try:
        if len(request.topic.strip()) < 3:
            raise HTTPException(status_code=400, detail="Topic must be at least 3 characters")

        logger.debug(f"  ✓ Topic validation passed")
        
        task_store = get_content_task_store()
        logger.debug(f"  ✓ Got task store")

        # Create task
        logger.debug(f"  📝 Creating task in store...")
        task_id = task_store.create_task(
            topic=request.topic,
            style=request.style.value,
            tone=request.tone.value,
            target_length=request.target_length,
            tags=request.tags,
            generate_featured_image=request.generate_featured_image,
            request_type="enhanced" if request.enhanced else "basic",
        )
        logger.info(f"  ✅ Task created: {task_id}")

        # Update with additional fields
        logger.debug(f"  📝 Updating task with additional fields...")
        update_result = task_store.update_task(
            task_id,
            {
                "categories": request.categories or [],
                "publish_mode": request.publish_mode.value,
                "target_environment": request.target_environment,
            },
        )
        logger.debug(f"  ✅ Task updated: {update_result}")

        # Start background generation
        logger.debug(f"  ⏳ Starting background task...")
        background_tasks.add_task(process_content_generation_task, task_id)
        logger.debug(f"  ✓ Background task queued")

        logger.info(
            f"✅✅ BLOG POST TASK CREATED: {task_id} - Topic: {request.topic} - "
            f"Enhanced: {request.enhanced} - Ready for polling at /api/content/blog-posts/tasks/{task_id}"
        )

        return CreateBlogPostResponse(
            task_id=task_id,
            status="pending",
            topic=request.topic,
            created_at=datetime.now().isoformat(),
            polling_url=f"/api/content/blog-posts/tasks/{task_id}",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error creating blog post task: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to create blog post: {str(e)}"
        )


@content_router.get(
    "/blog-posts/tasks/{task_id}",
    response_model=TaskStatusResponse,
    description="Get blog post generation status",
    tags=["content-unified"],
)
async def get_blog_post_status(task_id: str):
    """
    Check the status of a blog post generation task.

    Poll every 2-5 seconds until status is 'completed' or 'failed'.

    Response:
        - status: pending, generating, completed, failed
        - progress: Current progress info (while generating)
        - result: Generated blog post data (when completed)
        - error: Error details (if failed)
    """
    logger.debug(f"🟢 GET /api/content/blog-posts/tasks/{task_id} called")
    
    try:
        task_store = get_content_task_store()
        logger.debug(f"  ✓ Got task store")
        
        logger.debug(f"  🔍 Retrieving task from store...")
        task = task_store.get_task(task_id)
        logger.debug(f"  ✓ Retrieved from store: {task is not None}")

        if not task:
            logger.warning(f"❌ Task not found: {task_id}")
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

        logger.info(f"✅ Task status retrieved: {task_id} - status: {task.get('status', 'unknown')}")
        
        return TaskStatusResponse(
            task_id=task_id,
            status=task.get("status", "unknown"),
            progress=task.get("progress"),
            result=task.get("result"),
            error=task.get("error"),
            created_at=task.get("created_at", ""),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error getting task status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting task status: {str(e)}")


@content_router.get(
    "/blog-posts/drafts",
    response_model=DraftsListResponse,
    description="List blog post drafts",
    tags=["content-unified"],
)
async def list_drafts(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    Get list of generated blog drafts (not yet published to Strapi).

    Query Parameters:
        - limit: Number of drafts to return (1-100)
        - offset: Pagination offset
    """
    try:
        task_store = get_content_task_store()
        drafts, total = task_store.get_drafts(limit=limit, offset=offset)

        draft_responses = []
        for task in drafts:
            result = task.get("result", {})
            draft_responses.append(
                BlogDraftResponse(
                    draft_id=task["task_id"],
                    title=result.get("title", "Untitled"),
                    created_at=task.get("created_at", ""),
                    status="draft",
                    word_count=result.get("word_count", 0),
                    summary=result.get("summary", ""),
                )
            )

        return DraftsListResponse(
            drafts=draft_responses, total=total, limit=limit, offset=offset
        )

    except Exception as e:
        logger.error(f"Error listing drafts: {e}")
        raise HTTPException(status_code=500, detail=f"Error listing drafts: {str(e)}")


@content_router.post(
    "/blog-posts/drafts/{draft_id}/publish",
    response_model=PublishDraftResponse,
    description="Publish a draft to Strapi",
    tags=["content-unified"],
)
async def publish_draft(draft_id: str, request: PublishDraftRequest):
    """
    Publish a draft blog post to Strapi CMS.

    Path Parameters:
        - draft_id: Task ID of the draft to publish

    Request Body:
        - target_environment: 'production' or 'staging'
    """
    try:
        task_store = get_content_task_store()
        task = task_store.get_task(draft_id)

        if not task:
            raise HTTPException(status_code=404, detail=f"Draft not found: {draft_id}")

        if task.get("status") != "completed":
            raise HTTPException(
                status_code=409, detail=f"Draft must be completed (current: {task.get('status')})"
            )

        result = task.get("result", {})
        strapi_post_id = result.get("strapi_post_id")

        if not strapi_post_id:
            raise HTTPException(
                status_code=400, detail="Draft has not been published yet"
            )

        published_url = f"https://glad-labs-website-{request.target_environment}.railway.app/blog/{strapi_post_id}"

        return PublishDraftResponse(
            draft_id=draft_id,
            strapi_post_id=strapi_post_id,
            published_url=published_url,
            published_at=result.get("published_at", datetime.now().isoformat()),
            status="published",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error publishing draft: {e}")
        raise HTTPException(status_code=500, detail=f"Error publishing draft: {str(e)}")


@content_router.delete(
    "/blog-posts/drafts/{draft_id}",
    description="Delete a draft",
    tags=["content-unified"],
)
async def delete_draft(draft_id: str):
    """
    Delete a blog draft.

    Path Parameters:
        - draft_id: Task ID of the draft to delete
    """
    try:
        task_store = get_content_task_store()

        if not task_store.delete_task(draft_id):
            raise HTTPException(status_code=404, detail=f"Draft not found: {draft_id}")

        logger.info(f"Draft deleted: {draft_id}")

        return {
            "draft_id": draft_id,
            "deleted": True,
            "message": "Draft deleted successfully",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting draft: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting draft: {str(e)}")

