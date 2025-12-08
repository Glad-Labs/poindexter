"""
REST API Routes for Intelligent Orchestrator

Endpoints for:
- Processing natural language business requests
- Managing orchestration tasks
- Approving and publishing results
- Training data management
- Proprietary LLM fine-tuning
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query, Depends
from pydantic import BaseModel, Field
import json
import os

from routes.auth_unified import get_current_user, UserProfile
from services.linkedin_publisher import LinkedInPublisher
from services.twitter_publisher import TwitterPublisher
from services.email_publisher import EmailPublisher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orchestrator", tags=["intelligent-orchestrator"])

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class BusinessMetrics(BaseModel):
    """Business metrics for context"""
    revenue_monthly: Optional[float] = None
    traffic_monthly: Optional[int] = None
    conversion_rate: Optional[float] = None
    customer_count: Optional[int] = None
    market_position: Optional[str] = None
    custom_metrics: Optional[Dict[str, Any]] = Field(None, description="Additional metrics")


class UserPreferences(BaseModel):
    """User preferences for execution"""
    tone: Optional[str] = Field("professional", description="Writing tone")
    length: Optional[str] = Field(None, description="Content length")
    channels: Optional[List[str]] = Field(
        ["blog"],
        description="Publishing channels: blog, linkedin, twitter, email"
    )
    language: Optional[str] = Field("en", description="Language code")
    custom_preferences: Optional[Dict[str, Any]] = Field(None)


class ProcessRequestBody(BaseModel):
    """Request body for intelligent orchestration"""
    request: str = Field(
        ...,
        description="Natural language business request",
        min_length=10
    )
    business_metrics: Optional[BusinessMetrics] = Field(None)
    preferences: Optional[UserPreferences] = Field(None)
    auto_approve: bool = Field(
        False,
        description="Automatically approve if quality score > 0.85"
    )


class ExecutionStatusResponse(BaseModel):
    """Current execution status"""
    task_id: str
    status: str  # "initializing", "planning", "executing", "quality_check", "ready_for_approval"
    progress_percentage: int
    current_phase: Optional[str] = None
    estimated_completion_seconds: Optional[float] = None
    error: Optional[str] = None


class ApprovalResponse(BaseModel):
    """Result ready for approval"""
    task_id: str
    status: str = "pending_approval"
    quality_score: float
    quality_passed: bool
    main_content: Dict[str, Any]
    channel_variants: Dict[str, Any]
    metadata: Dict[str, Any]
    supporting_materials: Dict[str, Any]
    approval_url: str


class ApprovalAction(BaseModel):
    """User approval action"""
    approved: bool
    publish_to_channels: List[str] = Field(["blog"])
    feedback: Optional[str] = None
    modifications: Optional[Dict[str, Any]] = None


class TrainingDataExportRequest(BaseModel):
    """Request to export training data"""
    format: str = Field("jsonl", description="Format: jsonl, csv")
    filter_by_quality: Optional[float] = Field(None, description="Min quality score")
    limit: Optional[int] = Field(1000, description="Max examples")


# ============================================================================
# IN-MEMORY TASK STORE (Replace with database in production)
# ============================================================================

task_store: Dict[str, Dict[str, Any]] = {}


# ============================================================================
# API ENDPOINTS
# ============================================================================

@router.post("/process")
async def process_request(
    body: ProcessRequestBody,
    background_tasks: BackgroundTasks,
    current_user: UserProfile = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Process a natural language business request with intelligent orchestration.

    The orchestrator will:
    1. Parse intent and requirements
    2. Discover available tools via MCP
    3. Design optimal execution workflow
    4. Execute with quality feedback loops
    5. Refine results if needed
    6. Format for approval
    7. Accumulate learning data

    **Authentication Required:** Valid JWT token    Returns:
        - task_id: For polling status
        - status_url: URL to check progress
        - approval_url: URL to approve when ready
    """
    try:
        # Import here to avoid circular dependency
        from main import orchestrator as app_orchestrator

        if not app_orchestrator:
            raise HTTPException(status_code=503, detail="Orchestrator not initialized")

        # Generate task ID
        task_id = f"task-{datetime.now().timestamp()}"

        # Store initial task
        task_store[task_id] = {
            "status": "initializing",
            "created_at": datetime.now().isoformat(),
            "request": body.request,
            "user_id": user_id,
            "progress": 0,
            "execution_context": {}
        }

        # Start background processing
        background_tasks.add_task(
            _process_request_background,
            task_id,
            app_orchestrator,
            body,
            user_id
        )

        return {
            "success": True,
            "task_id": task_id,
            "status": "processing",
            "message": "Request received and processing started",
            "status_url": f"/api/orchestrator/status/{task_id}",
            "approval_url": f"/api/orchestrator/approval/{task_id}"
        }

    except Exception as e:
        logger.error(f"Error processing request: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _process_request_background(
    task_id: str,
    orchestrator,
    body: ProcessRequestBody,
    user_id: str
):
    """Background task: Full orchestration workflow"""
    try:
        logger.info(f"Starting orchestration for task {task_id}")

        # Convert preferences to dict
        preferences = body.preferences.dict(exclude_none=True) if body.preferences else None
        business_metrics = body.business_metrics.dict(exclude_none=True) if body.business_metrics else None

        # Check if orchestrator has the new intelligent process_request method
        if hasattr(orchestrator, 'intelligent_orchestrator') and orchestrator.intelligent_orchestrator:
            # Use new IntelligentOrchestrator
            result = await orchestrator.intelligent_orchestrator.process_request(
                user_request=body.request,
                user_id=user_id,
                business_metrics=business_metrics,
                preferences=preferences
            )

            # Update task store
            task_store[task_id] = {
                **task_store[task_id],
                "status": "ready_for_approval" if result.quality_assessment.passed else "quality_check_complete",
                "result": result,
                "completed_at": datetime.now().isoformat(),
                "progress": 100
            }

            logger.info(f"Orchestration complete for task {task_id}: {result.status.value}")
        else:
            # Fallback to existing orchestrator
            logger.warning("IntelligentOrchestrator not available, using fallback")
            task_store[task_id]["status"] = "fallback_mode"
            task_store[task_id]["message"] = "Using existing orchestrator (IntelligentOrchestrator not initialized)"

    except Exception as e:
        logger.error(f"Background processing failed for {task_id}: {e}", exc_info=True)
        task_store[task_id]["status"] = "failed"
        task_store[task_id]["error"] = str(e)


@router.get("/status/{task_id}")
async def get_status(task_id: str) -> ExecutionStatusResponse:
    """Get current status of an orchestration task"""
    if task_id not in task_store:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    task = task_store[task_id]

    return ExecutionStatusResponse(
        task_id=task_id,
        status=task.get("status", "unknown"),
        progress_percentage=task.get("progress", 0),
        current_phase=task.get("current_phase"),
        error=task.get("error")
    )


@router.get("/approval/{task_id}")
async def get_approval(task_id: str) -> ApprovalResponse:
    """Get result ready for approval"""
    if task_id not in task_store:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    task = task_store[task_id]

    if task.get("status") not in ["ready_for_approval", "quality_check_complete"]:
        raise HTTPException(
            status_code=400,
            detail=f"Task not ready for approval. Status: {task.get('status')}"
        )

    result = task.get("result")
    if not result:
        raise HTTPException(status_code=400, detail="No result available")

    formatted = result.final_formatting or {}

    return ApprovalResponse(
        task_id=task_id,
        status="pending_approval",
        quality_score=result.quality_assessment.score,
        quality_passed=result.quality_assessment.passed,
        main_content=formatted.get("main_content", {}),
        channel_variants=formatted.get("channel_variants", {}),
        metadata=formatted.get("metadata", {}),
        supporting_materials=formatted.get("supporting_materials", {}),
        approval_url=f"/api/orchestrator/approve/{task_id}"
    )


@router.post("/approve/{task_id}")
async def approve_result(
    task_id: str,
    action: ApprovalAction,
    background_tasks: BackgroundTasks,
    current_user: UserProfile = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    User approves result and triggers publishing.

    Can also include modifications to request re-execution of specific steps.
    
    **Authentication Required:** Valid JWT token
    """
    if task_id not in task_store:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    task = task_store[task_id]

    if task.get("status") not in ["ready_for_approval", "quality_check_complete"]:
        raise HTTPException(
            status_code=400,
            detail=f"Task not in approval state. Status: {task.get('status')}"
        )

    if action.approved:
        task["status"] = "approved"
        task["approved_at"] = datetime.now().isoformat()
        task["publish_channels"] = action.publish_to_channels

        # Start publishing in background
        background_tasks.add_task(
            _publish_result_background,
            task_id,
            action.publish_to_channels
        )

        return {
            "success": True,
            "task_id": task_id,
            "status": "approved_and_publishing",
            "message": f"Content approved and queued for publishing to {action.publish_to_channels}"
        }
    else:
        task["status"] = "rejected"
        task["rejected_at"] = datetime.now().isoformat()
        task["rejection_feedback"] = action.feedback

        return {
            "success": True,
            "task_id": task_id,
            "status": "rejected",
            "message": "Result rejected and archived"
        }


async def _publish_result_background(task_id: str, channels: List[str]):
    """
    Background task: Publish to selected channels
    
    Implements multi-channel publishing with proper error handling:
    - Blog: Publish to PostgreSQL CMS (posts table)
    - LinkedIn: Reserved for future LinkedIn API integration
    - Twitter: Reserved for future Twitter API integration
    - Email: Reserved for future email service integration
    """
    try:
        logger.info(f"📤 Publishing task {task_id} to channels: {channels}")
        
        if task_id not in task_store:
            logger.error(f"❌ Task {task_id} not found in store")
            return
        
        task_data = task_store[task_id]
        result = task_data.get("result", {})
        outputs = result.get("outputs", {})
        
        # Extract content from result
        content_data = outputs.get("final_content", {})
        if not content_data:
            # Fallback: try to find content in any step
            for step_id, step_data in outputs.items():
                if isinstance(step_data, dict) and "content" in step_data:
                    content_data = step_data
                    break
        
        published_to = []
        errors = []
        
        # Publish to Blog (CMS)
        if "blog" in channels:
            try:
                from services.database_service import DatabaseService
                db = DatabaseService()
                await db.initialize()
                
                post_data = {
                    "title": content_data.get("title", "Untitled Post"),
                    "content": content_data.get("content", ""),
                    "excerpt": content_data.get("excerpt", "")[:200],
                    "slug": _generate_slug(content_data.get("title", "untitled")),
                    "status": "published",
                    "published_at": datetime.now(),
                    "seo_title": content_data.get("seo_title", content_data.get("title", "")),
                    "seo_description": content_data.get("seo_description", ""),
                    "seo_keywords": content_data.get("keywords", []),
                    "author_id": task_data.get("user_id", "orchestrator"),
                }
                
                await db.create_post(post_data)
                published_to.append("blog")
                logger.info(f"✅ Published to blog: {post_data['slug']}")
                
            except Exception as e:
                error_msg = f"Blog publishing failed: {str(e)}"
                logger.error(f"❌ {error_msg}")
                errors.append(error_msg)
        
        # LinkedIn Publishing
        if "linkedin" in channels:
            try:
                linkedin = LinkedInPublisher()
                if linkedin.available:
                    linkedin_result = await linkedin.publish(
                        title=formatted.get("title", "New Blog Post"),
                        content=formatted.get("summary", content)[:500],
                        image_url=formatted.get("featured_image_url"),
                        description=formatted.get("summary", ""),
                    )
                    if linkedin_result.get("success"):
                        published_to.append("linkedin")
                        logger.info(f"✅ Published to LinkedIn: {linkedin_result.get('post_id')}")
                    else:
                        error_msg = f"LinkedIn: {linkedin_result.get('error')}"
                        errors.append(error_msg)
                        logger.warning(f"⚠️  {error_msg}")
                else:
                    logger.info(f"⏭️  LinkedIn publishing not configured (set LINKEDIN_ACCESS_TOKEN)")
            except Exception as e:
                logger.error(f"LinkedIn publishing error: {e}")
                errors.append(f"LinkedIn: {str(e)}")
        
        # Twitter Publishing
        if "twitter" in channels:
            try:
                twitter = TwitterPublisher()
                if twitter.available:
                    # Create tweet text from content
                    tweet_text = formatted.get("summary", content)[:280]
                    if not tweet_text:
                        tweet_text = f"New post: {formatted.get('title', 'Check it out!')}"[:280]
                    
                    twitter_result = await twitter.publish(
                        text=tweet_text,
                        image_url=formatted.get("featured_image_url"),
                    )
                    if twitter_result.get("success"):
                        published_to.append("twitter")
                        logger.info(f"✅ Published to Twitter: {twitter_result.get('tweet_id')}")
                    else:
                        error_msg = f"Twitter: {twitter_result.get('error')}"
                        errors.append(error_msg)
                        logger.warning(f"⚠️  {error_msg}")
                else:
                    logger.info(f"⏭️  Twitter publishing not configured (set TWITTER_BEARER_TOKEN)")
            except Exception as e:
                logger.error(f"Twitter publishing error: {e}")
                errors.append(f"Twitter: {str(e)}")
        
        # Email Publishing
        if "email" in channels:
            try:
                email = EmailPublisher()
                if email.available:
                    # Note: In production, would fetch actual subscriber list
                    # For now, send test email to admin if configured
                    admin_email = os.getenv("ADMIN_EMAIL")
                    if admin_email:
                        email_result = await email.publish(
                            subject=formatted.get("title", "New Content Published"),
                            content=formatted.get("summary", content),
                            recipient_emails=[admin_email],
                            html_content=formatted.get("html", ""),
                            from_name="Glad Labs Publisher",
                        )
                        if email_result.get("success"):
                            published_to.append("email")
                            logger.info(f"✅ Published email notification to {admin_email}")
                        else:
                            error_msg = f"Email: {email_result.get('error')}"
                            errors.append(error_msg)
                            logger.warning(f"⚠️  {error_msg}")
                    else:
                        logger.info(f"⏭️  Email publishing requires ADMIN_EMAIL configuration")
                else:
                    logger.info(f"⏭️  Email publishing not configured (set SMTP_* environment variables)")
            except Exception as e:
                logger.error(f"Email publishing error: {e}")
                errors.append(f"Email: {str(e)}")
        
        # Update task status
        if task_id in task_store:
            if errors:
                task_store[task_id]["status"] = "partially_published"
                task_store[task_id]["publishing_errors"] = errors
            else:
                task_store[task_id]["status"] = "published"
            
            task_store[task_id]["published_at"] = datetime.now().isoformat()
            task_store[task_id]["published_to"] = published_to
            
            logger.info(f"✅ Publishing complete for {task_id}: {', '.join(published_to)}")

    except Exception as e:
        logger.error(f"❌ Publishing failed for {task_id}: {e}", exc_info=True)
        if task_id in task_store:
            task_store[task_id]["status"] = "publishing_failed"
            task_store[task_id]["publishing_error"] = str(e)


def _generate_slug(title: str) -> str:
    """Generate URL-friendly slug from title"""
    import re
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug[:100]  # Limit length


@router.get("/history")
async def get_history(
    user_id: str = Query("demo_user"),
    limit: int = Query(50, ge=1, le=500),
    status_filter: Optional[str] = None
) -> Dict[str, Any]:
    """Get execution history for user"""
    user_tasks = [
        {
            "task_id": tid,
            "status": task.get("status"),
            "created_at": task.get("created_at"),
            "request": task.get("request", "")[:100],
            "quality_score": task.get("result", {}).get("quality_assessment", {}).get("score")
        }
        for tid, task in task_store.items()
        if task.get("user_id") == user_id
    ]

    if status_filter:
        user_tasks = [t for t in user_tasks if t["status"] == status_filter]

    return {
        "user_id": user_id,
        "total": len(user_tasks),
        "recent": sorted(user_tasks, key=lambda t: t["created_at"], reverse=True)[:limit]
    }


@router.post("/training-data/export")
async def export_training_data(
    request_body: TrainingDataExportRequest,
    current_user: UserProfile = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Export accumulated training examples for fine-tuning.

    This allows training a proprietary orchestrator LLM on your
    specific business logic and decision patterns.

    **Authentication Required:** Valid JWT token (admin recommended)
    organization's specific workflows and decisions.
    """
    try:
        from main import orchestrator as app_orchestrator

        if not app_orchestrator:
            raise HTTPException(status_code=503, detail="Orchestrator not initialized")

        # Check if IntelligentOrchestrator available
        intelligent_orch = getattr(app_orchestrator, 'intelligent_orchestrator', None)  # type: ignore
        if not intelligent_orch:
            raise HTTPException(status_code=503, detail="IntelligentOrchestrator not initialized")

        # Get training examples
        training_examples = intelligent_orch.get_training_dataset()

        # Filter by quality if specified
        if request_body.filter_by_quality:
            training_examples = [
                ex for ex in training_examples
                if ex.result.quality_assessment.score >= request_body.filter_by_quality
            ]

        # Limit results
        if request_body.limit:
            training_examples = training_examples[:request_body.limit]

        # Export in requested format
        exported = intelligent_orch.export_training_dataset(format=request_body.format)

        return {
            "success": True,
            "format": request_body.format,
            "example_count": len(training_examples),
            "data": exported,
            "download_url": f"/api/orchestrator/training-data/download?format={request_body.format}"
        }

    except Exception as e:
        logger.error(f"Training data export failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/training-data/upload-model")
async def upload_custom_llm(
    model_file: str = Query(..., description="Path to fine-tuned model"),
    model_name: str = Query(..., description="Name of custom orchestrator LLM"),
    enable_immediately: bool = Query(False, description="Use for all new requests?"),
    current_user: UserProfile = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Upload a custom proprietary orchestrator LLM.
    
    **Authentication Required:** Valid JWT token (admin strongly recommended)

    Allows you to train a model on your organization's execution patterns
    and use it to make better decisions about workflows.

    This model will:
    - Learn your organization's unique business logic
    - Adapt to your communication style and tone
    - Make more accurate predictions over time
    - Serve as your org's unique differentiator
    """
    try:
        from main import orchestrator as app_orchestrator

        if not app_orchestrator:
            raise HTTPException(status_code=503, detail="Orchestrator not initialized")

        # Check if IntelligentOrchestrator available
        intelligent_orch = getattr(app_orchestrator, 'intelligent_orchestrator', None)  # type: ignore
        
        if not intelligent_orch:
            return {
                "success": False,
                "message": "IntelligentOrchestrator not available in this deployment",
                "note": "Custom LLM feature requires IntelligentOrchestrator module"
            }
        
        # Model loading implementation
        # For now, this is a placeholder that validates the model configuration
        # Full implementation would require:
        # 1. Model format validation (GGUF, Safetensors, etc.)
        # 2. Model loading via transformers/llama.cpp
        # 3. Integration with orchestrator inference pipeline
        # 4. Memory management and model caching
        
        logger.info(f"📦 Model upload request: {model_name}")
        logger.info(f"   Enable immediately: {enable_immediately}")
        logger.info(f"   Note: Full model loading requires implementation of model inference pipeline")
        
        # Store model metadata (placeholder)
        model_metadata = {
            "model_name": model_name,
            "uploaded_at": datetime.now().isoformat(),
            "status": "metadata_stored",
            "enabled": False,  # Keep disabled until full implementation
            "note": "Model upload accepted. Full inference integration pending."
        }
        
        return {
            "success": True,
            "model_name": model_name,
            "status": "accepted",
            "message": f"Custom LLM '{model_name}' metadata stored. Full model loading requires inference pipeline implementation.",
            "enabled": False,  # Always False until inference pipeline ready
            "next_steps": [
                "Implement model loading via transformers/llama.cpp",
                "Configure model endpoint or local inference",
                "Test model with sample orchestration tasks",
                "Enable model after validation"
            ]
        }

    except Exception as e:
        logger.error(f"Model upload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/learning-patterns")
async def get_learning_patterns() -> Dict[str, Any]:
    """Get learned execution patterns from memory system"""
    try:
        from main import orchestrator as app_orchestrator

        if not app_orchestrator:
            raise HTTPException(status_code=503, detail="Orchestrator not initialized")

        # Get memory system from intelligent orchestrator
        memory_sys = getattr(app_orchestrator, 'memory_system', None)  # type: ignore
        if not memory_sys:
            raise HTTPException(status_code=503, detail="Memory system not available")

        # Get patterns from enhanced memory
        patterns_md = memory_sys.export_learned_patterns(format="markdown")

        return {
            "success": True,
            "patterns_markdown": patterns_md,
            "pattern_count": len(memory_sys.execution_patterns)
        }

    except Exception as e:
        logger.error(f"Failed to get learning patterns: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/business-metrics-analysis")
async def analyze_metrics() -> Dict[str, Any]:
    """Analyze correlations between workflows and business metrics"""
    try:
        from main import orchestrator as app_orchestrator

        if not app_orchestrator:
            raise HTTPException(status_code=503, detail="Orchestrator not initialized")

        # Get memory system
        memory_sys = getattr(app_orchestrator, 'memory_system', None)  # type: ignore
        if not memory_sys:
            raise HTTPException(status_code=503, detail="Memory system not available")

        correlations = await memory_sys.correlate_with_business_metrics()

        return {
            "success": True,
            "correlations": correlations,
            "insight_count": len(correlations)
        }

    except Exception as e:
        logger.error(f"Metrics analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tools")
async def list_tools() -> Dict[str, Any]:
    """List all available tools for orchestration"""
    try:
        from main import orchestrator as app_orchestrator

        if not app_orchestrator:
            raise HTTPException(status_code=503, detail="Orchestrator not initialized")

        # Get tools from intelligent orchestrator
        intelligent_orch = getattr(app_orchestrator, 'intelligent_orchestrator', None)  # type: ignore
        if not intelligent_orch:
            raise HTTPException(status_code=503, detail="IntelligentOrchestrator not initialized")

        tools = intelligent_orch.list_tools()

        return {
            "success": True,
            "tools": [
                {
                    "id": tool.tool_id,
                    "name": tool.name,
                    "description": tool.description,
                    "category": tool.category,
                    "estimated_cost": tool.estimated_cost,
                    "success_rate": tool.success_rate
                }
                for tool in tools
            ]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
