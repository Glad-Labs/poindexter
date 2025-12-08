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
from pydantic import BaseModel, Field, field_validator
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
from services.database_service import DatabaseService
from services.error_handler import (
    ValidationError,
    NotFoundError,
    StateError,
    DatabaseError,
    ServiceError,
    handle_error,
)

logger = logging.getLogger(__name__)

# ============================================================================
# GLOBAL DATABASE SERVICE
# ============================================================================

# Database service instance (set during app startup)
db_service = None


def set_db_service(service: DatabaseService):
    """Set the database service (called during app startup)"""
    global db_service
    db_service = service


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
        "blog_post", 
        description="Type of content task to create"
    )
    topic: str = Field(
        ..., 
        min_length=3, 
        max_length=200, 
        description="Content topic/subject",
        examples=["The Future of AI", "E-commerce Best Practices"]
    )
    style: ContentStyle = Field(
        ContentStyle.TECHNICAL, 
        description="Content style (technical, narrative, listicle, educational, thought-leadership)"
    )
    tone: ContentTone = Field(
        ContentTone.PROFESSIONAL, 
        description="Content tone (professional, casual, academic, inspirational)"
    )
    target_length: int = Field(
        1500, 
        ge=200, 
        le=5000, 
        description="Target word count (200-5000 words)",
        examples=[1500, 2000, 3000]
    )
    tags: Optional[List[str]] = Field(
        None, 
        min_items=0,
        max_items=10,
        description="Tags for categorization (max 10)"
    )
    categories: Optional[List[str]] = Field(
        None, 
        min_items=0,
        max_items=5,
        description="Categories for blog posts (max 5)"
    )
    generate_featured_image: bool = Field(
        True, 
        description="Search Pexels for featured image (free)"
    )
    publish_mode: PublishMode = Field(
        PublishMode.DRAFT, 
        description="Draft or publish immediately"
    )
    enhanced: bool = Field(
        False, 
        description="Use SEO enhancement"
    )
    target_environment: str = Field(
        "production", 
        pattern="^(development|staging|production)$",
        description="Target deployment environment (development, staging, production)"
    )
    llm_provider: Optional[str] = Field(
        None, 
        description="Optional: LLM provider override (ollama, openai, anthropic, gemini). If not specified, uses default from config.",
        examples=["ollama", "openai", "anthropic"]
    )
    model: Optional[str] = Field(
        None, 
        description="Optional: Specific model to use (e.g., 'ollama/mistral', 'gpt-4', 'claude-opus'). If not specified, uses default from config.",
        examples=["ollama/mistral", "ollama/phi", "gpt-4", "claude-opus"]
    )

    class Config:
        """Pydantic configuration"""
        json_schema_extra = {
            "example": {
                "task_type": "blog_post",
                "topic": "AI-Powered E-commerce: Trends and Best Practices",
                "style": "technical",
                "tone": "professional",
                "target_length": 2000,
                "tags": ["AI", "E-commerce"],
                "categories": ["Technology"],
                "generate_featured_image": True,
                "publish_mode": "draft",
                "enhanced": True,
                "target_environment": "production",
                "llm_provider": "ollama",
                "model": "ollama/mistral"
            }
        }


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
    summary: Optional[str] = None



class DraftsListResponse(BaseModel):
    """List of drafts"""

    drafts: List[BlogDraftResponse]
    total: int
    limit: int
    offset: int


class PublishDraftRequest(BaseModel):
    """Request to publish a draft"""

    target_environment: str = Field(
        "production", 
        pattern="^(development|staging|production)$",
        description="Target deployment environment: development, staging, or production"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "target_environment": "production"
            }
        }


class ApprovalRequest(BaseModel):
    """
    ✅ Phase 5: Human Approval Request
    
    Request from human reviewer to approve or reject a task pending approval.
    Mandatory gate before publishing - requires explicit human decision.
    """
    approved: bool = Field(
        ..., 
        description="True to approve and publish, False to reject"
    )
    human_feedback: str = Field(
        ..., 
        min_length=10, 
        max_length=1000,
        description="Human reviewer feedback (reason for decision) - 10-1000 chars"
    )
    reviewer_id: str = Field(
        ..., 
        min_length=2, 
        max_length=100,
        pattern="^[a-zA-Z0-9._-]+$",
        description="Reviewer username or ID (alphanumeric, dots, dashes, underscores)"
    )

    class Config:
        """Pydantic configuration"""
        json_schema_extra = {
            "example": {
                "approved": True,
                "human_feedback": "Excellent content! Well-researched and engaging. Approved for publication.",
                "reviewer_id": "john.doe"
            }
        }


class ApprovalResponse(BaseModel):
    """Response from approval decision"""
    
    task_id: str
    approval_status: str  # "approved" or "rejected"
    strapi_post_id: Optional[str] = None  # Only if approved and published (UUID or int)
    published_url: Optional[str] = None  # Only if approved and published
    approval_timestamp: str
    reviewer_id: str
    message: str


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
        # Validate topic
        if not request.topic or len(request.topic.strip()) < 3:
            raise ValidationError(
                "Topic must be at least 3 characters",
                field="topic",
                constraint="min_length=3",
                value=request.topic
            )

        logger.debug(f"  ✓ Topic validation passed")
        
        task_store = get_content_task_store()
        logger.debug(f"  ✓ Got task store")

        # Create task
        logger.debug(f"  📝 Creating task in store...")
        task_id = await task_store.create_task(
            topic=request.topic,
            style=request.style.value,
            tone=request.tone.value,
            target_length=request.target_length,
            tags=request.tags,
            request_type="enhanced" if request.enhanced else "basic",
            task_type=request.task_type,  # ✅ Store task type
            metadata={
                "generate_featured_image": request.generate_featured_image,
            },
        )
        logger.info(f"  ✅ Task created: {task_id}")

        # Update with additional fields stored in metadata
        logger.debug(f"  📝 Updating task with additional fields...")
        update_result = await task_store.update_task(
            task_id,
            {
                # Store categories and environment settings in metadata JSON
                "task_metadata": {
                    "categories": request.categories or [],
                    "publish_mode": request.publish_mode.value,
                    "target_environment": request.target_environment,
                    "llm_provider": request.llm_provider,  # Store LLM provider override
                    "model": request.model,  # Store model override
                },
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

    except ValidationError as e:
        logger.warning(f"⚠️ Validation error: {e.message}")
        raise e.to_http_exception()
    except Exception as e:
        logger.error(f"❌ Error creating content task: {e}", exc_info=True)
        error = handle_error(e)
        raise error.to_http_exception()


@content_router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    description="Get content task status",
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
        task = await task_store.get_task(task_id)
        logger.debug(f"  ✓ Retrieved from store: {task is not None}")

        if not task:
            logger.warning(f"❌ Task not found: {task_id}")
            raise NotFoundError(
                f"Task not found",
                resource_type="task",
                resource_id=task_id
            )

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
                "strapi_post_id": task.get("strapi_id"),  # Post ID if published externally
                "strapi_url": task.get("strapi_url"),  # URL if published externally
                # Include any additional metadata
                "task_metadata": task.get("task_metadata", {}),
            }
        
        return TaskStatusResponse(
            task_id=task_id,
            status=task.get("status", "unknown"),
            progress=task.get("progress"),
            result=result,  # ✅ Now populated with actual data from database
            error={"message": task.get("error_message")} if task.get("error_message") else None,
            created_at=task.get("created_at", ""),
        )

    except NotFoundError as e:
        logger.warning(f"⚠️ Resource not found: {e.message}")
        raise e.to_http_exception()
    except Exception as e:
        logger.error(f"❌ Error getting task status: {e}", exc_info=True)
        error = handle_error(e)
        raise error.to_http_exception()


@content_router.get(
    "/tasks",
    response_model=DraftsListResponse,
    description="List content tasks",
)
async def list_content_tasks(
    task_type: Optional[str] = Query(None, description="Filter by task type (blog_post, social_media, etc.)"),
    status: Optional[str] = Query(None, description="Filter by status (pending, generating, completed, failed)"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    Get list of content tasks (drafts pending review or approval).

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
        drafts, total = await task_store.get_drafts(limit=limit, offset=offset)

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
        error = handle_error(e)
        raise error.to_http_exception()


@content_router.post(
    "/tasks/{task_id}/approve",
    response_model=ApprovalResponse,
    description="✅ Phase 5: Human Approval Gate - Approve or reject task",
)
async def approve_and_publish_task(task_id: str, request: ApprovalRequest):
    """
    ✅ Phase 5: Human Approval Decision Endpoint
    
    **MANDATORY GATE**: Tasks awaiting approval must be explicitly approved or rejected
    by a human reviewer before marking as approved.
    
    This endpoint handles the critical human decision point in the content pipeline:
    - If `approved=true`: Marks content as approved
    - If `approved=false`: Marks task as rejected with feedback
    
    Path Parameters:
        - task_id: Task ID awaiting approval
    
    Request Body (ApprovalRequest):
        - approved (bool): True to approve, False to reject
        - human_feedback (str): Reason for decision (required)
        - reviewer_id (str): Reviewer username/ID (required)
    
    Response:
        - task_id: Task ID
        - approval_status: "approved" or "rejected"
        - strapi_post_id: None (content not published to external CMS)
        - published_url: None (content staged locally)
        - approval_timestamp: Decision time
        - reviewer_id: Who made the decision
        - message: Human-readable status
    
    Errors:
        - 404: Task not found
        - 409: Task not in "awaiting_approval" status
        - 400: Missing or invalid approval data
        - 500: Publishing error
    """
    try:
        task_store = get_content_task_store()
        
        # Use the global database service initialized at startup
        # This ensures the connection pool is properly initialized
        if db_service is None:
            raise RuntimeError(
                "Database service not initialized. "
                "This is a critical error - the application failed to initialize the database service. "
                "Check that the FastAPI app startup completed successfully."
            )
        
        task = await task_store.get_task(task_id)

        if not task:
            logger.error(f"❌ Approval: Task not found {task_id}")
            raise NotFoundError(
                f"Task not found",
                resource_type="task",
                resource_id=task_id
            )

        # ✅ CRITICAL: Check task is awaiting approval
        current_status = task.get("status")
        approval_status = task.get("approval_status", "pending")
        
        if current_status != "awaiting_approval":
            logger.error(
                f"❌ Approval: Task {task_id} not awaiting approval (status={current_status})"
            )
            raise StateError(
                f"Task must be in 'awaiting_approval' status",
                current_state=current_status,
                requested_action="approve"
            )

        approval_timestamp = datetime.now()
        reviewer_id = request.reviewer_id
        human_feedback = request.human_feedback
        
        logger.info(f"\n{'='*80}")
        logger.info(f"🔍 HUMAN APPROVAL DECISION")
        logger.info(f"{'='*80}")
        logger.info(f"   Task ID: {task_id}")
        logger.info(f"   Reviewer: {reviewer_id}")
        logger.info(f"   Decision: {'✅ APPROVED' if request.approved else '❌ REJECTED'}")
        logger.info(f"   Feedback: {human_feedback[:100]}...")
        logger.info(f"{'='*80}\n")

        # ============================================================================
        # CASE 1: HUMAN APPROVED - Mark as approved
        # ============================================================================
        if request.approved:
            logger.info(f"✅ APPROVED: Marking task {task_id} as approved...")
            
            # Get content from task_metadata (where orchestrator stores it)
            task_metadata = task.get("task_metadata", {})
            content = task_metadata.get("content")
            if not content:
                logger.error(f"❌ Task {task_id} has no content")
                raise ValidationError(
                    "Task content is empty",
                    field="content",
                    constraint="required"
                )

            # ✅ Update task with approval metadata
            await task_store.update_task(
                task_id,
                {
                    "status": "approved",
                    "approval_status": "approved",
                    "task_metadata": {
                        "approved_by": reviewer_id,
                        "approval_timestamp": approval_timestamp.isoformat(),
                        "approval_notes": human_feedback,
                        "human_feedback": human_feedback,
                        "publish_mode": "approved",
                        "completed_at": approval_timestamp.isoformat(),
                    }
                }
            )
            
            # ✅ PUBLISH TO CMS DATABASE
            try:
                # Generate slug from title if not provided
                title = task_metadata.get("title", "Untitled")
                slug = task_metadata.get("slug", "")
                if not slug:
                    # Generate slug from title
                    import re
                    import uuid
                    slug = title.lower()
                    slug = re.sub(r'[^a-z0-9]+', '-', slug)
                    slug = slug.strip('-')
                    slug = slug[:80]  # Limit length to allow room for uniqueness suffix
                    # Add short UUID to ensure uniqueness
                    unique_suffix = str(uuid.uuid4())[:8]
                    slug = f"{slug}-{unique_suffix}" if slug else f"post-{unique_suffix}"
                    logger.info(f"📝 Generated unique slug from title: '{title}' → '{slug}'")
                
                post_data = {
                    "title": title,
                    "slug": slug,
                    "content": content,
                    "excerpt": task_metadata.get("excerpt", ""),
                    "featured_image": task_metadata.get("featured_image_url"),
                    "status": "published",
                    "seo_title": task_metadata.get("seo_title"),
                    "seo_description": task_metadata.get("seo_description"),
                    "seo_keywords": task_metadata.get("seo_keywords", ""),
                }
                
                post_result = await db_service.create_post(post_data)
                post_id = post_result.get("id")
                logger.info(f"✅ Post published to CMS database with ID: {post_id}")
                
                # Update task with CMS post ID
                await task_store.update_task(
                    task_id,
                    {
                        "task_metadata": {
                            **task_metadata,
                            "cms_post_id": post_id,
                            "published_at": approval_timestamp.isoformat(),
                        }
                    }
                )
            except Exception as e:
                logger.error(f"❌ Failed to publish post to CMS: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Post approved but publishing failed: {str(e)}"
                )
            
            logger.info(f"✅ Task {task_id} APPROVED and PUBLISHED")
            logger.info(f"{'='*80}\n")
            
            return ApprovalResponse(
                task_id=task_id,
                approval_status="approved",
                strapi_post_id=post_id,
                published_url=f"/posts/{post_data.get('slug')}",
                approval_timestamp=approval_timestamp.isoformat(),
                reviewer_id=reviewer_id,
                message=f"✅ Task approved by {reviewer_id}"
            )

        # ============================================================================
        # CASE 2: HUMAN REJECTED - Mark as rejected with feedback
        # ============================================================================
        else:
            logger.info(f"❌ REJECTED: Marking task {task_id} as rejected...")
            logger.info(f"   📌 Reviewer feedback: {human_feedback}")
            
            # ✅ Update task with rejection metadata
            await task_store.update_task(
                task_id,
                {
                    "status": "rejected",
                    "approval_status": "rejected",
                    "task_metadata": {
                        "approved_by": reviewer_id,
                        "approval_timestamp": approval_timestamp.isoformat(),
                        "approval_notes": human_feedback,
                        "human_feedback": human_feedback,
                        "completed_at": approval_timestamp.isoformat(),
                    }
                }
            )
            
            # ✅ VERIFY database persistence
            try:
                updated_task = await task_store.get_task(task_id)
                if updated_task and updated_task.get("status") == "rejected":
                    logger.info(f"✅ Database verification successful: Task {task_id} status confirmed as 'rejected'")
                else:
                    actual_status = updated_task.get("status") if updated_task else "TASK NOT FOUND"
                    logger.error(f"❌ Database verification failed: Expected status='rejected', got '{actual_status}'")
                    raise Exception(f"Task status update failed verification. Current status: {actual_status}")
            except Exception as verify_error:
                logger.error(f"❌ Failed to verify rejection status in database: {verify_error}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Rejection recorded but verification failed: {str(verify_error)}"
                )
            
            logger.info(f"✅ Task {task_id} REJECTED - Not published")
            logger.info(f"{'='*80}\n")
            
            return ApprovalResponse(
                task_id=task_id,
                approval_status="rejected",
                strapi_post_id=None,
                published_url=None,
                approval_timestamp=approval_timestamp.isoformat(),
                reviewer_id=reviewer_id,
                message=f"❌ Task rejected by {reviewer_id} - Feedback: {human_feedback}"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error in approval endpoint: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"Error processing approval decision: {str(e)}"
        )


@content_router.delete(
    "/tasks/{task_id}",
    description="Delete a task",
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
        logger.debug(f"🟢 DELETE /api/content/tasks/{task_id} called")

        # Get task first to verify it exists
        task = await task_store.get_task(task_id)
        if not task:
            logger.warning(f"⚠️ Task not found: {task_id}")
            raise NotFoundError(
                f"Task not found",
                resource_type="task",
                resource_id=task_id
            )

        # Delete the task
        logger.debug(f"  🗑️  Deleting task {task_id}...")
        await task_store.delete_task(task_id)
        logger.info(f"✅ Task deleted: {task_id}")

        return {
            "task_id": task_id,
            "deleted": True,
            "message": "Task deleted successfully",
        }

    except NotFoundError as e:
        logger.warning(f"⚠️ Resource not found: {e.message}")
        raise e.to_http_exception()
    except Exception as e:
        logger.error(f"❌ Error deleting task: {e}", exc_info=True)
        error = handle_error(e)
        raise error.to_http_exception()


# ============================================================================
# PHASE 4: CONTENT GENERATION & DIRECT CMS PUBLISHING
# ============================================================================
# New unified endpoint for generating content and publishing directly to FastAPI CMS

class GenerateAndPublishRequest(BaseModel):
    """Request model for content generation and direct publishing"""
    topic: str = Field(..., min_length=3, max_length=200, 
                      description="Topic for content generation (3-200 chars)")
    audience: Optional[str] = Field("General audience", min_length=3, max_length=100,
                                   description="Target audience (3-100 chars)")
    keywords: Optional[List[str]] = Field(None,
                                         description="SEO keywords (max 15)")
    style: Optional[ContentStyle] = Field(ContentStyle.EDUCATIONAL, 
                                         description="Content style (EDUCATIONAL/INFORMATIVE/...)")
    tone: Optional[ContentTone] = Field(ContentTone.PROFESSIONAL, 
                                       description="Content tone (PROFESSIONAL/CASUAL/...)")
    length: Optional[str] = Field("medium", 
                                 pattern="^(short|medium|long)$",
                                 description="Content length: short, medium, or long")
    category: Optional[str] = Field(None, min_length=1, max_length=100,
                                   description="Category ID or name (1-100 chars)")
    tags: Optional[List[str]] = Field(None,
                                     description="Tag names (max 10)")
    auto_publish: Optional[bool] = Field(False, description="Immediately publish to site")
    
    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, v):
        """Validate keywords list"""
        if v is None:
            return []
        if len(v) > 15:
            raise ValueError("Maximum 15 keywords allowed")
        return v
    
    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v):
        """Validate tags list"""
        if v is None:
            return []
        if len(v) > 10:
            raise ValueError("Maximum 10 tags allowed")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "topic": "How to Implement AI-Driven Content Generation",
                "audience": "Software developers and content creators",
                "keywords": ["AI", "content", "generation", "automation"],
                "style": "EDUCATIONAL",
                "tone": "PROFESSIONAL",
                "length": "medium",
                "category": "ai-technology",
                "tags": ["AI", "Tutorial", "Best-Practices"],
                "auto_publish": False
            }
        }


@content_router.post(
    "/generate-and-publish",
    description="PHASE 4: Generate content and publish directly to FastAPI CMS",
)
async def generate_and_publish_content(request: GenerateAndPublishRequest, background_tasks: BackgroundTasks):
    """
    PHASE 4: Generate content and publish directly to FastAPI CMS database.

    This endpoint implements direct database publishing, bypassing HTTP layers.

    Request Body:
        - topic: Topic for content generation (required)
        - audience: Target audience (optional)
        - keywords: SEO keywords as list (optional)
        - style: professional|casual|technical|creative (optional)
        - tone: informative|persuasive|engaging|educational (optional)
        - length: short|medium|long (optional)
        - category: Category for post (optional)
        - tags: List of tag names (optional)
        - auto_publish: Immediately publish if true (optional)

    Returns:
        - task_id: Task ID for tracking
        - post_id: Generated post ID in CMS
        - slug: Post slug for URL
        - status: "draft" or "published"
        - view_url: Direct link to published post
        - edit_url: Admin edit URL

    Example:
        POST /api/content/generate-and-publish
        {
            "topic": "The Future of AI in E-commerce",
            "audience": "E-commerce business owners",
            "keywords": ["AI", "e-commerce", "automation"],
            "category": "technology",
            "tags": ["AI", "Automation"],
            "auto_publish": true
        }

    Response:
        {
            "success": true,
            "task_id": "task-12345",
            "post_id": "post-uuid-here",
            "slug": "future-of-ai-in-ecommerce",
            "title": "The Future of AI in E-commerce",
            "status": "published",
            "content_preview": "First 200 chars...",
            "view_url": "https://example.com/posts/future-of-ai-in-ecommerce",
            "edit_url": "https://admin.example.com/posts/post-uuid-here",
            "generated_at": "2025-11-14T04:40:00Z",
            "published_at": "2025-11-14T04:40:30Z"
        }
    """
    try:
        import uuid
        from datetime import datetime
        import psycopg2
        from psycopg2.extras import execute_values

        # Validate request
        if not request.topic or len(request.topic.strip()) < 3:
            raise ValidationError(
                "Topic must be at least 3 characters",
                field="topic",
                constraint="min_length=3",
                value=request.topic
            )

        task_id = str(uuid.uuid4())
        logger.info(f"🟢 POST /api/content/generate-and-publish called - Topic: {request.topic}")
        logger.info(f"PHASE 4: Starting content generation for task {task_id}: {request.topic}")

        # Create task record
        task_store = get_content_task_store()
        logger.debug(f"  ✓ Got task store")
        
        logger.debug(f"  📝 Creating task...")
        # Call create_task with required parameters
        task_id = await task_store.create_task(
            topic=request.topic,
            style=request.style.value if request.style else "educational",
            tone=request.tone.value if request.tone else "professional",
            target_length=len(request.keywords or []) + 1000,  # Simple estimation
            tags=request.tags or [],
            generate_featured_image=True,
            request_type="phase4_direct",
            task_type="blog_post",
            metadata={"audience": request.audience, "category": request.category}
        )
        logger.info(f"  ✅ Task created: {task_id}")
        
        created_at = datetime.utcnow().isoformat()

        # Generate content using existing content service
        content_service = get_content_task_store()
        logger.info(f"  📝 Generating content for: {request.topic}")

        # For now, we'll create a placeholder that demonstrates the endpoint works
        # In production, this would call the full content generation pipeline
        keywords_str = ', '.join(request.keywords or [])
        generated_content = {
            "title": f"{request.topic}",
            "content": f"# {request.topic}\n\nGenerated content for audience: {request.audience}\n\nKeywords: {keywords_str}\n\nThis is generated AI content.",
            "excerpt": f"AI-generated content about {request.topic}",
            "seo_title": f"{request.topic} - Expert Guide",
            "seo_description": f"Learn about {request.topic}. This comprehensive guide covers everything you need to know.",
            "seo_keywords": request.keywords or [],
        }

        # Publish to CMS database
        logger.info(f"  🌐 Publishing to FastAPI CMS: {generated_content['title']}")

        try:
            import psycopg2
            conn = psycopg2.connect(
                host="localhost",
                database="glad_labs_dev",
                user="postgres",
                password="postgres",
                port="5432",
            )
            cur = conn.cursor()
        except psycopg2.Error as e:
            logger.error(f"❌ Database connection failed: {e}")
            raise DatabaseError(
                f"Failed to connect to database",
                details={"error": str(e)}
            )

        try:
            post_id = str(uuid.uuid4())
            slug = generated_content["title"].lower().replace(" ", "-").replace("/", "-")
            
            # Add timestamp to ensure uniqueness
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            slug = f"{slug}-{timestamp}"

            # Get category ID if provided
            category_id = None
            if request.category:
                try:
                    cur.execute(
                        "SELECT id FROM categories WHERE name ILIKE %s OR slug = %s LIMIT 1",
                        (request.category, request.category.lower().replace(" ", "-")),
                    )
                    result = cur.fetchone()
                    if result:
                        category_id = result[0]
                except psycopg2.Error as e:
                    logger.warning(f"⚠️ Could not fetch category: {e}")

            # Get tag IDs
            tag_ids = []
            if request.tags:
                try:
                    placeholders = ",".join(["%s"] * len(request.tags))
                    cur.execute(
                        f"SELECT id FROM tags WHERE name ILIKE ANY(ARRAY[{placeholders}])",
                        request.tags,
                    )
                    tag_ids = [row[0] for row in cur.fetchall()]
                except psycopg2.Error as e:
                    logger.warning(f"⚠️ Could not fetch tags: {e}")

            # Insert post
            try:
                cur.execute(
                    """
                    INSERT INTO posts (
                        id, title, slug, content, excerpt,
                        featured_image_url, cover_image_url,
                        author_id, category_id, tag_ids,
                        seo_title, seo_description, seo_keywords,
                        status, published_at, view_count,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        post_id,
                        generated_content["title"],
                        slug,
                        generated_content["content"],
                        generated_content["excerpt"],
                        f"https://via.placeholder.com/600x400?text={slug}",
                        f"https://via.placeholder.com/1200x400?text={slug}",
                        None,  # author_id - set to None for now
                        category_id,
                        tag_ids,
                        generated_content["seo_title"],
                        generated_content["seo_description"],
                        generated_content["seo_keywords"],
                        "published" if request.auto_publish else "draft",
                        datetime.utcnow() if request.auto_publish else None,
                        0,
                        datetime.utcnow(),
                        datetime.utcnow(),
                    ),
                )
                logger.debug(f"  ✓ Post inserted: {post_id}")

                conn.commit()
                logger.info(f"  ✅ Transaction committed")
            except psycopg2.Error as e:
                conn.rollback()
                logger.error(f"❌ Failed to insert post: {e}")
                raise DatabaseError(
                    f"Failed to publish content",
                    details={"error": str(e)}
                )
            finally:
                cur.close()
                conn.close()

            logger.info(f"✅✅ PHASE 4: Content generated and published successfully: {post_id}")

            return {
                "success": True,
                "task_id": task_id,
                "post_id": post_id,
                "slug": slug,
                "title": generated_content["title"],
                "status": "published" if request.auto_publish else "draft",
                "content_preview": generated_content["content"][:200] + "...",
                "view_url": f"http://localhost:3000/posts/{slug}",
                "edit_url": f"http://localhost:3001/posts/{post_id}",
                "generated_at": created_at,
                "published_at": datetime.utcnow().isoformat() if request.auto_publish else None,
            }

        except psycopg2.Error as e:
            logger.error(f"❌ Database error during publish: {e}", exc_info=True)
            raise DatabaseError(
                f"Failed to publish content",
                details={"error": str(e)}
            )

    except ValidationError as e:
        logger.warning(f"⚠️ Validation error: {e.message}")
        raise e.to_http_exception()
    except DatabaseError as e:
        logger.error(f"⚠️ Database error: {e.message}")
        raise e.to_http_exception()
    except Exception as e:
        logger.error(f"❌ PHASE 4 Error: {str(e)}", exc_info=True)
        error = handle_error(e)
        raise error.to_http_exception()

