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

import logging
from typing import Any, Dict, List, Optional

from .ai_content_generator import get_content_generator
from .database_service import DatabaseService
from .image_service import get_image_service
from .prompt_manager import get_prompt_manager
from .quality_service import EvaluationMethod, UnifiedQualityService
from .seo_content_generator import get_seo_content_generator
from .webhook_delivery_service import emit_webhook_event

logger = logging.getLogger(__name__)


# ============================================================================
# TEXT NORMALIZATION — replace Unicode smart quotes / dashes with ASCII
# ============================================================================

def _normalize_text(text: str) -> str:
    """Replace Unicode smart quotes, dashes, and special whitespace with ASCII equivalents.

    Ollama (and other LLMs) frequently produce these characters, which can cause
    encoding / rendering issues on the public site.
    """
    if not text:
        return text
    return (
        text
        .replace("\u2019", "'")   # right single quote
        .replace("\u2018", "'")   # left single quote
        .replace("\u201c", '"')   # left double quote
        .replace("\u201d", '"')   # right double quote
        .replace("\u2014", "--")  # em dash
        .replace("\u2013", "-")   # en dash
        .replace("\u2026", "...") # ellipsis
        .replace("\u00a0", " ")   # non-breaking space
        .replace("\u2011", "-")   # non-breaking hyphen
    )


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
        logger.info("📋 [CONTENT_TASK_STORE] Creating task (async)")
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

            logger.debug("   📝 Calling database_service.add_task() (async)...")

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

            logger.info("✅ [CONTENT_TASK_STORE] Task CREATED and PERSISTED (async)")
            logger.info(f"   Task ID: {task_id}")
            logger.info("   Status: pending")
            logger.debug("   🎯 Ready for processing")
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
            return False

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
            return []
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
        writing_style_context: Optional[str] = None,
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
            writing_style_context: Optional writing style excerpts for voice matching

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
                writing_style_context=writing_style_context,
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
            logger.warning(f"Error generating image prompt: {e}", exc_info=True)
            return f"Featured image for: {topic}"


# ============================================================================
# ============================================================================
# BACKGROUND TASK PROCESSORS
# ============================================================================


async def _generate_canonical_title(
    topic: str, primary_keyword: str, content_excerpt: str
) -> Optional[str]:
    """
    Generate a canonical, SEO-optimized title for blog content using unified prompt manager.
    Consolidates all title generation logic into a single, testable function.

    Args:
        topic: The blog topic
        primary_keyword: Primary SEO keyword
        content_excerpt: First 500 chars of generated content for context

    Returns:
        Generated title or None if generation fails
    """
    try:
        from .model_consolidation_service import get_model_consolidation_service

        pm = get_prompt_manager()
        service = get_model_consolidation_service()

        # Use unified prompt manager to get SEO title generation prompt
        prompt = pm.get_prompt(
            "seo.generate_title",
            content=content_excerpt,
            primary_keyword=primary_keyword or topic,
        )

        # Use model consolidation service for intelligent provider fallback
        result = await service.generate(
            prompt=prompt,
            temperature=0.7,
            # Service will automatically select best available model
        )

        if result and result.text:
            # Clean up the title
            title = result.text.strip().strip('"').strip("'").strip()
            # Truncate if too long
            if len(title) > 100:
                title = title[:97] + "..."
            logger.debug(f"Generated title: {title}")
            return title

        return None

    except Exception as e:
        logger.warning(f"Error generating canonical title: {e}", exc_info=True)
        return None


async def _stage_verify_task(database_service, task_id, result):
    """Stage 1: Verify task record exists in database."""
    logger.info("📋 STAGE 1: Verifying task record exists...")
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
        logger.error(f"❌ Failed to verify task: {e}", exc_info=True)
        result["stages"]["1_content_task_created"] = False


def _parse_model_preferences(models_by_phase):
    """Stage 2A: Parse user model preferences from models_by_phase dict.

    Returns (preferred_model, preferred_provider) tuple.
    """
    preferred_model = None
    preferred_provider = None
    logger.info("🔍 STEP 2A: Processing model selections from UI")
    logger.info(f"   models_by_phase = {models_by_phase}")
    if not models_by_phase:
        return preferred_model, preferred_provider

    # Try to get model for 'draft' phase (main content generation)
    draft_model = (
        models_by_phase.get("draft")
        or models_by_phase.get("generate")
        or models_by_phase.get("content")
    )
    logger.info(f"   draft_model = {draft_model}")
    if not draft_model or draft_model == "auto":
        return preferred_model, preferred_provider

    # Clean up malformed model names (e.g., "gemini-gemini-pro" → "gemini-pro")
    draft_model = draft_model.strip()

    # Parse provider and model from selection
    # Format can be: "gemini", "gemini/gemini-pro", "gpt-4", "claude-3-opus", etc.
    if "/" in draft_model:
        preferred_provider, preferred_model = draft_model.split("/", 1)
    else:
        # Infer provider from model name
        draft_model_lower = draft_model.lower()

        # Handle duplicate provider prefixes (e.g., "gemini-gemini-pro", "gpt-gpt-4")
        if draft_model_lower.startswith("gemini-gemini-"):
            # "gemini-gemini-1.5-pro" → provider: "gemini", model: "gemini-1.5-pro"
            preferred_provider = "gemini"
            preferred_model = draft_model_lower[7:]  # Strip first "gemini-"
        elif draft_model_lower.startswith("gpt-gpt-"):
            # "gpt-gpt-4" → provider: "openai", model: "gpt-4"
            preferred_provider = "openai"
            preferred_model = draft_model_lower[4:]  # Strip first "gpt-"
        elif draft_model_lower.startswith("claude-claude-"):
            # "claude-claude-opus" → provider: "anthropic", model: "claude-opus"
            preferred_provider = "anthropic"
            preferred_model = draft_model_lower[7:]  # Strip first "claude-"
        elif "gemini" in draft_model_lower:
            preferred_provider = "gemini"
            preferred_model = draft_model
        elif "gpt" in draft_model_lower or "openai" in draft_model_lower:
            preferred_provider = "openai"
            preferred_model = draft_model
        elif "claude" in draft_model_lower or "anthropic" in draft_model_lower:
            preferred_provider = "anthropic"
            preferred_model = draft_model
        elif (
            "ollama" in draft_model_lower
            or "mistral" in draft_model_lower
            or "llama" in draft_model_lower
        ):
            preferred_provider = "ollama"
            preferred_model = draft_model
        else:
            # Default to model name as-is
            preferred_model = draft_model

    logger.info(
        f"   ✅ FINAL: preferred_model='{preferred_model}', preferred_provider='{preferred_provider}'"
    )
    logger.info(
        f"🎯 User selected model: {preferred_model or 'auto'} (provider: {preferred_provider or 'auto'})"
    )
    return preferred_model, preferred_provider


async def _build_writing_style_context(
    database_service: Optional[DatabaseService],
    max_samples: int = 3,
    max_words_per_sample: int = 500,
) -> Optional[str]:
    """Fetch active writing style samples and build a context string for LLM prompts.

    Queries the writing_samples table for active samples, extracts a truncated
    excerpt from each (up to *max_words_per_sample* words), and returns a
    formatted string suitable for injection into a system prompt.

    Returns None if no samples are available or if the database is unreachable.
    """
    if not database_service:
        return None

    try:
        writing_style_db = getattr(database_service, "writing_style", None)
        if not writing_style_db:
            return None

        # Fetch all user samples (we don't know the user in this context,
        # so we look for any active samples across users)
        samples = await writing_style_db.get_user_writing_samples(
            user_id="default", limit=max_samples
        )

        if not samples:
            return None

        excerpts = []
        for sample in samples[:max_samples]:
            content = sample.get("content", "")
            title = sample.get("title", "Untitled")
            if not content:
                continue

            # Truncate to max_words_per_sample words
            words = content.split()
            if len(words) > max_words_per_sample:
                excerpt = " ".join(words[:max_words_per_sample]) + "..."
            else:
                excerpt = content

            excerpts.append(f"### Sample: {title}\n{excerpt}")

        if not excerpts:
            return None

        logger.info(
            "Loaded %d writing style sample(s) for voice matching", len(excerpts)
        )
        return "\n\n".join(excerpts)

    except Exception as e:
        logger.warning(
            "Failed to load writing style samples (non-fatal, proceeding without): %s",
            e,
            exc_info=True,
        )
        return None


async def _stage_generate_content(
    database_service, task_id, topic, style, tone, target_length, tags, models_by_phase, result
):
    """Stage 2: Generate blog content via AI and store in database.

    Returns (content_text, model_used, metrics, title).
    """
    logger.info("✍️  STAGE 2: Generating blog content...")

    content_generator = get_content_generator()
    preferred_model, preferred_provider = _parse_model_preferences(models_by_phase)

    # Fetch active writing style samples for voice/tone matching
    writing_style_context = await _build_writing_style_context(database_service)

    # Build research context — real links, internal posts, web sources
    research_context = ""
    try:
        from services.research_service import ResearchService
        research_svc = ResearchService(pool=database_service.pool if database_service else None)
        research_context = await research_svc.build_context(topic)
        if research_context:
            logger.info("📚 Research context built: %d chars", len(research_context))
    except Exception as e:
        logger.warning("Research context skipped: %s", e)

    content_text, model_used, metrics = await content_generator.generate_blog_post(
        topic=topic,
        style=style,
        tone=tone,
        target_length=target_length,
        tags=tags or [],
        preferred_model=preferred_model,
        preferred_provider=preferred_provider,
        writing_style_context=writing_style_context,
        research_context=research_context,
    )

    # Validate content_text is not None
    if not content_text:
        logger.error("❌ Content generation returned None or empty")
        raise ValueError("Content generation failed: no content produced")

    # Generate canonical title based on topic and content
    logger.info("📌 Generating title from content...")
    primary_keyword = tags[0] if tags else topic
    title = await _generate_canonical_title(topic, primary_keyword, content_text[:500])
    if not title:
        title = topic  # Fallback to topic if title generation fails
    logger.info(f"✅ Title generated: {title}")

    # Normalize smart quotes / special chars before persisting
    content_text = _normalize_text(content_text)
    title = _normalize_text(title)

    # Update content_task with generated content, title, and model tracking
    await database_service.update_task(
        task_id=task_id,
        updates={
            "status": "in_progress",
            "content": content_text,
            "title": title,
            "model_used": model_used,
            "models_used_by_phase": metrics.get("models_used_by_phase", {}),
            "model_selection_log": metrics.get("model_selection_log", {}),
        },
    )

    result["content"] = content_text
    result["content_length"] = len(content_text)
    result["title"] = title
    result["model_used"] = model_used
    result["models_used_by_phase"] = metrics.get("models_used_by_phase", {})
    result["model_selection_log"] = metrics.get("model_selection_log", {})
    result["stages"]["2_content_generated"] = True
    logger.info(f"✅ Content generated ({len(content_text)} chars) using {model_used}\n")

    # Log cloud API cost if tracked by the generator
    cost_log = metrics.get("cost_log")
    if cost_log and database_service:
        try:
            cost_log["task_id"] = task_id
            await database_service.log_cost(cost_log)
            logger.info("💰 Cost logged: $%.4f (%s/%s)", cost_log["cost_usd"], cost_log["provider"], cost_log["model"])
        except Exception as e:
            logger.warning("Cost logging failed (non-critical): %s", e)

    return content_text, model_used, metrics, title


async def _stage_quality_evaluation(topic, tags, content_text, quality_service, result):
    """Stage 2B: Early quality evaluation of generated content.

    Returns the quality_result object.
    """
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
        logger.error("❌ Quality evaluation returned None")
        raise ValueError("Quality evaluation failed: no result produced")

    result["quality_score"] = quality_result.overall_score
    result["quality_passing"] = quality_result.passing
    result["truncation_detected"] = quality_result.truncation_detected
    result["quality_details_initial"] = {
        "clarity": quality_result.dimensions.clarity,
        "accuracy": quality_result.dimensions.accuracy,
        "completeness": quality_result.dimensions.completeness,
        "relevance": quality_result.dimensions.relevance,
        "seo_quality": quality_result.dimensions.seo_quality,
        "readability": quality_result.dimensions.readability,
        "engagement": quality_result.dimensions.engagement,
        "truncation_detected": quality_result.truncation_detected,
    }
    result["stages"]["2b_quality_evaluated_initial"] = True
    logger.info("✅ Initial quality evaluation complete:")
    logger.info(f"   Overall Score: {quality_result.overall_score:.1f}/100")
    logger.info(f"   Passing: {quality_result.passing} (threshold ≥70.0)")
    if quality_result.truncation_detected:
        logger.warning("   ⚠️  TRUNCATION DETECTED — content appears cut off mid-sentence")
    logger.info("")

    return quality_result


async def _stage_replace_inline_images(database_service, task_id, topic, content_text, image_service, result):
    """Stage 2C: Replace [IMAGE-N] placeholders with Pexels images.

    Returns the (possibly modified) content_text.
    """
    import re as _re

    image_placeholders = _re.findall(r"\[IMAGE-(\d+)(?::\s*([^\]]*))?\]", content_text)

    # If LLM didn't produce placeholders (common with local models), inject them
    # after the 2nd and 4th ## headings for visual variety
    if not image_placeholders:
        headings = list(_re.finditer(r"^## .+$", content_text, _re.MULTILINE))
        if len(headings) >= 3:
            # Insert after 2nd heading's paragraph, and after 4th if available
            insert_positions = []
            for idx in [1, 3]:
                if idx < len(headings):
                    h = headings[idx]
                    # Find end of first paragraph after this heading
                    para_end = content_text.find("\n\n", h.end())
                    if para_end > 0:
                        insert_positions.append((para_end, idx + 1))

            # Insert in reverse order to preserve positions
            for pos, img_num in reversed(insert_positions):
                placeholder = f"\n[IMAGE-{img_num}: {topic} illustration]\n"
                content_text = content_text[:pos] + placeholder + content_text[pos:]

            # Re-scan for the injected placeholders
            image_placeholders = _re.findall(r"\[IMAGE-(\d+)(?::\s*([^\]]*))?\]", content_text)
            if image_placeholders:
                logger.info("📌 Injected %d image placeholders (LLM didn't produce any)", len(image_placeholders))

    if not image_placeholders:
        result["stages"]["2c_inline_images_replaced"] = False
        logger.info("⏭️  No [IMAGE-N] placeholders to replace\n")
        return content_text

    logger.info(
        f"🖼️  STAGE 2C: Replacing {len(image_placeholders)} inline image placeholders..."
    )
    used_image_ids = set()  # Avoid duplicate images

    for num, desc in image_placeholders:
        # Use the LLM's description as search query, fall back to topic
        search_query = desc.strip() if desc else topic
        # Shorten to first 5 words for better Pexels search results
        search_words = search_query.split()[:5]
        short_query = " ".join(search_words)

        # Build safe keywords list — guard against empty topic (#1263 Copilot review)
        keywords = [topic.split()[0]] if topic and topic.strip() else []

        try:
            img = await image_service.search_featured_image(
                topic=short_query, keywords=keywords
            )

            if img and img.url and img.url not in used_image_ids:
                used_image_ids.add(img.url)
                alt_text = desc.strip() if desc else f"{topic} illustration"
                # Clean alt text of special chars for markdown
                alt_text = (
                    alt_text.replace("[", "").replace("]", "").replace("\n", " ")[:120]
                )
                photographer = getattr(img, "photographer", "Pexels")
                markdown_img = (
                    f"\n\n![{alt_text}]({img.url})\n*Photo by {photographer} on Pexels*\n\n"
                )

                # Use regex to handle spacing variations in [IMAGE-N: desc]
                content_text = _re.sub(
                    rf"\[IMAGE-{num}[^\]]*\]", markdown_img, content_text, count=1
                )
                logger.info(f"  ✅ [IMAGE-{num}] → Pexels image by {photographer}")
            else:
                # Remove placeholder if no image found
                content_text = _re.sub(rf"\[IMAGE-{num}[^\]]*\]", "", content_text, count=1)
                logger.warning(
                    f"  ⚠️ [IMAGE-{num}] — no suitable image found, removed placeholder"
                )
        except Exception as e:
            logger.error(f"  ❌ [IMAGE-{num}] search failed: {e}", exc_info=True)
            content_text = _re.sub(rf"\[IMAGE-{num}[^\]]*\]", "", content_text, count=1)

    # Normalize again after image placeholder substitution
    content_text = _normalize_text(content_text)
    # Update DB with image-populated content
    await database_service.update_task(task_id=task_id, updates={"content": content_text})
    result["content"] = content_text
    result["stages"]["2c_inline_images_replaced"] = True
    result["inline_images_replaced"] = len(used_image_ids)
    logger.info(f"✅ Replaced {len(used_image_ids)} inline images in content\n")

    return content_text


async def _stage_source_featured_image(topic, tags, generate_featured_image, image_service, result):
    """Stage 3: Source a featured image — try SDXL generation first, fall back to Pexels.

    Returns the featured_image object (or None).
    """
    logger.info("🖼️  STAGE 3: Sourcing featured image...")

    featured_image = None

    if not generate_featured_image:
        result["stages"]["3_featured_image_found"] = False
        logger.info("⏭️  Image search skipped (disabled)\n")
        return featured_image

    # Strategy 1: Try SDXL generation for a unique image
    if image_service.sdxl_available or not image_service.sdxl_initialized:
        try:
            import os
            import tempfile
            sdxl_prompt = (
                f"Cyberpunk tech aesthetic blog header about {topic}. "
                f"Dark matte black background, cyan and teal neon accents, "
                f"chrome metallic details, subtle purple highlights. "
                f"Clean but raw, polished but technical. No text overlay. "
                f"Professional digital art, 16:9 aspect ratio."
            )
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir=os.path.join(
                os.path.expanduser("~"), "Downloads", "glad-labs-generated-images"
            )) as tmp:
                output_path = tmp.name
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            success = await image_service.generate_image(
                prompt=sdxl_prompt,
                output_path=output_path,
                high_quality=True,
            )
            if success and os.path.exists(output_path):
                # Create a featured_image-like object for the pipeline
                from dataclasses import dataclass

                @dataclass
                class GeneratedImage:
                    url: str
                    photographer: str
                    source: str

                featured_image = GeneratedImage(
                    url=output_path,  # Local path for now
                    photographer="SDXL (generated)",
                    source="sdxl_local",
                )
                result["featured_image_url"] = output_path
                result["featured_image_photographer"] = "SDXL (generated)"
                result["featured_image_source"] = "sdxl_local"
                result["stages"]["3_featured_image_found"] = True
                result["stages"]["3_image_source"] = "sdxl"
                logger.info("✅ Featured image generated via SDXL\n")
                return featured_image
        except Exception as e:
            logger.info("SDXL generation skipped (%s), falling back to Pexels", e)

    # Strategy 2: Fall back to Pexels (free stock photos)
    search_keywords = tags or [topic]
    try:
        featured_image = await image_service.search_featured_image(
            topic=topic, keywords=search_keywords
        )
        if featured_image:
            result["featured_image_url"] = featured_image.url
            result["featured_image_photographer"] = featured_image.photographer
            result["featured_image_source"] = featured_image.source
            result["stages"]["3_featured_image_found"] = True
            result["stages"]["3_image_source"] = "pexels"
            logger.info(
                f"✅ Featured image found: {featured_image.photographer} (Pexels)\n"
            )
        else:
            result["stages"]["3_featured_image_found"] = False
            logger.warning(f"⚠️  No featured image found for '{topic}'\n")
    except Exception as e:
        logger.error(f"❌ Image search failed: {e}", exc_info=True)
        result["stages"]["3_featured_image_found"] = False

    return featured_image


async def _stage_generate_seo_metadata(topic, tags, content_text, content_generator, result):
    """Stage 4: Generate SEO metadata (title, description, keywords).

    Returns (seo_title, seo_description, seo_keywords).
    """
    logger.info("📊 STAGE 4: Generating SEO metadata...")

    seo_generator = get_seo_content_generator(content_generator)
    # SEOOptimizedContentGenerator wraps ContentMetadataGenerator which has generate_seo_assets
    seo_assets = seo_generator.metadata_gen.generate_seo_assets(
        title=topic, content=content_text, topic=topic
    )

    # Validate seo_assets is not None and is a dict
    if not seo_assets or not isinstance(seo_assets, dict):
        logger.error("❌ SEO generation returned None or invalid format")
        raise ValueError("SEO metadata generation failed: invalid result")

    seo_keywords = seo_assets.get("meta_keywords") or (tags or [])
    # Ensure seo_keywords is a list, filter out None/empty values
    if isinstance(seo_keywords, list):
        seo_keywords = [kw for kw in seo_keywords if kw and isinstance(kw, str) and kw.strip()][
            :10
        ]
    elif seo_keywords and isinstance(seo_keywords, str):
        seo_keywords = [seo_keywords.strip()][:10] if seo_keywords.strip() else []
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
    result["seo_keywords"] = ", ".join(seo_keywords) if isinstance(seo_keywords, list) else (seo_keywords or "")
    result["stages"]["4_seo_metadata_generated"] = True
    logger.info("✅ SEO metadata generated:")
    logger.info(f"   Title: {seo_title}")
    logger.info(f"   Description: {seo_description[:80]}...")
    logger.info(f"   Keywords: {', '.join(seo_keywords[:5])}...\n")

    return seo_title, seo_description, seo_keywords


async def _stage_capture_training_data(
    database_service, task_id, topic, style, tone, target_length, tags,
    content_text, quality_result, featured_image, result
):
    """Stage 6: Capture quality evaluation and training data in PostgreSQL."""
    logger.info("🎓 STAGE 6: Capturing training data...")

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

    # Wrap in try/except — re-processed tasks (auto-retry, GPU scheduler)
    # may already have training data from a previous run.
    try:
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
    except Exception as _td_err:
        logger.warning("Training data insert skipped (likely re-processed task): %s", _td_err)

    result["stages"]["6_training_data_captured"] = True
    logger.info("✅ Training data captured for learning pipeline\n")


async def _stage_finalize_task(
    database_service, task_id, topic, style, tone, content_text,
    quality_result, seo_title, seo_description, seo_keywords,
    category, target_audience, result
):
    """Final stage: Update content_task with final status and all metadata."""
    # ⚠️ STAGE 5 NOTE: Posts record creation is SKIPPED here.
    # Posts should ONLY be created when task is approved via POST /api/tasks/{task_id}/approve.
    # This maintains clean separation: generation != publishing.
    logger.info("📝 STAGE 5: Posts record creation SKIPPED")
    logger.info("   ℹ️  Posts will be created when task is approved by user")
    result["post_id"] = None
    result["post_slug"] = None
    result["stages"]["5_post_created"] = False
    logger.info("ℹ️  Skipping automatic post creation\n")

    # Normalize SEO text fields before final persist
    seo_title = _normalize_text(seo_title) if seo_title else seo_title
    seo_description = _normalize_text(seo_description) if seo_description else seo_description
    content_text = _normalize_text(content_text)

    # 🔑 CRITICAL: Store featured_image_url and all other metadata so approval endpoint can find it
    await database_service.update_task(
        task_id=task_id,
        updates={
            "status": "awaiting_approval",
            "approval_status": "pending",
            "quality_score": int(quality_result.overall_score),
            "featured_image_url": result.get("featured_image_url"),
            "seo_title": seo_title,
            "seo_description": seo_description,
            "seo_keywords": ", ".join(seo_keywords) if isinstance(seo_keywords, list) else (seo_keywords or ""),
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
    result["approval_status"] = "pending"


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
    Complete Content Generation Pipeline with Image Sourcing & SEO Metadata.

    Process a content generation request through the full pipeline:

    STAGE 1: Verify content_task record exists
    STAGE 2: Generate blog content
    STAGE 2B: Early quality evaluation
    STAGE 2C: Replace inline image placeholders
    STAGE 3: Source featured image from Pexels
    STAGE 3.5+3.7: Multi-model QA (programmatic validator + cloud critic)
    STAGE 4: Generate SEO metadata
    STAGE 5: (Skipped) Posts record created at approval time
    STAGE 6: Capture training data for learning

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

    # Generate task_id if not provided
    if not task_id:
        task_id = str(uuid4())

    if not database_service:
        logger.error("❌ DatabaseService not provided - cannot persist content")
        raise ValueError("DatabaseService is required for content_tasks persistence")

    logger.info(f"\n{'='*80}")
    logger.info("🚀 COMPLETE CONTENT GENERATION PIPELINE")
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

        # Stage 1: Verify task record
        await _stage_verify_task(database_service, task_id, result)

        # Stage 2: Generate blog content
        content_text, model_used, metrics, title = await _stage_generate_content(
            database_service, task_id, topic, style, tone, target_length, tags, models_by_phase, result
        )

        # Stage 2B: Quality evaluation
        quality_result = await _stage_quality_evaluation(topic, tags, content_text, quality_service, result)

        # Stage 2C: Replace inline image placeholders
        content_text = await _stage_replace_inline_images(
            database_service, task_id, topic, content_text, image_service, result
        )

        # Stage 3: Source featured image
        featured_image = await _stage_source_featured_image(
            topic, tags, generate_featured_image, image_service, result
        )

        # Stage 3.5 + 3.7: Multi-Model QA (programmatic validator + cloud critic)
        # Replaces separate programmatic validation and ad-hoc cross-model review
        # with unified MultiModelQA: deterministic hallucination checks (40% weight)
        # + adversarial cloud model review (60% weight). Approval requires all
        # reviewers to pass AND weighted score >= 70.
        from services.multi_model_qa import MultiModelQA
        _qa = MultiModelQA(pool=database_service.pool)
        _qa_result = await _qa.review(
            title=_normalize_text(result.get("seo_title", topic)),
            content=_normalize_text(content_text),
            topic=topic,
        )
        result["qa_final_score"] = _qa_result.final_score
        result["qa_reviews"] = [
            {"reviewer": r.reviewer, "score": r.score, "approved": r.approved,
             "feedback": r.feedback, "provider": r.provider}
            for r in _qa_result.reviews
        ]
        # Log QA review cost to database
        if _qa_result.cost_log and database_service:
            try:
                _qa_result.cost_log["task_id"] = task_id
                await database_service.log_cost(_qa_result.cost_log)
                logger.info("💰 QA cost logged: $%.4f (%s/%s)", _qa_result.cost_log["cost_usd"], _qa_result.cost_log["provider"], _qa_result.cost_log["model"])
            except Exception as e:
                logger.warning("QA cost logging failed (non-critical): %s", e)

        if not _qa_result.approved:
            logger.warning(
                "[MULTI_QA] Content rejected for task %s:\n%s",
                task_id[:8], _qa_result.summary,
            )
            result["status"] = "rejected"
            await database_service.update_task(task_id, {
                "status": "rejected",
                "error_message": f"Multi-model QA rejected (score: {_qa_result.final_score:.0f}): "
                    + (_qa_result.reviews[-1].feedback[:200] if _qa_result.reviews else "No feedback"),
            })
            return result
        logger.info("[MULTI_QA] Content approved for task %s: %s", task_id[:8], _qa_result.summary.split("\\n")[0])

        # Stage 4: Generate SEO metadata
        content_generator = get_content_generator()
        seo_title, seo_description, seo_keywords = await _stage_generate_seo_metadata(
            topic, tags, content_text, content_generator, result
        )

        # Stage 6: Capture training data
        await _stage_capture_training_data(
            database_service, task_id, topic, style, tone, target_length, tags,
            content_text, quality_result, featured_image, result
        )

        # Final: Update task with status and metadata
        await _stage_finalize_task(
            database_service, task_id, topic, style, tone, content_text,
            quality_result, seo_title, seo_description, seo_keywords,
            category, target_audience, result
        )

        logger.info(f"{'='*80}")
        logger.info("✅ COMPLETE CONTENT GENERATION PIPELINE FINISHED")
        logger.info(f"{'='*80}")
        logger.info(f"   Task ID: {task_id}")
        logger.info(f"   Post ID: {result.get('post_id', 'NOT_YET_CREATED')}")
        logger.info(
            f"   Featured Image: {result.get('featured_image_url', 'NONE')[:100] if result.get('featured_image_url') else 'NONE'}"
        )
        logger.info(f"   Quality Score: {quality_result.overall_score:.1f}/100")
        logger.info(f"   Status: {result['status']}")
        logger.info("   Next: Human review & approval")
        logger.info(f"{'='*80}\n")

        return result

    except Exception as e:
        logger.error(f"❌ [BG-TASK] Pipeline error for task {task_id[:8]}...: {e}", exc_info=True)
        logger.error("[BG-TASK] Detailed traceback:", exc_info=True)

        # Update content_task with failure status
        # 🔑 CRITICAL: Preserve all partially-generated data (content, image, metadata)
        # so it's available for review/approval workflow
        try:
            logger.debug("[BG-TASK] Attempting to update task status to 'failed'...")
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
                    "error_message": str(e),
                    "task_metadata": failure_metadata,  # ✅ Preserve all data
                },
            )
            logger.debug("[BG-TASK] ✅ Task status updated to 'failed' with preserved data")

            # Emit webhook so OpenClaw is notified of pipeline failure
            try:
                await emit_webhook_event(database_service.pool, "task.failed", {
                    "task_id": task_id, "topic": topic, "error": str(e)[:200],
                })
            except Exception:
                logger.warning("[WEBHOOK] Failed to emit task.failed event from pipeline", exc_info=True)
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
        logger.error(f"Error selecting category: {e}", exc_info=True)
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
            author_id = await conn.fetchval("""
                INSERT INTO authors (name, slug, email, bio, avatar_url)
                VALUES ('Poindexter AI', 'poindexter-ai', 'poindexter@glad-labs.ai', 
                        'AI Content Generation Engine', NULL)
                ON CONFLICT (slug) DO NOTHING
                RETURNING id
                """)

            if author_id:
                logger.info(f"Created default author: Poindexter AI ({author_id})")
                return author_id

            # Fallback: return any author
            fallback_id = await conn.fetchval("SELECT id FROM authors LIMIT 1")
            return fallback_id

    except Exception as e:
        logger.error(f"Error getting/creating default author: {e}", exc_info=True)
        return None
