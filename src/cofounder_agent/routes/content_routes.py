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

from fastapi import (
    APIRouter,
    HTTPException,
    BackgroundTasks,
    Query,
    Depends,
    Request,
    WebSocketDisconnect,
    WebSocket,
)
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
import logging
import json
from uuid import UUID, uuid4

from routes.auth_unified import get_current_user
from services.content_router_service import (
    ContentStyle,
    ContentTone,
    PublishMode,
    get_content_task_store,
    process_content_generation_task,
)
from services.model_validator import ModelValidator
from schemas.task_status import TaskStatus
from services.database_service import DatabaseService
from services.error_handler import (
    ValidationError,
    NotFoundError,
    StateError,
    DatabaseError,
    ServiceError,
    handle_error,
)
from utils.route_utils import get_database_dependency
from utils.error_responses import ErrorResponseBuilder
from schemas.content_schemas import (
    CreateBlogPostRequest,
    TaskStatusResponse,
    BlogDraftResponse,
    DraftsListResponse,
    PublishDraftRequest,
    ApprovalRequest,
    ApprovalResponse,
    PublishDraftResponse,
    GenerateAndPublishRequest,
)
from schemas.unified_task_response import UnifiedTaskResponse, CreateBlogPostResponse, ProgressInfo

logger = logging.getLogger(__name__)

# ============================================================================
# ROUTER SETUP
# ============================================================================

content_router = APIRouter(prefix="/api/content", tags=["content"])

# ============================================================================
# PRIMARY UNIFIED ENDPOINTS
# ============================================================================


@content_router.post(
    "/tasks",
    response_model=UnifiedTaskResponse,
    status_code=201,
    description="Create a content task (blog post, social media, email, etc.)",
)
async def create_content_task(
    request: CreateBlogPostRequest,
    background_tasks: BackgroundTasks,
    db: DatabaseService = Depends(get_database_dependency),
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
    logger.info(
        f"🟢 POST /api/content/tasks called - Type: {request.task_type} - Topic: {request.topic}"
    )

    try:
        # Validate topic
        if not request.topic or len(request.topic.strip()) < 3:
            raise ValidationError(
                "Topic must be at least 3 characters",
                field="topic",
                constraint="min_length=3",
                value=request.topic,
            )

        logger.debug(f"  ✓ Topic validation passed")

        # ========================================================================
        # VALIDATE MODEL SELECTION
        # ========================================================================
        logger.debug(f"  🤖 Validating model selection...")
        model_validator = ModelValidator()

        # Validate models_by_phase if provided
        if request.models_by_phase:
            is_valid, errors = model_validator.validate_models_by_phase(request.models_by_phase)
            if not is_valid:
                error_details = "; ".join([f"{phase}: {msg}" for phase, msg in errors.items()])
                raise ValidationError(
                    f"Invalid model selection: {error_details}",
                    field="models_by_phase",
                    constraint="valid_models_required",
                    value=request.models_by_phase,
                )
            logger.debug(f"  ✅ Model selection valid: {list(request.models_by_phase.keys())}")

        # Validate quality_preference (auto-select uses this)
        if request.quality_preference:
            valid_preferences = {"budget", "balanced", "quality", "premium"}
            if request.quality_preference.lower() not in valid_preferences:
                raise ValidationError(
                    f"Invalid quality preference: {request.quality_preference}. Valid options: {', '.join(valid_preferences)}",
                    field="quality_preference",
                    constraint="valid_preference",
                    value=request.quality_preference,
                )
            logger.debug(f"  ✅ Quality preference valid: {request.quality_preference}")

        task_store = get_content_task_store(db)
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

        # Calculate estimated costs early (before update call)
        from services.cost_calculator import get_cost_calculator

        cost_calculator = get_cost_calculator()
        models_used = {}
        cost_breakdown = {}
        estimated_cost = 0.0

        if request.models_by_phase:
            # Use specified models with CostCalculator
            cost_result = cost_calculator.calculate_task_cost(request.models_by_phase)
            estimated_cost = cost_result.total_cost
            cost_breakdown = cost_result.by_phase
            models_used = request.models_by_phase
        else:
            # Auto-calculate based on quality preference
            quality_pref = request.quality_preference or "balanced"
            cost_result = cost_calculator.calculate_cost_with_defaults(quality_pref)
            estimated_cost = cost_result.total_cost
            cost_breakdown = cost_result.by_phase
            # Extract models from the default selection
            models_used = cost_calculator._select_default_models(quality_pref)

        # Include costs in the update
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
                    "cost_breakdown": cost_breakdown,
                    "estimated_cost": estimated_cost,
                    "models_used": models_used,
                },
                "estimated_cost": estimated_cost,
                "cost_breakdown": cost_breakdown,
                "model_selections": models_used,
                "quality_preference": request.quality_preference or "balanced",
            },
        )
        logger.debug(f"  ✅ Task updated: {update_result}")

        # ========================================================================
        # Start background content generation with complete pipeline
        # ========================================================================
        logger.debug(f"  ⏳ Starting background content generation task...")
        background_tasks.add_task(
            process_content_generation_task,
            topic=request.topic,
            style=request.style.value,
            tone=request.tone.value,
            target_length=request.target_length,
            tags=request.tags,
            generate_featured_image=request.generate_featured_image,
            database_service=db,
            task_id=task_id,
            # NEW: Model selection parameters (Week 1 cost tracking)
            models_by_phase=request.models_by_phase,
            quality_preference=request.quality_preference,
        )
        logger.debug(f"  ✓ Background task queued with complete parameters")

        logger.info(
            f"✅✅ CONTENT TASK CREATED: {task_id} - Type: {request.task_type} - Topic: {request.topic} - "
            f"Image Search: {request.generate_featured_image} - Estimated cost: ${estimated_cost:.6f} - "
            f"Ready for polling at /api/content/tasks/{task_id}"
        )

        return CreateBlogPostResponse(
            task_id=task_id,
            task_type=request.task_type,  # ✅ Include task type in response
            status="pending",
            topic=request.topic,
            created_at=datetime.now().isoformat(),
            polling_url=f"/api/content/tasks/{task_id}",
            estimated_cost=round(estimated_cost, 6),
            cost_breakdown=cost_breakdown,
            models_used=models_used,
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
    response_model=UnifiedTaskResponse,
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
            raise NotFoundError(f"Task not found", resource_type="task", resource_id=task_id)

        logger.info(
            f"✅ Task status retrieved: {task_id} - status: {task.get('status', 'unknown')}"
        )

        # ✅ FIXED: Build result object from actual database fields
        result = None
        if task.get("status") in ["completed", "failed"]:
            result = {
                "title": task.get("topic", "Untitled"),
                "content": task.get("content", ""),  # ✅ From content field
                "excerpt": task.get("excerpt", ""),  # ✅ From excerpt field
                "summary": task.get("excerpt", ""),  # Alias for excerpt
                "word_count": len(task.get("content", "").split()) if task.get("content") else 0,
                "featured_image_url": task.get(
                    "featured_image_url"
                ),  # ✅ From featured_image_url field
                "featured_image_source": task.get("task_metadata", {}).get("featured_image_source"),
                "model_used": task.get("model_used"),  # ✅ From model_used field
                "quality_score": task.get("quality_score"),  # ✅ From quality_score field
                "tags": task.get("tags", []),
                "strapi_post_id": task.get("strapi_id"),  # Post ID if published externally
                "strapi_url": task.get("strapi_url"),  # URL if published externally
                # Include any additional metadata
                "task_metadata": task.get("task_metadata", {}),
            }

        return UnifiedTaskResponse(
            task_id=task_id,
            id=task_id,
            status=task.get("status", "unknown"),
            progress=(
                ProgressInfo(
                    stage=task.get("stage", "pending"),
                    percentage=task.get("percentage", 0),
                    message=task.get("message"),
                    node=task.get("stage"),
                )
                if task.get("stage")
                else None
            ),
            result=result,  # ✅ Now populated with actual data from database
            error_details=(
                {"message": task.get("error_message")} if task.get("error_message") else None
            ),
            created_at=task.get("created_at", ""),
            updated_at=task.get("updated_at", ""),
            content=result.get("content") if result else None,
            featured_image_url=result.get("featured_image_url") if result else None,
            task_type=task.get("task_type", "blog_post"),
            topic=task.get("topic", ""),
            task_name=task.get("topic", ""),
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
    task_type: Optional[str] = Query(
        None, description="Filter by task type (blog_post, social_media, etc.)"
    ),
    status: Optional[str] = Query(
        None, description="Filter by status (pending, generating, completed, failed)"
    ),
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
            drafts = [
                t
                for t in drafts
                if t.get("task_type") == task_type or t.get("request_type") == task_type
            ]
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
                    word_count=(
                        len(task.get("content", "").split()) if task.get("content") else 0
                    ),  # ✅ Calculate from content
                    summary=task.get("excerpt", ""),  # ✅ Use excerpt field
                )
            )

        return DraftsListResponse(drafts=draft_responses, total=total, limit=limit, offset=offset)

    except Exception as e:
        logger.error(f"Error listing tasks: {e}")
        error = handle_error(e)
        raise error.to_http_exception()


@content_router.post(
    "/tasks/{task_id}/approve",
    response_model=ApprovalResponse,
    description="✅ Phase 5: Human Approval Gate - Approve or reject task",
)
async def approve_and_publish_task(
    task_id: str,
    request: ApprovalRequest,
    db_service: DatabaseService = Depends(get_database_dependency),
):
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

        task = await task_store.get_task(task_id)

        if not task:
            logger.error(f"❌ Approval: Task not found {task_id}")
            raise NotFoundError(f"Task not found", resource_type="task", resource_id=task_id)

        # ✅ CRITICAL: Check task is awaiting approval
        current_status = task.get("status")
        approval_status = task.get("approval_status", "pending")

        # Handle already-published/approved tasks - return success instead of error
        if current_status in ["published", "approved", "completed"]:
            logger.info(f"ℹ️  Task {task_id} is already {current_status}")
            logger.info(f"   Returning success response (idempotent operation)")

            return ApprovalResponse(
                task_id=task_id,
                approval_status="approved",
                strapi_post_id=task.get("strapi_id"),
                published_url=task.get("strapi_url", f"/posts/{task_id}"),
                approval_timestamp=str(task.get("updated_at", approval_timestamp_iso)),
                reviewer_id=task.get("task_metadata", {}).get("approved_by", "system"),
                message=f"Task {task_id} is already {current_status}",
            )

        if current_status != "awaiting_approval":
            logger.error(
                f"❌ Approval: Task {task_id} not awaiting approval (status={current_status})"
            )
            raise StateError(
                f"Task must be in 'awaiting_approval' status",
                current_state=current_status,
                requested_action="approve",
            )

        approval_timestamp = datetime.now()
        approval_timestamp_iso = approval_timestamp.isoformat()  # Convert immediately to ISO format
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

            # Get content from multiple possible locations
            task_metadata = task.get("task_metadata", {})

            # Try to find content in order of priority:
            # 1. Top-level content field
            # 2. task_metadata.content (where orchestrator stores it)
            # 3. result field (if it contains content)
            content = task.get("content")
            content_location = "top-level"

            if not content and isinstance(task_metadata, dict):
                content = task_metadata.get("content")
                content_location = "task_metadata.content"

            if not content:
                result = task.get("result", {})
                if isinstance(result, dict):
                    content = result.get("content")
                    content_location = "result.content"

            if not content:
                logger.error(
                    f"❌ Task {task_id} has no content in any field (checked: content, task_metadata.content, result.content)"
                )
                logger.debug(f"Task fields available: {list(task.keys())}")
                raise ValidationError(
                    "Task content is empty", field="content", constraint="required"
                )

            logger.debug(f"✅ Found content from {content_location} ({len(content)} chars)")

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
                    },
                },
            )

            # ✅ PUBLISH TO CMS DATABASE
            try:
                # ============================================================================
                # USE UNIFIED METADATA SERVICE (Single source of truth)
                # ============================================================================
                from services.unified_metadata_service import get_unified_metadata_service
                from services.cloudinary_cms_service import get_cloudinary_cms_service

                metadata_service = get_unified_metadata_service()
                cloudinary_service = get_cloudinary_cms_service()

                # Extract featured image URL from multiple possible locations
                # Priority: 1) Approval request (from UI), 2) task_metadata, 3) fallback to null
                featured_image_url = None
                logger.debug(f"🔍 Searching for featured_image_url...")
                logger.debug(f"   Request featured_image_url: {request.featured_image_url}")

                # 1️⃣ First check if provided in the approval request (highest priority)
                if request.featured_image_url:
                    featured_image_url = request.featured_image_url
                    logger.info(
                        f"✅ Using featured_image_url from approval request: {featured_image_url[:100]}"
                    )
                # 2️⃣ Then check task_metadata (fallback)
                elif "featured_image_url" in task_metadata:
                    featured_image_url = task_metadata.get("featured_image_url")
                    logger.info(
                        f"✅ Found featured_image_url in task_metadata: {featured_image_url[:100] if featured_image_url else 'EMPTY'}"
                    )
                elif "image" in task_metadata and isinstance(task_metadata["image"], dict):
                    featured_image_url = task_metadata["image"].get("url")
                    logger.info(
                        f"✅ Found featured_image_url in task_metadata.image.url: {featured_image_url[:100] if featured_image_url else 'EMPTY'}"
                    )
                elif "image_url" in task_metadata:
                    featured_image_url = task_metadata.get("image_url")
                    logger.info(
                        f"✅ Found featured_image_url in task_metadata.image_url: {featured_image_url[:100] if featured_image_url else 'EMPTY'}"
                    )
                elif "featured_image" in task_metadata and isinstance(
                    task_metadata["featured_image"], dict
                ):
                    featured_image_url = task_metadata["featured_image"].get("url")
                    logger.info(
                        f"✅ Found featured_image_url in task_metadata.featured_image.url: {featured_image_url[:100] if featured_image_url else 'EMPTY'}"
                    )
                else:
                    logger.warning(
                        f"⚠️  No featured_image_url found in approval request or task_metadata"
                    )

                # ✅ Optimize featured image on Cloudinary for CDN delivery
                featured_image_metadata = {}
                if featured_image_url:
                    logger.debug(f"🎨 Optimizing featured image: {featured_image_url[:100]}...")
                    optimized_url, cloudinary_meta = (
                        await cloudinary_service.optimize_featured_image(
                            featured_image_url, content_title=task_metadata.get("topic")
                        )
                    )
                    featured_image_url = optimized_url
                    featured_image_metadata = cloudinary_meta
                    logger.info(
                        f"✅ Featured image optimized: {cloudinary_meta.get('source')} - {cloudinary_meta.get('optimized')}"
                    )
                else:
                    logger.warning(
                        f"⚠️  No featured_image_url to optimize - image will be empty in post"
                    )

                # Get available categories and tags for matching
                categories = await db_service.get_all_categories()
                tags = await db_service.get_all_tags()

                # Convert CategoryResponse and TagResponse objects to dicts for metadata service
                categories_dict = [
                    {"id": cat.id, "name": cat.name, "description": cat.description}
                    for cat in categories
                ] if categories else None
                tags_dict = [
                    {"id": tag.id, "name": tag.name}
                    for tag in tags
                ] if tags else None

                # ============================================================================
                # BATCH GENERATE ALL METADATA (Most efficient)
                # ============================================================================
                logger.info("🔄 Generating complete metadata...")
                metadata = await metadata_service.generate_all_metadata(
                    content=content,
                    topic=task_metadata.get("topic"),
                    title=task_metadata.get("title"),
                    excerpt=task_metadata.get("excerpt"),
                    featured_image_url=featured_image_url,
                    available_categories=categories_dict,
                    available_tags=tags_dict,
                    author_id=task_metadata.get("author_id"),
                )

                logger.info(
                    f"✅ Metadata generated: title={metadata.title[:50]}, "
                    f"category={metadata.category_name}, tags={len(metadata.tag_ids)}"
                )

                # Use Poindexter AI UUID as default reviewer/system user
                DEFAULT_SYSTEM_AUTHOR_ID = "14c9cad6-57ca-474a-8a6d-fab897388ea8"
                reviewer_author_id = DEFAULT_SYSTEM_AUTHOR_ID

                # Build post data from unified metadata
                # ✅ CRITICAL: Convert tag_ids UUIDs to strings for Pydantic validation
                tag_ids_str = None
                if metadata.tag_ids:
                    tag_ids_str = [str(tag_id) for tag_id in metadata.tag_ids]
                    logger.debug(f"✅ Converted tag_ids to strings: {tag_ids_str}")
                
                post_data = {
                    "id": task_metadata.get("post_id"),
                    "title": metadata.title,  # ✅ Extracted/generated
                    "slug": metadata.slug,  # ✅ Generated from title
                    "content": content,
                    "excerpt": metadata.excerpt,  # ✅ Generated
                    "featured_image_url": metadata.featured_image_url,
                    "cover_image_url": task_metadata.get("cover_image_url"),
                    "author_id": str(metadata.author_id) if metadata.author_id else None,  # ✅ Convert to string
                    "category_id": str(metadata.category_id) if metadata.category_id else None,  # ✅ Convert to string
                    "tag_ids": tag_ids_str,  # ✅ Extracted and converted to strings
                    "status": "published",
                    "seo_title": metadata.seo_title,  # ✅ Generated
                    "seo_description": metadata.seo_description,  # ✅ Generated
                    "seo_keywords": metadata.seo_keywords,  # ✅ Generated
                    "created_by": reviewer_author_id,  # System UUID for created_by (reviewer who approved)
                    "updated_by": reviewer_author_id,  # System UUID for updated_by (reviewer who approved)
                }

                logger.debug(
                    f"📝 Post data prepared:"
                    f"\n  - featured_image_url={post_data.get('featured_image_url')}"
                    f"\n  - title={post_data.get('title')[:50]}"
                    f"\n  - slug={post_data.get('slug')}"
                )

                post_result = await db_service.create_post(post_data)
                post_id = post_result.get("id")
                
                # ✅ Verify featured_image_url was persisted
                logger.info(
                    f"✅ Post published to CMS database with ID: {post_id}"
                    f"\n  - Featured image in DB: {post_result.get('featured_image_url')}"
                )

                # Update task with CMS post ID and published timestamp
                await task_store.update_task(
                    task_id,
                    {
                        "status": "published",
                        "published_at": approval_timestamp_iso,
                        "completed_at": approval_timestamp_iso,
                        "task_metadata": {
                            **task_metadata,
                            "cms_post_id": post_id,
                            "published_at": approval_timestamp_iso,
                            "published_to_db": True,
                        },
                    },
                )
            except Exception as e:
                logger.error(f"❌ Failed to publish post to CMS: {e}")
                raise HTTPException(
                    status_code=500, detail=f"Post approved but publishing failed: {str(e)}"
                )

            logger.info(f"✅ Task {task_id} APPROVED and PUBLISHED")
            logger.info(f"{'='*80}\n")

            return ApprovalResponse(
                task_id=task_id,
                approval_status="approved",
                strapi_post_id=post_id,
                published_url=f"/posts/{post_data.get('slug')}",
                approval_timestamp=approval_timestamp_iso,
                reviewer_id=reviewer_id,
                message=f"✅ Task approved by {reviewer_id}",
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
                        "approval_timestamp": approval_timestamp_iso,
                        "approval_notes": human_feedback,
                        "human_feedback": human_feedback,
                        "completed_at": approval_timestamp_iso,
                    },
                },
            )

            # ✅ VERIFY database persistence
            try:
                updated_task = await task_store.get_task(task_id)
                if updated_task and updated_task.get("status") == "rejected":
                    logger.info(
                        f"✅ Database verification successful: Task {task_id} status confirmed as 'rejected'"
                    )
                else:
                    actual_status = updated_task.get("status") if updated_task else "TASK NOT FOUND"
                    logger.error(
                        f"❌ Database verification failed: Expected status='rejected', got '{actual_status}'"
                    )
                    raise Exception(
                        f"Task status update failed verification. Current status: {actual_status}"
                    )
            except Exception as verify_error:
                logger.error(f"❌ Failed to verify rejection status in database: {verify_error}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Rejection recorded but verification failed: {str(verify_error)}",
                )

            logger.info(f"✅ Task {task_id} REJECTED - Not published")
            logger.info(f"{'='*80}\n")

            return ApprovalResponse(
                task_id=task_id,
                approval_status="rejected",
                strapi_post_id=None,
                published_url=None,
                approval_timestamp=approval_timestamp_iso,
                reviewer_id=reviewer_id,
                message=f"❌ Task rejected by {reviewer_id} - Feedback: {human_feedback}",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error in approval endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing approval decision: {str(e)}")


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
            raise NotFoundError(f"Task not found", resource_type="task", resource_id=task_id)

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


@content_router.post(
    "/generate-and-publish",
    description="PHASE 4: Generate content and publish directly to FastAPI CMS",
)
async def generate_and_publish_content(
    request: GenerateAndPublishRequest, background_tasks: BackgroundTasks
):
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
                value=request.topic,
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
            metadata={"audience": request.audience, "category": request.category},
        )
        logger.info(f"  ✅ Task created: {task_id}")

        created_at = datetime.utcnow().isoformat()

        # Generate content using existing content service
        content_service = get_content_task_store()
        logger.info(f"  📝 Generating content for: {request.topic}")

        # For now, we'll create a placeholder that demonstrates the endpoint works
        # In production, this would call the full content generation pipeline
        keywords_str = ", ".join(request.keywords or [])
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
            raise DatabaseError(f"Failed to connect to database", details={"error": str(e)})

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
                        None,  # featured_image_url - None instead of placeholder
                        None,  # featured_image_alt - None instead of placeholder
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
                raise DatabaseError(f"Failed to publish content", details={"error": str(e)})
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
            raise DatabaseError(f"Failed to publish content", details={"error": str(e)})

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


# ============================================================================
# LANGGRAPH ENDPOINTS (NEW - Graph-based orchestration)
# ============================================================================


class BlogPostLangGraphRequest(BaseModel):
    """LangGraph blog post creation request"""

    topic: str = Field(..., description="Blog post topic")
    keywords: List[str] = Field(default_factory=list, description="SEO keywords")
    audience: str = Field(default="general", description="Target audience")
    tone: str = Field(default="professional", description="Writing tone")
    word_count: int = Field(default=800, description="Target word count")


class BlogPostLangGraphResponse(BaseModel):
    """LangGraph blog post creation response"""

    request_id: str
    task_id: str
    status: str
    message: str
    ws_endpoint: str


@content_router.post(
    "/langgraph/blog-posts",
    response_model=BlogPostLangGraphResponse,
    status_code=202,
    tags=["langgraph"],
    description="Create blog post using LangGraph workflow engine",
)
async def create_blog_post_langgraph(request: BlogPostLangGraphRequest, http_request: Request):
    """
    Create blog post using LangGraph pipeline.

    Returns WebSocket endpoint for streaming progress.

    Features:
    - Graph-based workflow with automatic state management
    - Quality assessment with refinement loops
    - Real-time progress streaming
    - Metadata generation
    """
    logger.info(f"Creating blog post via LangGraph: {request.topic}")

    # Get the FastAPI app from the request
    app = http_request.app if http_request else None
    langgraph = getattr(app.state, "langgraph_orchestrator", None) if app else None

    if not langgraph:
        logger.error("LangGraph orchestrator not available")
        raise HTTPException(status_code=503, detail="LangGraph orchestrator not available")

    # Get user info from token if available, otherwise use anonymous
    auth_header = http_request.headers.get("Authorization", "")
    user_id = "anonymous"

    if auth_header.startswith("Bearer "):
        try:
            from services.token_validator import JWTTokenValidator

            token = auth_header[7:]
            claims = JWTTokenValidator.verify_token(token)
            user_id = claims.get("user_id", "anonymous")
        except Exception as e:
            logger.warning(f"Token validation failed: {str(e)}, using anonymous user")

    # Execute pipeline (non-streaming)
    try:
        result = await langgraph.execute_content_pipeline(
            request_data=request.dict(), user_id=user_id, stream=False
        )
    except Exception as e:
        logger.error(f"Pipeline execution error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Pipeline execution failed: {str(e)}")

    if not result.get("success"):
        raise HTTPException(
            status_code=500, detail=result.get("error", "Pipeline execution failed")
        )

    return BlogPostLangGraphResponse(
        request_id=result["request_id"],
        task_id=result["task_id"],
        status=result["status"],
        message=f"Pipeline completed with {result['refinement_count']} refinements",
        ws_endpoint=f"/api/content/langgraph/ws/blog-posts/{result['request_id']}",
    )


@content_router.websocket("/langgraph/ws/blog-posts/{request_id}")
async def websocket_blog_creation(
    websocket: WebSocket, request_id: str, db: DatabaseService = Depends(get_database_dependency)
):
    """
    WebSocket endpoint for real-time blog creation progress.

    Streams real task progress from PostgreSQL database.

    Stream events:
    - type: "progress" - Current phase and progress percentage
    - type: "complete" - Pipeline finished
    - type: "error" - Pipeline failed
    """
    await websocket.accept()
    logger.info(f"🔌 WebSocket connected for request {request_id}")

    try:
        import asyncio

        # Map task phases to expected pipeline stages
        phase_mapping = {
            "pending": 5,
            "research": 15,
            "outline": 25,
            "draft": 50,
            "assess": 70,
            "refine": 85,
            "finalize": 95,
            "completed": 100,
            "published": 100,
            "approved": 80,
            "awaiting_approval": 75,
        }

        last_progress = 0
        poll_interval = 1  # Check database every 1 second
        timeout = 600  # 10 minutes max wait
        elapsed = 0

        while elapsed < timeout:
            try:
                # Fetch real task progress from database
                task = await db.get_task(request_id)

                if not task:
                    # Task not found - may still be created
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval
                    continue

                # Extract real progress data from database
                current_stage = task.get("stage", "pending")
                current_percentage = task.get("percentage", 0)
                task_status = task.get("status", "pending")
                message = task.get("message", f"Processing {current_stage}")

                # Send progress if it changed
                if current_percentage != last_progress or elapsed == 0:
                    await websocket.send_json(
                        {
                            "type": "progress",
                            "node": current_stage,
                            "progress": current_percentage,
                            "status": task_status,
                            "message": message,
                            "timestamp": datetime.utcnow().isoformat(),
                        }
                    )
                    logger.debug(f"📤 WebSocket progress: {current_stage} {current_percentage}%")
                    last_progress = current_percentage

                # Check if task is complete
                if task_status in ["completed", "published", "approved"]:
                    await websocket.send_json(
                        {
                            "type": "complete",
                            "request_id": request_id,
                            "status": task_status,
                            "percentage": 100,
                            "content": task.get("content"),
                            "featured_image_url": task.get("featured_image_url"),
                            "timestamp": datetime.utcnow().isoformat(),
                        }
                    )
                    logger.info(f"✅ Task completed via WebSocket: {request_id}")
                    break

                # Check if task failed
                elif task_status in ["failed", "rejected"]:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "request_id": request_id,
                            "status": task_status,
                            "error": task.get("error_message", "Task execution failed"),
                            "timestamp": datetime.utcnow().isoformat(),
                        }
                    )
                    logger.warning(f"⚠️ Task failed via WebSocket: {request_id}")
                    break

                # Poll database again
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

            except Exception as e:
                logger.error(f"❌ Error fetching task progress: {e}", exc_info=True)
                await websocket.send_json(
                    {"type": "error", "error": f"Failed to fetch progress: {str(e)}"}
                )
                break

        # Timeout occurred
        if elapsed >= timeout:
            logger.warning(f"⏱️ WebSocket timeout for request {request_id}")
            await websocket.send_json(
                {"type": "error", "error": "Task execution timeout - check task status manually"}
            )

    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket disconnected: {request_id}")
    except Exception as e:
        logger.error(f"🔌 WebSocket error: {str(e)}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "error": str(e)})
        except RuntimeError as send_err:
            logger.debug(f"Failed to send error message over WebSocket: {send_err}")
    finally:
        try:
            await websocket.close()
            logger.info(f"🔌 WebSocket closed: {request_id}")
        except RuntimeError as close_err:
            logger.debug(f"WebSocket already closed: {close_err}")
