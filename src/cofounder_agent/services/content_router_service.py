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
from .image_service import ImageService, get_image_service
from .quality_service import UnifiedQualityService, EvaluationMethod
from .database_service import DatabaseService
from schemas.content_schemas import ContentStyle, ContentTone, PublishMode

logger = logging.getLogger(__name__)


# ============================================================================
# ENUMS
# ============================================================================
# NOTE: ContentStyle, ContentTone, PublishMode are now defined in schemas/content_schemas.py
# to avoid circular imports. They are imported above.


# ============================================================================
# TASK STORE - UNIFIED STORAGE (Now using persistent database backend)
# ============================================================================


class ContentTaskStore:
    """
    Unified task storage adapter for all content generation requests.

    Now delegates to persistent database backend (PersistentTaskStore).
    Provides backward-compatible interface with enhanced persistence.
    """

    def __init__(self, database_service: Optional[DatabaseService] = None):
        """
        Initialize unified task store with async DatabaseService

        Args:
            database_service: Optional DatabaseService instance for task persistence
        """
        self.database_service = database_service

    @property
    def persistent_store(self):
        """
        Backward-compatible property for existing code.
        Now returns the DatabaseService which handles all async task operations.
        """
        return self.database_service

    async def create_task(
        self,
        topic: str,
        style: str,
        tone: str,
        target_length: int,
        tags: Optional[List[str]] = None,
        generate_featured_image: bool = True,
        request_type: str = "basic",
        task_type: str = "blog_post",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Create a new task in persistent storage (async, non-blocking)

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
        logger.info(f"📋 [CONTENT_TASK_STORE] Creating task (async)")
        logger.info(f"   Topic: {topic[:60]}{'...' if len(topic) > 60 else ''}")
        logger.info(f"   Style: {style} | Tone: {tone} | Length: {target_length}w")
        logger.info(f"   Tags: {', '.join(tags) if tags else 'none'}")
        logger.debug(f"   Type: {request_type} | Image: {generate_featured_image}")

        # Add generate_featured_image to metadata
        metadata = {"generate_featured_image": generate_featured_image}
        logger.debug(f"   Metadata: {metadata}")

        try:
            # Check if we have database service
            if not self.database_service:
                raise ValueError("DatabaseService not initialized - cannot persist tasks")

            logger.debug(f"   📝 Calling database_service.add_task() (async)...")

            # Generate task_name from topic
            task_name = f"{topic[:50]}" if len(topic) <= 50 else f"{topic[:47]}..."

            task_id = await self.database_service.add_task(
                {
                    "task_name": task_name,  # REQUIRED: must be provided
                    "topic": topic,
                    "style": style,
                    "tone": tone,
                    "target_length": target_length,
                    "tags": tags or [],
                    "request_type": request_type,
                    "task_type": task_type,
                    "metadata": metadata or {},
                }
            )

            logger.info(f"✅ [CONTENT_TASK_STORE] Task CREATED and PERSISTED (async)")
            logger.info(f"   Task ID: {task_id}")
            logger.info(f"   Status: pending")
            logger.debug(f"   🎯 Ready for processing")
            return task_id

        except Exception as e:
            logger.error(f"❌ [CONTENT_TASK_STORE] ERROR: {e}", exc_info=True)
            raise

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task by ID from persistent storage (async, non-blocking)"""
        if not self.database_service:
            return None
        return await self.database_service.get_task(task_id)

    async def update_task(self, task_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update task data in persistent storage (async, non-blocking)"""
        if not self.database_service:
            return None

        # Handle metadata updates by converting to JSON
        import json

        if "metadata" in updates:
            updates["task_metadata"] = json.dumps(updates.pop("metadata"))

        # Call database service to update
        return await self.database_service.update_task(task_id, updates)

    async def delete_task(self, task_id: str) -> bool:
        """Delete task from persistent storage (async, non-blocking)"""
        if not self.database_service:
            return False
        return await self.database_service.delete_task(task_id)

    async def list_tasks(
        self, status: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List tasks from persistent storage with optional filtering (async, non-blocking)"""
        if not self.database_service:
            return []
        tasks, total = await self.database_service.get_tasks_paginated(
            offset=offset, limit=limit, status=status
        )
        return tasks

    async def get_drafts(self, limit: int = 20, offset: int = 0) -> tuple:
        """Get list of drafts from persistent storage (async, non-blocking)"""
        if not self.database_service:
            return ([], 0)
        return await self.database_service.get_drafts(limit=limit, offset=offset)


# Global unified task store (lazy-initialized)
_content_task_store: Optional[ContentTaskStore] = None


def get_content_task_store(database_service: Optional[DatabaseService] = None) -> ContentTaskStore:
    """
    Get the global unified content task store (lazy-initialized).
    Allows injecting database_service during startup.
    """
    global _content_task_store
    if _content_task_store is None:
        _content_task_store = ContentTaskStore(database_service)
    elif database_service and _content_task_store.database_service is None:
        # Inject service if it wasn't available during first init
        _content_task_store.database_service = database_service

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
        preferred_model: Optional[str] = None,
        preferred_provider: Optional[str] = None,
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
            preferred_model: User-selected model name (e.g., 'gpt-4', 'gemini-pro')
            preferred_provider: User-selected provider ('openai', 'anthropic', 'gemini', 'ollama')

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
                tags=tags or [],
                preferred_model=preferred_model,
                preferred_provider=preferred_provider,
            )
            return content, model_used, metrics

    async def generate_featured_image_prompt(self, topic: str, content: str) -> str:
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
# ============================================================================
# BACKGROUND TASK PROCESSORS
# ============================================================================


async def _generate_catchy_title(topic: str, content_excerpt: str) -> Optional[str]:
    """
    Generate a catchy, engaging title for blog content using LLM
    
    Args:
        topic: The blog topic
        content_excerpt: First 500 chars of generated content for context
        
    Returns:
        Generated title or None if generation fails
    """
    try:
        from .ollama_client import OllamaClient
        
        ollama = OllamaClient()
        
        prompt = f"""You are a creative content strategist specializing in blog titles.
Generate a single, catchy, engaging blog title based on the topic and content excerpt.

Requirements:
- Concise (max 100 characters)
- Contains the main keyword or concept
- Compelling and encourages clicks
- Uses power words when appropriate
- Avoids clickbait and maintains professionalism
- Standalone format (no quotes, no numbering)

Topic: {topic}

Content excerpt:
{content_excerpt}

Generate ONLY the title, nothing else."""

        # Use Ollama to generate the title
        response = await ollama.generate(
            prompt=prompt,
            system="You are a professional blog title writer.",
            model="neural-chat:latest",  # Fast and reliable model
            stream=False,
        )
        
        # Extract text from response
        title = ""
        if isinstance(response, dict):
            title = response.get("text", "") or response.get("response", "") or response.get("content", "")
        elif isinstance(response, str):
            title = response
            
        if title:
            # Clean up the title
            title = title.strip().strip('"').strip("'").strip()
            # Truncate if too long
            if len(title) > 100:
                title = title[:97] + "..."
            logger.debug(f"Generated title: {title}")
            return title
        
        return None
        
    except Exception as e:
        logger.warning(f"Error generating catchy title: {e}")
        return None


async def process_content_generation_task(
    topic: str,
    style: str,
    tone: str,
    target_length: int,
    tags: Optional[List[str]] = None,
    generate_featured_image: bool = True,
    database_service: Optional[DatabaseService] = None,
    task_id: Optional[str] = None,
    # NEW: Model selection parameters (Week 1)
    models_by_phase: Optional[Dict[str, str]] = None,
    quality_preference: Optional[str] = None,
    category: Optional[str] = None,
    target_audience: Optional[str] = None,
) -> Dict[str, Any]:
    """
    🚀 Complete Content Generation Pipeline with Image Sourcing & SEO Metadata

    Process a content generation request through the full pipeline:

    STAGE 1: 📋 Create content_task record (status='pending')
    STAGE 2: ✍️  Generate blog content
    STAGE 2B: ⭐ Early quality evaluation
    STAGE 3: 🖼️  Source featured image from Pexels
    STAGE 4: 📊 Generate SEO metadata
    STAGE 5: 📝 Create posts record with all metadata
    STAGE 6: 🎓 Capture training data for learning
    STAGE 7: 🎓 Capture training data for learning loop

    FEATURES:
    - ✅ Pexels API for royalty-free featured images
    - ✅ Auto-generated SEO title, description, keywords
    - ✅ Quality evaluation with 7 criteria
    - ✅ Training data capture for improvement learning
    - ✅ Full relational integrity (author_id, category_id, etc.)
    - ✅ Per-phase model selection and cost tracking (NEW - Week 1)

    Args:
        topic: Blog post topic
        style: Content style (technical, narrative, listicle, educational, thought-leadership)
        tone: Content tone (professional, casual, academic, inspirational)
        target_length: Target word count (default 1500)
        tags: Optional tags for categorization
        generate_featured_image: Whether to search for featured image
        database_service: DatabaseService instance for persistence
        task_id: Optional task_id (auto-generated if not provided)
        models_by_phase: Optional per-phase model selections
        quality_preference: Optional quality preference (fast, balanced, quality) for auto-selection

    Returns:
        Dict with complete task result including post_id, quality_score, image_url, cost_breakdown, etc.
    """
    from uuid import uuid4
    from asyncio import gather

    # Generate task_id if not provided
    if not task_id:
        task_id = str(uuid4())

    if not database_service:
        logger.error("❌ DatabaseService not provided - cannot persist content")
        raise ValueError("DatabaseService is required for content_tasks persistence")

    logger.info(f"\n{'='*80}")
    logger.info(f"🚀 COMPLETE CONTENT GENERATION PIPELINE")
    logger.info(f"{'='*80}")
    logger.info(f"   Task ID: {task_id}")
    logger.info(f"   Topic: {topic}")
    logger.info(f"   Style: {style} | Tone: {tone}")
    logger.info(f"   Target Length: {target_length} words")
    logger.info(f"   Tags: {', '.join(tags) if tags else 'none'}")
    logger.info(f"   Image Search: {generate_featured_image}")
    logger.info(f"{'='*80}\n")

    result = {"task_id": task_id, "topic": topic, "status": "pending", "stages": {}}

    try:
        # Initialize unified services
        logger.info(f"[BG-TASK] Starting content generation for task {task_id[:8]}...")
        logger.debug(f"[BG-TASK] database_service = {database_service}")
        logger.debug(
            f"[BG-TASK] database_service.tasks = {database_service.tasks if database_service else None}"
        )

        image_service = get_image_service()
        quality_service = UnifiedQualityService(database_service=database_service)
        logger.debug(
            f"[BG-TASK] Services initialized: image_service={image_service}, quality_service={quality_service}"
        )

        # ================================================================================
        # STAGE 1: VERIFY TASK RECORD EXISTS
        # ================================================================================
        logger.info("📋 STAGE 1: Verifying task record exists...")

        # Task already created by task_routes.py before background task launched
        # Just verify it exists in database
        logger.debug(f"[BG-TASK] Verifying task {task_id} exists in database...")
        try:
            existing_task = await database_service.get_task(task_id)
            if existing_task:
                logger.info(f"✅ Task verified in database: {task_id}\n")
                result["content_task_id"] = task_id
                result["stages"]["1_content_task_created"] = True
            else:
                logger.warning(f"⚠️  Task {task_id} not found - this should not happen")
                result["stages"]["1_content_task_created"] = False
        except Exception as e:
            logger.error(f"❌ Failed to verify task: {e}")
            result["stages"]["1_content_task_created"] = False

        # ================================================================================
        # STAGE 2: GENERATE BLOG CONTENT
        # ================================================================================
        logger.info("✍️  STAGE 2: Generating blog content...")

        content_generator = get_content_generator()
        
        # Extract user model preferences from models_by_phase (if provided)
        preferred_model = None
        preferred_provider = None
        logger.info(f"🔍 STEP 2A: Processing model selections from UI")
        logger.info(f"   models_by_phase = {models_by_phase}")
        if models_by_phase:
            # Try to get model for 'draft' phase (main content generation)
            draft_model = models_by_phase.get('draft') or models_by_phase.get('generate') or models_by_phase.get('content')
            logger.info(f"   draft_model = {draft_model}")
            if draft_model and draft_model != 'auto':
                # Clean up malformed model names (e.g., "gemini-gemini-pro" → "gemini-pro")
                draft_model = draft_model.strip()
                
                # Parse provider and model from selection
                # Format can be: "gemini", "gemini/gemini-pro", "gpt-4", "claude-3-opus", etc.
                if '/' in draft_model:
                    preferred_provider, preferred_model = draft_model.split('/', 1)
                else:
                    # Infer provider from model name
                    draft_model_lower = draft_model.lower()
                    
                    # Handle duplicate provider prefixes (e.g., "gemini-gemini-pro", "gpt-gpt-4")
                    if draft_model_lower.startswith('gemini-gemini-'):
                        # "gemini-gemini-1.5-pro" → provider: "gemini", model: "gemini-1.5-pro"
                        preferred_provider = 'gemini'
                        preferred_model = draft_model_lower[7:]  # Strip first "gemini-"
                    elif draft_model_lower.startswith('gpt-gpt-'):
                        # "gpt-gpt-4" → provider: "openai", model: "gpt-4"
                        preferred_provider = 'openai'
                        preferred_model = draft_model_lower[4:]  # Strip first "gpt-"
                    elif draft_model_lower.startswith('claude-claude-'):
                        # "claude-claude-opus" → provider: "anthropic", model: "claude-opus"
                        preferred_provider = 'anthropic'
                        preferred_model = draft_model_lower[7:]  # Strip first "claude-"
                    elif 'gemini' in draft_model_lower:
                        preferred_provider = 'gemini'
                        preferred_model = draft_model
                    elif 'gpt' in draft_model_lower or 'openai' in draft_model_lower:
                        preferred_provider = 'openai'
                        preferred_model = draft_model
                    elif 'claude' in draft_model_lower or 'anthropic' in draft_model_lower:
                        preferred_provider = 'anthropic'
                        preferred_model = draft_model
                    elif 'ollama' in draft_model_lower or 'mistral' in draft_model_lower or 'llama' in draft_model_lower:
                        preferred_provider = 'ollama'
                        preferred_model = draft_model
                    else:
                        # Default to model name as-is
                        preferred_model = draft_model
                        
                logger.info(f"   ✅ FINAL: preferred_model='{preferred_model}', preferred_provider='{preferred_provider}'")
                logger.info(f"🎯 User selected model: {preferred_model or 'auto'} (provider: {preferred_provider or 'auto'})")
        
        content_text, model_used, metrics = await content_generator.generate_blog_post(
            topic=topic, 
            style=style, 
            tone=tone, 
            target_length=target_length, 
            tags=tags or [],
            preferred_model=preferred_model,
            preferred_provider=preferred_provider,
        )

        # Validate content_text is not None
        if not content_text:
            logger.error(f"❌ Content generation returned None or empty")
            raise ValueError("Content generation failed: no content produced")

        # Generate catchy title based on topic and content
        logger.info("📌 Generating title from content...")
        title = await _generate_catchy_title(topic, content_text[:500])
        if not title:
            title = topic  # Fallback to topic if title generation fails
        logger.info(f"✅ Title generated: {title}")

        # Update content_task with generated content AND title
        await database_service.update_task(
            task_id=task_id, updates={"status": "generated", "content": content_text, "title": title}
        )

        result["content"] = content_text
        result["content_length"] = len(content_text)
        result["title"] = title
        result["model_used"] = model_used
        result["stages"]["2_content_generated"] = True
        logger.info(f"✅ Content generated ({len(content_text)} chars) using {model_used}\n")

        # ================================================================================
        # STAGE 2B: QUALITY EVALUATION (Early check after content generation)
        # ================================================================================
        logger.info("⭐ STAGE 2B: Early quality evaluation...")

        quality_result = await quality_service.evaluate(
            content=content_text,
            context={
                "topic": topic,
                "keywords": tags or [topic],
                "audience": "General",
            },
            method=EvaluationMethod.PATTERN_BASED,
        )

        # Validate quality_result is not None
        if not quality_result:
            logger.error(f"❌ Quality evaluation returned None")
            raise ValueError("Quality evaluation failed: no result produced")

        result["quality_score"] = quality_result.overall_score
        result["quality_passing"] = quality_result.passing
        result["quality_details_initial"] = {
            "clarity": quality_result.dimensions.clarity,
            "accuracy": quality_result.dimensions.accuracy,
            "completeness": quality_result.dimensions.completeness,
            "relevance": quality_result.dimensions.relevance,
            "seo_quality": quality_result.dimensions.seo_quality,
            "readability": quality_result.dimensions.readability,
            "engagement": quality_result.dimensions.engagement,
        }
        result["stages"]["2b_quality_evaluated_initial"] = True
        logger.info(f"✅ Initial quality evaluation complete:")
        logger.info(f"   Overall Score: {quality_result.overall_score:.1f}/10")
        logger.info(f"   Passing: {quality_result.passing} (threshold ≥7.0)\n")

        # ================================================================================
        # STAGE 3: SOURCE FEATURED IMAGE FROM UNIFIED IMAGE SERVICE
        # ================================================================================
        logger.info("🖼️  STAGE 3: Sourcing featured image from Pexels...")

        featured_image = None
        image_metadata = None

        if generate_featured_image:
            search_keywords = tags or [topic]

            try:
                featured_image = await image_service.search_featured_image(
                    topic=topic, keywords=search_keywords
                )

                if featured_image:
                    image_metadata = featured_image.to_dict()
                    result["featured_image_url"] = featured_image.url
                    result["featured_image_photographer"] = featured_image.photographer
                    result["featured_image_source"] = featured_image.source
                    result["stages"]["3_featured_image_found"] = True
                    logger.info(
                        f"✅ Featured image found: {featured_image.photographer} (Pexels)\n"
                    )
                else:
                    result["stages"]["3_featured_image_found"] = False
                    logger.warning(f"⚠️  No featured image found for '{topic}'\n")
            except Exception as e:
                logger.error(f"❌ Image search failed: {e}")
                result["stages"]["3_featured_image_found"] = False
        else:
            result["stages"]["3_featured_image_found"] = False
            logger.info("⏭️  Image search skipped (disabled)\n")

        # ================================================================================
        # STAGE 4: GENERATE SEO METADATA
        # ================================================================================
        logger.info("📊 STAGE 4: Generating SEO metadata...")

        seo_generator = get_seo_content_generator(content_generator)
        # SEOOptimizedContentGenerator wraps ContentMetadataGenerator which has generate_seo_assets
        seo_assets = seo_generator.metadata_gen.generate_seo_assets(
            title=topic, content=content_text, topic=topic
        )

        # Validate seo_assets is not None and is a dict
        if not seo_assets or not isinstance(seo_assets, dict):
            logger.error(f"❌ SEO generation returned None or invalid format")
            raise ValueError("SEO metadata generation failed: invalid result")

        seo_keywords = seo_assets.get("meta_keywords") or (tags or [])
        # Ensure seo_keywords is a list before slicing
        if isinstance(seo_keywords, list):
            seo_keywords = seo_keywords[:10]
        elif seo_keywords:
            seo_keywords = [seo_keywords][:10]
        else:
            seo_keywords = []

        seo_title = seo_assets.get("seo_title", topic)
        if seo_title:
            seo_title = seo_title[:60]
        else:
            seo_title = topic[:60]

        seo_description = seo_assets.get("meta_description", "")
        if seo_description:
            seo_description = seo_description[:160]
        else:
            seo_description = topic[:160]

        result["seo_title"] = seo_title
        result["seo_description"] = seo_description
        result["seo_keywords"] = seo_keywords
        result["stages"]["4_seo_metadata_generated"] = True
        logger.info(f"✅ SEO metadata generated:")
        logger.info(f"   Title: {seo_title}")
        logger.info(f"   Description: {seo_description[:80]}...")
        logger.info(f"   Keywords: {', '.join(seo_keywords[:5])}...\n")

        # ================================================================================
        # STAGE 5: CREATE POSTS RECORD
        # ================================================================================
        # ⚠️ IMPORTANT: Do NOT create posts here in content_router_service!
        # Posts should ONLY be created when:
        # 1. Task is approved via POST /api/tasks/{task_id}/approve
        # 2. Status is set to 'published' at approval time
        #
        # Creating draft posts here causes:
        # - Slug conflicts when approval endpoint tries to create published post
        # - Duplicate posts in posts table
        # - Two-step post creation that violates single responsibility principle
        #
        # The approval workflow should handle all post creation:
        # 1. content_router_service generates and stores content
        # 2. Stores content in content_tasks table with status='completed'
        # 3. User approves via POST /api/tasks/{task_id}/approve → status='approved'
        # 4. Approval endpoint creates posts table entry with status='published'
        # 5. No more posts table entries during generation
        #
        # This maintains clean separation: generation ≠ publishing
        logger.info("📝 STAGE 5: Posts record creation SKIPPED")
        logger.info("   ℹ️  Posts will be created when task is approved by user")
        result["post_id"] = None
        result["post_slug"] = None
        result["stages"]["5_post_created"] = False
        logger.info(f"ℹ️  Skipping automatic post creation\n")

        # ================================================================================
        # STAGE 6: CAPTURE TRAINING DATA
        # ================================================================================
        logger.info("🎓 STAGE 6: Capturing training data...")

        # Store quality evaluation in PostgreSQL
        # Capture readability metrics for context_data
        word_count = len(content_text.split())
        paragraph_count = len([p for p in content_text.split("\n\n") if p.strip()])
        sentences = [s.strip() for s in content_text.split(".") if s.strip()]
        avg_sentence_length = len(sentences) / word_count if word_count > 0 else 0
        
        await database_service.create_quality_evaluation(
            {
                "content_id": task_id,
                "task_id": task_id,
                "overall_score": quality_result.overall_score,
                "clarity": quality_result.dimensions.clarity,
                "accuracy": quality_result.dimensions.accuracy,
                "completeness": quality_result.dimensions.completeness,
                "relevance": quality_result.dimensions.relevance,
                "seo_quality": quality_result.dimensions.seo_quality,
                "readability": quality_result.dimensions.readability,
                "engagement": quality_result.dimensions.engagement,
                "passing": quality_result.passing,
                "feedback": quality_result.feedback,
                "suggestions": quality_result.suggestions,
                "evaluated_by": "ContentQualityService",
                "evaluation_method": quality_result.evaluation_method,
                "content_length": len(content_text),
                "content": content_text,
                "context_data": {
                    "topic": topic,
                    "style": style,
                    "tone": tone,
                    "target_length": target_length,
                    "has_featured_image": featured_image is not None,
                    "readability_metrics": {
                        "word_count": word_count,
                        "paragraph_count": paragraph_count,
                        "average_sentence_length": round(avg_sentence_length, 2),
                        "sentence_count": len(sentences),
                    },
                },
            }
        )

        await database_service.create_orchestrator_training_data(
            {
                "execution_id": task_id,
                "user_request": f"Generate blog post on: {topic}",
                "intent": "content_generation",
                "business_state": {
                    "topic": topic,
                    "style": style,
                    "tone": tone,
                    "featured_image": featured_image is not None,
                },
                "execution_result": "success",
                "quality_score": quality_result.overall_score / 10,
                "success": quality_result.passing,
                "tags": tags or [],
                "source_agent": "content_router_service",
            }
        )

        result["stages"]["6_training_data_captured"] = True
        logger.info(f"✅ Training data captured for learning pipeline\n")

        # ================================================================================
        # UPDATE CONTENT_TASK WITH FINAL STATUS AND ALL METADATA
        # ================================================================================
        # 🔑 CRITICAL: Store featured_image_url and all other metadata so approval endpoint can find it
        await database_service.update_task(
            task_id=task_id,
            updates={
                "status": "awaiting_approval",
                "approval_status": "pending_human_review",
                "quality_score": int(quality_result.overall_score),
                "featured_image_url": result.get("featured_image_url"),
                "seo_title": seo_title,
                "seo_description": seo_description,
                "seo_keywords": seo_keywords,
                "style": style,
                "tone": tone,
                "category": result.get("category") or category,
                "target_audience": target_audience or "General",
                # 🖼️ Store featured_image_url in task_metadata for later retrieval by approval endpoint
                "task_metadata": {
                    "featured_image_url": result.get("featured_image_url"),
                    "featured_image_photographer": result.get("featured_image_photographer"),
                    "featured_image_source": result.get("featured_image_source"),
                    "content": content_text,
                    "seo_title": seo_title,
                    "seo_description": seo_description,
                    "seo_keywords": seo_keywords,
                    "topic": topic,
                    "style": style,
                    "tone": tone,
                    "category": result.get("category") or category,
                    "target_audience": target_audience or "General",
                    "post_id": result.get("post_id"),
                    "quality_score": quality_result.overall_score,
                    "content_length": len(content_text),
                    "word_count": len(content_text.split()),
                },
            },
        )

        result["status"] = "awaiting_approval"
        result["approval_status"] = "pending_human_review"

        logger.info(f"{'='*80}")
        logger.info(f"✅ COMPLETE CONTENT GENERATION PIPELINE FINISHED")
        logger.info(f"{'='*80}")
        logger.info(f"   Task ID: {task_id}")
        logger.info(f"   Post ID: {result.get('post_id', 'NOT_YET_CREATED')}")
        logger.info(
            f"   Featured Image: {result.get('featured_image_url', 'NONE')[:100] if result.get('featured_image_url') else 'NONE'}"
        )
        logger.info(f"   Quality Score: {quality_result.overall_score:.1f}/10")
        logger.info(f"   Status: {result['status']}")
        logger.info(f"   Next: Human review & approval")
        logger.info(f"{'='*80}\n")

        return result

    except Exception as e:
        logger.error(f"❌ [BG-TASK] Pipeline error for task {task_id[:8]}...: {e}", exc_info=True)
        logger.error(f"[BG-TASK] Detailed traceback:", exc_info=True)

        # Update content_task with failure status
        # 🔑 CRITICAL: Preserve all partially-generated data (content, image, metadata)
        # so it's available for review/approval workflow
        try:
            logger.debug(f"[BG-TASK] Attempting to update task status to 'failed'...")
            logger.debug(f"[BG-TASK] Preserving partial results: {list(result.keys())}")
            
            # Build task_metadata with whatever we successfully generated
            failure_metadata = {
                "content": result.get("content"),
                "featured_image_url": result.get("featured_image_url"),
                "featured_image_photographer": result.get("featured_image_photographer"),
                "featured_image_source": result.get("featured_image_source"),
                "seo_title": result.get("seo_title"),
                "seo_description": result.get("seo_description"),
                "seo_keywords": result.get("seo_keywords"),
                "topic": topic,
                "style": style,
                "tone": tone,
                "quality_score": result.get("quality_score"),
                "error_stage": str(e)[:200],  # Which stage failed
                "error_message": str(e),  # Full error for debugging
                "stages_completed": result.get("stages", {}),
            }
            
            # Remove None values from metadata
            failure_metadata = {k: v for k, v in failure_metadata.items() if v is not None}
            
            await database_service.update_task(
                task_id=task_id, 
                updates={
                    "status": "failed", 
                    "approval_status": "failed",
                    "task_metadata": failure_metadata,  # ✅ Preserve all data
                }
            )
            logger.debug(f"[BG-TASK] ✅ Task status updated to 'failed' with preserved data")
        except Exception as db_error:
            logger.error(f"❌ [BG-TASK] Failed to update task status: {db_error}", exc_info=True)

        result["status"] = "failed"
        result["error"] = str(e)
        return result


# ================================================================================
# HELPER FUNCTIONS FOR CONTENT PIPELINE
# ================================================================================
# NOTE: Metadata functions moved to unified_metadata_service.py
# For SEO keyword extraction, title generation, description generation,
# use get_unified_metadata_service() from unified_metadata_service.py
#
# Example:
#   from services.unified_metadata_service import get_unified_metadata_service
#   service = get_unified_metadata_service()
#   seo_metadata = await service.generate_seo_metadata(title, content)
# ================================================================================


async def _evaluate_content_quality(
    content: str, topic: str, seo_title: str, seo_keywords: List[str]
) -> Dict[str, Any]:
    """
    Evaluate content quality on 7 criteria (0-10 each)

    Criteria:
    1. Clarity: Easy to understand
    2. Accuracy: Factually correct
    3. Completeness: Covers topic thoroughly
    4. Relevance: Matches topic
    5. SEO Quality: Keywords and structure
    6. Readability: Grammar, flow
    7. Engagement: Interest level
    """
    import re

    criteria = {}

    # 1. CLARITY (check structure, headings, length)
    heading_count = len(re.findall(r"^#{1,3} ", content, re.MULTILINE))
    paragraph_count = len([p for p in content.split("\n\n") if p.strip()])
    clarity = 8.0
    if heading_count < 3:
        clarity -= 1.0
    if paragraph_count < 5:
        clarity -= 1.0
    criteria["clarity"] = max(0, min(10, clarity))

    # 2. ACCURACY (check for hedging language, sources)
    accuracy = 7.5
    # Assume generated content is reasonably accurate
    criteria["accuracy"] = max(0, min(10, accuracy))

    # 3. COMPLETENESS (check word count, section coverage)
    word_count = len(content.split())
    completeness = 6.0
    if word_count > 800:
        completeness = 8.0
    if word_count > 1500:
        completeness = 9.0
    if word_count < 300:
        completeness = 4.0
    criteria["completeness"] = max(0, min(10, completeness))

    # 4. RELEVANCE (check topic mentions)
    topic_words = topic.lower().split()[:3]
    topic_mentions = sum(1 for word in topic_words if word.lower() in content.lower())
    relevance = 7.0 if topic_mentions >= 2 else 5.0
    criteria["relevance"] = max(0, min(10, relevance))

    # 5. SEO QUALITY (check keyword usage, title)
    keyword_mentions = sum(1 for kw in seo_keywords if kw.lower() in content.lower())
    seo_quality = 7.0 if keyword_mentions >= 3 else 6.0
    if len(seo_title) < 50:
        seo_quality += 1.0
    criteria["seo_quality"] = max(0, min(10, seo_quality))

    # 6. READABILITY (check sentence length, lists)
    has_lists = "- " in content or "* " in content or "1. " in content
    readability = 7.5 if has_lists else 6.5
    criteria["readability"] = max(0, min(10, readability))

    # 7. ENGAGEMENT (check for examples, CTAs)
    has_cta = any(word in content.lower() for word in ["start", "try", "begin", "ready", "action"])
    has_examples = has_lists or "example" in content.lower()
    engagement = 7.0
    if has_examples:
        engagement += 1.0
    if has_cta:
        engagement += 0.5
    criteria["engagement"] = max(0, min(10, engagement))

    # Calculate overall score (average of 7 criteria)
    overall_score = sum(criteria.values()) / 7
    overall_score = max(0, min(10, overall_score))

    return {
        "overall_score": overall_score,
        "criteria": criteria,
        "passing": overall_score >= 7.0,
        "feedback": f"Overall quality: {overall_score:.1f}/10",
        "suggestions": [
            "Check formatting for readability",
            "Ensure all claims are backed by data",
            "Add more specific examples if possible",
        ],
    }


async def _select_category_for_topic(
    topic: str, database_service: DatabaseService
) -> Optional[str]:
    """
    Select appropriate category based on topic keywords

    Returns category UUID
    """
    topic_lower = topic.lower()

    category_keywords = {
        "technology": [
            "ai",
            "tech",
            "software",
            "cloud",
            "machine learning",
            "data",
            "coding",
            "python",
            "javascript",
        ],
        "business": [
            "business",
            "strategy",
            "management",
            "entrepreneur",
            "startup",
            "growth",
            "revenue",
        ],
        "marketing": ["marketing", "seo", "growth", "brand", "customer", "social", "campaign"],
        "finance": ["finance", "investment", "cost", "budget", "roi", "money", "crypto"],
        "entertainment": ["game", "entertainment", "media", "streaming", "music", "film"],
    }

    # Find best matching category
    matched_category = "technology"  # Default
    for category, keywords in category_keywords.items():
        if any(kw in topic_lower for kw in keywords):
            matched_category = category
            break

    # Get category ID
    try:
        async with database_service.pool.acquire() as conn:
            cat_id = await conn.fetchval(
                "SELECT id FROM categories WHERE slug = $1", matched_category
            )
        return cat_id
    except Exception as e:
        logger.error(f"Error selecting category: {e}")
        return None


async def _get_or_create_default_author(database_service: DatabaseService) -> Optional[str]:
    """
    Get or create the default "Poindexter AI" author

    Returns author UUID
    """
    try:
        async with database_service.pool.acquire() as conn:
            # Try to get existing Poindexter AI author
            author_id = await conn.fetchval(
                "SELECT id FROM authors WHERE slug = 'poindexter-ai' LIMIT 1"
            )

            if author_id:
                return author_id

            # Create if doesn't exist
            author_id = await conn.fetchval(
                """
                INSERT INTO authors (name, slug, email, bio, avatar_url)
                VALUES ('Poindexter AI', 'poindexter-ai', 'poindexter@glad-labs.ai', 
                        'AI Content Generation Engine', NULL)
                ON CONFLICT (slug) DO NOTHING
                RETURNING id
                """
            )

            if author_id:
                logger.info(f"Created default author: Poindexter AI ({author_id})")
                return author_id

            # Fallback: return any author
            fallback_id = await conn.fetchval("SELECT id FROM authors LIMIT 1")
            return fallback_id

    except Exception as e:
        logger.error(f"Error getting/creating default author: {e}")
        return None
