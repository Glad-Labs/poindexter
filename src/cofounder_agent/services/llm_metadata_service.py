"""
LLM Metadata Service

Generates intelligent metadata, tags, SEO content, and excerpts from content
when manual extraction/fallback strategies don't work.

This service leverages LLMs to:
1. Extract titles from content (when topic-based extraction fails)
2. Generate professional excerpts for social media
3. Create SEO-optimized title, description, keywords
4. Intelligently match categories from available options
5. Extract relevant tags from available pool
6. Generate author profiles and descriptions

Strategy:
- Always try manual/simple extraction first
- Use LLM as intelligent fallback for complex metadata
- Batch process multiple LLM calls for efficiency
- Cache results to avoid duplicate calls
"""

import logging
from typing import Optional, Dict, List, Any, Tuple
from datetime import datetime
import json
import re

logger = logging.getLogger(__name__)

# Try to import the LLM client based on available models
try:
    from anthropic import Anthropic

    ANTHROPIC_AVAILABLE = True
    anthropic_client = Anthropic()
except ImportError:
    ANTHROPIC_AVAILABLE = False
    anthropic_client = None

try:
    import openai

    OPENAI_AVAILABLE = True
    openai.api_key = None  # Use environment variable
except ImportError:
    OPENAI_AVAILABLE = False


class LLMMetadataService:
    """Generate metadata using LLMs with fallback strategies"""

    def __init__(self, model: str = "auto"):
        """
        Initialize metadata service

        Args:
            model: "auto" (use best available), "claude-3-haiku", "gpt-4", etc.
        """
        self.model = model
        self.llm_available = ANTHROPIC_AVAILABLE or OPENAI_AVAILABLE

        if not self.llm_available:
            logger.warning(
                "⚠️  No LLM available (Anthropic or OpenAI). Using fallback strategies only."
            )

    # ========================================================================
    # TITLE EXTRACTION (Advanced)
    # ========================================================================

    async def extract_title(
        self, content: str, topic: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Extract or generate title from content

        Strategy:
        1. Use stored title if available and not "Untitled"
        2. Use topic if provided
        3. Extract first meaningful line/heading from content
        4. Use LLM to generate title from content
        5. Fallback to date-based title
        """

        # Strategy 1: Check metadata for stored title
        if metadata:
            stored_title = metadata.get("title")
            if stored_title and stored_title.lower() != "untitled":
                logger.debug(f"✓ Using stored title: {stored_title[:50]}")
                return stored_title

        # Strategy 2: Use topic
        if topic and topic.lower() != "untitled":
            logger.debug(f"✓ Using topic as title: {topic[:50]}")
            return topic[:100]

        # Strategy 3: Extract from content (first meaningful line)
        title = self._extract_first_meaningful_line(content)
        if title:
            logger.debug(f"✓ Extracted title from content: {title[:50]}")
            return title

        # Strategy 4: Use LLM to generate title
        if self.llm_available and content:
            try:
                title = await self._llm_generate_title(content)
                if title:
                    logger.info(f"✓ LLM generated title: {title[:50]}")
                    return title
            except Exception as e:
                logger.warning(f"⚠️  LLM title generation failed: {e}")

        # Strategy 5: Fallback to date
        title = f"Blog Post - {datetime.now().strftime('%B %d, %Y')}"
        logger.debug(f"✓ Using date-based fallback: {title}")
        return title

    def _extract_first_meaningful_line(self, content: str) -> Optional[str]:
        """Extract first meaningful non-empty line from content"""
        if not content:
            return None

        lines = content.split("\n")
        for line in lines:
            cleaned = line.strip()
            # Skip empty lines, very short lines, and markdown formatting
            if (
                cleaned
                and len(cleaned) > 10
                and not cleaned.startswith("-")
                and not cleaned.startswith("*")
            ):
                # Check if it looks like a heading (under 150 chars, reasonable structure)
                if len(cleaned) < 150 and "," not in cleaned[:50]:
                    return cleaned[:100]

        return None

    async def _llm_generate_title(self, content: str) -> Optional[str]:
        """Use LLM to generate a professional title from content"""
        if not self.llm_available:
            return None

        prompt = f"""Given the following content, generate a short, engaging, and professional title (max 100 characters).

Content:
{content[:500]}

Generate ONLY the title, nothing else. No quotes, no explanation."""

        try:
            if ANTHROPIC_AVAILABLE:
                response = anthropic_client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=100,
                    messages=[{"role": "user", "content": prompt}],
                )
                title = response.content[0].text.strip()
                return title[:100] if title else None

            elif OPENAI_AVAILABLE:
                import openai as openai_module

                response = openai_module.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=100,
                    temperature=0.7,
                )
                title = response.choices[0].message.content.strip()
                return title[:100] if title else None

        except Exception as e:
            logger.warning(f"⚠️  LLM title generation error: {e}")
            return None

    # ========================================================================
    # EXCERPT GENERATION
    # ========================================================================

    async def generate_excerpt(
        self, content: str, stored_excerpt: Optional[str] = None, max_length: int = 200
    ) -> str:
        """
        Generate excerpt for content (for social media, previews)

        Strategy:
        1. Use stored excerpt if available
        2. Extract first paragraph
        3. Use LLM to generate professional excerpt
        """

        # Strategy 1: Use stored excerpt if good
        if stored_excerpt and len(stored_excerpt) > 20:
            logger.debug(f"✓ Using stored excerpt")
            return stored_excerpt[:max_length]

        # Strategy 2: Extract first paragraph
        excerpt = self._extract_first_paragraph(content, max_length)
        if excerpt:
            logger.debug(f"✓ Extracted excerpt from first paragraph")
            return excerpt

        # Strategy 3: Use LLM to generate excerpt
        if self.llm_available and content:
            try:
                excerpt = await self._llm_generate_excerpt(content, max_length)
                if excerpt:
                    logger.info(f"✓ LLM generated excerpt")
                    return excerpt
            except Exception as e:
                logger.warning(f"⚠️  LLM excerpt generation failed: {e}")

        # Fallback: Use content start
        excerpt = content[:max_length]
        if len(content) > max_length:
            excerpt += "..."
        logger.debug(f"✓ Using content start as fallback")
        return excerpt

    def _extract_first_paragraph(self, content: str, max_length: int) -> Optional[str]:
        """Extract first paragraph from content"""
        if not content:
            return None

        paragraphs = content.split("\n\n")
        for para in paragraphs:
            para_clean = para.strip()
            if para_clean and len(para_clean) > 30:
                excerpt = para_clean[:max_length]
                if len(para_clean) > max_length:
                    excerpt += "..."
                # Clean up line breaks
                excerpt = excerpt.replace("\n", " ")
                excerpt = " ".join(excerpt.split())  # Normalize whitespace
                return excerpt

        return None

    async def _llm_generate_excerpt(self, content: str, max_length: int) -> Optional[str]:
        """Use LLM to generate professional excerpt"""
        if not self.llm_available:
            return None

        prompt = f"""Given the following content, generate a concise, engaging summary (max {max_length} characters) suitable for social media or blog preview.

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

            elif OPENAI_AVAILABLE:
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
            logger.warning(f"⚠️  LLM excerpt generation error: {e}")
            return None

    # ========================================================================
    # SEO METADATA GENERATION
    # ========================================================================

    async def generate_seo_metadata(
        self, title: str, content: str, stored_seo: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """
        Generate SEO metadata (seo_title, seo_description, seo_keywords)

        Returns:
            {"seo_title": "...", "seo_description": "...", "seo_keywords": "..."}
        """

        result = {}

        # SEO Title
        if stored_seo and stored_seo.get("seo_title"):
            result["seo_title"] = stored_seo["seo_title"]
            logger.debug(f"✓ Using stored SEO title")
        else:
            result["seo_title"] = title[:60] if len(title) > 60 else title

        # SEO Description
        if stored_seo and stored_seo.get("seo_description"):
            result["seo_description"] = stored_seo["seo_description"]
            logger.debug(f"✓ Using stored SEO description")
        elif self.llm_available and content:
            try:
                seo_desc = await self._llm_generate_seo_description(title, content)
                if seo_desc:
                    result["seo_description"] = seo_desc
                    logger.info(f"✓ LLM generated SEO description")
                else:
                    result["seo_description"] = (
                        f"Read about {title.lower()}. Discover insights and expert guidance."
                    )
            except Exception as e:
                logger.warning(f"⚠️  SEO description generation failed: {e}")
                result["seo_description"] = f"Explore our article on {title}."
        else:
            result["seo_description"] = (
                f"Read about {title.lower()}. Discover insights and expert guidance."
            )

        # SEO Keywords
        if stored_seo and stored_seo.get("seo_keywords"):
            result["seo_keywords"] = stored_seo["seo_keywords"]
            logger.debug(f"✓ Using stored SEO keywords")
        elif self.llm_available and content:
            try:
                keywords = await self._llm_extract_keywords(title, content)
                if keywords:
                    result["seo_keywords"] = keywords
                    logger.info(f"✓ LLM extracted keywords")
                else:
                    result["seo_keywords"] = self._extract_keywords_fallback(title)
            except Exception as e:
                logger.warning(f"⚠️  Keyword extraction failed: {e}")
                result["seo_keywords"] = self._extract_keywords_fallback(title)
        else:
            result["seo_keywords"] = self._extract_keywords_fallback(title)

        logger.debug(f"SEO Metadata: {result}")
        return result

    async def _llm_generate_seo_description(self, title: str, content: str) -> Optional[str]:
        """Generate SEO meta description (155 characters)"""
        if not self.llm_available:
            return None

        prompt = f"""Generate a compelling SEO meta description (max 155 characters) for this content:

Title: {title}
Content (first 500 chars):
{content[:500]}

Generate ONLY the description, nothing else. No quotes."""

        try:
            if ANTHROPIC_AVAILABLE:
                response = anthropic_client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=160,
                    messages=[{"role": "user", "content": prompt}],
                )
                desc = response.content[0].text.strip()
                return desc[:155] if desc else None

            elif OPENAI_AVAILABLE:
                import openai as openai_module

                response = openai_module.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=160,
                    temperature=0.7,
                )
                desc = response.choices[0].message.content.strip()
                return desc[:155] if desc else None

        except Exception as e:
            logger.warning(f"⚠️  SEO description generation error: {e}")
            return None

    async def _llm_extract_keywords(self, title: str, content: str) -> Optional[str]:
        """Extract 5-7 SEO keywords from content"""
        if not self.llm_available:
            return None

        prompt = f"""Extract 5-7 comma-separated SEO keywords from this content (most important first):

Title: {title}
Content (first 500 chars):
{content[:500]}

Generate ONLY comma-separated keywords, nothing else. Example format: keyword1, keyword2, keyword3"""

        try:
            if ANTHROPIC_AVAILABLE:
                response = anthropic_client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=100,
                    messages=[{"role": "user", "content": prompt}],
                )
                keywords = response.content[0].text.strip()
                return keywords if keywords else None

            elif OPENAI_AVAILABLE:
                import openai as openai_module

                response = openai_module.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=100,
                    temperature=0.7,
                )
                keywords = response.choices[0].message.content.strip()
                return keywords if keywords else None

        except Exception as e:
            logger.warning(f"⚠️  Keyword extraction error: {e}")
            return None

    def _extract_keywords_fallback(self, title: str) -> str:
        """Fallback keyword extraction from title"""
        # Simple: split title into meaningful words
        words = title.lower().split()
        # Filter out very common words
        common_words = {"a", "an", "the", "and", "or", "but", "is", "are", "to", "of", "in", "on"}
        keywords = [w.strip(".,;:") for w in words if w not in common_words and len(w) > 3]
        return ", ".join(keywords[:7]) if keywords else title

    # ========================================================================
    # CATEGORY & TAG MATCHING (with LLM intelligence)
    # ========================================================================

    async def match_category(
        self, content: str, available_categories: List[Dict[str, str]], title: Optional[str] = None
    ) -> Optional[str]:
        """
        Intelligently match content to best category

        Args:
            content: Post content
            available_categories: List of {"id": "...", "name": "...", "description": "..."}
            title: Post title (optional, helps matching)

        Returns:
            Category ID of best match, or None
        """

        if not available_categories:
            return None

        # Strategy 1: Simple keyword matching
        best_category, score = self._keyword_match_category(content, available_categories, title)
        if score > 0:
            logger.debug(f"✓ Keyword matched category: {best_category.get('name')} (score={score})")
            return best_category.get("id")

        # Strategy 2: Use LLM for intelligent matching
        if self.llm_available and content:
            try:
                category_id = await self._llm_match_category(content, available_categories, title)
                if category_id:
                    logger.info(f"✓ LLM matched category: {category_id}")
                    return category_id
            except Exception as e:
                logger.warning(f"⚠️  LLM category matching failed: {e}")

        # Fallback: return first category
        logger.debug(f"✓ Using first category as fallback")
        return available_categories[0].get("id")

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

            # Description keywords (medium weight)
            for keyword in cat_desc.split():
                if len(keyword) > 3 and keyword in search_text:
                    score += 2

            # Category name keywords (medium weight)
            for keyword in cat_name.split():
                if len(keyword) > 3 and keyword in search_text:
                    score += 3

            if score > best_score:
                best_score = score
                best_category = cat

        return (
            best_category or (available_categories[0] if available_categories else None),
            best_score,
        )

    async def _llm_match_category(
        self, content: str, available_categories: List[Dict[str, str]], title: Optional[str] = None
    ) -> Optional[str]:
        """Use LLM to intelligently match category"""
        if not self.llm_available:
            return None

        categories_text = "\n".join(
            [f"- {cat['name']}: {cat.get('description', 'N/A')}" for cat in available_categories]
        )

        prompt = f"""Given the following content and available categories, select the BEST category.

Title: {title or 'N/A'}

Content (first 500 chars):
{content[:500]}

Available Categories:
{categories_text}

Respond with ONLY the exact category name from the list above, nothing else."""

        try:
            if ANTHROPIC_AVAILABLE:
                response = anthropic_client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=50,
                    messages=[{"role": "user", "content": prompt}],
                )
                matched_name = response.content[0].text.strip()

            elif OPENAI_AVAILABLE:
                import openai as openai_module

                response = openai_module.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=50,
                    temperature=0.7,
                )
                matched_name = response.choices[0].message.content.strip()

            # Find matching category by name
            for cat in available_categories:
                if matched_name.lower() in cat.get("name", "").lower():
                    return cat.get("id")

            return None

        except Exception as e:
            logger.warning(f"⚠️  LLM category matching error: {e}")
            return None

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
            List of tag IDs (max_tags limit)
        """

        if not available_tags:
            return []

        # Strategy 1: Simple keyword matching
        matched_tags = self._keyword_match_tags(content, available_tags, title)
        if matched_tags:
            logger.debug(f"✓ Keyword matched {len(matched_tags)} tags")
            return matched_tags[:max_tags]

        # Strategy 2: Use LLM for intelligent tagging
        if self.llm_available and content:
            try:
                tag_ids = await self._llm_extract_tags(content, available_tags, title, max_tags)
                if tag_ids:
                    logger.info(f"✓ LLM extracted {len(tag_ids)} tags")
                    return tag_ids
            except Exception as e:
                logger.warning(f"⚠️  LLM tag extraction failed: {e}")

        logger.debug(f"✓ No tags matched")
        return []

    def _keyword_match_tags(
        self, content: str, available_tags: List[Dict[str, str]], title: Optional[str] = None
    ) -> List[str]:
        """Simple keyword-based tag matching"""

        search_text = f"{title or ''} {content[:500]}".lower()
        matched = []

        for tag in available_tags:
            tag_name = tag.get("name", "").lower()
            tag_slug = tag.get("slug", "").lower()

            # Check if tag name or slug appears in content
            if tag_name in search_text or tag_slug in search_text:
                matched.append(tag.get("id"))

        return matched

    async def _llm_extract_tags(
        self,
        content: str,
        available_tags: List[Dict[str, str]],
        title: Optional[str],
        max_tags: int,
    ) -> List[str]:
        """Use LLM to extract relevant tags"""
        if not self.llm_available:
            return []

        available_tag_names = [tag.get("name") for tag in available_tags]
        tags_text = ", ".join(available_tag_names[:30])  # Limit to 30 to avoid token issues

        prompt = f"""From this list of tags, select the {max_tags} MOST RELEVANT tags for this content:

Available Tags: {tags_text}

Title: {title or 'N/A'}

Content (first 500 chars):
{content[:500]}

Respond with ONLY comma-separated tag names, in order of relevance. Example: tag1, tag2, tag3"""

        try:
            if ANTHROPIC_AVAILABLE:
                response = anthropic_client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=100,
                    messages=[{"role": "user", "content": prompt}],
                )
                matched_tags_text = response.content[0].text.strip()

            elif OPENAI_AVAILABLE:
                import openai as openai_module

                response = openai_module.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=100,
                    temperature=0.7,
                )
                matched_tags_text = response.choices[0].message.content.strip()

            # Parse matched tag names
            matched_names = [t.strip() for t in matched_tags_text.split(",")]

            # Find IDs for matched tags
            result_ids = []
            for name in matched_names:
                for tag in available_tags:
                    if name.lower() in tag.get("name", "").lower():
                        result_ids.append(tag.get("id"))
                        break

            return result_ids[:max_tags]

        except Exception as e:
            logger.warning(f"⚠️  LLM tag extraction error: {e}")
            return []


# Singleton instance
_metadata_service: Optional[LLMMetadataService] = None


def get_llm_metadata_service() -> LLMMetadataService:
    """Get or create singleton LLM metadata service"""
    global _metadata_service
    if _metadata_service is None:
        _metadata_service = LLMMetadataService()
    return _metadata_service
