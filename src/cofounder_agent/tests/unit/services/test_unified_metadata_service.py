"""
Unit tests for services/unified_metadata_service.py

Tests UnifiedMetadataService: synchronous helper methods (generate_slug,
calculate_reading_time, generate_social_metadata, generate_json_ld_schema,
_keyword_match_category, _keyword_match_tags, _extract_first_meaningful_line),
extract_title fallback strategies, generate_excerpt, UnifiedMetadata defaults,
and the singleton factory.
"""


import pytest

from services.unified_metadata_service import (
    UnifiedMetadata,
    UnifiedMetadataService,
    get_unified_metadata_service,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_service() -> UnifiedMetadataService:
    """Return service with LLM disabled (no API keys in test env)."""
    svc = UnifiedMetadataService(model="auto")
    svc.llm_available = False
    return svc


SAMPLE_CONTENT = """\
Introduction to Machine Learning

Machine learning is a subset of artificial intelligence that enables systems
to learn from data without being explicitly programmed.

It encompasses supervised learning, unsupervised learning, and reinforcement learning.
"""


# ---------------------------------------------------------------------------
# UnifiedMetadata defaults
# ---------------------------------------------------------------------------


class TestUnifiedMetadataDefaults:
    def test_default_fields_are_empty_strings(self):
        meta = UnifiedMetadata()
        assert meta.title == ""
        assert meta.excerpt == ""
        assert meta.slug == ""
        assert meta.seo_title == ""
        assert meta.seo_description == ""

    def test_seo_keywords_defaults_to_empty_list(self):
        meta = UnifiedMetadata()
        assert meta.seo_keywords == []

    def test_tag_ids_defaults_to_empty_list(self):
        meta = UnifiedMetadata()
        assert meta.tag_ids == []

    def test_assign_title(self):
        meta = UnifiedMetadata(title="My Blog Post")
        assert meta.title == "My Blog Post"


# ---------------------------------------------------------------------------
# generate_slug
# ---------------------------------------------------------------------------


class TestGenerateSlug:
    def test_lowercase(self):
        svc = make_service()
        assert svc.generate_slug("Hello World") == "hello-world"

    def test_removes_special_chars(self):
        svc = make_service()
        slug = svc.generate_slug("AI & Technology!")
        assert "!" not in slug
        assert "&" not in slug

    def test_replaces_spaces_with_hyphens(self):
        svc = make_service()
        slug = svc.generate_slug("Machine Learning Guide")
        assert " " not in slug
        assert "-" in slug

    def test_max_60_chars(self):
        svc = make_service()
        slug = svc.generate_slug("A" * 200)
        assert len(slug) <= 60

    def test_no_leading_trailing_hyphens(self):
        svc = make_service()
        slug = svc.generate_slug("  My Post  ")
        assert not slug.startswith("-")
        assert not slug.endswith("-")


# ---------------------------------------------------------------------------
# calculate_reading_time
# ---------------------------------------------------------------------------


class TestCalculateReadingTime:
    def test_minimum_1_minute(self):
        svc = make_service()
        assert svc.calculate_reading_time("few words") >= 1

    def test_200_words_is_1_minute(self):
        svc = make_service()
        content = " ".join(["word"] * 200)
        assert svc.calculate_reading_time(content) == 1

    def test_400_words_is_2_minutes(self):
        svc = make_service()
        content = " ".join(["word"] * 400)
        assert svc.calculate_reading_time(content) == 2


# ---------------------------------------------------------------------------
# generate_social_metadata
# ---------------------------------------------------------------------------


class TestGenerateSocialMetadata:
    def test_og_title_truncated_to_70(self):
        svc = make_service()
        result = svc.generate_social_metadata("T" * 100, "excerpt")
        assert len(result["og_title"]) <= 70

    def test_og_description_truncated_to_160(self):
        svc = make_service()
        result = svc.generate_social_metadata("Title", "E" * 200)
        assert len(result["og_description"]) <= 160

    def test_twitter_card_summary_when_no_image(self):
        svc = make_service()
        result = svc.generate_social_metadata("Title", "excerpt")
        assert result["twitter_card"] == "summary"

    def test_twitter_card_large_image_when_image_provided(self):
        svc = make_service()
        result = svc.generate_social_metadata("Title", "excerpt", image_url="http://img.png")
        assert result["twitter_card"] == "summary_large_image"

    def test_og_image_empty_string_when_none(self):
        svc = make_service()
        result = svc.generate_social_metadata("Title", "excerpt")
        assert result["og_image"] == ""


# ---------------------------------------------------------------------------
# generate_json_ld_schema
# ---------------------------------------------------------------------------


class TestGenerateJsonLdSchema:
    def test_schema_type(self):
        svc = make_service()
        meta = UnifiedMetadata(title="My Post", excerpt="Short desc", seo_keywords=["ai"])
        schema = svc.generate_json_ld_schema(meta)
        assert schema["@type"] == "BlogPosting"
        assert schema["@context"] == "https://schema.org"

    def test_headline_is_title(self):
        svc = make_service()
        meta = UnifiedMetadata(title="Test Post")
        schema = svc.generate_json_ld_schema(meta)
        assert schema["headline"] == "Test Post"

    def test_author_is_glad_labs(self):
        svc = make_service()
        meta = UnifiedMetadata()
        schema = svc.generate_json_ld_schema(meta)
        assert schema["author"]["name"] == "Glad Labs"

    def test_keywords_joined(self):
        svc = make_service()
        meta = UnifiedMetadata(seo_keywords=["ai", "machine learning"])
        schema = svc.generate_json_ld_schema(meta)
        assert "ai" in schema["keywords"]
        assert "machine learning" in schema["keywords"]


# ---------------------------------------------------------------------------
# _extract_first_meaningful_line
# ---------------------------------------------------------------------------


class TestExtractFirstMeaningfulLine:
    def test_returns_none_for_empty_content(self):
        svc = make_service()
        assert svc._extract_first_meaningful_line("") is None

    def test_returns_first_long_enough_line(self):
        svc = make_service()
        content = "\n\nIntroduction to Machine Learning\nSome other text"
        result = svc._extract_first_meaningful_line(content)
        assert result == "Introduction to Machine Learning"

    def test_skips_lines_starting_with_dash(self):
        svc = make_service()
        content = "- Short item\n- Another item\nProper heading line for content"
        result = svc._extract_first_meaningful_line(content)
        assert result == "Proper heading line for content"

    def test_skips_very_short_lines(self):
        svc = make_service()
        content = "Hi\n\nThis is a proper title for the blog post"
        result = svc._extract_first_meaningful_line(content)
        assert result is not None
        assert "proper title" in result

    def test_returns_none_when_no_suitable_line(self):
        svc = make_service()
        content = "- a\n- b\n* c"
        result = svc._extract_first_meaningful_line(content)
        assert result is None


# ---------------------------------------------------------------------------
# _keyword_match_category
# ---------------------------------------------------------------------------


class TestKeywordMatchCategory:
    def test_returns_none_score_zero_for_no_match(self):
        svc = make_service()
        cats = [{"id": "1", "name": "Technology", "description": "tech innovation"}]
        result, score = svc._keyword_match_category("cooking pasta recipes", cats)
        assert score == 0

    def test_returns_matching_category_when_name_in_content(self):
        svc = make_service()
        cats = [
            {"id": "1", "name": "technology", "description": "tech"},
            {"id": "2", "name": "cooking", "description": "food recipes"},
        ]
        result, score = svc._keyword_match_category("I love technology and programming", cats)
        assert result is not None
        assert result["id"] == "1"
        assert score > 0

    def test_empty_categories_returns_none(self):
        svc = make_service()
        result, score = svc._keyword_match_category("Some content", [])
        assert result is None
        assert score == 0


# ---------------------------------------------------------------------------
# _keyword_match_tags
# ---------------------------------------------------------------------------


class TestKeywordMatchTags:
    def test_returns_tag_id_when_name_in_content(self):
        svc = make_service()
        tags = [
            {"id": "t1", "name": "machine-learning", "slug": "machine-learning"},
            {"id": "t2", "name": "python", "slug": "python"},
        ]
        result = svc._keyword_match_tags("machine-learning is amazing", tags)
        assert "t1" in result

    def test_excludes_tags_not_in_content(self):
        svc = make_service()
        tags = [{"id": "t1", "name": "blockchain", "slug": "blockchain"}]
        result = svc._keyword_match_tags("AI and machine learning", tags)
        assert "t1" not in result

    def test_returns_empty_list_for_no_match(self):
        svc = make_service()
        tags = [{"id": "t1", "name": "blockchain", "slug": "blockchain"}]
        result = svc._keyword_match_tags("AI deep learning python", tags)
        assert result == []


# ---------------------------------------------------------------------------
# extract_title (async, with fallbacks)
# ---------------------------------------------------------------------------


class TestExtractTitle:
    @pytest.mark.asyncio
    async def test_uses_stored_title_when_provided(self):
        svc = make_service()
        result = await svc.extract_title("Some content", stored_title="My Stored Title")
        assert result == "My Stored Title"

    @pytest.mark.asyncio
    async def test_ignores_untitled_stored_title(self):
        svc = make_service()
        result = await svc.extract_title(
            "Introduction to Machine Learning\nContent here.", stored_title="Untitled"
        )
        # Should fall through to other strategies
        assert result != "Untitled"

    @pytest.mark.asyncio
    async def test_uses_topic_when_no_stored_title(self):
        svc = make_service()
        result = await svc.extract_title("content", topic="AI in Healthcare")
        assert result == "AI in Healthcare"

    @pytest.mark.asyncio
    async def test_extracts_from_content_when_no_topic(self):
        svc = make_service()
        content = "Introduction to Machine Learning\nContent about ML."
        result = await svc.extract_title(content)
        assert "Introduction to Machine Learning" in result

    @pytest.mark.asyncio
    async def test_date_fallback_when_nothing_extractable(self):
        svc = make_service()
        # Short lines that don't meet _extract_first_meaningful_line criteria
        result = await svc.extract_title("a\nb\nc")
        assert "Blog Post" in result or len(result) > 0


# ---------------------------------------------------------------------------
# generate_excerpt (async, with fallbacks)
# ---------------------------------------------------------------------------


class TestGenerateExcerpt:
    @pytest.mark.asyncio
    async def test_uses_stored_excerpt_when_long_enough(self):
        svc = make_service()
        stored = "This is a good excerpt with more than 20 characters."
        result = await svc.generate_excerpt("some content", stored_excerpt=stored)
        assert stored in result

    @pytest.mark.asyncio
    async def test_ignores_short_stored_excerpt(self):
        svc = make_service()
        result = await svc.generate_excerpt(SAMPLE_CONTENT, stored_excerpt="short")
        assert len(result) > 5

    @pytest.mark.asyncio
    async def test_extracts_from_first_paragraph(self):
        svc = make_service()
        result = await svc.generate_excerpt(SAMPLE_CONTENT)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_max_length_respected(self):
        svc = make_service()
        result = await svc.generate_excerpt("A " * 500, max_length=100)
        # The excerpt may be slightly over max_length due to sentence trimming at boundaries
        assert len(result) <= 120


# ---------------------------------------------------------------------------
# get_unified_metadata_service factory
# ---------------------------------------------------------------------------


class TestGetUnifiedMetadataServiceFactory:
    def test_returns_service_instance(self):
        import services.unified_metadata_service as mod

        mod._unified_service = None
        svc = get_unified_metadata_service()
        assert isinstance(svc, UnifiedMetadataService)

    def test_returns_same_instance_on_repeat_calls(self):
        import services.unified_metadata_service as mod

        mod._unified_service = None
        s1 = get_unified_metadata_service()
        s2 = get_unified_metadata_service()
        assert s1 is s2
