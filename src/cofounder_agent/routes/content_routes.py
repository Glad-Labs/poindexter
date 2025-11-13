"""
Unified Content Task Routes

Consolidates all content creation functionality into a single,
task-type-agnostic API supporting multiple task types (blog posts,
social media, emails, etc.) with a unified interface.

This architecture supports the multi-agent system where the LLM chat
interface can request different task types and the system routes them
to the appropriate agent pipeline.

Primary Endpoints (NEW):
- POST   /api/content/tasks                    Create task (blog_post, social_media, email, etc.)
- GET    /api/content/tasks/{task_id}          Get task status and result
- GET    /api/content/tasks                    List all tasks (filterable by type/status)
- POST   /api/content/tasks/{task_id}/approve  Approve/publish task
- DELETE /api/content/tasks/{task_id}          Delete task

Task Types Supported:
- blog_post      Blog post generation with self-critique pipeline
- social_media   Multi-platform social content (Twitter, LinkedIn, etc.)
- email          Email campaign generation
- newsletter     Newsletter generation
- (extensible)   New types can be added as agent pipelines are created

Query Parameters:
- GET /api/content/tasks?type=blog_post        Filter by task type
- GET /api/content/tasks?status=completed      Filter by status
- GET /api/content/tasks?limit=20&offset=0     Pagination

Backward Compatible Endpoints (DEPRECATED):
- POST   /api/content/create
- POST   /api/content/create-blog-post
- POST   /api/content/generate
- GET    /api/content/status/{task_id}
- GET    /api/content/blog-posts/*             Redirect to /api/content/tasks/*
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
    """Request to create a content task (blog post, social media, email, etc.)"""

    task_type: Literal["blog_post", "social_media", "email", "newsletter"] = Field(
        "blog_post", description="Type of content task to create"
    )
    topic: str = Field(..., min_length=3, max_length=200, description="Content topic/subject")
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
        None, description="Strapi categories (blog_post only)"
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
    """Response from task creation"""

    task_id: str
    task_type: str
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
    "/tasks",
    response_model=CreateBlogPostResponse,
    status_code=201,
    description="Create a content task (blog post, social media, email, etc.)",
    tags=["content-tasks"],
)
async def create_content_task(
    request: CreateBlogPostRequest, background_tasks: BackgroundTasks
):
    """
    Create a new content task with AI generation.

    This is an async operation - returns immediately with task_id.
    Poll /api/content/tasks/{task_id} to check progress.

    Task Types:
        - blog_post: Blog post generation with self-critique pipeline
        - social_media: Multi-platform social content
        - email: Email campaign generation
        - newsletter: Newsletter generation

    Request:
        - task_type: Type of content to generate (default: blog_post)
        - topic: Content topic/subject
        - style: technical, narrative, listicle, educational, thought-leadership
        - tone: professional, casual, academic, inspirational
        - target_length: Target word count (200-5000)
        - tags: Optional tags for categorization
        - generate_featured_image: Search Pexels for featured image (blog_post only)
        - enhanced: Use SEO enhancement
        - publish_mode: draft or publish immediately

    Returns:
        - task_id: Use to poll for status
        - task_type: Type of task created
        - polling_url: Endpoint to check progress
    """
    logger.info(f"🟢 POST /api/content/tasks called - Type: {request.task_type} - Topic: {request.topic}")
    
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
            task_type=request.task_type,  # ✅ Store task type
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
            f"✅✅ CONTENT TASK CREATED: {task_id} - Type: {request.task_type} - Topic: {request.topic} - "
            f"Enhanced: {request.enhanced} - Ready for polling at /api/content/tasks/{task_id}"
        )

        return CreateBlogPostResponse(
            task_id=task_id,
            task_type=request.task_type,  # ✅ Include task type in response
            status="pending",
            topic=request.topic,
            created_at=datetime.now().isoformat(),
            polling_url=f"/api/content/tasks/{task_id}",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error creating content task: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to create content task: {str(e)}"
        )


@content_router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    description="Get content task status",
    tags=["content-tasks"],
)
async def get_content_task_status(task_id: str):
    """
    Check the status of a content task.

    Poll every 2-5 seconds until status is 'completed' or 'failed'.

    Response:
        - status: pending, generating, completed, failed
        - progress: Current progress info (while generating)
        - result: Generated content data (when completed)
        - error: Error details (if failed)
    """
    logger.debug(f"🟢 GET /api/content/tasks/{task_id} called")
    
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
        
        # ✅ FIXED: Build result object from actual database fields
        result = None
        if task.get("status") in ["completed", "failed"]:
            result = {
                "title": task.get("topic", "Untitled"),
                "content": task.get("content", ""),  # ✅ From content field
                "excerpt": task.get("excerpt", ""),  # ✅ From excerpt field
                "summary": task.get("excerpt", ""),  # Alias for excerpt
                "word_count": len(task.get("content", "").split()) if task.get("content") else 0,
                "featured_image_url": task.get("featured_image_url"),  # ✅ From featured_image_url field
                "featured_image_source": task.get("task_metadata", {}).get("featured_image_source"),
                "model_used": task.get("model_used"),  # ✅ From model_used field
                "quality_score": task.get("quality_score"),  # ✅ From quality_score field
                "tags": task.get("tags", []),
                "strapi_post_id": task.get("strapi_id"),  # Strapi post ID if published
                "strapi_url": task.get("strapi_url"),  # Strapi URL if published
                # Include any additional metadata
                "task_metadata": task.get("task_metadata", {}),
            }
        
        return TaskStatusResponse(
            task_id=task_id,
            status=task.get("status", "unknown"),
            progress=task.get("progress"),
            result=result,  # ✅ Now populated with actual data from database
            error=task.get("error_message") if task.get("error_message") else None,
            created_at=task.get("created_at", ""),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error getting task status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting task status: {str(e)}")


@content_router.get(
    "/tasks",
    response_model=DraftsListResponse,
    description="List content tasks",
    tags=["content-tasks"],
)
async def list_content_tasks(
    task_type: Optional[str] = Query(None, description="Filter by task type (blog_post, social_media, etc.)"),
    status: Optional[str] = Query(None, description="Filter by status (pending, generating, completed, failed)"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    Get list of content tasks (drafts not yet published to Strapi).

    Query Parameters:
        - task_type: Filter by type (blog_post, social_media, email, newsletter)
        - status: Filter by status (pending, generating, completed, failed)
        - limit: Number of tasks to return (1-100)
        - offset: Pagination offset

    Example:
        - GET /api/content/tasks?task_type=blog_post&status=completed
        - GET /api/content/tasks?status=generating&limit=50
    """
    try:
        task_store = get_content_task_store()
        drafts, total = task_store.get_drafts(limit=limit, offset=offset)

        # Apply filters
        if task_type:
            drafts = [t for t in drafts if t.get("task_type") == task_type or t.get("request_type") == task_type]
            total = len(drafts)
        
        if status:
            drafts = [t for t in drafts if t.get("status") == status]
            total = len(drafts)

        draft_responses = []
        for task in drafts:
            # ✅ Get title and summary from actual database fields
            draft_responses.append(
                BlogDraftResponse(
                    draft_id=task["task_id"],
                    title=task.get("topic", "Untitled"),  # ✅ Use topic field
                    created_at=task.get("created_at", ""),
                    status=task.get("status", "draft"),  # ✅ Use actual status
                    word_count=len(task.get("content", "").split()) if task.get("content") else 0,  # ✅ Calculate from content
                    summary=task.get("excerpt", ""),  # ✅ Use excerpt field
                )
            )

        return DraftsListResponse(
            drafts=draft_responses, total=total, limit=limit, offset=offset
        )

    except Exception as e:
        logger.error(f"Error listing tasks: {e}")
        raise HTTPException(status_code=500, detail=f"Error listing tasks: {str(e)}")


@content_router.post(
    "/tasks/{task_id}/approve",
    response_model=PublishDraftResponse,
    description="Approve and publish a task to Strapi",
    tags=["content-tasks"],
)
async def approve_and_publish_task(task_id: str, request: PublishDraftRequest):
    """
    Approve and publish a completed task to Strapi CMS.

    This endpoint approves a generated content task and publishes it to Strapi.
    Currently supports blog posts; will extend to other content types.

    Path Parameters:
        - task_id: Task ID of the task to approve and publish

    Request Body:
        - target_environment: 'production' or 'staging'

    Response:
        - strapi_post_id: ID of the published post in Strapi
        - published_url: URL of the published post
        - status: 'published'
    """
    try:
        task_store = get_content_task_store()
        task = task_store.get_task(task_id)

        if not task:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

        if task.get("status") != "completed":
            raise HTTPException(
                status_code=409, detail=f"Task must be completed (current: {task.get('status')})"
            )

        # ✅ Get content from actual database fields, not result object
        content = task.get("content")
        if not content:
            raise HTTPException(
                status_code=400, detail="Task content is empty - cannot publish"
            )

        strapi_post_id = task.get("strapi_id")  # Check if already published
        
        if not strapi_post_id:
            # ✅ Publish to Strapi if not already published
            logger.info(f"📤 Publishing task {task_id} to Strapi...")
            from services.strapi_publisher import StrapiPublisher
            
            publisher = StrapiPublisher()
            await publisher.connect()
            
            try:
                result = await publisher.create_post(
                    title=task.get("topic", "Untitled"),
                    content=content,
                    excerpt=task.get("excerpt", ""),
                    featured_image_url=task.get("featured_image_url"),
                    tags=task.get("tags", []),
                )
                
                if result.get("success") and result.get("post_id"):
                    strapi_post_id = str(result.get("post_id"))
                    strapi_url = f"/blog/{strapi_post_id}"  # Strapi URL format
                    
                    # ✅ Save strapi_id and strapi_url to database
                    task_store.update_task(
                        task_id,
                        {
                            "strapi_id": strapi_post_id,
                            "strapi_url": strapi_url,
                            "publish_mode": "published",
                        },
                    )
                    logger.info(f"✅ Published to Strapi - Post ID: {strapi_post_id}")
                else:
                    raise HTTPException(
                        status_code=500, detail=f"Failed to publish to Strapi: {result.get('message', 'Unknown error')}"
                    )
            finally:
                await publisher.disconnect()
        else:
            logger.info(f"ℹ️ Task already published - Strapi ID: {strapi_post_id}")
            strapi_url = task.get("strapi_url", "")

        published_url = strapi_url or f"https://glad-labs-website-{request.target_environment or 'production'}.railway.app/blog/{strapi_post_id}"

        return PublishDraftResponse(
            draft_id=task_id,
            strapi_post_id=int(strapi_post_id),  # Convert to int for response
            published_url=published_url,
            published_at=datetime.now().isoformat(),
            status="published",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error approving/publishing task: {e}")
        raise HTTPException(status_code=500, detail=f"Error approving/publishing task: {str(e)}")


@content_router.delete(
    "/tasks/{task_id}",
    description="Delete a task",
    tags=["content-tasks"],
)
async def delete_content_task(task_id: str):
    """
    Delete a content task.

    Path Parameters:
        - task_id: Task ID to delete

    Returns:
        - Confirmation of deletion
    """
    try:
        task_store = get_content_task_store()

        if not task_store.delete_task(task_id):
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

        logger.info(f"Task deleted: {task_id}")

        return {
            "task_id": task_id,
            "deleted": True,
            "message": "Task deleted successfully",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting task: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting task: {str(e)}")

