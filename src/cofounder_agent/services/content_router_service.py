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
from .pexels_client import PexelsClient
from .task_store_service import get_persistent_task_store
from .content_orchestrator import get_content_orchestrator

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
        request_type: str = "basic",
        task_type: str = "blog_post",
        metadata: Optional[Dict[str, Any]] = None
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
                task_type=task_type,
                metadata=metadata or {},
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
# BACKGROUND TASK PROCESSORS
# ============================================================================


async def process_content_generation_task(task_id: str):
    """
    🚀 Phase 5 Implementation: Content Generation with MANDATORY HUMAN APPROVAL GATE
    
    Process a content generation task using the 6-stage orchestrator pipeline:
    
    STAGE 1: 📚 Research        (10%)
    STAGE 2: ✍️  Creative Draft  (25%)
    STAGE 3: 🔍 QA Review Loop  (45%)
    STAGE 4: 🖼️  Image Selection (60%)
    STAGE 5: 📝 Formatting      (75%)
    STAGE 6: ⏳ AWAITING HUMAN APPROVAL (100%) ← MANDATORY GATE
    
    **CRITICAL**: Pipeline returns status="awaiting_approval"
    **NO AUTO-PUBLISHING** - Requires explicit human decision via API
    
    Human decision endpoint: POST /api/content/tasks/{task_id}/approve
    """
    task_store = get_content_task_store()
    task = task_store.get_task(task_id)

    if not task:
        logger.error(f"❌ Task not found: {task_id}")
        return

    topic = task.get('topic', 'Unknown')
    
    logger.info(f"\n{'='*80}")
    logger.info(f"🚀 PHASE 5: CONTENT GENERATION WITH HUMAN APPROVAL GATE")
    logger.info(f"{'='*80}")
    logger.info(f"   Task ID: {task_id}")
    logger.info(f"   Topic: {topic}")
    logger.info(f"   Style: {task.get('style')} | Tone: {task.get('tone')}")
    logger.info(f"   Request Type: {task.get('request_type', 'standard')}")
    logger.info(f"{'='*80}\n")

    try:
        # ✅ IMPORT ORCHESTRATOR (moved to top-level imports for multiprocessing compatibility)
        logger.info(f"🎯 Initializing Content Orchestrator...")
        
        # ✅ GET ORCHESTRATOR INSTANCE
        orchestrator = get_content_orchestrator(task_store)
        
        logger.info(f"📊 Running 6-stage pipeline for task {task_id}...\n")
        
        # ✅ RUN 6-STAGE PIPELINE
        # Returns: status="awaiting_approval" (STOPS HERE - No auto-publishing!)
        orchestrator_result = await orchestrator.run(
            topic=task["topic"],
            keywords=task.get("tags") or [task["topic"]],
            style=task.get("style", "educational"),
            tone=task.get("tone", "professional"),
            task_id=task_id,
            metadata={
                "request_type": task.get("request_type", "standard"),
                "publish_mode": task.get("publish_mode", "draft"),
                "generate_featured_image": task.get("generate_featured_image", True),
            }
        )
        
        logger.info(f"\n✅ Orchestrator pipeline complete!")
        logger.info(f"   Status: {orchestrator_result.get('status')}")
        logger.info(f"   Approval Status: {orchestrator_result.get('approval_status')}")
        logger.info(f"   Quality Score: {orchestrator_result.get('quality_score', 0)}/100")
        logger.info(f"   Next Action: {orchestrator_result.get('next_action', 'N/A')}\n")
        
        # ✅ CRITICAL: Pipeline returns status="awaiting_approval"
        # NOTHING PUBLISHES UNTIL HUMAN APPROVES!
        logger.info(f"⏳ TASK AWAITING HUMAN APPROVAL")
        logger.info(f"{'='*80}")
        logger.info(f"   ⏳ Pipeline STOPPED at human approval gate")
        logger.info(f"   📌 Human must approve/reject via:")
        logger.info(f"      POST /api/content/tasks/{task_id}/approve")
        logger.info(f"   📌 With JSON body:")
        logger.info(f"      {{")
        logger.info(f"         'approved': true/false,")
        logger.info(f"         'human_feedback': 'Your decision reason',")
        logger.info(f"         'reviewer_id': 'reviewer_username'")
        logger.info(f"      }}")
        logger.info(f"{'='*80}\n")
        
        # Task status is now "awaiting_approval" - stored in task_store by orchestrator
        return orchestrator_result

    except Exception as e:
        logger.error(f"❌ Pipeline error for task {task_id}: {e}", exc_info=True)
        task_store.update_task(
            task_id,
            {
                "status": "failed",
                "approval_status": "failed",
                "error_message": str(e),
                "completed_at": datetime.now(),
            },
        )
        raise
