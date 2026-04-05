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

import asyncio

from services.logger_config import get_logger
from typing import Any, Dict, List, Optional

from .ai_content_generator import get_content_generator
from .audit_log import audit_log_bg
from .database_service import DatabaseService
from .image_service import get_image_service
from .prompt_manager import get_prompt_manager
from .quality_service import EvaluationMethod, UnifiedQualityService
from .seo_content_generator import get_seo_content_generator
from .webhook_delivery_service import emit_webhook_event

logger = get_logger(__name__)

# Per-stage timeouts in seconds — easy to tune in one place.
# Prevents a single slow stage (e.g. Ollama generation) from eating the entire budget.
_DEFAULT_STAGE_TIMEOUTS = {
    "verify_task": 30,
    "generate_content": 480,      # Stage 2: Draft — 8 min (long posts with code examples need time)
    "quality_evaluation": 60,     # Stage 2B: Pattern QA — 1 min
    "url_validation": 60,         # Stage 2B.1: URL checks — 1 min
    "replace_inline_images": 120, # Stage 2C: Image replacement — 2 min
    "source_featured_image": 120, # Stage 3: Image search — 2 min
    "cross_model_qa": 180,        # Stage 3.5+3.7: Multi-model QA — 3 min
    "generate_seo_metadata": 60,  # Stage 4: SEO — 1 min
    "capture_training_data": 30,  # Stage 5: Training data — 30s
    "finalize_task": 30,          # Stage 6: Finalize — 30s
}


def _load_stage_timeouts() -> dict:
    """Build stage timeouts from defaults, overridden by app_settings keys like stage_timeout_draft."""
    from services.site_config import site_config

    timeouts = dict(_DEFAULT_STAGE_TIMEOUTS)
    # Map app_settings keys to stage names
    _overrides = {
        "stage_timeout_verify_task": "verify_task",
        "stage_timeout_draft": "generate_content",
        "stage_timeout_qa": "quality_evaluation",
        "stage_timeout_url_validation": "url_validation",
        "stage_timeout_inline_images": "replace_inline_images",
        "stage_timeout_featured_image": "source_featured_image",
        "stage_timeout_cross_model_qa": "cross_model_qa",
        "stage_timeout_seo": "generate_seo_metadata",
        "stage_timeout_training_data": "capture_training_data",
        "stage_timeout_finalize": "finalize_task",
    }
    for setting_key, stage_name in _overrides.items():
        val = site_config.get(setting_key)
        if val is not None:
            try:
                timeouts[stage_name] = int(val)
            except (ValueError, TypeError):
                pass
    return timeouts


STAGE_TIMEOUTS = _load_stage_timeouts()


async def _run_stage_with_timeout(coro, stage_name: str, task_id: str):
    """Wrap a stage coroutine with a timeout. On timeout, log and return None."""
    timeout = STAGE_TIMEOUTS.get(stage_name, 120)
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.error(
            "Stage '%s' timed out after %ds for task %s",
            stage_name, timeout, task_id[:8],
        )
        audit_log_bg(
            "stage_timeout", "content_router",
            {"stage": stage_name, "timeout_seconds": timeout},
            task_id=task_id, severity="warning",
        )
        return None


async def _is_stage_enabled(pool, stage_key: str) -> bool:
    """Check if a pipeline stage is enabled in the database.

    Returns True if the stage is enabled or if the table doesn't exist (backwards compatible).
    """
    if pool is None:
        return True  # No DB = run everything (local dev)
    try:
        row = await pool.fetchrow(
            "SELECT enabled FROM pipeline_stages WHERE key = $1", stage_key
        )
        if row is None:
            return True  # Stage not in DB = run it (backwards compatible)
        return row["enabled"]
    except Exception:
        return True  # Table doesn't exist = run everything


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
            audit_log_bg("task_created", "content_router", {
                "topic": topic[:100], "style": style, "tone": tone,
                "target_length": target_length, "request_type": request_type,
            }, task_id=task_id)
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
    topic: str, primary_keyword: str, content_excerpt: str, existing_titles: str = ""
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

        # Inject existing titles to avoid repetition
        if existing_titles:
            prompt += f"\n\n⚠️ AVOID SIMILARITY to these recent titles:\n{existing_titles}\n\nYour title must be DISTINCTLY DIFFERENT in structure and wording."

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


async def _build_rag_context(
    database_service: Optional[DatabaseService], topic: str
) -> Optional[str]:
    """Embed the topic and search pgvector for similar published posts.

    Returns a formatted context string for injection into the generation prompt,
    or None if RAG is unavailable or finds no results.

    This is non-blocking: if Ollama or pgvector is unavailable, returns None
    so the pipeline continues without RAG.
    """
    if not database_service or not getattr(database_service, "embeddings", None):
        return None

    try:
        from .ollama_client import OllamaClient

        ollama = OllamaClient()
        topic_embedding = await ollama.embed(topic)
        await ollama.close()

        similar_posts = await database_service.embeddings.search_similar(
            embedding=topic_embedding,
            limit=5,
            source_type="post",
            min_similarity=0.3,
        )

        if not similar_posts:
            return None

        # Look up post details (title, excerpt, slug) for each match
        lines = [
            "RELATED POSTS WE'VE PUBLISHED (reference for internal linking, avoid repeating same angles):"
        ]
        pool = database_service.pool if database_service else None
        for i, match in enumerate(similar_posts, 1):
            post_id = match.get("source_id", "")
            similarity = match.get("similarity", 0)
            metadata = match.get("metadata") or {}
            title = metadata.get("title", "Untitled")

            # Try to fetch slug and excerpt from the posts table
            slug = ""
            excerpt = ""
            if pool:
                try:
                    row = await pool.fetchrow(
                        "SELECT slug, excerpt FROM posts WHERE id::text = $1 LIMIT 1",
                        post_id,
                    )
                    if row:
                        slug = row.get("slug", "")
                        excerpt = row.get("excerpt", "") or ""
                except Exception:
                    pass  # Non-critical — use metadata title only

            excerpt_short = (excerpt[:120] + "...") if len(excerpt) > 120 else excerpt
            url = f"/posts/{slug}" if slug else f"(post id: {post_id})"
            lines.append(
                f"{i}. [{title}] -- {excerpt_short} ({url}) [similarity: {similarity:.2f}]"
            )

        return "\n".join(lines)

    except Exception as e:
        logger.debug("RAG context build failed (non-fatal): %s", e)
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

    # RAG: Embed the topic and find similar published posts via pgvector
    try:
        rag_context = await _build_rag_context(database_service, topic)
        if rag_context:
            research_context = f"{research_context}\n\n{rag_context}" if research_context else rag_context
            logger.info("🔍 RAG context injected: %d chars", len(rag_context))
    except Exception as e:
        logger.warning("RAG context skipped (non-fatal): %s", e)

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
    # Inject recent titles so the LLM avoids repetition
    logger.info("📌 Generating title from content...")
    primary_keyword = tags[0] if tags else topic
    existing_titles = ""
    try:
        if database_service and hasattr(database_service, 'pool') and database_service.pool:
            async with database_service.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT title FROM content_tasks WHERE status = 'published' ORDER BY created_at DESC LIMIT 20"
                )
                if rows:
                    existing_titles = "\n".join(f"- {r['title']}" for r in rows if r['title'])
    except Exception:
        pass  # Non-critical — proceed without diversity check
    title = await _generate_canonical_title(topic, primary_keyword, content_text[:500], existing_titles=existing_titles)
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
    # dynamically based on word count: 1 image per ~500 words, evenly distributed
    if not image_placeholders:
        word_count = len(content_text.split())
        target_images = max(1, word_count // 500)
        headings = list(_re.finditer(r"^## .+$", content_text, _re.MULTILINE))

        if headings:
            # Distribute images evenly across available headings
            # Skip the first heading (too close to featured image), use the rest
            usable_headings = headings[1:] if len(headings) > 1 else headings
            # Pick evenly spaced headings
            step = max(1, len(usable_headings) // target_images)
            selected = usable_headings[::step][:target_images]

            insert_positions = []
            for i, h in enumerate(selected):
                para_end = content_text.find("\n\n", h.end())
                if para_end > 0:
                    # Use heading text as context for image generation
                    heading_text = h.group().lstrip("# ").strip()
                    insert_positions.append((para_end, i + 1, heading_text))

            # Insert in reverse order to preserve positions
            for pos, img_num, heading_ctx in reversed(insert_positions):
                placeholder = f"\n[IMAGE-{img_num}: {heading_ctx} illustration]\n"
                content_text = content_text[:pos] + placeholder + content_text[pos:]

            # Re-scan for the injected placeholders
            image_placeholders = _re.findall(r"\[IMAGE-(\d+)(?::\s*([^\]]*))?\]", content_text)
            if image_placeholders:
                logger.info(
                    "📌 Injected %d image placeholders (%d words, 1 per 500 words)",
                    len(image_placeholders), word_count,
                )

    if not image_placeholders:
        result["stages"]["2c_inline_images_replaced"] = False
        logger.info("⏭️  No [IMAGE-N] placeholders to replace\n")
        return content_text

    logger.info(
        f"🖼️  STAGE 2C: Replacing {len(image_placeholders)} inline image placeholders..."
    )
    used_image_ids = set()  # Avoid duplicate images

    for num, desc in image_placeholders:
        search_query = desc.strip() if desc else topic
        alt_text = desc.strip() if desc else f"{topic} illustration"
        alt_text = alt_text.replace("[", "").replace("]", "").replace("\n", " ")[:120]
        image_replaced = False

        # Strategy 1: SDXL generation (unique, on-topic images)
        try:
            import os as _os
            import tempfile as _tf
            import httpx as _hx2

            sdxl_url = _os.environ.get("SDXL_SERVER_URL", "http://host.docker.internal:9836")
            ollama_url = _os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
            _model = _os.environ.get("DEFAULT_OLLAMA_MODEL", "llama3:latest")
            if _model == "auto":
                _model = "llama3:latest"

            # Generate SDXL prompt via Ollama
            _img_prompt_req = (
                f"Write a Stable Diffusion XL image prompt for a blog illustration about: {search_query}\n"
                f"Article topic: {topic}\n\n"
                "Requirements: photorealistic scene, cinematic lighting, no people, no text, no faces. "
                "Describe a specific scene. 1 sentence only. Output ONLY the prompt."
            )
            async with _hx2.AsyncClient(timeout=30) as _c2:
                _pr = await _c2.post(f"{ollama_url}/api/generate", json={
                    "model": _model, "prompt": _img_prompt_req, "stream": False,
                    "options": {"num_predict": 100, "temperature": 0.8},
                })
                _pr.raise_for_status()
                sdxl_inline_prompt = _pr.json().get("response", "").strip().strip('"')

            if sdxl_inline_prompt and len(sdxl_inline_prompt) > 20:
                # Generate the image
                neg = "text, words, letters, watermark, face, person, hands, blurry, low quality, distorted, ugly, deformed"
                async with _hx2.AsyncClient(timeout=120) as _c3:
                    _ir = await _c3.post(f"{sdxl_url}/generate", json={
                        "prompt": sdxl_inline_prompt, "negative_prompt": neg,
                        "steps": 4, "guidance_scale": 1.0,
                    })
                    if _ir.status_code == 200 and _ir.headers.get("content-type", "").startswith("image/"):
                        # Save and upload to Cloudinary
                        output_dir = _os.path.join(_os.path.expanduser("~"), "Downloads", "glad-labs-generated-images")
                        _os.makedirs(output_dir, exist_ok=True)
                        with _tf.NamedTemporaryFile(suffix=".png", delete=False, dir=output_dir) as _tmp:
                            _tmp.write(_ir.content)
                            tmp_path = _tmp.name

                        # Try Cloudinary upload
                        img_url = tmp_path
                        try:
                            import cloudinary
                            import cloudinary.uploader
                            from services.site_config import site_config as _sc_img
                            cloudinary.config(
                                cloud_name=_sc_img.get("cloudinary_cloud_name"),
                                api_key=_sc_img.get("cloudinary_api_key"),
                                api_secret=_sc_img.get("cloudinary_api_secret"),
                            )
                            import asyncio
                            _up = await asyncio.get_event_loop().run_in_executor(
                                None, lambda: cloudinary.uploader.upload(
                                    tmp_path, folder="generated/inline/", resource_type="image",
                                )
                            )
                            img_url = _up.get("secure_url", tmp_path)
                            _os.remove(tmp_path)
                        except Exception:
                            logger.debug("[IMAGE] Cloudinary upload failed for inline, using local path")

                        if img_url not in used_image_ids:
                            used_image_ids.add(img_url)
                            markdown_img = f"\n\n![{alt_text}]({img_url})\n\n"
                            content_text = _re.sub(
                                rf"\[IMAGE-{num}[^\]]*\]", markdown_img, content_text, count=1
                            )
                            logger.info(f"  ✅ [IMAGE-{num}] → SDXL generated")
                            image_replaced = True
        except Exception as sdxl_err:
            logger.debug(f"  [IMAGE-{num}] SDXL inline failed: {sdxl_err}")

        # Strategy 2: Pexels fallback
        if not image_replaced:
            search_words = search_query.split()[:5]
            short_query = " ".join(search_words)
            keywords = [topic.split()[0]] if topic and topic.strip() else []
            try:
                img = await image_service.search_featured_image(
                    topic=short_query, keywords=keywords
                )
                if img and img.url and img.url not in used_image_ids:
                    used_image_ids.add(img.url)
                    photographer = getattr(img, "photographer", "Pexels")
                    markdown_img = (
                        f"\n\n![{alt_text}]({img.url})\n*Photo by {photographer} on Pexels*\n\n"
                    )
                    content_text = _re.sub(
                        rf"\[IMAGE-{num}[^\]]*\]", markdown_img, content_text, count=1
                    )
                    logger.info(f"  ✅ [IMAGE-{num}] → Pexels image by {photographer}")
                    image_replaced = True
            except Exception as e:
                logger.error(f"  ❌ [IMAGE-{num}] Pexels search failed: {e}")

        if not image_replaced:
            content_text = _re.sub(rf"\[IMAGE-{num}[^\]]*\]", "", content_text, count=1)
            logger.warning(f"  ⚠️ [IMAGE-{num}] — no image source available, removed placeholder")

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

    # Strategy 1: Try SDXL generation with category-specific style from DB
    if image_service.sdxl_available or not image_service.sdxl_initialized:
        try:
            import os
            import tempfile
            from services.site_config import site_config

            # Use LLM-generated image prompt if available, otherwise fall back to generic
            category = result.get("category", "technology")
            negative = site_config.get("image_negative_prompt", "text, words, letters, watermark, face, person, hands, blurry, low quality, distorted, ugly, deformed")

            # Check if the content pipeline already generated an image prompt
            sdxl_prompt = result.get("featured_image_prompt", "")
            if not sdxl_prompt:
                # Style diversity: check recent images and pick least-used style
                _IMAGE_STYLES = [
                    ("photorealistic scene", "cinematic lighting, shallow depth of field, 4k"),
                    ("isometric 3D illustration", "clean vector style, soft shadows, minimal"),
                    ("dark moody editorial photograph", "dramatic side lighting, high contrast, film grain"),
                    ("clean minimal flat design", "soft pastel colors, geometric shapes, modern"),
                    ("aerial drone photograph", "bird's eye view, golden hour lighting, wide angle"),
                    ("macro close-up photograph", "extreme detail, bokeh background, studio lighting"),
                    ("cyberpunk neon cityscape", "rain-slicked streets, neon reflections, atmospheric fog"),
                ]
                import random as _rnd
                # Check what styles were used recently
                try:
                    import asyncpg as _apg
                    _cloud_url = os.environ.get("DATABASE_URL", "")
                    if _cloud_url:
                        _cconn = await _apg.connect(_cloud_url)
                        _recent = await _cconn.fetch("""
                            SELECT metadata->>'image_style' as style
                            FROM posts WHERE status = 'published'
                            AND metadata->>'image_style' IS NOT NULL
                            ORDER BY published_at DESC LIMIT 5
                        """)
                        await _cconn.close()
                        _recent_styles = [r["style"] for r in _recent if r["style"]]
                        # Filter out recently used styles, pick from remaining
                        _available = [s for s in _IMAGE_STYLES if s[0] not in _recent_styles]
                        if not _available:
                            _available = _IMAGE_STYLES  # All used recently, reset
                        _chosen_style, _style_tags = _rnd.choice(_available)
                    else:
                        _chosen_style, _style_tags = _rnd.choice(_IMAGE_STYLES)
                except Exception:
                    _chosen_style, _style_tags = _rnd.choice(_IMAGE_STYLES)

                # Store chosen style in result metadata for tracking
                result["image_style"] = _chosen_style

                # Generate SDXL prompt via Ollama with the chosen style
                try:
                    import httpx as _hx
                    _ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
                    content_excerpt = result.get("content", "")[:300]
                    _img_prompt = (
                        f"Write a Stable Diffusion XL image prompt for a blog featured image.\n"
                        f"Topic: {topic}\n"
                        f"Context: {content_excerpt}\n"
                        f"Style: {_chosen_style}\n\n"
                        f"Requirements: {_style_tags}, no people, no text, no faces, no hands. "
                        "Describe a specific concrete scene. 1-2 sentences only. "
                        "Output ONLY the prompt, nothing else."
                    )
                    async with _hx.AsyncClient(timeout=30) as _c:
                        _r = await _c.post(f"{_ollama_url}/api/generate", json={
                            "model": "llama3:latest", "prompt": _img_prompt, "stream": False,
                            "options": {"num_predict": 150, "temperature": 0.7},
                        })
                        _r.raise_for_status()
                        sdxl_prompt = _r.json().get("response", "").strip().strip('"')
                    logger.info("[IMAGE] Style: %s | SDXL prompt: %s", _chosen_style, sdxl_prompt[:80])
                except Exception as prompt_err:
                    logger.warning("[IMAGE] LLM prompt generation failed, using fallback: %s", prompt_err)
                    sdxl_prompt = f"{_chosen_style} of {topic}, {_style_tags}, no people, no text"
            output_dir = os.path.join(os.path.expanduser("~"), "Downloads", "glad-labs-generated-images")
            os.makedirs(output_dir, exist_ok=True)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir=output_dir) as tmp:
                output_path = tmp.name

            success = await image_service.generate_image(
                prompt=sdxl_prompt,
                output_path=output_path,
                negative_prompt=negative,
                high_quality=True,
            )
            if success and os.path.exists(output_path):
                # Upload to Cloudinary for CDN hosting
                image_url = output_path  # Fallback to local path
                try:
                    import cloudinary
                    import cloudinary.uploader
                    cloudinary.config(
                        cloud_name=site_config.get("cloudinary_cloud_name"),
                        api_key=site_config.get("cloudinary_api_key"),
                        api_secret=site_config.get("cloudinary_api_secret"),
                    )
                    import asyncio
                    upload_result = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: cloudinary.uploader.upload(
                            output_path, folder="generated/", resource_type="image",
                            tags=["featured", category or "default"],
                        ),
                    )
                    image_url = upload_result.get("secure_url", output_path)
                    logger.info("Uploaded to Cloudinary: %s", image_url[:80])
                    os.remove(output_path)  # Clean up local file
                except Exception as upload_err:
                    logger.warning("Cloudinary upload failed (using local): %s", upload_err)

                from dataclasses import dataclass

                @dataclass
                class GeneratedImage:
                    url: str
                    photographer: str
                    source: str

                featured_image = GeneratedImage(
                    url=image_url,
                    photographer="AI Generated (SDXL)",
                    source="sdxl_cloudinary" if "cloudinary" in image_url else "sdxl_local",
                )
                result["featured_image_url"] = image_url
                result["featured_image_photographer"] = "AI Generated (SDXL)"
                result["featured_image_source"] = featured_image.source
                result["stages"]["3_featured_image_found"] = True
                result["stages"]["3_image_source"] = "sdxl"
                logger.info("Featured image generated via SDXL + Cloudinary\n")
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
    """Stage 6: Capture quality evaluation and training data in PostgreSQL.

    This entire stage is non-critical — failures must never crash the pipeline.
    """
    logger.info("🎓 STAGE 6: Capturing training data...")

    try:
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
    except Exception as _qe_err:
        logger.warning("Quality evaluation insert failed (non-critical): %s", _qe_err)

    # Upsert training data — ON CONFLICT in content_db handles duplicates,
    # but wrap in try/except as a safety net for any DB errors.
    try:
        # quality_score is DECIMAL(3,2) i.e. 0.00-9.99; schema expects 0.0-1.0
        normalized_score = min(quality_result.overall_score / 100, 1.0)

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
                "quality_score": normalized_score,
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

    result = {"task_id": task_id, "topic": topic, "status": "pending", "stages": {}, "category": category or "technology"}

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
        await _run_stage_with_timeout(
            _stage_verify_task(database_service, task_id, result),
            "verify_task", task_id,
        )
        audit_log_bg("task_started", "content_router", {"topic": topic[:100]}, task_id=task_id)

        # Stage 2: Generate blog content (critical — fail pipeline on timeout)
        gen_result = await _run_stage_with_timeout(
            _stage_generate_content(
                database_service, task_id, topic, style, tone, target_length, tags, models_by_phase, result
            ),
            "generate_content", task_id,
        )
        if gen_result is None:
            raise RuntimeError(f"Stage 'generate_content' timed out after {STAGE_TIMEOUTS['generate_content']}s — cannot continue without content")
        content_text, model_used, metrics, title = gen_result
        audit_log_bg("generation_complete", "content_router", {
            "model": model_used, "word_count": len(content_text.split()) if content_text else 0,
        }, task_id=task_id)

        # Stage 2B: Quality evaluation (critical — fail pipeline on timeout)
        quality_result = await _run_stage_with_timeout(
            _stage_quality_evaluation(topic, tags, content_text, quality_service, result),
            "quality_evaluation", task_id,
        )
        if quality_result is None:
            raise RuntimeError(f"Stage 'quality_evaluation' timed out after {STAGE_TIMEOUTS['quality_evaluation']}s — cannot continue without QA score")
        audit_log_bg("qa_passed" if quality_result.overall_score >= 50 else "qa_failed", "content_router", {
            "score": quality_result.overall_score, "stage": "early_eval",
        }, task_id=task_id)

        # Stage 2B.1: URL validation (non-blocking — gate-checked via pipeline_stages)
        _pool = database_service.pool if database_service else None
        _url_validation_enabled = await _is_stage_enabled(_pool, "url_validation")
        try:
            if not _url_validation_enabled:
                logger.info("URL validation skipped (disabled in pipeline_stages)")
                result["url_validation"] = {"skipped": True}
                raise Exception("stage_disabled")
            from services.url_validator import get_url_validator
            _url_validator = get_url_validator()
            _extracted_urls = _url_validator.extract_urls(content_text)
            if _extracted_urls:
                _url_results = await _url_validator.validate_urls(_extracted_urls)
                _broken = {u: s for u, s in _url_results.items() if s == "invalid"}
                result["url_validation"] = {
                    "total_urls": len(_extracted_urls),
                    "valid": sum(1 for v in _url_results.values() if v == "valid"),
                    "invalid": len(_broken),
                    "broken_urls": list(_broken.keys()),
                }
                if _broken:
                    logger.warning(
                        "URL validation: %d/%d broken links in task %s: %s",
                        len(_broken), len(_extracted_urls), task_id[:8],
                        ", ".join(list(_broken.keys())[:5]),
                    )
                else:
                    logger.info("URL validation: all %d links valid for task %s", len(_extracted_urls), task_id[:8])
            else:
                result["url_validation"] = {"total_urls": 0, "valid": 0, "invalid": 0, "broken_urls": []}
        except Exception as _url_err:
            logger.warning("URL validation failed (non-critical): %s", _url_err)
            result["url_validation"] = {"error": str(_url_err)}

        # Stage 2C: Replace inline image placeholders (non-critical — skip on timeout)
        _img_result = await _run_stage_with_timeout(
            _stage_replace_inline_images(
                database_service, task_id, topic, content_text, image_service, result
            ),
            "replace_inline_images", task_id,
        )
        if _img_result is not None:
            content_text = _img_result

        # Stage 3: Source featured image (gate-checked, non-critical — skip on timeout)
        if await _is_stage_enabled(_pool, "featured_image"):
            featured_image = await _run_stage_with_timeout(
                _stage_source_featured_image(
                    topic, tags, generate_featured_image, image_service, result
                ),
                "source_featured_image", task_id,
            )
        else:
            featured_image = None
            logger.info("Featured image skipped (disabled in pipeline_stages)")

        # Stage 3.5 + 3.7: Multi-Model QA (gate-checked)
        if not await _is_stage_enabled(_pool, "cross_model_qa"):
            logger.info("Cross-model QA skipped (disabled in pipeline_stages)")
            result["qa_final_score"] = quality_result.overall_score
            result["qa_reviews"] = []
        else:
            from services.multi_model_qa import MultiModelQA
            _qa = MultiModelQA(pool=database_service.pool)
            _qa_result = await _run_stage_with_timeout(
                _qa.review(
                    title=_normalize_text(result.get("seo_title", topic)),
                    content=_normalize_text(content_text),
                    topic=topic,
                ),
                "cross_model_qa", task_id,
            )
            if _qa_result is None:
                logger.warning("Cross-model QA timed out for task %s — using early QA score", task_id[:8])
                result["qa_final_score"] = quality_result.overall_score
                result["qa_reviews"] = [{"reviewer": "timeout", "score": 0, "approved": True,
                                         "feedback": "QA stage timed out — skipped", "provider": "none"}]
            else:
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
                        logger.info("QA cost logged: $%.4f (%s/%s)", _qa_result.cost_log["cost_usd"], _qa_result.cost_log["provider"], _qa_result.cost_log["model"])
                        audit_log_bg("cost_logged", "content_router", {
                            "cost_usd": _qa_result.cost_log.get("cost_usd"),
                            "provider": _qa_result.cost_log.get("provider"),
                            "model": _qa_result.cost_log.get("model"),
                            "phase": "multi_model_qa",
                        }, task_id=task_id)
                    except Exception as e:
                        logger.warning("QA cost logging failed (non-critical): %s", e)

                if not _qa_result.approved:
                    logger.warning(
                        "[MULTI_QA] Content rejected for task %s:\n%s",
                        task_id[:8], _qa_result.summary,
                    )
                    audit_log_bg("qa_failed", "content_router", {
                        "score": _qa_result.final_score, "stage": "multi_model_qa",
                        "summary": _qa_result.summary[:300],
                    }, task_id=task_id, severity="warning")
                    result["status"] = "rejected"
                    await database_service.update_task(task_id, {
                        "status": "rejected",
                        "error_message": f"Multi-model QA rejected (score: {_qa_result.final_score:.0f}): "
                            + (_qa_result.reviews[-1].feedback[:200] if _qa_result.reviews else "No feedback"),
                    })
                    return result
                audit_log_bg("qa_passed", "content_router", {
                    "score": _qa_result.final_score, "stage": "multi_model_qa",
                }, task_id=task_id)
                logger.info("[MULTI_QA] Content approved for task %s: %s", task_id[:8], _qa_result.summary.split("\\n")[0])

        # Stage 4: Generate SEO metadata (non-critical — use fallbacks on timeout)
        content_generator = get_content_generator()
        seo_result = await _run_stage_with_timeout(
            _stage_generate_seo_metadata(
                topic, tags, content_text, content_generator, result
            ),
            "generate_seo_metadata", task_id,
        )
        if seo_result is not None:
            seo_title, seo_description, seo_keywords = seo_result
        else:
            logger.warning("SEO metadata timed out for task %s — using topic as fallback", task_id[:8])
            seo_title, seo_description, seo_keywords = topic[:60], topic[:160], tags or []

        # Stage 5/6: Capture training data (non-critical — skip on timeout)
        await _run_stage_with_timeout(
            _stage_capture_training_data(
                database_service, task_id, topic, style, tone, target_length, tags,
                content_text, quality_result, featured_image, result
            ),
            "capture_training_data", task_id,
        )

        # Final: Update task with status and metadata
        await _run_stage_with_timeout(
            _stage_finalize_task(
                database_service, task_id, topic, style, tone, content_text,
                quality_result, seo_title, seo_description, seo_keywords,
                category, target_audience, result
            ),
            "finalize_task", task_id,
        )

        audit_log_bg("pipeline_complete", "content_router", {
            "quality_score": quality_result.overall_score,
            "qa_final_score": result.get("qa_final_score"),
            "status": result["status"],
        }, task_id=task_id)

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
        audit_log_bg("error", "content_router", {
            "error": str(e)[:500], "stages_completed": list(result.get("stages", {}).keys()),
        }, task_id=task_id, severity="error")

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
    topic: str, database_service: DatabaseService, requested_category: Optional[str] = None
) -> Optional[str]:
    """
    Select appropriate category. Priority:
    1. Requested category from task metadata (if it exists in DB)
    2. Keyword matching against topic
    3. Default to "technology"

    Returns category UUID
    """
    # Priority 1: Use the requested category if valid
    if requested_category:
        try:
            async with database_service.pool.acquire() as conn:
                cat_id = await conn.fetchval(
                    "SELECT id FROM categories WHERE slug = $1 OR name ILIKE $1", requested_category
                )
            if cat_id:
                return cat_id
        except Exception:
            pass

    topic_lower = topic.lower()

    category_keywords = {
        "technology": ["ai", "tech", "software", "cloud", "machine learning", "data", "coding", "python", "javascript", "docker", "kubernetes", "api", "database"],
        "business": ["business", "strategy", "management", "entrepreneur", "growth", "revenue", "marketing", "saas"],
        "startup": ["startup", "founder", "bootstrapper", "mvp", "launch", "validate", "solo founder", "side project"],
        "security": ["security", "hack", "owasp", "zero trust", "vulnerability", "auth", "encryption", "secrets"],
        "engineering": ["engineering", "architecture", "monorepo", "git", "technical debt", "migration", "ci/cd", "testing"],
        "insights": ["trend", "landscape", "state of", "productivity", "remote work", "future of", "prediction"],
    }

    # Priority 2: Keyword matching
    matched_category = "technology"  # Default
    best_score = 0
    for category, keywords in category_keywords.items():
        score = sum(1 for kw in keywords if kw in topic_lower)
        if score > best_score:
            best_score = score
            matched_category = category

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
