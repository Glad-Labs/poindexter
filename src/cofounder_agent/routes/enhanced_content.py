"""
Enhanced Content Routes with Full SEO and Metadata Support

Provides endpoints for creating SEO-optimized blog posts with:
- Complete metadata generation
- Featured image prompts
- JSON-LD structured data
- Social media optimization
- Category and tag suggestions
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
import uuid
import logging

from services.ai_content_generator import get_content_generator
from services.seo_content_generator import get_seo_content_generator

logger = logging.getLogger(__name__)

# Router for enhanced content endpoints
enhanced_content_router = APIRouter(prefix="/api/v1/content/enhanced", tags=["enhanced-content"])

# In-memory task storage
enhanced_task_store: Dict[str, Dict[str, Any]] = {}


class EnhancedBlogPostRequest(BaseModel):
    """Request for SEO-optimized blog post creation"""
    topic: str = Field(..., min_length=5, max_length=300)
    style: Literal["technical", "narrative", "listicle", "educational", "thought-leadership"] = "technical"
    tone: Literal["professional", "casual", "academic", "inspirational"] = "professional"
    target_length: int = Field(1500, ge=300, le=5000)
    tags: Optional[List[str]] = None
    generate_featured_image: bool = True
    auto_publish: bool = False


class EnhancedBlogPostResponse(BaseModel):
    """Response with complete SEO metadata"""
    task_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    created_at: str


class BlogPostMetadata(BaseModel):
    """Complete metadata for a blog post"""
    seo_title: str
    meta_description: str
    slug: str
    meta_keywords: List[str]
    reading_time_minutes: int
    word_count: int
    featured_image_prompt: str
    featured_image_url: Optional[str]
    json_ld_schema: Dict[str, Any]
    category: str
    tags: List[str]
    og_title: str
    og_description: str
    twitter_title: str
    twitter_description: str


@enhanced_content_router.post(
    "/blog-posts/create-seo-optimized",
    response_model=EnhancedBlogPostResponse,
    description="Create a complete SEO-optimized blog post with full metadata"
)
async def create_seo_optimized_blog_post(
    request: EnhancedBlogPostRequest,
    background_tasks: BackgroundTasks
):
    """
    Create a complete, SEO-optimized blog post with:
    - Full content generation with self-checking
    - SEO title and meta description
    - Featured image prompt
    - JSON-LD structured data
    - Social media metadata
    - Category and tag suggestions
    - Reading time calculation
    
    Returns task ID for polling progress.
    """
    try:
        # Validate input
        if len(request.topic.strip()) < 5:
            raise HTTPException(status_code=400, detail="Topic must be at least 5 characters")
        
        # Create task ID
        task_id = f"blog_seo_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        
        # Initialize task
        task_data = {
            "task_id": task_id,
            "status": "pending",
            "topic": request.topic,
            "style": request.style,
            "tone": request.tone,
            "target_length": request.target_length,
            "tags": request.tags or [],
            "generate_featured_image": request.generate_featured_image,
            "created_at": datetime.now().isoformat(),
            "progress": {"stage": "queued", "percentage": 0}
        }
        
        enhanced_task_store[task_id] = task_data
        
        # Start background generation
        background_tasks.add_task(
            _generate_seo_optimized_blog_post,
            task_id,
            request
        )
        
        logger.info(f"SEO blog post task created: {task_id} - Topic: {request.topic}")
        
        return EnhancedBlogPostResponse(
            task_id=task_id,
            status="pending",
            created_at=datetime.now().isoformat()
        )
        
    except Exception as e:
        logger.error(f"Error creating blog post task: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create blog post: {str(e)}")


@enhanced_content_router.get(
    "/blog-posts/tasks/{task_id}",
    description="Get SEO blog post generation status"
)
async def get_seo_blog_post_status(task_id: str):
    """
    Poll for blog post generation status and results.
    
    Returns complete metadata and content when ready.
    """
    try:
        if task_id not in enhanced_task_store:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        
        task = enhanced_task_store[task_id]
        
        return {
            "task_id": task_id,
            "status": task["status"],
            "progress": task.get("progress"),
            "result": task.get("result"),
            "error": task.get("error"),
            "created_at": task["created_at"]
        }
        
    except Exception as e:
        logger.error(f"Error getting task status: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting task status: {str(e)}")


@enhanced_content_router.get(
    "/blog-posts/available-models",
    description="Get list of available models"
)
async def get_available_models():
    """Get list of available LLM models for content generation"""
    try:
        provider_manager = get_content_generator()
        
        available_models = {
            "local": [],
            "free_tier": [],
            "paid": []
        }
        
        if provider_manager.ollama_available:
            available_models["local"] = [
                "neural-chat:latest",
                "mistral:latest",
                "llama2:latest",
                "qwen2.5:14b"
            ]
        
        if provider_manager.hf_token:
            available_models["free_tier"] = [
                "mistralai/Mistral-7B-Instruct",
                "meta-llama/Llama-2-7b-chat",
                "tiiuae/falcon-7b-instruct"
            ]
        
        if provider_manager.gemini_key:
            available_models["paid"] = ["gemini-2.5-flash"]
        
        return {
            "available_models": available_models,
            "recommendation": "Use local models for cost savings, HuggingFace for free tier, Gemini as fallback"
        }
        
    except Exception as e:
        logger.error(f"Error getting models: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Background task for SEO blog post generation
async def _generate_seo_optimized_blog_post(task_id: str, request: EnhancedBlogPostRequest):
    """
    Background task for generating SEO-optimized blog post.
    
    Stages:
    1. Generate core content with self-checking (25%)
    2. Generate SEO metadata (50%)
    3. Create featured image prompt (75%)
    4. Generate structured data (90%)
    5. Complete and store result (100%)
    """
    try:
        task = enhanced_task_store.get(task_id)
        if not task:
            logger.error(f"Task not found: {task_id}")
            return
        
        # Get generators
        ai_generator = get_content_generator()
        seo_generator = get_seo_content_generator(ai_generator)
        
        # Stage 1: Generate core content
        task["status"] = "generating"
        task["progress"] = {"stage": "content_generation", "percentage": 25, "message": "Generating core content..."}
        
        logger.info(f"Generating SEO blog post for task: {task_id}")
        
        enhanced_blog_post = await seo_generator.generate_complete_blog_post(
            topic=request.topic,
            style=request.style,
            tone=request.tone,
            target_length=request.target_length,
            tags_input=request.tags,
            generate_images=request.generate_featured_image
        )
        
        # Update task with result
        task["result"] = {
            "title": enhanced_blog_post.title,
            "content": enhanced_blog_post.content,
            "excerpt": enhanced_blog_post.excerpt,
            "word_count": enhanced_blog_post.metadata.word_count,
            "reading_time": enhanced_blog_post.metadata.reading_time_minutes,
            "model_used": enhanced_blog_post.model_used,
            "quality_score": enhanced_blog_post.quality_score,
            "generation_time": enhanced_blog_post.generation_time_seconds,
            "metadata": {
                "seo_title": enhanced_blog_post.metadata.seo_title,
                "meta_description": enhanced_blog_post.metadata.meta_description,
                "slug": enhanced_blog_post.metadata.slug,
                "meta_keywords": enhanced_blog_post.metadata.meta_keywords,
                "category": enhanced_blog_post.metadata.category,
                "tags": enhanced_blog_post.metadata.tags,
                "featured_image_prompt": enhanced_blog_post.metadata.featured_image_prompt,
                "featured_image_alt_text": enhanced_blog_post.metadata.featured_image_alt_text,
                "featured_image_caption": enhanced_blog_post.metadata.featured_image_caption,
                "json_ld_schema": enhanced_blog_post.metadata.json_ld_schema,
                "og_title": enhanced_blog_post.metadata.og_title,
                "og_description": enhanced_blog_post.metadata.og_description,
                "twitter_title": enhanced_blog_post.metadata.twitter_title,
                "twitter_description": enhanced_blog_post.metadata.twitter_description,
            },
            "validation_results": enhanced_blog_post.validation_results
        }
        
        # Mark as completed
        task["status"] = "completed"
        task["progress"] = {"stage": "complete", "percentage": 100, "message": "Done!"}
        task["completed_at"] = datetime.now().isoformat()
        
        logger.info(f"SEO blog post generation completed: {task_id}")
        
    except Exception as e:
        logger.error(f"Error in SEO blog post generation {task_id}: {e}")
        task = enhanced_task_store.get(task_id)
        if task:
            task["status"] = "failed"
            task["error"] = {
                "message": str(e),
                "type": type(e).__name__
            }
            task["completed_at"] = datetime.now().isoformat()
