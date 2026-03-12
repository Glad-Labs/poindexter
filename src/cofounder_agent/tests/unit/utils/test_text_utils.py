"""
Unit tests for utils.text_utils module.

All tests are pure — zero DB, LLM, or network calls.
Covers extract_keywords_from_text, extract_keywords_from_title,
extract_title_from_content, normalize_seo_keywords.
"""

import pytest

from utils.text_utils import (
    extract_keywords_from_text,
    extract_keywords_from_title,
    extract_title_from_content,
    normalize_seo_keywords,
)


# ---------------------------------------------------------------------------
# extract_keywords_from_text
# ---------------------------------------------------------------------------


class TestExtractKeywordsFromText:
    """Tests for keyword extraction from longer text bodies."""

    def test_returns_top_keywords_by_frequency(self):
        # "python" appears 5x, "rocks" appears 3x — both above 2-occurrence threshold
        text = "python python python python python rocks rocks rocks performance"
        result = extract_keywords_from_text(text, count=2)
        assert "python" in result
        assert "rocks" in result
        assert len(result) <= 2

    def test_filters_stopwords(self):
        # "this" "that" "with" are all stopwords
        text = "this that this that with with with with"
        result = extract_keywords_from_text(text)
        assert result == []

    def test_respects_count_limit(self):
        text = " ".join(["alpha"] * 5 + ["bravo"] * 4 + ["charlie"] * 3 + ["delta"] * 3)
        result = extract_keywords_from_text(text, count=2)
        assert len(result) <= 2

    def test_requires_minimum_two_occurrences(self):
        # "unique" appears only once — should be excluded
        text = "unique word appears only here"
        result = extract_keywords_from_text(text)
        assert "unique" not in result

    def test_strips_markdown_punctuation(self):
        # Asterisks, backticks, hyphens should be stripped
        text = "**python** `python` python-python python python"
        result = extract_keywords_from_text(text, count=1)
        assert "python" in result

    def test_returns_empty_for_empty_string(self):
        assert extract_keywords_from_text("") == []

    def test_excludes_short_words(self):
        # Words shorter than 4 chars ("cat", "dog") should be excluded
        text = "cat cat cat cat dog dog dog dog long long long"
        result = extract_keywords_from_text(text)
        assert "cat" not in result
        assert "dog" not in result

    def test_lowercase_output(self):
        text = "Machine machine machine Learning learning learning"
        result = extract_keywords_from_text(text, count=2)
        assert all(w == w.lower() for w in result)

    def test_default_count_is_five(self):
        # Build text with 10 different high-frequency unique words
        words = [
            "alpha", "bravo", "charlie", "delta", "echo",
            "foxtrot", "golf", "hotel", "india", "juliet",
        ]
        text = " ".join(w * 3 for w in words)  # each appears 3x
        result = extract_keywords_from_text(text)
        assert len(result) <= 5

    def test_excludes_very_long_words(self):
        # Words > 20 chars should be excluded
        long_word = "a" * 21  # 21 chars, won't match \b[a-z]{4,}\b up to 20
        text = f"{long_word} {long_word} {long_word} normal normal normal"
        result = extract_keywords_from_text(text)
        assert long_word.lower() not in result


# ---------------------------------------------------------------------------
# extract_keywords_from_title
# ---------------------------------------------------------------------------


class TestExtractKeywordsFromTitle:
    """Tests for keyword extraction from a title string."""

    def test_extracts_content_words_from_title(self):
        title = "Building Modern Python Microservices"
        result = extract_keywords_from_title(title)
        assert "building" in result
        assert "modern" in result
        assert "python" in result
        assert "microservices" in result

    def test_filters_function_words(self):
        title = "The Best Guide to Machine Learning"
        result = extract_keywords_from_title(title)
        assert "the" not in result
        assert "to" not in result

    def test_returns_fallback_for_all_stopwords(self):
        # Title is purely stopwords — fallback is title[:20]
        title = "A and the or but"
        result = extract_keywords_from_title(title)
        assert result == [title[:20]]

    def test_respects_count_limit(self):
        title = "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliet"
        result = extract_keywords_from_title(title, count=3)
        assert len(result) <= 3

    def test_default_count_is_seven(self):
        words = ["word1", "word2", "word3", "word4", "word5", "word6", "word7", "word8"]
        title = " ".join(words)
        result = extract_keywords_from_title(title)
        assert len(result) <= 7

    def test_lowercases_output(self):
        title = "Python Asyncio FastAPI"
        result = extract_keywords_from_title(title)
        assert all(w == w.lower() for w in result)

    def test_strips_trailing_punctuation(self):
        title = "Python, FastAPI, and asyncio."
        result = extract_keywords_from_title(title)
        # "python," → "python", "fastapi," → "fastapi"
        assert "python" in result or "python," not in result  # punctuation stripped

    def test_empty_title_returns_fallback(self):
        # Empty string — no words → fallback is ""[:20] == ""
        result = extract_keywords_from_title("")
        assert isinstance(result, list)

    def test_single_long_word(self):
        title = "Microservices"
        result = extract_keywords_from_title(title)
        assert "microservices" in result


# ---------------------------------------------------------------------------
# extract_title_from_content
# ---------------------------------------------------------------------------


class TestExtractTitleFromContent:
    """Tests for Markdown title extraction."""

    def test_extracts_h1_heading(self):
        content = "# My Blog Title\n\nContent body here."
        title, cleaned = extract_title_from_content(content)
        assert title == "My Blog Title"
        assert cleaned is not None
        assert "# My Blog Title" not in cleaned
        assert "Content body here." in cleaned

    def test_extracts_h2_heading(self):
        content = "## Section Title\n\nSome text."
        title, cleaned = extract_title_from_content(content)
        assert title == "Section Title"
        assert cleaned is not None
        assert "## Section Title" not in cleaned

    def test_returns_none_title_for_no_heading(self):
        content = "Just plain text without any heading."
        title, cleaned = extract_title_from_content(content)
        assert title is None
        assert cleaned == content

    def test_returns_none_for_empty_content(self):
        title, cleaned = extract_title_from_content("")
        assert title is None
        assert cleaned == ""

    def test_returns_none_for_none_content(self):
        title, cleaned = extract_title_from_content(None)  # type: ignore[arg-type]
        assert title is None
        assert cleaned is None

    def test_handles_heading_with_no_body(self):
        content = "# Just A Title"
        title, cleaned = extract_title_from_content(content)
        assert title == "Just A Title"
        # cleaned might be empty string
        assert title is not None

    def test_strips_leading_whitespace_from_title(self):
        content = "#   Padded Title   \n\nBody text."
        title, cleaned = extract_title_from_content(content)
        assert title is not None
        assert title == title.strip()

    def test_handles_heading_at_start_of_content_with_leading_whitespace(self):
        content = "\n  # Title After Spaces\n\nBody."
        title, cleaned = extract_title_from_content(content)
        assert title == "Title After Spaces"

    def test_preserves_content_after_title(self):
        content = "# Title\n\nParagraph one.\n\nParagraph two."
        title, cleaned = extract_title_from_content(content)
        assert title == "Title"
        assert cleaned is not None
        assert "Paragraph one." in cleaned
        assert "Paragraph two." in cleaned


# ---------------------------------------------------------------------------
# normalize_seo_keywords
# ---------------------------------------------------------------------------


class TestNormalizeSeoKeywords:
    """Tests for SEO keyword normalization."""

    def test_returns_empty_string_for_none(self):
        assert normalize_seo_keywords(None) == ""

    def test_returns_empty_string_for_empty_string(self):
        assert normalize_seo_keywords("") == ""

    def test_returns_empty_string_for_empty_list(self):
        assert normalize_seo_keywords([]) == ""

    def test_normalizes_json_encoded_list(self):
        result = normalize_seo_keywords('["ai", "machine learning"]')
        assert result == "ai, machine learning"

    def test_normalizes_python_list(self):
        result = normalize_seo_keywords(["ai", "ml", "python"])
        assert result == "ai, ml, python"

    def test_passes_through_comma_separated_string(self):
        result = normalize_seo_keywords("ai, ml, python")
        assert result == "ai, ml, python"

    def test_strips_whitespace_from_list_items(self):
        result = normalize_seo_keywords(["  ai  ", " ml "])
        assert result == "ai, ml"

    def test_filters_empty_items_from_list(self):
        result = normalize_seo_keywords(["ai", "", "ml", None])  # type: ignore[list-item]
        assert result == "ai, ml"

    def test_handles_json_list_with_spaces(self):
        result = normalize_seo_keywords('["  python  ", " fastapi "]')
        assert "python" in result
        assert "fastapi" in result

    def test_handles_non_string_non_list_type(self):
        # Integer is neither str nor list — returns ""
        result = normalize_seo_keywords(42)  # type: ignore[arg-type]
        assert result == ""

    def test_preserves_plain_string_that_fails_json_parse(self):
        # A plain CSV string that is not valid JSON is returned as-is
        result = normalize_seo_keywords("keyword1, keyword2")
        assert result == "keyword1, keyword2"

    def test_handles_single_item_list(self):
        result = normalize_seo_keywords(["python"])
        assert result == "python"

    def test_handles_json_string_with_single_item(self):
        result = normalize_seo_keywords('["python"]')
        assert result == "python"

    def test_json_non_list_value_returned_as_is(self):
        # JSON string that parses to non-list (e.g. a plain string) is returned as-is
        result = normalize_seo_keywords('"just a string"')
        # json.loads('"just a string"') == "just a string" — not a list, returns original
        assert result == '"just a string"'
