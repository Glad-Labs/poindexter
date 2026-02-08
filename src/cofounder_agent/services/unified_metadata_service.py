"""
Unified Metadata Service

Consolidates all metadata generation functionality from:
- llm_metadata_service.py (LLM-powered smart extraction)
- seo_content_generator.py (Simple/fast extraction)
- content_router_service.py (duplicate implementations)

Single source of truth for all metadata operations with:
- LLM-intelligent fallbacks for all operations
- Simple/fast local extraction first
- Batch processing for efficiency
- Comprehensive metadata generation
- Social media optimization
- Featured image prompt generation
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .model_consolidation_service import get_model_consolidation_service
from .prompt_manager import get_prompt_manager
from .provider_checker import ProviderChecker

logger = logging.getLogger(__name__)

# Legacy provider checks are kept for backward compatibility during migration
# but new code should use unified model_router and prompt_manager

# Check for Anthropic availability and API key
try:
    from anthropic import Anthropic

    ANTHROPIC_AVAILABLE = ProviderChecker.is_anthropic_available()
    if ANTHROPIC_AVAILABLE:
        anthropic_client = Anthropic(api_key=ProviderChecker.get_anthropic_api_key())
    else:
        anthropic_client = None
        logger.debug("⚠️  ANTHROPIC_API_KEY not set in environment")
except ImportError:
    ANTHROPIC_AVAILABLE = False
    anthropic_client = None
    logger.debug("⚠️  Anthropic package not installed")

# Check for OpenAI availability and API key
try:
    import openai

    OPENAI_AVAILABLE = ProviderChecker.is_openai_available()
    if OPENAI_AVAILABLE:
        openai.api_key = ProviderChecker.get_openai_api_key()
    else:
        logger.debug("⚠️  OPENAI_API_KEY not set in environment")
except ImportError:
    OPENAI_AVAILABLE = False
    logger.debug("⚠️  OpenAI package not installed")

# Check for Google Gemini availability and API key
try:
    # Try new SDK first, fall back to old one
    use_new_sdk = False
    try:
        import google.genai as genai
        use_new_sdk = True
    except ImportError:
        import google.generativeai as genai

    GOOGLE_AVAILABLE = ProviderChecker.is_gemini_available()
    if GOOGLE_AVAILABLE:
        # Configure API key based on SDK version
        if use_new_sdk:
            genai.api_key = ProviderChecker.get_gemini_api_key()
        else:
            genai.configure(api_key=ProviderChecker.get_gemini_api_key())
    else:
        logger.debug("⚠️  GOOGLE_API_KEY not set in environment")
except ImportError:
    GOOGLE_AVAILABLE = False
    logger.debug("⚠️  Google genai package not installed")


@dataclass
class UnifiedMetadata:
    """Complete metadata for content - single data structure"""

    # Core metadata
    title: str = ""
    excerpt: str = ""
    slug: str = ""

    # SEO metadata
    seo_title: str = ""
    seo_description: str = ""
    seo_keywords: List[str] = field(default_factory=list)

    # Organization
    category_id: Optional[str] = None
    category_name: str = ""
    tag_ids: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    # Author
    author_id: str = "14c9cad6-57ca-474a-8a6d-fab897388ea8"  # Default: Poindexter AI

    # Featured image
    featured_image_prompt: str = ""
    featured_image_url: Optional[str] = None
    featured_image_alt_text: str = ""

    # Social media
    og_title: str = ""
    og_description: str = ""
    og_image: Optional[str] = None
    twitter_title: str = ""
    twitter_description: str = ""
    twitter_card: str = "summary_large_image"

    # Structured data
    json_ld_schema: Optional[Dict[str, Any]] = None

    # Analytics
    word_count: int = 0
    reading_time_minutes: int = 0


class UnifiedMetadataService:
    """Single source of truth for all metadata operations"""

    def __init__(self, model: str = "auto"):
        """
        Initialize unified metadata service

        Args:
            model: "auto" (use best available), "claude-3-haiku", "gpt-4", etc.
        """
        self.model = model
        self.llm_available = ANTHROPIC_AVAILABLE or OPENAI_AVAILABLE or GOOGLE_AVAILABLE

        if not self.llm_available:
            logger.warning(
                "⚠️  No LLM available (Anthropic, OpenAI, or Google Gemini). Using fallback strategies only."
            )

    # ========================================================================
    # BATCH METADATA GENERATION (Most Efficient)
    # ========================================================================

    async def generate_all_metadata(
        self,
        content: str,
        topic: Optional[str] = None,
        title: Optional[str] = None,
        excerpt: Optional[str] = None,
        featured_image_url: Optional[str] = None,
        available_categories: Optional[List[Dict[str, str]]] = None,
        available_tags: Optional[List[Dict[str, str]]] = None,
        author_id: Optional[str] = None,
    ) -> UnifiedMetadata:
        """
        Generate ALL metadata in one operation (batch processing)

        This is the primary entry point - use this instead of individual methods

        Args:
            content: The actual content
            topic: Topic/subject (used as fallback for title)
            title: Pre-extracted title (optional)
            excerpt: Pre-generated excerpt (optional)
            featured_image_url: If already generated
            available_categories: List of categories to match against
            available_tags: List of tags to match against
            author_id: Override default author

        Returns:
            UnifiedMetadata with all fields populated
        """
        logger.info("🔄 Generating complete metadata batch...")

        metadata = UnifiedMetadata(author_id=author_id or "14c9cad6-57ca-474a-8a6d-fab897388ea8")

        # 1. Extract/generate title
        metadata.title = await self.extract_title(content, topic, title)

        # 2. Generate excerpt
        metadata.excerpt = await self.generate_excerpt(content, excerpt)

        # 3. Generate slug
        metadata.slug = self.generate_slug(metadata.title)

        # 4. Generate SEO metadata
        seo = await self.generate_seo_metadata(metadata.title, content)
        metadata.seo_title = seo["seo_title"]
        metadata.seo_description = seo["seo_description"]
        metadata.seo_keywords = seo["seo_keywords"]

        # 5. Match category
        if available_categories:
            category = await self.match_category(content, available_categories, metadata.title)
            if category:
                metadata.category_id = category.get("id")
                metadata.category_name = category.get("name", "")

        # 6. Extract tags
        if available_tags:
            tag_ids = await self.extract_tags(content, available_tags, metadata.title)
            metadata.tag_ids = tag_ids
            # Get tag names for display
            metadata.tags = [
                next((t.get("name") for t in available_tags if t.get("id") == tid), "")
                for tid in tag_ids
            ]

        # 7. Generate featured image prompt
        metadata.featured_image_prompt = self.generate_featured_image_prompt(
            metadata.title, content, metadata.category_name
        )
        metadata.featured_image_url = featured_image_url
        metadata.featured_image_alt_text = f"Featured image for {metadata.title}"

        # 8. Generate social metadata
        social = self.generate_social_metadata(metadata.title, metadata.excerpt, featured_image_url)
        metadata.og_title = social["og_title"]
        metadata.og_description = social["og_description"]
        metadata.og_image = social["og_image"]
        metadata.twitter_title = social["twitter_title"]
        metadata.twitter_description = social["twitter_description"]
        metadata.twitter_card = social["twitter_card"]

        # 9. Generate JSON-LD schema
        metadata.json_ld_schema = self.generate_json_ld_schema(metadata)

        # 10. Calculate reading time and word count
        metadata.word_count = len(content.split())
        metadata.reading_time_minutes = self.calculate_reading_time(content)

        logger.info(
            f"✅ Metadata generation complete: title={metadata.title[:50]}, "
            f"category={metadata.category_name}, tags={len(metadata.tag_ids)}"
        )

        return metadata

    # ========================================================================
    # TITLE EXTRACTION
    # ========================================================================

    async def extract_title(
        self, content: str, topic: Optional[str] = None, stored_title: Optional[str] = None
    ) -> str:
        """
        Extract or generate title from content (5-level fallback)

        Strategy:
        1. Use stored title if not "Untitled"
        2. Use topic if provided and valid
        3. Extract first meaningful line from content
        4. Use LLM to generate title
        5. Fallback to date-based title
        """

        # Strategy 1: Check stored title
        if stored_title and stored_title.lower() != "untitled":
            logger.debug("Using stored title: %s", stored_title[:50])
            return stored_title[:100]

        # Strategy 2: Use topic
        if topic and topic.lower() != "untitled" and len(topic) > 5:
            logger.debug("Using topic as title: %s", topic[:50])
            return topic[:100]

        # Strategy 3: Extract from content
        title = self._extract_first_meaningful_line(content)
        if title:
            logger.debug("Extracted title from content: %s", title[:50])
            return title

        # Strategy 4: Use LLM to generate
        if self.llm_available and content:
            try:
                title = await self._llm_generate_title(content)
                if title:
                    logger.info("LLM generated title: %s", title[:50])
                    return title
            except Exception as e:
                logger.warning("LLM title generation failed: %s", e)

        # Strategy 5: Fallback to date
        title = f"Blog Post - {datetime.now().strftime('%B %d, %Y')}"
        logger.debug("Using date-based fallback: %s", title)
        return title

    def _extract_first_meaningful_line(self, content: str) -> Optional[str]:
        """Extract first meaningful non-empty line from content"""
        if not content:
            return None

        lines = content.split("\n")
        for line in lines:
            cleaned = line.strip()
            # Skip empty lines, very short lines, markdown formatting
            if (
                cleaned
                and len(cleaned) > 10
                and not cleaned.startswith("-")
                and not cleaned.startswith("*")
            ):
                if len(cleaned) < 150 and "," not in cleaned[:50]:
                    return cleaned[:100]

        return None

    async def _llm_generate_title(self, content: str) -> Optional[str]:
        """Use unified prompt manager and model consolidation service to generate professional title from content"""
        try:
            pm = get_prompt_manager()
            service = get_model_consolidation_service()

            prompt = pm.get_prompt(
                "seo.generate_title",
                content=content[:500],
                max_length=100,
            )

            result = await service.generate(
                prompt=prompt,
                temperature=0.7,
                # model consolidation service handles model selection automatically
            )
            return result.text[:100] if result and result.text else None

        except Exception as e:
            logger.warning("LLM title generation error: %s", e)
            return None

    # ========================================================================
    # EXCERPT GENERATION
    # ========================================================================

    async def generate_excerpt(
        self, content: str, stored_excerpt: Optional[str] = None, max_length: int = 200
    ) -> str:
        """
        Generate excerpt for social media/previews (3-level strategy)

        Strategy:
        1. Use stored excerpt if good
        2. Extract first paragraph
        3. Use LLM for professional summary
        """

        # Strategy 1: Use stored excerpt
        if stored_excerpt and len(stored_excerpt) > 20:
            logger.debug("Using stored excerpt")
            return stored_excerpt[:max_length]

        # Strategy 2: Extract first paragraph
        excerpt = self._extract_first_paragraph(content, max_length)
        if excerpt:
            logger.debug("Extracted excerpt from content")
            return excerpt

        # Strategy 3: Use LLM
        if self.llm_available and content:
            try:
                excerpt = await self._llm_generate_excerpt(content, max_length)
                if excerpt:
                    logger.info("LLM generated excerpt")
                    return excerpt
            except Exception as e:
                logger.warning("LLM excerpt generation failed: %s", e)

        # Fallback: Use content start
        excerpt = content[:max_length]
        if len(content) > max_length:
            excerpt += "..."
        logger.debug("Using content start as fallback")
        return excerpt

    def _extract_first_paragraph(self, content: str, max_length: int) -> Optional[str]:
        """Extract first meaningful paragraph from content"""
        if not content:
            return None

        paragraphs = content.split("\n\n")
        for para in paragraphs:
            para_clean = para.strip()
            if para_clean and len(para_clean) > 30 and not para_clean.startswith("#"):
                excerpt = para_clean[:max_length]
                if len(para_clean) > max_length:
                    excerpt += "..."
                # Normalize whitespace
                excerpt = excerpt.replace("\n", " ")
                excerpt = " ".join(excerpt.split())
                return excerpt

        return None

    async def _llm_generate_excerpt(self, content: str, max_length: int) -> Optional[str]:
        """Use LLM to generate professional excerpt"""
        if not self.llm_available:
            return None

        prompt = f"""Generate a concise, engaging summary (max {max_length} characters) for social media/blog preview.

Content:
{content[:800]}

Generate ONLY the excerpt, nothing else. No quotes, no explanation."""

        try:
            if ANTHROPIC_AVAILABLE:
                response = anthropic_client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=max_length,
                    messages=[{"role": "user", "content": prompt}],
                )
                excerpt = response.content[0].text.strip()
                return excerpt[:max_length] if excerpt else None

            if OPENAI_AVAILABLE:
                import openai as openai_module

                response = openai_module.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_length,
                    temperature=0.7,
                )
                excerpt = response.choices[0].message.content.strip()
                return excerpt[:max_length] if excerpt else None

        except Exception as e:
            logger.warning("LLM excerpt generation error: %s", e)
            return None

    # ========================================================================
    # SEO METADATA GENERATION
    # ========================================================================

    async def generate_seo_metadata(
        self, title: str, content: str, stored_seo: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Generate SEO metadata (title, description, keywords)

        Returns:
            {"seo_title": "...", "seo_description": "...", "seo_keywords": [...]}
        """
        result = {}

        # SEO Title (60 chars optimal for search results)
        if stored_seo and stored_seo.get("seo_title"):
            result["seo_title"] = stored_seo["seo_title"]
        else:
            result["seo_title"] = title[:60] if len(title) > 60 else title

        # SEO Description (155-160 chars optimal)
        if stored_seo and stored_seo.get("seo_description"):
            result["seo_description"] = stored_seo["seo_description"]
        elif self.llm_available and content:
            try:
                desc = await self._llm_generate_seo_description(title, content)
                if desc:
                    result["seo_description"] = desc
                else:
                    result["seo_description"] = content[:155]
            except Exception as e:
                logger.warning("LLM SEO description failed: %s", e)
                result["seo_description"] = content[:155]
        else:
            result["seo_description"] = content[:155]

        # SEO Keywords (5-7 keywords) - convert list to comma-separated string
        keywords_list = None
        if stored_seo and stored_seo.get("seo_keywords"):
            keywords_list = stored_seo["seo_keywords"]
            if isinstance(keywords_list, str):
                keywords_list = [k.strip() for k in keywords_list.split(",")]
        elif self.llm_available and content:
            try:
                keywords_list = await self._llm_extract_keywords(title, content)
                if not keywords_list:
                    keywords_list = self._extract_keywords_fallback(title)
            except Exception as e:
                logger.warning("LLM keywordd extraction error: %s", e)
                keywords_list = self._extract_keywords_fallback(title)
        else:
            keywords_list = self._extract_keywords_fallback(title)

        # Convert list to comma-separated string for database storage
        result["seo_keywords"] = ", ".join(keywords_list) if keywords_list else ""

        logger.debug("SEO metadata generated: %s", result['seo_title'][:40])
        return result

    async def _llm_generate_seo_description(self, title: str, content: str) -> Optional[str]:
        """Use unified prompt manager and model consolidation service to generate SEO meta description (155 chars)"""
        try:
            pm = get_prompt_manager()
            service = get_model_consolidation_service()

            prompt = pm.get_prompt(
                "seo.generate_meta_description",
                title=title,
                content=content[:500],
                max_length=155,
            )

            result = await service.generate(
                prompt=prompt,
                temperature=0.7,
            )
            return result.text[:155] if result and result.text else None

        except Exception as e:
            logger.warning("LLM SEO description error: %s", e)
            return None

    async def _llm_extract_keywords(self, title: str, content: str) -> Optional[List[str]]:
        """Use unified prompt manager and model consolidation service to extract 5-7 SEO keywords"""
        try:
            pm = get_prompt_manager()
            service = get_model_consolidation_service()

            prompt = pm.get_prompt(
                "seo.extract_keywords",
                title=title,
                content=content[:500],
                count=7,
            )

            result = await service.generate(
                prompt=prompt,
                temperature=0.7,
            )

            if result and result.text:
                return [k.strip() for k in result.text.split(",")]
            return None

        except Exception as e:
            logger.warning("LLM keyword extraction error: %s", e)
            return None

    def _extract_keywords_fallback(self, title: str) -> List[str]:
        """Fallback keyword extraction from title"""
        words = title.lower().split()
        common_words = {
            "a",
            "an",
            "the",
            "and",
            "or",
            "but",
            "is",
            "are",
            "to",
            "of",
            "in",
            "on",
            "for",
        }
        keywords = [w.strip(".,;:") for w in words if w not in common_words and len(w) > 3]
        return keywords[:7] if keywords else [title[:20]]

    # ========================================================================
    # SLUG GENERATION
    # ========================================================================

    def generate_slug(self, title: str) -> str:
        """Convert title to URL-friendly slug"""
        slug = title.lower()
        slug = re.sub(r"[^\w\s-]", "", slug)  # Remove special chars
        slug = re.sub(r"[-\s]+", "-", slug)  # Replace spaces with hyphens
        slug = slug.strip("-")  # Remove leading/trailing dashes
        return slug[:60]

    # ========================================================================
    # CATEGORY MATCHING (with LLM intelligence)
    # ========================================================================

    async def match_category(
        self, content: str, available_categories: List[Dict[str, str]], title: Optional[str] = None
    ) -> Optional[Dict[str, str]]:
        """
        Intelligently match content to best category

        Args:
            content: Post content
            available_categories: List of {"id": "...", "name": "...", "description": "..."}
            title: Post title (helps matching)

        Returns:
            Selected category dict or None
        """

        if not available_categories:
            logger.debug("✓ No categories provided")
            return None

        # Strategy 1: Simple keyword matching
        best_category, score = self._keyword_match_category(content, available_categories, title)
        if score > 0:
            logger.debug("Keyword matched category: %s (score=%s)", best_category.get('name'), score)
            return best_category

        # Strategy 2: Use LLM for intelligent matching
        if self.llm_available and content:
            try:
                best_category = await self._llm_match_category(content, available_categories, title)
                if best_category:
                    logger.info("LLM matched category: %s", best_category.get('name'))
                    return best_category
            except Exception as e:
                logger.warning("LLM category matching failed: %s", e)

        # Fallback: return first category
        logger.debug(f"✓ Using first category as fallback: {available_categories[0].get('name')}")
        return available_categories[0]

    def _keyword_match_category(
        self, content: str, available_categories: List[Dict[str, str]], title: Optional[str] = None
    ) -> Tuple[Optional[Dict[str, str]], int]:
        """Simple keyword-based category matching"""

        search_text = f"{title or ''} {content[:500]}".lower()
        best_category = None
        best_score = 0

        for cat in available_categories:
            cat_name = cat.get("name", "").lower()
            cat_desc = cat.get("description", "").lower()

            score = 0

            # Exact name match (high weight)
            if cat_name in search_text:
                score += 10

            # Keywords from description (medium weight)
            for keyword in cat_desc.split():
                if len(keyword) > 3 and keyword in search_text:
                    score += 2

            if score > best_score:
                best_score = score
                best_category = cat

        return best_category, best_score

    async def _llm_match_category(
        self, content: str, available_categories: List[Dict[str, str]], title: Optional[str] = None
    ) -> Optional[Dict[str, str]]:
        """Use LLM to intelligently match category"""
        if not self.llm_available:
            return None

        categories_text = "\n".join(
            [f"- {cat['name']}: {cat.get('description', 'N/A')}" for cat in available_categories]
        )

        prompt = f"""Select the BEST category for this content.

Title: {title or 'N/A'}
Content (first 500 chars):
{content[:500]}

Available Categories:
{categories_text}

Respond with ONLY the exact category name from the list, nothing else."""

        try:
            if ANTHROPIC_AVAILABLE:
                response = anthropic_client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=100,
                    messages=[{"role": "user", "content": prompt}],
                )
                category_name = response.content[0].text.strip()
                return next((c for c in available_categories if c["name"] == category_name), None)

            if OPENAI_AVAILABLE:
                import openai as openai_module

                response = openai_module.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=100,
                    temperature=0.7,
                )
                category_name = response.choices[0].message.content.strip()
                return next((c for c in available_categories if c["name"] == category_name), None)

        except Exception as e:
            logger.warning(f"⚠️  LLM category matching error: {e}")
            return None

    # ========================================================================
    # TAG EXTRACTION
    # ========================================================================

    async def extract_tags(
        self,
        content: str,
        available_tags: List[Dict[str, str]],
        title: Optional[str] = None,
        max_tags: int = 5,
    ) -> List[str]:
        """
        Extract relevant tags from available pool

        Args:
            content: Post content
            available_tags: List of {"id": "...", "name": "...", "slug": "..."}
            title: Post title (optional)
            max_tags: Maximum number of tags to return

        Returns:
            List of tag IDs
        """

        if not available_tags:
            logger.debug("✓ No tags available")
            return []

        # Strategy 1: Simple keyword matching
        matched_tags = self._keyword_match_tags(content, available_tags, title)
        if matched_tags:
            logger.debug(f"✓ Keyword matched {len(matched_tags)} tags")
            return matched_tags[:max_tags]

        # Strategy 2: Use LLM for intelligent extraction
        if self.llm_available and content:
            try:
                llm_tags = await self._llm_extract_tags(content, available_tags, title, max_tags)
                if llm_tags:
                    logger.info(f"✓ LLM extracted {len(llm_tags)} tags")
                    return llm_tags[:max_tags]
            except Exception as e:
                logger.warning(f"⚠️  LLM tag extraction failed: {e}")

        # Fallback: return empty (better than random tags)
        logger.debug("✓ No tags matched")
        return []

    def _keyword_match_tags(
        self, content: str, available_tags: List[Dict[str, str]], title: Optional[str] = None
    ) -> List[str]:
        """Simple keyword-based tag matching"""
        search_text = f"{title or ''} {content[:500]}".lower()
        matched_tag_ids = []

        for tag in available_tags:
            tag_name = tag.get("name", "").lower()
            tag_slug = tag.get("slug", "").lower()
            tag_id = tag.get("id")

            if tag_name in search_text or tag_slug in search_text:
                matched_tag_ids.append(tag_id)

        return matched_tag_ids

    async def _llm_extract_tags(
        self,
        content: str,
        available_tags: List[Dict[str, str]],
        title: Optional[str],
        max_tags: int,
    ) -> List[str]:
        """Use LLM to intelligently extract tags"""
        if not self.llm_available:
            return []

        tags_text = ", ".join([f"{tag['name']}" for tag in available_tags])

        prompt = f"""Extract the {max_tags} most relevant tags for this content.

Title: {title or 'N/A'}
Content (first 500 chars):
{content[:500]}

Available Tags: {tags_text}

Respond with ONLY comma-separated tag names, nothing else. Example: tag1, tag2, tag3"""

        try:
            if ANTHROPIC_AVAILABLE:
                response = anthropic_client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=100,
                    messages=[{"role": "user", "content": prompt}],
                )
                tags_str = response.content[0].text.strip()
                tag_names = [t.strip() for t in tags_str.split(",")]
                return [
                    next((tag["id"] for tag in available_tags if tag["name"] == name), None)
                    for name in tag_names
                    if any(tag["name"] == name for tag in available_tags)
                ]

            if OPENAI_AVAILABLE:
                import openai as openai_module

                response = openai_module.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=100,
                    temperature=0.7,
                )
                tags_str = response.choices[0].message.content.strip()
                tag_names = [t.strip() for t in tags_str.split(",")]
                return [
                    next((tag["id"] for tag in available_tags if tag["name"] == name), None)
                    for name in tag_names
                    if any(tag["name"] == name for tag in available_tags)
                ]

        except Exception as e:
            logger.warning(f"⚠️  LLM tag extraction error: {e}")
            return []

    # ========================================================================
    # FEATURED IMAGE & SOCIAL METADATA
    # ========================================================================

    def generate_featured_image_prompt(self, title: str, content: str, category: str = "") -> str:
        """Generate prompt for featured image generation with NO PEOPLE requirement"""
        first_section = content.split("\n\n")[0:3]
        context = " ".join(first_section)[:200]

        prompt = f"""Generate a professional, modern featured image for a blog post.

Title: {title}
Category: {category}
Context: {context}

Requirements:
- Professional and visually appealing
- Relevant to the topic
- High quality (1200x630px optimal)
- Modern design aesthetic
- ⚠️  NO PEOPLE - Absolutely no human figures, faces, or portraits
- Focus on: objects, nature, technology, concepts, landscapes
- If showing scale, use buildings, vehicles, or other non-human elements

Create an image suitable for social media sharing and blog display."""

        return prompt

    def generate_social_metadata(
        self, title: str, excerpt: str, image_url: Optional[str] = None
    ) -> Dict[str, str]:
        """Generate social media metadata (OG tags, Twitter cards)"""
        return {
            "og_title": title[:70],
            "og_description": excerpt[:160],
            "og_image": image_url or "",
            "twitter_title": title[:70],
            "twitter_description": excerpt[:280],
            "twitter_card": "summary_large_image" if image_url else "summary",
        }

    def generate_json_ld_schema(self, metadata: UnifiedMetadata) -> Dict[str, Any]:
        """Generate JSON-LD structured data for rich snippets"""
        return {
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": metadata.title,
            "description": metadata.excerpt,
            "author": {"@type": "Organization", "name": "Glad Labs"},
            "datePublished": datetime.now().isoformat(),
            "keywords": ",".join(metadata.seo_keywords),
            "image": metadata.featured_image_url,
        }

    # ========================================================================
    # UTILITY FUNCTIONS
    # ========================================================================

    def calculate_reading_time(self, content: str) -> int:
        """Calculate reading time in minutes (avg 200 words/minute)"""
        word_count = len(content.split())
        reading_time = max(1, round(word_count / 200))
        return reading_time

    def _extract_keywords_from_content(self, content: str, count: int = 5) -> List[str]:
        """Extract keywords by word frequency from content"""
        # Remove markdown and common words
        clean_content = re.sub(r"[#*`_\-\[\]()]", "", content).lower()
        words = re.findall(r"\b[a-z]{4,}\b", clean_content)

        # Count frequencies
        word_freq = {}
        for word in words:
            word_freq[word] = word_freq.get(word, 0) + 1

        # Get top words
        keywords = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        return [word for word, _ in keywords[:count]]


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_unified_service: Optional[UnifiedMetadataService] = None


def get_unified_metadata_service() -> UnifiedMetadataService:
    """Get or create singleton unified metadata service"""
    global _unified_service
    if _unified_service is None:
        _unified_service = UnifiedMetadataService()
    return _unified_service
