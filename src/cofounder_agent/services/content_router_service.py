"""
Unified Content Router Service

Consolidates functionality from:
- routes/content.py (full-featured blog creation)
- routes/content_generation.py (Ollama-focused generation)
- routes/enhanced_content.py (SEO-optimized generation)

Provides centralized blog post generation with:
- Multi-model AI support (Ollama → HuggingFace → Gemini)
- Featured image search (Pexels - free)
- SEO optimization and metadata
- Strapi CMS integration
- Draft management
- Comprehensive task tracking
"""

from typing import Dict, Any, Optional, List, Literal
from datetime import datetime
from enum import Enum
import uuid
import logging

from .ai_content_generator import get_content_generator
from .seo_content_generator import get_seo_content_generator
from .strapi_client import StrapiClient, StrapiEnvironment
from .pexels_client import PexelsClient
from .task_store_service import get_persistent_task_store

logger = logging.getLogger(__name__)


# ============================================================================
# ENUMS
# ============================================================================


class ContentStyle(str, Enum):
    """Content styles for generation"""
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


class PublishMode(str, Enum):
    """Publishing modes"""
    DRAFT = "draft"
    PUBLISH = "publish"


# ============================================================================
# TASK STORE - UNIFIED STORAGE (Now using persistent database backend)
# ============================================================================


class ContentTaskStore:
    """
    Unified task storage adapter for all content generation requests.
    
    Now delegates to persistent database backend (PersistentTaskStore).
    Provides backward-compatible interface with enhanced persistence.
    """

    def __init__(self):
        """Initialize unified task store (delegates to persistent backend)"""
        self._persistent_store = None

    @property
    def persistent_store(self):
        """Lazy-load persistent task store on first access"""
        if self._persistent_store is None:
            self._persistent_store = get_persistent_task_store()
        return self._persistent_store

    def create_task(
        self,
        topic: str,
        style: str,
        tone: str,
        target_length: int,
        tags: Optional[List[str]] = None,
        generate_featured_image: bool = True,
        request_type: str = "basic"
    ) -> str:
        """
        Create a new task in persistent storage

        Args:
            topic: Blog post topic
            style: Content style
            tone: Content tone
            target_length: Target word count
            tags: Tags for categorization
            generate_featured_image: Whether to search for featured image
            request_type: Type of request (basic, enhanced, etc.)

        Returns:
            Task ID for tracking
        """
        logger.info(f"� [CONTENT_TASK_STORE] Creating task")
        logger.info(f"   Topic: {topic[:60]}{'...' if len(topic) > 60 else ''}")
        logger.info(f"   Style: {style} | Tone: {tone} | Length: {target_length}w")
        logger.info(f"   Tags: {', '.join(tags) if tags else 'none'}")
        logger.debug(f"   Type: {request_type} | Image: {generate_featured_image}")
        
        # Add generate_featured_image to metadata
        metadata = {"generate_featured_image": generate_featured_image}
        logger.debug(f"   Metadata: {metadata}")
        
        try:
            # Get persistent store
            logger.debug(f"   📌 Getting persistent_store...")
            persistent_store = self.persistent_store
            logger.debug(f"   📌 Store ready: {persistent_store is not None}")
            
            # Create task in persistent store
            logger.debug(f"   📝 Calling persistent_store.create_task()...")
            task_id = persistent_store.create_task(
                topic=topic,
                style=style,
                tone=tone,
                target_length=target_length,
                tags=tags or [],
                request_type=request_type,
                metadata=metadata,
            )
            
            logger.info(f"✅ [CONTENT_TASK_STORE] Task CREATED and PERSISTED")
            logger.info(f"   Task ID: {task_id}")
            logger.info(f"   Status: pending")
            logger.debug(f"   🎯 Ready for processing")
            return task_id
            
        except Exception as e:
            logger.error(f"❌ [CONTENT_TASK_STORE] ERROR: {e}", exc_info=True)
            raise

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task by ID from persistent storage"""
        return self.persistent_store.get_task(task_id)

    def update_task(self, task_id: str, updates: Dict[str, Any]) -> bool:
        """Update task data in persistent storage"""
        return self.persistent_store.update_task(task_id, updates)

    def delete_task(self, task_id: str) -> bool:
        """Delete task from persistent storage"""
        return self.persistent_store.delete_task(task_id)

    def list_tasks(
        self, status: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List tasks from persistent storage with optional filtering"""
        tasks, total = self.persistent_store.list_tasks(
            status=status, limit=limit, offset=offset
        )
        return tasks

    def get_drafts(self, limit: int = 20, offset: int = 0) -> tuple:
        """Get list of drafts from persistent storage"""
        drafts, total = self.persistent_store.get_drafts(limit=limit, offset=offset)
        return drafts, total


# Global unified task store (lazy-initialized)
_content_task_store: Optional[ContentTaskStore] = None


def get_content_task_store() -> ContentTaskStore:
    """Get the global unified content task store (lazy-initialized)"""
    global _content_task_store
    if _content_task_store is None:
        _content_task_store = ContentTaskStore()
    return _content_task_store


# ============================================================================
# CONTENT GENERATION SERVICE
# ============================================================================


class ContentGenerationService:
    """Service for AI-powered content generation"""

    def __init__(self):
        """Initialize with available generators"""
        self.ai_generator = get_content_generator()
        self.seo_generator = get_seo_content_generator(self.ai_generator)

    async def generate_blog_post(
        self,
        topic: str,
        style: str,
        tone: str,
        target_length: int,
        tags: Optional[List[str]] = None,
        enhanced: bool = False,
    ) -> tuple:
        """
        Generate blog post content

        Args:
            topic: Blog post topic
            style: Content style
            tone: Content tone
            target_length: Target word count
            tags: Tags for categorization
            enhanced: Whether to use SEO enhancement

        Returns:
            Tuple of (content, model_used, metrics)
        """
        if enhanced:
            logger.info(f"Generating SEO-enhanced blog post: {topic}")
            result = await self.seo_generator.generate_complete_blog_post(
                topic=topic,
                style=style,
                tone=tone,
                target_length=target_length,
                tags_input=tags,
                generate_images=False,  # Handle separately
            )
            return (
                result.content,
                result.model_used,
                {
                    "quality_score": result.quality_score,
                    "generation_time": result.generation_time_seconds,
                    "validation_results": result.validation_results,
                },
            )
        else:
            logger.info(f"Generating blog post: {topic}")
            content, model_used, metrics = await self.ai_generator.generate_blog_post(
                topic=topic,
                style=style,
                tone=tone,
                target_length=target_length,
                tags=tags,
            )
            return content, model_used, metrics

    async def generate_featured_image_prompt(
        self, topic: str, content: str
    ) -> str:
        """Generate a detailed image prompt for featured image"""
        try:
            generator = get_content_generator()
            # Use generator to create image prompt
            prompt = f"Create a visual representation for: {topic}\n\nContext: {content[:200]}"
            return prompt
        except Exception as e:
            logger.warning(f"Error generating image prompt: {e}")
            return f"Featured image for: {topic}"


# ============================================================================
# FEATURED IMAGE SERVICE
# ============================================================================


class FeaturedImageService:
    """Service for featured image generation and search"""

    def __init__(self):
        """Initialize Pexels client"""
        self.pexels = PexelsClient()

    async def search_featured_image(
        self, topic: str, keywords: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Search for featured image via Pexels (free, no cost)

        Args:
            topic: Blog post topic
            keywords: Optional search keywords

        Returns:
            Image dict with url and metadata, or None if not found
        """
        try:
            search_keywords = keywords or [topic]
            image = self.pexels.get_featured_image(topic, keywords=search_keywords)

            if image:
                logger.info(f"Found featured image: {image.get('photographer')}")
                return image
            else:
                logger.warning(f"No Pexels image found for: {topic}")
                return None

        except Exception as e:
            logger.error(f"Error searching for featured image: {e}")
            return None


# ============================================================================
# STRAPI PUBLISHING SERVICE
# ============================================================================


class StrapiPublishingService:
    """Service for publishing content to Strapi CMS"""

    def __init__(self, environment: str = "production"):
        """Initialize Strapi client"""
        env = (
            StrapiEnvironment.STAGING
            if environment == "staging"
            else StrapiEnvironment.PRODUCTION
        )
        self.strapi = StrapiClient(env)

    async def publish_blog_post(
        self,
        title: str,
        content: str,
        summary: str,
        tags: Optional[List[str]] = None,
        categories: Optional[List[str]] = None,
        featured_image_url: Optional[str] = None,
        auto_publish: bool = False,
    ) -> Dict[str, Any]:
        """
        Publish blog post to Strapi

        Args:
            title: Post title
            content: Post content (markdown)
            summary: Post summary/excerpt
            tags: Tags
            categories: Categories
            featured_image_url: Featured image URL
            auto_publish: Auto-publish (True) or create as draft (False)

        Returns:
            Strapi response with post ID and metadata
        """
        try:
            logger.info(f"Publishing blog post to Strapi: {title}")

            result = await self.strapi.create_blog_post(
                title=title,
                content=content,
                summary=summary,
                tags=tags or [],
                categories=categories or [],
                featured_image_url=featured_image_url,
                publish=auto_publish,
            )

            post_id = result.get("data", {}).get("id")
            logger.info(
                f"Blog post published to Strapi. ID: {post_id}, "
                f"Status: {'published' if auto_publish else 'draft'}"
            )

            return result

        except Exception as e:
            logger.error(f"Error publishing to Strapi: {e}")
            raise


# ============================================================================
# BACKGROUND TASK PROCESSORS
# ============================================================================


async def process_content_generation_task(task_id: str):
    """
    Process a content generation task

    Handles the full lifecycle of blog post generation:
    1. Generate content with AI
    2. Search for featured image (if requested)
    3. Publish to Strapi (if requested)
    4. Track progress and handle errors
    """
    task_store = get_content_task_store()
    task = task_store.get_task(task_id)

    if not task:
        logger.error(f"❌ [PROCESS_TASK] Task not found: {task_id}")
        return

    topic = task.get('topic', 'Unknown')
    logger.info(f"\n{'='*80}")
    logger.info(f"� [PROCESS_TASK] STARTING BACKGROUND GENERATION")
    logger.info(f"{'='*80}")
    logger.info(f"   Task ID: {task_id}")
    logger.info(f"   Topic: {topic}")
    logger.info(f"   Style: {task.get('style')} | Tone: {task.get('tone')}")
    logger.info(f"   Target: {task.get('target_length')} words")
    logger.info(f"{'='*80}\n")

    try:
        # Stage 1: Generate content
        logger.info(f"📝 [STAGE 1/4] Generating content with AI...")
        logger.info(f"   └─ Updating task status to 'generating'...")
        update_result = task_store.update_task(
            task_id,
            {
                "status": "generating",
                "progress": {
                    "stage": "content_generation",
                    "percentage": 25,
                    "message": "Generating content with AI...",
                },
            },
        )
        logger.info(f"   └─ Status update: {'✅ Success' if update_result else '❌ Failed'}")
        
        # Verify update worked
        updated_task = task_store.get_task(task_id)
        if updated_task:
            logger.info(f"   └─ Verified status: {updated_task.get('status')}")

        logger.info(f"   └─ Calling AI content generator...")
        gen_service = ContentGenerationService()
        enhanced = task.get("request_type") == "enhanced"
        tags = task.get("tags") or []  # Default to empty list if None
        content, model_used, metrics = await gen_service.generate_blog_post(
            topic=task["topic"],
            style=task["style"],
            tone=task["tone"],
            target_length=task["target_length"],
            tags=tags,
            enhanced=enhanced,
        )

        logger.info(f"✅ [STAGE 1/4] Content generation complete")
        logger.info(f"   └─ Model: {model_used}")
        logger.info(f"   └─ Quality Score: {metrics.get('final_quality_score', 0):.1f}")
        logger.info(f"   └─ Content size: {len(content)} characters")
        logger.info(f"   └─ Generation time: {metrics.get('generation_time_seconds', 0):.1f}s\n")

        # Check if generation failed (fallback was used)
        is_fallback = "Fallback" in model_used or model_used == "Fallback (no AI models available)"
        if is_fallback:
            logger.warning(f"⚠️  GENERATION USED FALLBACK - ALL AI MODELS FAILED")
            logger.warning(f"   └─ This indicates all AI providers are unavailable or failed")
            logger.warning(f"   └─ Content quality may be reduced")
            task_store.update_task(
                task_id,
                {
                    "progress": {
                        "stage": "content_generation",
                        "percentage": 25,
                        "message": "Content generation used fallback (AI models unavailable)",
                    },
                    "generation_failed": True,
                },
            )
        else:
            logger.info(f"   └─ Generation successful with {model_used}")
            task_store.update_task(
                task_id,
                {
                    "progress": {
                        "stage": "content_generation",
                        "percentage": 25,
                        "message": "Content generation successful",
                    },
                    "generation_failed": False,
                },
            )


        # Stage 2: Search for featured image
        featured_image_url = None
        image_source = None

        if task.get("generate_featured_image"):
            logger.info(f"🖼️  [STAGE 2/4] Searching for featured image...")
            logger.info(f"   └─ Topic: {topic}")
            task_store.update_task(
                task_id,
                {
                    "progress": {
                        "stage": "image_search",
                        "percentage": 50,
                        "message": "Searching for featured image...",
                    }
                },
            )

            image_service = FeaturedImageService()
            image = await image_service.search_featured_image(
                task["topic"], task.get("tags")
            )

            if image:
                featured_image_url = image.get("url")
                image_source = image.get("photographer")
                logger.info(f"✅ [STAGE 2/4] Image found")
                logger.info(f"   └─ Source: {image_source}")
                if featured_image_url:
                    logger.info(f"   └─ URL: {featured_image_url[:50] if len(featured_image_url) > 50 else featured_image_url}...")
                logger.info(f"")
            else:
                logger.warning(f"⚠️  [STAGE 2/4] No image found, continuing without featured image\n")
        else:
            logger.info(f"⏭️  [STAGE 2/4] Featured image disabled - skipping\n")

        # Stage 3: Publish to Strapi (if requested)
        strapi_post_id = None

        if task.get("publish_mode") == "publish":
            logger.info(f"📤 [STAGE 3/4] Publishing to Strapi...")
            logger.info(f"   └─ Environment: {task.get('target_environment', 'production')}")
            task_store.update_task(
                task_id,
                {
                    "progress": {
                        "stage": "publishing",
                        "percentage": 75,
                        "message": "Publishing to Strapi...",
                    }
                },
            )

            pub_service = StrapiPublishingService(
                task.get("target_environment", "production")
            )
            logger.info(f"   └─ Sending to Strapi...")
            strapi_result = await pub_service.publish_blog_post(
                title=task["topic"],
                content=content,
                summary=content[:200] + "...",
                tags=task.get("tags"),
                categories=task.get("categories"),
                featured_image_url=featured_image_url,
                auto_publish=True,
            )

            strapi_post_id = strapi_result.get("data", {}).get("id")
            logger.info(f"✅ [STAGE 3/4] Published to Strapi")
            logger.info(f"   └─ Post ID: {strapi_post_id}\n")
        else:
            logger.info(f"💾 [STAGE 3/4] Publish mode is DRAFT - saving as draft\n")

        # Stage 4: Complete
        logger.info(f"✨ [STAGE 4/4] Finalizing task...")
        final_status = "failed" if is_fallback else "completed"
        logger.info(f"   └─ Final status: {final_status}")
        
        # ✅ FIXED: Save content directly to content_tasks table fields
        # Instead of nesting in "result" object, map to actual database columns
        excerpt = content[:200] + "..." if len(content) > 200 else content
        
        task_store.update_task(
            task_id,
            {
                "status": final_status,
                "content": content,  # ✅ SAVE TO content FIELD
                "excerpt": excerpt,  # ✅ SAVE TO excerpt FIELD
                "featured_image_url": featured_image_url,  # ✅ SAVE TO featured_image_url FIELD
                "model_used": model_used,  # ✅ SAVE TO model_used FIELD
                "quality_score": int(metrics.get('final_quality_score', 0)),  # ✅ SAVE TO quality_score FIELD
                "progress": {
                    "stage": "complete",
                    "percentage": 100,
                    "message": "Generation complete" if not is_fallback else "Generation completed with fallback content",
                },
                "completed_at": datetime.now(),
                "task_metadata": {  # Store additional metadata
                    "title": task["topic"],
                    "summary": excerpt,
                    "word_count": len(content.split()),
                    "featured_image_source": image_source,
                    "generation_metrics": metrics,
                    "strapi_post_id": strapi_post_id,
                },
            },
        )
        logger.info(f"✅ Content persisted to database:")
        logger.info(f"   └─ content field: {len(content)} characters saved")
        logger.info(f"   └─ excerpt field: saved")
        logger.info(f"   └─ featured_image_url: {'saved' if featured_image_url else 'none'}")
        logger.info(f"   └─ model_used: {model_used} saved")

        if is_fallback:
            logger.warning(f"❌ Task {task_id} completed with fallback content (AI models failed)")
        else:
            logger.info(f"✅ Task {task_id} completed successfully")

    except Exception as e:
        logger.error(f"Error processing task {task_id}: {e}", exc_info=True)
        task_store.update_task(
            task_id,
            {
                "status": "failed",
                "error": {
                    "message": str(e),
                    "type": type(e).__name__,
                    "details": "Check logs for more information",
                },
                "completed_at": datetime.now(),
            },
        )
