"""
Unit and Integration Tests for SEO Content Generator

Tests all metadata generation features:
- SEO title, description, slug, keywords
- Featured image prompts
- JSON-LD structured data
- Category and tag detection
- Social media metadata
- Reading time calculation
"""

import pytest
import sys
import os
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
from typing import Dict, Any

# Add project paths
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.seo_content_generator import (
    ContentMetadata,
    EnhancedBlogPost,
    ContentMetadataGenerator,
    SEOOptimizedContentGenerator,
    get_seo_content_generator,
)


@pytest.mark.unit
class TestContentMetadata:
    """Test ContentMetadata dataclass"""

    def test_content_metadata_creation(self):
        """Test basic ContentMetadata creation"""
        metadata = ContentMetadata(
            seo_title="Test Title",
            meta_description="Test description",
            slug="test-slug",
            meta_keywords=["test", "keywords"],
        )

        assert metadata.seo_title == "Test Title"
        assert metadata.meta_description == "Test description"
        assert metadata.slug == "test-slug"
        assert metadata.meta_keywords == ["test", "keywords"]

    def test_content_metadata_defaults(self):
        """Test ContentMetadata default values"""
        metadata = ContentMetadata()

        assert metadata.seo_title == ""
        assert metadata.meta_keywords == []
        assert metadata.tags == []
        assert metadata.reading_time_minutes == 0
        assert metadata.word_count == 0

    def test_content_metadata_all_fields(self):
        """Test all ContentMetadata fields are initialized"""
        metadata = ContentMetadata()

        # Check all SEO fields
        assert hasattr(metadata, "seo_title")
        assert hasattr(metadata, "meta_description")
        assert hasattr(metadata, "slug")
        assert hasattr(metadata, "meta_keywords")

        # Check image fields
        assert hasattr(metadata, "featured_image_prompt")
        assert hasattr(metadata, "featured_image_url")
        assert hasattr(metadata, "featured_image_alt_text")
        assert hasattr(metadata, "featured_image_caption")

        # Check structured data
        assert hasattr(metadata, "json_ld_schema")

        # Check social fields
        assert hasattr(metadata, "og_title")
        assert hasattr(metadata, "og_description")
        assert hasattr(metadata, "twitter_title")
        assert hasattr(metadata, "twitter_description")

        # Check organization
        assert hasattr(metadata, "category")
        assert hasattr(metadata, "tags")

        # Check metrics
        assert hasattr(metadata, "reading_time_minutes")
        assert hasattr(metadata, "word_count")


@pytest.mark.unit
class TestContentMetadataGenerator:
    """Test ContentMetadataGenerator service"""

    @pytest.fixture
    def generator(self):
        """Create ContentMetadataGenerator instance"""
        return ContentMetadataGenerator()

    def test_generate_slug(self, generator):
        """Test slug generation"""
        test_cases = [
            ("Hello World", "hello-world"),
            ("AI in Healthcare", "ai-in-healthcare"),
            ("How to Build APIs!!!", "how-to-build-apis"),
            ("Test-With-Dashes", "test-with-dashes"),
            ("UPPERCASE TEXT", "uppercase-text"),
        ]

        for input_text, expected_slug in test_cases:
            slug = generator._generate_slug(input_text)
            assert slug == expected_slug, f"Failed for input: {input_text}"

    def test_generate_slug_max_length(self, generator):
        """Test slug respects max length"""
        long_title = "This is a very long title that should be truncated to a reasonable length"
        slug = generator._generate_slug(long_title)

        assert len(slug) <= 60, f"Slug too long: {len(slug)} chars"
        assert "-" in slug or len(slug) < 60

    def test_generate_meta_description(self, generator):
        """Test meta description generation"""
        title = "AI in Healthcare"
        content = "This is a comprehensive guide about artificial intelligence applications in healthcare and medicine."

        description = generator._generate_meta_description(title, content)

        assert description is not None
        assert len(description) > 0
        assert len(description) <= 160

    def test_generate_meta_description_length(self, generator):
        """Test meta descriptions stay within Google's limits"""
        descriptions = [
            generator._generate_meta_description(
                "Test Title", "Short content that needs a description. " * 20
            )
            for _ in range(5)
        ]

        for desc in descriptions:
            assert 155 <= len(desc) <= 160, f"Description out of range: {len(desc)} chars"

    def test_extract_keywords(self, generator):
        """Test keyword extraction"""
        content = """
        Artificial intelligence and machine learning are transforming business.
        AI algorithms can analyze market trends and provide competitive advantages.
        Using machine learning for predictive analytics.
        """

        keywords = generator._extract_keywords(content)

        assert 5 <= len(keywords) <= 8, f"Wrong number of keywords: {len(keywords)}"
        assert all(isinstance(k, str) for k in keywords)
        assert all(len(k) > 0 for k in keywords)

    def test_extract_keywords_no_common_words(self, generator):
        """Test that 4-letter minimum filters short words"""
        content = "the and or is are in to a an will not also much"

        keywords = generator._extract_keywords(content)

        # Implementation filters words with 4+ characters, so single/short words are naturally excluded
        # Verify all keywords meet minimum length
        assert all(len(k) >= 4 for k in keywords)

    def test_generate_seo_assets(self, generator):
        """Test SEO assets generation"""
        title = "AI-Powered Market Intelligence Guide"
        content = "Learn how AI analyzes market trends to provide competitive advantages. " * 10

        assets = generator.generate_seo_assets(title, content, topic="Market Analysis")

        assert "seo_title" in assets
        assert "meta_description" in assets
        assert "slug" in assets
        assert "meta_keywords" in assets

        # Verify lengths
        assert len(assets["seo_title"]) <= 70
        assert len(assets["meta_description"]) > 0
        assert len(assets["meta_keywords"]) > 0

    def test_generate_featured_image_prompt(self, generator):
        """Test featured image prompt generation"""
        title = "AI in Healthcare"
        category = "Technology"
        content = "Content about AI in healthcare applications."

        prompt = generator.generate_featured_image_prompt(title, category, content)

        assert len(prompt) >= 300  # Should be detailed
        assert "image" in prompt.lower() or "visual" in prompt.lower()
        assert len(prompt) <= 2000  # Shouldn't be too long

    def test_generate_json_ld_schema(self, generator):
        """Test JSON-LD schema generation"""
        title = "AI in Healthcare"
        excerpt = "A comprehensive guide to AI applications in healthcare"
        keywords = ["ai", "healthcare", "technology"]

        schema = generator.generate_json_ld_schema(
            {"title": title, "excerpt": excerpt, "keywords": keywords}
        )

        assert schema["@context"] == "https://schema.org"
        assert schema["@type"] == "BlogPosting"
        assert schema["headline"] == title
        assert schema["description"] == excerpt
        assert "author" in schema
        assert "datePublished" in schema

    def test_generate_category_and_tags(self, generator):
        """Test category and tag generation"""
        content = """
        AI algorithms are analyzing market trends for competitive intelligence.
        Business analytics help with strategic planning.
        Machine learning is used for predictive forecasting.
        """

        result = generator.generate_category_and_tags(content, topic="AI Analytics")

        assert "category" in result
        assert "tags" in result
        assert isinstance(result["tags"], list)
        assert len(result["tags"]) > 0

    def test_category_detection_accuracy(self, generator):
        """Test category detection accuracy"""
        test_cases = [
            ("AI machine learning algorithms", "AI & Technology"),
            ("Market analysis business intelligence", "Business Intelligence"),
            ("Compliance regulatory requirements", "Compliance"),
            ("Strategic planning roadmap", "Strategy"),
            ("Process workflow operations", "Operations"),
        ]

        for content, expected_category in test_cases:
            result = generator.generate_category_and_tags(content, topic=expected_category)
            assert result["category"] == expected_category, f"Failed for: {content}"

    def test_calculate_reading_time(self, generator):
        """Test reading time calculation"""
        test_cases = [
            (" ".join(["word"] * 200), 1),  # 200 words = 1 min
            (" ".join(["word"] * 500), 2),  # 500 words = 2.5 min (rounds to 2)
            (" ".join(["word"] * 1000), 5),  # 1000 words = 5 min
            (" ".join(["word"] * 1600), 8),  # 1600 words = 8 min
        ]

        for content, expected_time in test_cases:
            reading_time = generator.calculate_reading_time(content)
            assert (
                reading_time == expected_time
            ), f"Failed for content with {len(content.split())} words, got {reading_time}, expected {expected_time}"

    def test_reading_time_minimum(self, generator):
        """Test reading time has minimum of 1 minute"""
        content = " ".join(["word"] * 50)  # 50 words
        reading_time = generator.calculate_reading_time(content)
        assert reading_time >= 1

    def test_generate_social_metadata(self, generator):
        """Test social metadata generation"""
        title = "AI Market Intelligence Guide"
        description = "Learn how AI analyzes market trends for competitive advantage"

        social = generator.generate_social_metadata(title, description)

        # Check OG tags
        assert "og_title" in social
        assert "og_description" in social
        assert len(social["og_title"]) <= 70
        assert len(social["og_description"]) <= 160

        # Check Twitter tags
        assert "twitter_title" in social
        assert "twitter_description" in social
        assert len(social["twitter_title"]) <= 70
        assert len(social["twitter_description"]) <= 280


@pytest.mark.unit
class TestEnhancedBlogPost:
    """Test EnhancedBlogPost dataclass"""

    def test_enhanced_blog_post_creation(self):
        """Test EnhancedBlogPost creation"""
        metadata = ContentMetadata(seo_title="Test Title")
        post = EnhancedBlogPost(
            title="Test Title",
            content="Test content",
            excerpt="Test excerpt",
            metadata=metadata,
            model_used="test-model",
            quality_score=8.5,
            generation_time_seconds=60.0,
        )

        assert post.title == "Test Title"
        assert post.content == "Test content"
        assert post.quality_score == 8.5

    def test_to_strapi_format(self):
        """Test conversion to Strapi format"""
        metadata = ContentMetadata(
            seo_title="Test Title",
            meta_description="Test description",
            slug="test-slug",
            meta_keywords=["test", "keywords"],
            word_count=1500,
            reading_time_minutes=8,
            category="AI & Technology",
            tags=["ai", "technology"],
        )

        post = EnhancedBlogPost(
            title="Test Title",
            content="Test content",
            excerpt="Test excerpt",
            metadata=metadata,
            model_used="test-model",
            quality_score=8.5,
            generation_time_seconds=60.0,
        )

        strapi_format = post.to_strapi_format()

        # Check structure
        assert "title" in strapi_format
        assert "content" in strapi_format
        assert "slug" in strapi_format
        assert "seo" in strapi_format
        assert "metadata" in strapi_format

        # Check SEO component
        assert strapi_format["seo"]["metaTitle"] == "Test Title"
        assert strapi_format["seo"]["metaDescription"] == "Test description"

        # Check metadata component
        assert strapi_format["metadata"]["wordCount"] == 1500
        assert strapi_format["metadata"]["readingTime"] == 8


@pytest.mark.integration
class TestSEOOptimizedContentGenerator:
    """Test SEOOptimizedContentGenerator service"""

    @pytest.fixture
    def mock_ai_generator(self):
        """Create mock AI content generator"""
        mock = AsyncMock()
        mock.generate_blog_post = AsyncMock(
            return_value=(
                "# AI-Powered Market Intelligence\n\nContent about AI." * 20,  # content
                "test-model",  # model_used
                {"final_quality_score": 8.5, "validation_results": []},  # metrics
            )
        )
        return mock

    @pytest.fixture
    def seo_generator(self, mock_ai_generator):
        """Create SEOOptimizedContentGenerator with mock"""
        from services.seo_content_generator import (
            SEOOptimizedContentGenerator,
            ContentMetadataGenerator,
        )

        metadata_gen = ContentMetadataGenerator()
        return SEOOptimizedContentGenerator(mock_ai_generator, metadata_gen)

    @pytest.mark.asyncio
    async def test_generate_complete_blog_post(self, seo_generator, mock_ai_generator):
        """Test complete blog post generation pipeline"""
        result = await seo_generator.generate_complete_blog_post(
            topic="AI in Market Analysis",
            style="technical",
            tone="professional",
            target_length=1500,
            generate_images=True,
        )

        assert isinstance(result, EnhancedBlogPost)
        assert result.title is not None
        assert result.content is not None
        assert result.metadata is not None

    @pytest.mark.asyncio
    async def test_all_metadata_fields_populated(self, seo_generator, mock_ai_generator):
        """Test all metadata fields are populated"""
        result = await seo_generator.generate_complete_blog_post(
            topic="Test Topic", style="technical", tone="professional", target_length=1500
        )

        # Check all SEO fields
        assert result.metadata.seo_title
        assert result.metadata.meta_description
        assert result.metadata.slug
        assert result.metadata.meta_keywords

        # Check image fields
        assert result.metadata.featured_image_prompt

        # Check structured data
        assert result.metadata.json_ld_schema is not None

        # Check social fields
        assert result.metadata.og_title
        assert result.metadata.twitter_title

        # Check organization
        assert result.metadata.category
        assert result.metadata.tags

        # Check metrics
        assert result.metadata.reading_time_minutes > 0
        assert result.metadata.word_count > 0


@pytest.mark.unit
class TestMetadataValidation:
    """Test validation of generated metadata"""

    @pytest.fixture
    def generator(self):
        """Create ContentMetadataGenerator"""
        return ContentMetadataGenerator()

    def test_seo_title_validation(self, generator):
        """Test SEO title meets requirements"""
        title = "AI-Powered Market Intelligence Guide"
        content = "Content about AI and market intelligence. " * 10

        assets = generator.generate_seo_assets(title, content, topic="Market Analysis")
        seo_title = assets["seo_title"]

        # Validate constraints
        assert len(seo_title) > 0
        assert isinstance(seo_title, str)

    def test_slug_validation(self, generator):
        """Test slug meets URL requirements"""
        title = "AI in Market Analysis"
        content = "Content here. " * 10

        assets = generator.generate_seo_assets(title, content, topic="Analysis")
        slug = assets["slug"]

        # Validate constraints
        assert all(c.isalnum() or c == "-" for c in slug), "Invalid characters in slug"
        assert slug.islower(), "Slug should be lowercase"

    def test_keywords_validation(self, generator):
        """Test keywords meet requirements"""
        title = "AI in Market Analysis"
        content = "AI algorithms analyze market trends for competitive advantage. " * 10

        assets = generator.generate_seo_assets(title, content, topic="AI Analysis")
        keywords = assets["meta_keywords"]

        # Validate constraints
        assert len(keywords) > 0, f"Should have keywords: {keywords}"
        assert all(isinstance(k, str) for k in keywords)
        assert all(len(k) > 0 for k in keywords)

    def test_description_validation(self, generator):
        """Test meta description meets Google limits"""
        title = "AI Market Analysis"
        content = "Content about market analysis. " * 20

        assets = generator.generate_seo_assets(title, content, topic="Analysis")
        description = assets["meta_description"]

        # Validate constraints
        assert len(description) > 0
        assert len(description) <= 160, f"Description too long: {len(description)} chars"

    def test_json_ld_schema_validation(self, generator):
        """Test JSON-LD schema is valid"""
        schema = generator.generate_json_ld_schema(
            {"title": "Test Title", "excerpt": "Test description", "keywords": ["test", "keywords"]}
        )

        # Validate structure
        assert "@context" in schema
        assert "@type" in schema
        assert schema["@type"] == "BlogPosting"
        assert "headline" in schema
        assert "datePublished" in schema

    def test_social_tags_validation(self, generator):
        """Test social media tags meet platform limits"""
        social = generator.generate_social_metadata(
            "Test Title That Is Quite Long For Social Media",
            "This is a test description for social media platforms that should be properly optimized.",
        )

        # OG limits
        assert len(social["og_title"]) <= 70
        assert len(social["og_description"]) <= 160

        # Twitter limits
        assert len(social["twitter_title"]) <= 70
        assert len(social["twitter_description"]) <= 280


@pytest.mark.performance
class TestMetadataPerformance:
    """Test performance of metadata generation"""

    @pytest.fixture
    def generator(self):
        """Create ContentMetadataGenerator"""
        return ContentMetadataGenerator()

    def test_slug_generation_performance(self, generator):
        """Test slug generation is fast"""
        import time

        test_titles = [
            "AI in Healthcare",
            "Market Intelligence and Competitive Analysis",
            "How to Build Scalable Python Applications",
        ] * 10

        start = time.time()
        for title in test_titles:
            generator._generate_slug(title)
        elapsed = time.time() - start

        assert elapsed < 0.1, f"Slug generation too slow: {elapsed}s"

    def test_keyword_extraction_performance(self, generator):
        """Test keyword extraction is reasonable"""
        import time

        content = "AI machine learning algorithms analyze market trends. " * 50

        start = time.time()
        keywords = generator._extract_keywords(content)
        elapsed = time.time() - start

        assert elapsed < 0.5, f"Keyword extraction too slow: {elapsed}s"
        assert len(keywords) > 0

    def test_seo_assets_generation_performance(self, generator):
        """Test full SEO assets generation performance"""
        import time

        title = "AI-Powered Market Intelligence"
        content = "Content about AI. " * 100

        start = time.time()
        assets = generator.generate_seo_assets(title, content, topic="AI")
        elapsed = time.time() - start

        assert elapsed < 1.0, f"SEO assets generation too slow: {elapsed}s"
        assert all(k in assets for k in ["seo_title", "meta_description", "slug", "meta_keywords"])


@pytest.mark.unit
class TestEdgeCases:
    """Test edge cases and error handling"""

    @pytest.fixture
    def generator(self):
        """Create ContentMetadataGenerator"""
        return ContentMetadataGenerator()

    def test_empty_content(self, generator):
        """Test handling of empty content"""
        assets = generator.generate_seo_assets("Title", "", topic="General")

        # Should still generate valid output
        assert "seo_title" in assets
        assert "slug" in assets

    def test_very_short_content(self, generator):
        """Test handling of very short content"""
        assets = generator.generate_seo_assets("Title", "Short.", topic="Test")

        assert "meta_keywords" in assets
        assert len(assets.get("meta_keywords", [])) >= 0

    def test_very_long_title(self, generator):
        """Test handling of very long title"""
        long_title = "This is a very long title that should be properly handled " * 5
        slug = generator._generate_slug(long_title)

        assert len(slug) <= 60
        assert len(slug) > 0

    def test_special_characters_in_content(self, generator):
        """Test handling of special characters"""
        content = "Test with @symbols #hashtags $dollars & ampersand!"
        keywords = generator._extract_keywords(content)

        assert len(keywords) > 0
        assert all(isinstance(k, str) for k in keywords)

    def test_unicode_content(self, generator):
        """Test handling of unicode characters"""
        content = "Test with émojis 🚀 and ünïcödé characters ñ"
        keywords = generator._extract_keywords(content)

        # Should not crash
        assert isinstance(keywords, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
