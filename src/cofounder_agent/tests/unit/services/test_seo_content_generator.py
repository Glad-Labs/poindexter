"""
Unit tests for services/seo_content_generator.py

Tests ContentMetadata, EnhancedBlogPost, ContentMetadataGenerator (slug, meta
description, keywords, reading time, social metadata, category/tags, JSON-LD),
SEOOptimizedContentGenerator, and the factory function.
All tests are pure / synchronous — no LLM calls.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.seo_content_generator import (
    ContentMetadata,
    ContentMetadataGenerator,
    EnhancedBlogPost,
    SEOOptimizedContentGenerator,
    get_seo_content_generator,
)


# ---------------------------------------------------------------------------
# ContentMetadata
# ---------------------------------------------------------------------------


class TestContentMetadata:
    def test_default_fields(self):
        meta = ContentMetadata()
        assert meta.seo_title == ""
        assert meta.meta_description == ""
        assert meta.slug == ""
        assert meta.meta_keywords == []
        assert meta.reading_time_minutes == 0
        assert meta.word_count == 0
        assert meta.tags == []
        assert meta.internal_links == []

    def test_assign_custom_fields(self):
        meta = ContentMetadata(seo_title="My Title", slug="my-title", word_count=500)
        assert meta.seo_title == "My Title"
        assert meta.slug == "my-title"
        assert meta.word_count == 500


# ---------------------------------------------------------------------------
# EnhancedBlogPost
# ---------------------------------------------------------------------------


class TestEnhancedBlogPost:
    def _make_post(self) -> EnhancedBlogPost:
        meta = ContentMetadata(
            seo_title="SEO Title",
            meta_description="Meta desc",
            slug="my-post",
            meta_keywords=["ai", "python"],
            category="AI & Technology",
            tags=["ai", "python"],
            word_count=800,
            reading_time_minutes=4,
        )
        return EnhancedBlogPost(
            title="My Blog Post",
            content="Content here...",
            excerpt="Short excerpt",
            metadata=meta,
            model_used="gpt-4",
            quality_score=8.5,
            generation_time_seconds=3.2,
        )

    def test_to_strapi_format_contains_title(self):
        post = self._make_post()
        data = post.to_strapi_format()
        assert data["title"] == "My Blog Post"

    def test_to_strapi_format_contains_slug(self):
        post = self._make_post()
        data = post.to_strapi_format()
        assert data["slug"] == "my-post"

    def test_to_strapi_format_contains_seo_block(self):
        post = self._make_post()
        data = post.to_strapi_format()
        assert "seo" in data
        assert data["seo"]["metaTitle"] == "SEO Title"
        assert data["seo"]["metaDescription"] == "Meta desc"

    def test_to_strapi_format_contains_metadata_block(self):
        post = self._make_post()
        data = post.to_strapi_format()
        assert "metadata" in data
        assert data["metadata"]["wordCount"] == 800
        assert data["metadata"]["readingTime"] == 4
        assert data["metadata"]["quality_score"] == 8.5

    def test_to_strapi_format_keywords_joined(self):
        post = self._make_post()
        data = post.to_strapi_format()
        assert data["seo"]["keywords"] == "ai,python"


# ---------------------------------------------------------------------------
# ContentMetadataGenerator._generate_slug
# ---------------------------------------------------------------------------


class TestGenerateSlug:
    def setup_method(self):
        self.gen = ContentMetadataGenerator()

    def test_lowercase(self):
        assert self.gen._generate_slug("Hello World") == "hello-world"

    def test_replaces_spaces_with_dash(self):
        assert "-" in self.gen._generate_slug("AI and Technology")

    def test_removes_special_chars(self):
        slug = self.gen._generate_slug("Hello, World! 2025")
        assert "," not in slug
        assert "!" not in slug

    def test_no_leading_trailing_dashes(self):
        slug = self.gen._generate_slug("  My Post  ")
        assert not slug.startswith("-")
        assert not slug.endswith("-")

    def test_max_60_chars(self):
        long_title = "A" * 200
        assert len(self.gen._generate_slug(long_title)) <= 60


# ---------------------------------------------------------------------------
# ContentMetadataGenerator._generate_meta_description
# ---------------------------------------------------------------------------


class TestGenerateMetaDescription:
    def setup_method(self):
        self.gen = ContentMetadataGenerator()

    def test_short_excerpt_returned_as_is(self):
        excerpt = "Short excerpt under 155 chars."
        result = self.gen._generate_meta_description("Title", excerpt)
        assert result == excerpt

    def test_long_excerpt_truncated_with_ellipsis(self):
        excerpt = "X" * 200
        result = self.gen._generate_meta_description("Title", excerpt)
        assert len(result) <= 160
        assert result.endswith("...")

    def test_combined_under_155_chars(self):
        title = "Short"
        excerpt = "A" * 100
        result = self.gen._generate_meta_description(title, excerpt)
        assert len(result) <= 155


# ---------------------------------------------------------------------------
# ContentMetadataGenerator.generate_seo_assets
# ---------------------------------------------------------------------------


class TestGenerateSEOAssets:
    def setup_method(self):
        self.gen = ContentMetadataGenerator()

    def test_returns_seo_title(self):
        result = self.gen.generate_seo_assets("AI Trends", "Content about AI.", "AI")
        assert result["seo_title"] == "AI Trends"

    def test_returns_slug(self):
        result = self.gen.generate_seo_assets("AI Trends 2025", "Content.", "AI")
        assert "ai" in result["slug"]

    def test_returns_meta_description(self):
        result = self.gen.generate_seo_assets("AI Trends", "Content.", "AI")
        assert "meta_description" in result
        assert isinstance(result["meta_description"], str)

    def test_returns_keywords_list(self):
        content = "machine learning machine learning neural networks neural networks python"
        result = self.gen.generate_seo_assets("ML Guide", content, "ML")
        assert "meta_keywords" in result
        assert isinstance(result["meta_keywords"], list)

    def test_returns_excerpt(self):
        result = self.gen.generate_seo_assets("Title", "First paragraph.\n\nSecond paragraph.", "")
        assert "excerpt" in result
        assert isinstance(result["excerpt"], str)


# ---------------------------------------------------------------------------
# ContentMetadataGenerator.calculate_reading_time
# ---------------------------------------------------------------------------


class TestCalculateReadingTime:
    def setup_method(self):
        self.gen = ContentMetadataGenerator()

    def test_minimum_1_minute(self):
        assert self.gen.calculate_reading_time("short") >= 1

    def test_200_words_is_1_minute(self):
        content = " ".join(["word"] * 200)
        assert self.gen.calculate_reading_time(content) == 1

    def test_1000_words_is_5_minutes(self):
        content = " ".join(["word"] * 1000)
        assert self.gen.calculate_reading_time(content) == 5


# ---------------------------------------------------------------------------
# ContentMetadataGenerator.generate_social_metadata
# ---------------------------------------------------------------------------


class TestGenerateSocialMetadata:
    def setup_method(self):
        self.gen = ContentMetadataGenerator()

    def test_contains_og_fields(self):
        result = self.gen.generate_social_metadata("Title", "Excerpt here")
        assert "og_title" in result
        assert "og_description" in result

    def test_og_title_truncated_to_70(self):
        long_title = "T" * 100
        result = self.gen.generate_social_metadata(long_title, "excerpt")
        assert len(result["og_title"]) <= 70

    def test_twitter_card_summary_when_no_image(self):
        result = self.gen.generate_social_metadata("Title", "Excerpt")
        assert result["twitter_card"] == "summary"

    def test_twitter_card_large_image_when_image_provided(self):
        result = self.gen.generate_social_metadata("Title", "Excerpt", image_url="http://img.png")
        assert result["twitter_card"] == "summary_large_image"

    def test_og_image_empty_when_none(self):
        result = self.gen.generate_social_metadata("Title", "Excerpt")
        assert result["og_image"] == ""


# ---------------------------------------------------------------------------
# ContentMetadataGenerator.generate_category_and_tags
# ---------------------------------------------------------------------------


class TestGenerateCategoryAndTags:
    def setup_method(self):
        self.gen = ContentMetadataGenerator()

    def test_ai_content_categorized_correctly(self):
        content = "This article covers machine learning and neural networks and automation algorithms."
        result = self.gen.generate_category_and_tags(content, "AI")
        assert result["category"] == "AI & Technology"

    def test_compliance_content_categorized_correctly(self):
        content = "regulatory compliance governance legal requirements regulatory"
        result = self.gen.generate_category_and_tags(content, "compliance")
        assert result["category"] == "Compliance"

    def test_unknown_content_defaults_to_general(self):
        result = self.gen.generate_category_and_tags("hello world test", "misc")
        assert result["category"] == "General"

    def test_returns_tags_list(self):
        content = "machine learning machine learning python python algorithms algorithms"
        result = self.gen.generate_category_and_tags(content, "AI")
        assert "tags" in result
        assert isinstance(result["tags"], list)


# ---------------------------------------------------------------------------
# ContentMetadataGenerator.generate_json_ld_schema
# ---------------------------------------------------------------------------


class TestGenerateJsonLdSchema:
    def setup_method(self):
        self.gen = ContentMetadataGenerator()

    def test_schema_type(self):
        schema = self.gen.generate_json_ld_schema(
            {"title": "My Post", "excerpt": "Short desc", "keywords": ["ai"]}
        )
        assert schema["@type"] == "BlogPosting"
        assert schema["@context"] == "https://schema.org"

    def test_headline_field(self):
        schema = self.gen.generate_json_ld_schema({"title": "My Post", "excerpt": "", "keywords": []})
        assert schema["headline"] == "My Post"

    def test_author_is_glad_labs(self):
        schema = self.gen.generate_json_ld_schema({"title": "X", "excerpt": "", "keywords": []})
        assert schema["author"]["name"] == "Glad Labs"

    def test_keywords_joined(self):
        schema = self.gen.generate_json_ld_schema(
            {"title": "X", "excerpt": "", "keywords": ["ai", "python"]}
        )
        assert "ai" in schema["keywords"]
        assert "python" in schema["keywords"]


# ---------------------------------------------------------------------------
# SEOOptimizedContentGenerator
# ---------------------------------------------------------------------------


class TestSEOOptimizedContentGenerator:
    def _make_generator(self) -> SEOOptimizedContentGenerator:
        mock_ai = MagicMock()
        return SEOOptimizedContentGenerator(ai_content_generator=mock_ai)

    def test_metadata_gen_assigned(self):
        gen = self._make_generator()
        assert isinstance(gen.metadata_gen, ContentMetadataGenerator)

    def test_custom_metadata_gen_accepted(self):
        custom_meta = ContentMetadataGenerator()
        mock_ai = MagicMock()
        gen = SEOOptimizedContentGenerator(mock_ai, metadata_generator=custom_meta)
        assert gen.metadata_gen is custom_meta


# ---------------------------------------------------------------------------
# get_seo_content_generator factory
# ---------------------------------------------------------------------------


class TestGetSEOContentGeneratorFactory:
    def test_returns_seo_generator_instance(self):
        mock_ai = MagicMock()
        gen = get_seo_content_generator(mock_ai)
        assert isinstance(gen, SEOOptimizedContentGenerator)

    def test_ai_generator_assigned(self):
        mock_ai = MagicMock()
        gen = get_seo_content_generator(mock_ai)
        assert gen.ai_generator is mock_ai
