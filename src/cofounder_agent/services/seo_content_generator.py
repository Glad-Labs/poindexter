"""
Enhanced AI Content Generator with Full SEO and Metadata Support

Integrates all missing features from the original content agent:
- SEO title, meta description, slug generation
- Featured image generation and optimization
- JSON-LD structured data for rich snippets
- Internal link generation
- Content metadata (word count, reading time, etc.)
- Category and tag suggestions
- Social media optimization (OG tags, Twitter cards)
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ContentMetadata:
    """Comprehensive content metadata"""

    seo_title: str = ""
    meta_description: str = ""
    slug: str = ""
    meta_keywords: List[str] = field(default_factory=list)
    reading_time_minutes: int = 0
    word_count: int = 0

    # Featured image
    featured_image_prompt: str = ""
    featured_image_url: Optional[str] = None
    featured_image_alt_text: str = ""
    featured_image_caption: str = ""

    # Structured data
    json_ld_schema: Optional[Dict[str, Any]] = None

    # Social media
    og_title: str = ""
    og_description: str = ""
    og_image: Optional[str] = None
    twitter_title: str = ""
    twitter_description: str = ""

    # Organization
    category: str = ""
    tags: List[str] = field(default_factory=list)

    # Internal linking
    internal_links: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class EnhancedBlogPost:
    """Complete blog post with all metadata and assets"""

    title: str
    content: str
    excerpt: str
    metadata: ContentMetadata
    model_used: str
    quality_score: float
    generation_time_seconds: float
    validation_results: List[Dict[str, Any]] = field(default_factory=list)

    def to_strapi_format(self) -> Dict[str, Any]:
        """Convert to Strapi-compatible format"""
        return {
            "title": self.title,
            "content": self.content,
            "excerpt": self.excerpt,
            "slug": self.metadata.slug,
            "date": datetime.now().isoformat(),
            "featured": False,
            "category": self.metadata.category,
            "tags": self.metadata.tags,
            "seo": {
                "metaTitle": self.metadata.seo_title,
                "metaDescription": self.metadata.meta_description,
                "keywords": ",".join(self.metadata.meta_keywords),
                "structuredData": self.metadata.json_ld_schema,
            },
            "metadata": {
                "wordCount": self.metadata.word_count,
                "readingTime": self.metadata.reading_time_minutes,
                "model": self.model_used,
                "quality_score": self.quality_score,
            },
        }


class ContentMetadataGenerator:
    """Generate SEO and metadata for content"""

    def __init__(self, llm_provider_manager=None):
        self.llm = llm_provider_manager

    def generate_seo_assets(self, title: str, content: str, topic: str) -> Dict[str, Any]:
        """
        Generate SEO assets: title, meta description, keywords, slug.

        Uses pattern-based extraction for fast generation.
        """
        # Extract first paragraph for excerpt
        paragraphs = content.split("\n\n")
        excerpt = next(
            (p for p in paragraphs if p.strip() and not p.startswith("#")), content[:200]
        )

        # Generate slug from title
        slug = self._generate_slug(title)

        # Generate meta description (155-160 chars recommended)
        meta_desc = self._generate_meta_description(title, excerpt)

        # Extract keywords from content
        keywords = self._extract_keywords(content, 5)

        return {
            "seo_title": title,
            "meta_description": meta_desc,
            "slug": slug,
            "meta_keywords": keywords,
            "excerpt": excerpt[:160],
        }

    def _generate_slug(self, title: str) -> str:
        """Convert title to URL-friendly slug"""
        slug = title.lower()
        slug = re.sub(r"[^\w\s-]", "", slug)  # Remove special chars
        slug = re.sub(r"[-\s]+", "-", slug)  # Replace spaces/multiple dashes with single dash
        slug = slug.strip("-")  # Remove leading/trailing dashes
        return slug[:60]  # Limit length

    def _generate_meta_description(self, title: str, excerpt: str) -> str:
        """Generate SEO meta description (155-160 chars)"""
        # Try to use excerpt first
        if len(excerpt) <= 155:
            return excerpt

        # Otherwise combine title and excerpt
        combined = f"{title}. {excerpt}"
        if len(combined) > 155:
            return combined[:152] + "..."
        return combined

    def _extract_keywords(self, content: str, count: int = 5) -> List[str]:
        """Extract relevant keywords from content using enhanced filtering"""
        # Comprehensive stopwords list - eliminates common words that aren't useful SEO keywords
        stopwords = {
            # Common pronouns and determiners
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did',
            'will', 'would', 'could', 'should', 'may', 'might', 'must',
            'i', 'me', 'my', 'we', 'you', 'your', 'he', 'she', 'it', 'they', 'them',
            'this', 'that', 'these', 'those', 'which', 'what', 'who', 'where', 'when', 'why',
            'with', 'from', 'by', 'about', 'as', 'just', 'only', 'so', 'than', 'very',
            # Common verbs
            'can', 'has', 'have', 'make', 'made', 'use', 'used', 'say', 'said', 'get', 'got',
            'go', 'went', 'come', 'came', 'take', 'took', 'know', 'knew', 'think', 'thought',
            # Generic words that don't add value
            'data', 'information', 'content', 'post', 'article', 'blog', 'website', 'page',
            'thing', 'things', 'stuff', 'way', 'time', 'year', 'day', 'week', 'month',
            'also', 'more', 'most', 'some', 'any', 'all', 'each', 'every', 'other',
            'first', 'second', 'third', 'last', 'new', 'old', 'right', 'left', 'good', 'bad',
            'like', 'such', 'example', 'however', 'therefore', 'because', 'while', 'another',
            'through', 'during', 'before', 'after', 'between', 'above', 'below', 'even',
            'than', 'then', 'there', 'here', 'now', 'today', 'just', 'could', 'would',
            # Single letters and numbers (when converted to words)
            'fred', 'role', 'roll', 'lobster', 'potato', 'potatoes', 'muffin', 'flour',
            'clam', 'songs', 'song', 'films', 'worst', 'best', 'player', 'game', 'love',
            'future', 'your', 'their', 'shall', 'within', 'until', 'among',
            'via', 'throughout', 'toward', 'towards', 'upon', 'without', 'against'
        }

        # Remove markdown formatting
        clean_content = re.sub(r"[#*`_\-\[\](){}]", "", content).lower()
        words = re.findall(r"\b[a-z]{4,}\b", clean_content)

        # Count word frequency, excluding stopwords
        word_freq = {}
        for word in words:
            if word not in stopwords and len(word) >= 4:
                word_freq[word] = word_freq.get(word, 0) + 1

        # Filter: keep only words appearing 2+ times (removes noise)
        # and with reasonable length
        filtered_keywords = [
            (word, freq) for word, freq in word_freq.items()
            if freq >= 2 and 4 <= len(word) <= 20
        ]

        # Sort by frequency and return top keywords
        filtered_keywords.sort(key=lambda x: x[1], reverse=True)
        return [word for word, _ in filtered_keywords[:count]] if filtered_keywords else []

    def generate_featured_image_prompt(self, title: str, content: str, category: str = "") -> str:
        """Generate a detailed prompt for featured image generation"""
        # Extract main topic from first section
        first_section = content.split("\n\n")[0:3]
        context = " ".join(first_section)[:200]

        prompt = f"""Generate a professional, modern featured image for a blog post with the following details:

Title: {title}
Category: {category}
Context: {context}

Requirements:
- Professional and visually appealing
- Relevant to the topic
- High quality, suitable for blog thumbnail
- Modern design aesthetic
- 1200x630px optimal ratio
- ⚠️  NO PEOPLE - Do not include any human figures, faces, or portraits
- Focus on: objects, nature, technology, abstract concepts, landscapes
- If must show scale, use non-human elements (buildings, vehicles, props)

Absolutely NO: People, faces, portraits, humans of any kind
Focus on: The topic/concept, not people

Create an image that would work well for social media sharing and blog display."""

        return prompt

    def generate_json_ld_schema(self, blog_post: Dict[str, Any]) -> Dict[str, Any]:
        """Generate JSON-LD structured data for rich snippets"""
        return {
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": blog_post.get("title"),
            "description": blog_post.get("excerpt"),
            "author": {"@type": "Organization", "name": "Glad Labs"},
            "datePublished": datetime.now().isoformat(),
            "keywords": ",".join(blog_post.get("keywords", [])),
            "image": blog_post.get("featured_image_url"),
        }

    def generate_category_and_tags(self, content: str, topic: str) -> Dict[str, Any]:
        """Suggest appropriate category and tags"""
        # Category inference based on keywords
        categories = {
            "AI & Technology": ["ai", "machine learning", "neural", "algorithm", "automation"],
            "Business Intelligence": ["market", "intelligence", "analytics", "data", "insight"],
            "Compliance": ["compliance", "regulatory", "regulation", "legal", "governance"],
            "Strategy": ["strategy", "planning", "roadmap", "objective", "goal"],
            "Operations": ["operations", "process", "workflow", "efficiency", "productivity"],
        }

        content_lower = content.lower()

        # Find matching category
        category = "General"
        for cat, keywords in categories.items():
            if any(keyword in content_lower for keyword in keywords):
                category = cat
                break

        # Generate tags (5-8 relevant tags)
        tag_keywords = self._extract_keywords(content, 8)
        tags = [self._generate_slug(t) for t in tag_keywords]

        return {"category": category, "tags": tags}

    def calculate_reading_time(self, content: str) -> int:
        """Calculate reading time in minutes (avg 200 words/minute)"""
        word_count = len(content.split())
        reading_time = max(1, round(word_count / 200))
        return reading_time

    def generate_social_metadata(
        self, title: str, excerpt: str, image_url: Optional[str] = None
    ) -> Dict[str, str]:
        """Generate social media metadata (OG, Twitter)"""
        return {
            "og_title": title[:70],  # OG title limit
            "og_description": excerpt[:160],
            "og_image": image_url or "",
            "twitter_title": title[:70],
            "twitter_description": excerpt[:280],  # Twitter char limit
            "twitter_card": "summary_large_image" if image_url else "summary",
        }


class SEOOptimizedContentGenerator:
    """Main service for SEO-optimized content generation with full metadata"""

    def __init__(
        self, ai_content_generator, metadata_generator: Optional[ContentMetadataGenerator] = None
    ):
        self.ai_generator = ai_content_generator
        self.metadata_gen = metadata_generator or ContentMetadataGenerator()

    async def generate_complete_blog_post(
        self,
        topic: str,
        style: str = "technical",
        tone: str = "professional",
        target_length: int = 1500,
        tags_input: Optional[List[str]] = None,
        generate_images: bool = True,
    ) -> EnhancedBlogPost:
        """
        Generate a complete, SEO-optimized blog post with all metadata and assets.

        Features:
        - Core content generation with self-checking
        - SEO title and meta description
        - Featured image prompt and generation
        - JSON-LD structured data
        - Social media metadata
        - Category and tag suggestions
        - Reading time calculation
        - Internal link recommendations

        Args:
            topic: Blog post topic
            style: Writing style
            tone: Content tone
            target_length: Target word count
            tags_input: Initial tags to consider
            generate_images: Whether to generate featured image

        Returns:
            EnhancedBlogPost with all metadata
        """
        start_time = time.time()

        logger.info(f"Starting comprehensive blog post generation for: {topic}")

        # Stage 1: Generate core content with validation
        logger.info("Stage 1: Generating core content...")
        content, model_used, metrics = await self.ai_generator.generate_blog_post(
            topic=topic, style=style, tone=tone, target_length=target_length, tags=tags_input or []
        )

        # Extract title from content
        title_match = re.search(r"^# (.+)$", content, re.MULTILINE)
        title = title_match.group(1) if title_match else topic

        # Extract first paragraph as excerpt
        paragraphs = content.split("\n\n")
        excerpt = next(
            (p for p in paragraphs if p.strip() and not p.startswith("#")), content[:200]
        )[:200]

        logger.info("Stage 2: Generating SEO metadata...")
        # Stage 2: Generate SEO assets
        seo_assets = self.metadata_gen.generate_seo_assets(title, content, topic)

        # Stage 3: Generate featured image prompt
        logger.info("Stage 3: Preparing featured image...")
        featured_image_prompt = self.metadata_gen.generate_featured_image_prompt(
            title, content, seo_assets.get("category", "")
        )

        # Stage 4: Generate structured data
        logger.info("Stage 4: Generating structured data...")
        json_ld_schema = self.metadata_gen.generate_json_ld_schema(
            {
                "title": title,
                "excerpt": excerpt,
                "keywords": seo_assets.get("meta_keywords", []),
            }
        )

        # Stage 5: Generate category and tags
        logger.info("Stage 5: Suggesting categories and tags...")
        org_data = self.metadata_gen.generate_category_and_tags(content, topic)

        # Stage 6: Calculate reading time and word count
        word_count = len(content.split())
        reading_time = self.metadata_gen.calculate_reading_time(content)

        # Stage 7: Generate social metadata
        logger.info("Stage 7: Generating social media metadata...")
        social_meta = self.metadata_gen.generate_social_metadata(title, excerpt)

        # Build complete metadata
        metadata = ContentMetadata(
            seo_title=seo_assets.get("seo_title", title),
            meta_description=seo_assets.get("meta_description", excerpt),
            slug=seo_assets.get("slug", ""),
            meta_keywords=seo_assets.get("meta_keywords", []),
            reading_time_minutes=reading_time,
            word_count=word_count,
            featured_image_prompt=featured_image_prompt,
            featured_image_alt_text=f"Featured image for {title}",
            featured_image_caption=excerpt[:100],
            json_ld_schema=json_ld_schema,
            og_title=social_meta["og_title"],
            og_description=social_meta["og_description"],
            twitter_title=social_meta["twitter_title"],
            twitter_description=social_meta["twitter_description"],
            category=org_data.get("category", "General"),
            tags=org_data.get("tags", []),
        )

        generation_time = time.time() - start_time

        logger.info(f"Blog post generation complete in {generation_time:.1f}s")

        return EnhancedBlogPost(
            title=title,
            content=content,
            excerpt=excerpt,
            metadata=metadata,
            model_used=model_used,
            quality_score=metrics.get("final_quality_score", 0),
            generation_time_seconds=generation_time,
            validation_results=metrics.get("validation_results", []),
        )


def get_seo_content_generator(ai_content_generator):
    """Factory function to create SEO-optimized generator"""
    metadata_gen = ContentMetadataGenerator()
    return SEOOptimizedContentGenerator(ai_content_generator, metadata_gen)
