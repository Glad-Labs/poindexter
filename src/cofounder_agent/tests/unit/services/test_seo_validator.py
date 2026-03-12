"""
Unit tests for SEOValidator service.

All tests are pure-function — zero DB, LLM, or network calls.
"""

import pytest

from services.seo_validator import (
    KeywordDensityStatus,
    SEOValidator,
    SEOValidationResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def validator() -> SEOValidator:
    return SEOValidator()


def _make_content(words: int = 200, keyword: str = "python", repeat: int = 2) -> str:
    """Build a minimal article with an H1, some paragraphs, and keyword repetitions."""
    body_words = ["lorem"] * words
    for i in range(repeat):
        body_words[i * (words // (repeat + 1))] = keyword
    paragraph = " ".join(body_words[:100])
    paragraph2 = " ".join(body_words[100:])
    return f"# Main Heading\n\n{paragraph}\n\n{paragraph2}"


# ---------------------------------------------------------------------------
# Title validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTitleValidation:
    def test_short_title_is_valid(self, validator):
        result = validator.validate(
            content=_make_content(),
            title="Short Title",
            meta_description="A valid meta description under 155 chars.",
            keywords=["python"],
        )
        assert result.title_valid is True
        assert result.title_char_count == len("Short Title")

    def test_title_at_exact_max_is_valid(self, validator):
        title = "A" * SEOValidator.TITLE_MAX_CHARS  # 60 chars
        result = validator.validate(
            content=_make_content(),
            title=title,
            meta_description="Valid meta.",
            keywords=["python"],
        )
        assert result.title_valid is True

    def test_title_over_max_generates_warning(self, validator):
        # Titles between TITLE_MAX_CHARS+1 and 80 chars trigger a warning, not a hard fail.
        # Hard-fail threshold is 80 chars (validator internal rule).
        title = "A" * (SEOValidator.TITLE_MAX_CHARS + 1)  # 61 chars
        result = validator.validate(
            content=_make_content(),
            title=title,
            meta_description="Valid meta.",
            keywords=["python"],
        )
        assert result.title_char_count > SEOValidator.TITLE_MAX_CHARS
        warning_text = " ".join(result.warnings).lower()
        assert "truncated" in warning_text or "title" in warning_text

    def test_title_hard_fail_over_80_chars(self, validator):
        # Hard-fail at 81+ characters
        title = "A" * 81
        result = validator.validate(
            content=_make_content(),
            title=title,
            meta_description="Valid meta.",
            keywords=["python"],
        )
        assert result.title_valid is False


# ---------------------------------------------------------------------------
# Meta description validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMetaDescriptionValidation:
    def test_meta_within_limit_is_valid(self, validator):
        result = validator.validate(
            content=_make_content(),
            title="Valid Title",
            meta_description="Short meta.",
            keywords=["python"],
        )
        assert result.meta_valid is True

    def test_meta_at_exact_max_is_valid(self, validator):
        meta = "B" * SEOValidator.META_MAX_CHARS  # 155 chars
        result = validator.validate(
            content=_make_content(),
            title="Valid Title",
            meta_description=meta,
            keywords=["python"],
        )
        assert result.meta_valid is True

    def test_meta_over_max_is_invalid(self, validator):
        meta = "B" * (SEOValidator.META_MAX_CHARS + 1)
        result = validator.validate(
            content=_make_content(),
            title="Valid Title",
            meta_description=meta,
            keywords=["python"],
        )
        assert result.meta_valid is False


# ---------------------------------------------------------------------------
# Slug validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSlugValidation:
    def test_valid_slug(self, validator):
        result = validator.validate(
            content=_make_content(),
            title="Valid Title",
            meta_description="Valid meta.",
            keywords=["python"],
            slug="my-valid-slug",
        )
        assert result.slug_valid is True

    def test_slug_with_uppercase_is_invalid(self, validator):
        result = validator.validate(
            content=_make_content(),
            title="Valid Title",
            meta_description="Valid meta.",
            keywords=["python"],
            slug="Invalid-Slug",
        )
        assert result.slug_valid is False

    def test_slug_with_spaces_is_invalid(self, validator):
        result = validator.validate(
            content=_make_content(),
            title="Valid Title",
            meta_description="Valid meta.",
            keywords=["python"],
            slug="slug with spaces",
        )
        assert result.slug_valid is False

    def test_slug_over_max_length_is_invalid(self, validator):
        long_slug = "a-" * 40  # 80 chars > 75
        result = validator.validate(
            content=_make_content(),
            title="Valid Title",
            meta_description="Valid meta.",
            keywords=["python"],
            slug=long_slug,
        )
        assert result.slug_valid is False


# ---------------------------------------------------------------------------
# Keyword density
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestKeywordDensity:
    def test_keyword_absent_is_too_low(self, validator):
        content = "# Heading\n\n" + " ".join(["lorem"] * 200)
        result = validator.validate(
            content=content,
            title="Title",
            meta_description="Meta.",
            keywords=["python"],
            primary_keyword="python",
        )
        # python doesn't appear — density should be too_low or keyword absent
        kv = result.keyword_validations[0]
        assert kv.status == KeywordDensityStatus.TOO_LOW or not kv.appears_in_content

    def test_keyword_present_at_optimal_density(self, validator):
        # 200 words, 2 keyword appearances ≈ 1% — optimal
        content = _make_content(words=200, keyword="python", repeat=2)
        result = validator.validate(
            content=content,
            title="Python Guide",
            meta_description="A guide about python.",
            keywords=["python"],
            primary_keyword="python",
        )
        kv = result.keyword_validations[0]
        assert kv.appears_in_content is True
        assert kv.status == KeywordDensityStatus.OPTIMAL

    def test_returns_validation_result_type(self, validator):
        result = validator.validate(
            content=_make_content(),
            title="Title",
            meta_description="Meta.",
            keywords=[],
        )
        assert isinstance(result, SEOValidationResult)


# ---------------------------------------------------------------------------
# H1 extraction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestH1Extraction:
    def test_h1_extracted_from_markdown(self, validator):
        content = "# My Main Heading\n\nSome content here lorem ipsum dolor sit."
        result = validator.validate(
            content=content,
            title="Title",
            meta_description="Meta.",
            keywords=[],
        )
        assert result.h1_text is not None
        assert "My Main Heading" in result.h1_text

    def test_no_h1_returns_none_or_invalid(self, validator):
        content = "No heading here. Just plain text paragraphs."
        result = validator.validate(
            content=content,
            title="Title",
            meta_description="Meta.",
            keywords=[],
        )
        # h1 is either None or h1_valid is False when absent
        assert result.h1_text is None or result.h1_valid is False


# ---------------------------------------------------------------------------
# Forbidden headings
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestForbiddenHeadings:
    def test_forbidden_heading_generates_warning(self, validator):
        content = "# My Article\n\n## Introduction\n\nSome content here lorem ipsum."
        result = validator.validate(
            content=content,
            title="Title",
            meta_description="Meta.",
            keywords=[],
        )
        # 'introduction' is in FORBIDDEN_HEADINGS
        combined = " ".join(result.warnings + result.errors).lower()
        assert "introduction" in combined or len(result.warnings) > 0
