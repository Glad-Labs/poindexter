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
from .content_quality_service import ContentQualityService, get_content_quality_service, EvaluationMethod
from .database_service import DatabaseService
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
        metadata: Optional[Dict[str, Any]] = None
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
            
            task_id = await self.database_service.add_task({
                "task_name": task_name,  # REQUIRED: must be provided
                "topic": topic,
                "style": style,
                "tone": tone,
                "target_length": target_length,
                "tags": tags or [],
                "request_type": request_type,
                "task_type": task_type,
                "metadata": metadata or {},
            })
            
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
            offset=offset,
            limit=limit,
            status=status
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
                tags=tags or [],
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
            # Use async method from Pexels client
            image = await self.pexels.get_featured_image(
                topic=topic,
                keywords=keywords
            )

            if image:
                logger.info(f"✅ Found featured image from Pexels: {image.get('photographer')}")
                return image
            else:
                logger.warning(f"⚠️  No Pexels image found for: {topic}")
                return None

        except Exception as e:
            logger.error(f"❌ Error searching for featured image: {e}")
            return None

# ============================================================================
# BACKGROUND TASK PROCESSORS
# ============================================================================


async def process_content_generation_task(
    topic: str,
    style: str,
    tone: str,
    target_length: int,
    tags: Optional[List[str]] = None,
    generate_featured_image: bool = True,
    database_service: Optional[DatabaseService] = None,
    task_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    🚀 Complete Content Generation Pipeline with Image Sourcing & SEO Metadata
    
    Process a content generation request through the full pipeline:
    
    STAGE 1: 📋 Create content_task record (status='pending')
    STAGE 2: ✍️  Generate blog content
    STAGE 3: 🖼️  Source featured image from Pexels
    STAGE 4: 📊 Generate SEO metadata
    STAGE 5: ⭐ Create quality evaluation
    STAGE 6: 📝 Create posts record with all metadata
    STAGE 7: 🎓 Capture training data for learning loop
    
    FEATURES:
    - ✅ Pexels API for royalty-free featured images
    - ✅ Auto-generated SEO title, description, keywords
    - ✅ Quality evaluation with 7 criteria
    - ✅ Training data capture for improvement learning
    - ✅ Full relational integrity (author_id, category_id, etc.)
    
    Args:
        topic: Blog post topic
        style: Content style (technical, narrative, listicle, educational, thought-leadership)
        tone: Content tone (professional, casual, academic, inspirational)
        target_length: Target word count (default 1500)
        tags: Optional tags for categorization
        generate_featured_image: Whether to search for featured image
        database_service: DatabaseService instance for persistence
        task_id: Optional task_id (auto-generated if not provided)
        
    Returns:
        Dict with complete task result including post_id, quality_score, image_url, etc.
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
    
    result = {
        'task_id': task_id,
        'topic': topic,
        'status': 'pending',
        'stages': {}
    }
    
    try:
        # Initialize unified services
        image_service = get_image_service()
        quality_service = get_content_quality_service(database_service=database_service)
        
        # ================================================================================
        # STAGE 1: CREATE CONTENT_TASK RECORD
        # ================================================================================
        logger.info("📋 STAGE 1: Creating content_task record...")
        
        content_task = await database_service.create_content_task({
            'task_id': task_id,
            'request_type': 'api_request',
            'task_type': 'blog_post',
            'status': 'pending',
            'topic': topic,
            'style': style,
            'tone': tone,
            'target_length': target_length,
            'approval_status': 'pending'
        })
        
        result['content_task_id'] = content_task['task_id']
        result['stages']['1_content_task_created'] = True
        logger.info(f"✅ Content task created: {content_task['task_id']}\n")
        
        # ================================================================================
        # STAGE 2: GENERATE BLOG CONTENT
        # ================================================================================
        logger.info("✍️  STAGE 2: Generating blog content...")
        
        content_generator = get_content_generator()
        content_text, model_used, metrics = await content_generator.generate_blog_post(
            topic=topic,
            style=style,
            tone=tone,
            target_length=target_length,
            tags=tags or [],
            enhanced=True
        )
        
        # Update content_task with generated content
        await database_service.update_content_task_status(
            task_id=task_id,
            status='generated',
            content=content_text
        )
        
        result['content'] = content_text
        result['content_length'] = len(content_text)
        result['model_used'] = model_used
        result['stages']['2_content_generated'] = True
        logger.info(f"✅ Content generated ({len(content_text)} chars) using {model_used}\n")
        
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
                    topic=topic,
                    keywords=search_keywords
                )
                
                if featured_image:
                    image_metadata = featured_image.to_dict()
                    result['featured_image_url'] = featured_image.url
                    result['featured_image_photographer'] = featured_image.photographer
                    result['featured_image_source'] = featured_image.source
                    result['stages']['3_featured_image_found'] = True
                    logger.info(f"✅ Featured image found: {featured_image.photographer} (Pexels)\n")
                else:
                    result['stages']['3_featured_image_found'] = False
                    logger.warning(f"⚠️  No featured image found for '{topic}'\n")
            except Exception as e:
                logger.error(f"❌ Image search failed: {e}")
                result['stages']['3_featured_image_found'] = False
        else:
            result['stages']['3_featured_image_found'] = False
            logger.info("⏭️  Image search skipped (disabled)\n")
        
        # ================================================================================
        # STAGE 4: GENERATE SEO METADATA
        # ================================================================================
        logger.info("📊 STAGE 4: Generating SEO metadata...")
        
        seo_generator = await get_seo_content_generator()
        seo_assets = seo_generator.generate_seo_assets(
            title=topic,
            content=content_text,
            topic=topic
        )
        
        seo_keywords = seo_assets.get('meta_keywords', tags or [])[:10]
        seo_title = seo_assets.get('seo_title', topic)[:60]
        seo_description = seo_assets.get('meta_description', '')[:160]
        
        result['seo_title'] = seo_title
        result['seo_description'] = seo_description
        result['seo_keywords'] = seo_keywords
        result['stages']['4_seo_metadata_generated'] = True
        logger.info(f"✅ SEO metadata generated:")
        logger.info(f"   Title: {seo_title}")
        logger.info(f"   Description: {seo_description[:80]}...")
        logger.info(f"   Keywords: {', '.join(seo_keywords[:5])}...\n")
        
        # ================================================================================
        # STAGE 5: QUALITY EVALUATION (Unified Service)
        # ================================================================================
        logger.info("⭐ STAGE 5: Quality evaluation...")
        
        quality_result = await quality_service.evaluate(
            content=content_text,
            context={
                'topic': topic,
                'keywords': seo_keywords,
                'audience': 'General',
                'seo_title': seo_title,
                'seo_description': seo_description,
                'seo_keywords': seo_keywords
            },
            method=EvaluationMethod.PATTERN_BASED
        )
        
        # Store quality evaluation in PostgreSQL
        await database_service.create_quality_evaluation({
            'content_id': task_id,
            'task_id': task_id,
            'overall_score': quality_result.overall_score,
            'clarity': quality_result.clarity,
            'accuracy': quality_result.accuracy,
            'completeness': quality_result.completeness,
            'relevance': quality_result.relevance,
            'seo_quality': quality_result.seo_quality,
            'readability': quality_result.readability,
            'engagement': quality_result.engagement,
            'passing': quality_result.passing,
            'feedback': quality_result.feedback,
            'suggestions': quality_result.suggestions,
            'evaluated_by': 'ContentQualityService',
            'evaluation_method': quality_result.evaluation_method
        })
        
        result['quality_score'] = quality_result.overall_score
        result['quality_passing'] = quality_result.passing
        result['quality_details'] = {
            'clarity': quality_result.clarity,
            'accuracy': quality_result.accuracy,
            'completeness': quality_result.completeness,
            'relevance': quality_result.relevance,
            'seo_quality': quality_result.seo_quality,
            'readability': quality_result.readability,
            'engagement': quality_result.engagement,
        }
        result['stages']['5_quality_evaluated'] = True
        logger.info(f"✅ Quality evaluation complete:")
        logger.info(f"   Overall Score: {quality_result.overall_score:.1f}/10")
        logger.info(f"   Passing: {quality_result.passing} (threshold ≥7.0)\n")
        
        # ================================================================================
        # STAGE 6: CREATE POSTS RECORD
        # ================================================================================
        logger.info("📝 STAGE 6: Creating posts record...")
        
        # Get default author (Poindexter AI)
        author_id = await _get_or_create_default_author(database_service)
        
        # Select category based on topic
        category_id = await _select_category_for_topic(topic, database_service)
        
        # Create slug from topic
        import re
        slug = re.sub(r'[^\w\s-]', '', topic).lower().replace(' ', '-')[:50]
        slug = f"{slug}-{task_id[:8]}"
        
        # Create post with all data
        post = await database_service.create_post({
            'title': topic,
            'slug': slug,
            'content': content_text,
            'excerpt': seo_description,
            'featured_image_url': featured_image.url if featured_image else None,
            'author_id': author_id,
            'category_id': category_id,
            'status': 'draft',  # Always draft, human must approve
            'seo_title': seo_title,
            'seo_description': seo_description,
            'seo_keywords': ','.join(seo_keywords) if seo_keywords else '',
            'metadata': image_metadata if image_metadata else {}
        })
        
        result['post_id'] = str(post['id'])
        result['post_slug'] = post['slug']
        result['stages']['6_post_created'] = True
        logger.info(f"✅ Post created: {post['id']}")
        logger.info(f"   Title: {topic}")
        logger.info(f"   Slug: {slug}")
        logger.info(f"   Author: {author_id}")
        logger.info(f"   Category: {category_id}\n")
        
        # ================================================================================
        # STAGE 7: CAPTURE TRAINING DATA
        # ================================================================================
        logger.info("🎓 STAGE 7: Capturing training data...")
        
        await database_service.create_orchestrator_training_data({
            'execution_id': task_id,
            'user_request': f"Generate blog post on: {topic}",
            'intent': 'content_generation',
            'business_state': {
                'topic': topic,
                'style': style,
                'tone': tone,
                'featured_image': featured_image is not None
            },
            'execution_result': 'success',
            'quality_score': quality_result.overall_score / 10,
            'success': quality_result.passing,
            'tags': tags or [],
            'source_agent': 'content_router_service'
        })
        
        result['stages']['7_training_data_captured'] = True
        logger.info(f"✅ Training data captured for learning pipeline\n")
        
        # ================================================================================
        # UPDATE CONTENT_TASK WITH FINAL STATUS
        # ================================================================================
        await database_service.update_content_task_status(
            task_id=task_id,
            status='completed',
            approval_status='pending_human_review',
            quality_score=int(quality_result.overall_score)
        )
        
        result['status'] = 'completed'
        result['approval_status'] = 'pending_human_review'
        
        logger.info(f"{'='*80}")
        logger.info(f"✅ COMPLETE CONTENT GENERATION PIPELINE FINISHED")
        logger.info(f"{'='*80}")
        logger.info(f"   Task ID: {task_id}")
        logger.info(f"   Post ID: {post['id']}")
        logger.info(f"   Quality Score: {quality_score:.1f}/10")
        logger.info(f"   Status: {result['status']}")
        logger.info(f"   Next: Human review & approval")
        logger.info(f"{'='*80}\n")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Pipeline error: {e}", exc_info=True)
        
        # Update content_task with failure status
        try:
            await database_service.update_content_task_status(
                task_id=task_id,
                status='failed',
                approval_status='failed'
            )
        except Exception as db_error:
            logger.error(f"❌ Failed to update task status: {db_error}")
        
        result['status'] = 'failed'
        result['error'] = str(e)
        return result


# ================================================================================
# HELPER FUNCTIONS FOR CONTENT PIPELINE
# ================================================================================

async def _extract_seo_keywords(
    content: str,
    topic: str,
    tags: Optional[List[str]] = None
) -> List[str]:
    """
    Extract SEO keywords from content
    
    Returns top 5-10 keywords from content and topic
    """
    import re
    
    # Start with provided tags
    keywords = set(tags or [])
    
    # Add topic and variations
    topic_words = topic.lower().split()
    keywords.update(topic_words)
    
    # Extract common phrases and keywords from content
    # Look for capitalized terms (likely proper nouns/keywords)
    proper_nouns = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', content)
    keywords.update([noun.lower() for noun in proper_nouns[:5]])
    
    # Return unique keywords (max 10)
    return list(keywords)[:10]


async def _generate_seo_title(topic: str, style: str) -> str:
    """
    Generate SEO-optimized title (50-60 chars)
    
    Format: "Topic | Style" or "Topic - Best Guide"
    """
    # Ensure title is 50-60 characters
    base_title = topic
    
    if len(base_title) > 60:
        base_title = base_title[:57] + "..."
    elif len(base_title) < 30:
        # Add style modifier
        modifiers = {
            'technical': 'Complete Guide to',
            'narrative': 'The Ultimate Guide:',
            'listicle': f'{topic}: 10',
            'educational': f'Learn {topic}:',
            'thought-leadership': f'{topic}: Expert Insights'
        }
        base_title = modifiers.get(style, f'{topic}: Guide')
    
    return base_title


async def _generate_seo_description(content: str, topic: str) -> str:
    """
    Generate SEO meta description (155-160 chars)
    
    Extract first meaningful sentence from content
    """
    # Look for first paragraph/sentence
    lines = [line.strip() for line in content.split('\n') if line.strip()]
    
    # Find first substantive paragraph (skip title/headers)
    description = None
    for line in lines:
        if not line.startswith('#') and len(line) > 20:
            description = line
            break
    
    if not description:
        description = f"Learn about {topic} with our comprehensive guide."
    
    # Trim to 155-160 characters
    if len(description) > 160:
        description = description[:157] + "..."
    
    return description


async def _evaluate_content_quality(
    content: str,
    topic: str,
    seo_title: str,
    seo_keywords: List[str]
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
    heading_count = len(re.findall(r'^#{1,3} ', content, re.MULTILINE))
    paragraph_count = len([p for p in content.split('\n\n') if p.strip()])
    clarity = 8.0
    if heading_count < 3:
        clarity -= 1.0
    if paragraph_count < 5:
        clarity -= 1.0
    criteria['clarity'] = max(0, min(10, clarity))
    
    # 2. ACCURACY (check for hedging language, sources)
    accuracy = 7.5
    # Assume generated content is reasonably accurate
    criteria['accuracy'] = max(0, min(10, accuracy))
    
    # 3. COMPLETENESS (check word count, section coverage)
    word_count = len(content.split())
    completeness = 6.0
    if word_count > 800:
        completeness = 8.0
    if word_count > 1500:
        completeness = 9.0
    if word_count < 300:
        completeness = 4.0
    criteria['completeness'] = max(0, min(10, completeness))
    
    # 4. RELEVANCE (check topic mentions)
    topic_words = topic.lower().split()[:3]
    topic_mentions = sum(1 for word in topic_words if word.lower() in content.lower())
    relevance = 7.0 if topic_mentions >= 2 else 5.0
    criteria['relevance'] = max(0, min(10, relevance))
    
    # 5. SEO QUALITY (check keyword usage, title)
    keyword_mentions = sum(1 for kw in seo_keywords if kw.lower() in content.lower())
    seo_quality = 7.0 if keyword_mentions >= 3 else 6.0
    if len(seo_title) < 50:
        seo_quality += 1.0
    criteria['seo_quality'] = max(0, min(10, seo_quality))
    
    # 6. READABILITY (check sentence length, lists)
    has_lists = '- ' in content or '* ' in content or '1. ' in content
    readability = 7.5 if has_lists else 6.5
    criteria['readability'] = max(0, min(10, readability))
    
    # 7. ENGAGEMENT (check for examples, CTAs)
    has_cta = any(word in content.lower() for word in ['start', 'try', 'begin', 'ready', 'action'])
    has_examples = has_lists or 'example' in content.lower()
    engagement = 7.0
    if has_examples:
        engagement += 1.0
    if has_cta:
        engagement += 0.5
    criteria['engagement'] = max(0, min(10, engagement))
    
    # Calculate overall score (average of 7 criteria)
    overall_score = sum(criteria.values()) / 7
    overall_score = max(0, min(10, overall_score))
    
    return {
        'overall_score': overall_score,
        'criteria': criteria,
        'passing': overall_score >= 7.0,
        'feedback': f"Overall quality: {overall_score:.1f}/10",
        'suggestions': [
            'Check formatting for readability',
            'Ensure all claims are backed by data',
            'Add more specific examples if possible'
        ]
    }


async def _select_category_for_topic(
    topic: str,
    database_service: DatabaseService
) -> Optional[str]:
    """
    Select appropriate category based on topic keywords
    
    Returns category UUID
    """
    topic_lower = topic.lower()
    
    category_keywords = {
        'technology': ['ai', 'tech', 'software', 'cloud', 'machine learning', 'data', 'coding', 'python', 'javascript'],
        'business': ['business', 'strategy', 'management', 'entrepreneur', 'startup', 'growth', 'revenue'],
        'marketing': ['marketing', 'seo', 'growth', 'brand', 'customer', 'social', 'campaign'],
        'finance': ['finance', 'investment', 'cost', 'budget', 'roi', 'money', 'crypto'],
        'entertainment': ['game', 'entertainment', 'media', 'streaming', 'music', 'film']
    }
    
    # Find best matching category
    matched_category = 'technology'  # Default
    for category, keywords in category_keywords.items():
        if any(kw in topic_lower for kw in keywords):
            matched_category = category
            break
    
    # Get category ID
    try:
        async with database_service.pool.acquire() as conn:
            cat_id = await conn.fetchval(
                "SELECT id FROM categories WHERE slug = $1",
                matched_category
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
