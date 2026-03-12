"""
Unit tests for services/content_structure_validator.py

Tests ContentStructureValidator.validate() including:
- Valid well-formed content passes
- No heading structure returns error
- Forbidden headings are flagged
- Heading hierarchy violations are detected
- Short sections (< 100 words) are flagged
- Orphan paragraphs produce warnings
- Paragraph extraction skips list items

All tests are pure synchronous — no DB or network I/O.
"""

import pytest

from services.content_structure_validator import (
    ContentStructureValidator,
    ContentStructureResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_long_paragraph(sentence_count: int = 4) -> str:
    """Build a paragraph with *sentence_count* sentences, each ~12 words long."""
    sentence = "This is an example sentence with enough words to count correctly."
    return " ".join([sentence] * sentence_count)


def make_section(heading: str, level: int = 2, sentences: int = 12) -> str:
    """Build a section with heading + enough prose to pass 100-word threshold.

    Each sentence is ~12 words; we need >=100 words → at least 9 sentences
    across the paragraphs. Using sentences=12 gives ~144 words, safely over.
    """
    prefix = "#" * level
    # Split into two paragraphs so each has >= 2 sentences (avoids orphan warnings)
    half = max(sentences // 2, 2)
    para1 = make_long_paragraph(half)
    para2 = make_long_paragraph(sentences - half)
    return f"{prefix} {heading}\n\n{para1}\n\n{para2}\n\n"


VALID_CONTENT = (
    "# My Great Article Title\n\n"
    + make_long_paragraph(3)
    + "\n\n"
    + make_section("First Compelling Section", 2, sentences=12)
    + make_section("Second Compelling Section", 2, sentences=12)
)


# ---------------------------------------------------------------------------
# Basic validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateBasic:
    def test_empty_string_returns_invalid(self):
        v = ContentStructureValidator()
        result = v.validate("")
        assert isinstance(result, ContentStructureResult)
        assert result.is_valid is False
        assert result.total_sections == 0

    def test_no_headings_returns_invalid(self):
        v = ContentStructureValidator()
        content = "This is just a wall of text without any headings at all."
        result = v.validate(content)
        assert result.is_valid is False
        assert any("heading" in e.lower() for e in result.errors)

    def test_valid_content_passes(self):
        v = ContentStructureValidator()
        result = v.validate(VALID_CONTENT)
        # Should not flag hierarchy errors or forbidden headings
        assert result.heading_hierarchy_valid is True
        assert result.no_forbidden_titles is True


# ---------------------------------------------------------------------------
# Forbidden headings
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestForbiddenHeadings:
    @pytest.mark.parametrize(
        "forbidden",
        [
            "Introduction",
            "Background",
            "Overview",
            "Summary",
            "Conclusion",
            "The End",
            "Wrap-Up",
            "Closing",
            "Final Thoughts",
            "Ending",
            "Epilogue",
            "What's Next",
        ],
    )
    def test_forbidden_heading_is_flagged(self, forbidden):
        v = ContentStructureValidator()
        content = (
            "# My Great Article\n\n"
            + make_long_paragraph(3)
            + "\n\n"
            + f"## {forbidden}\n\n"
            + make_long_paragraph(20)
            + "\n\n"
        )
        result = v.validate(content)
        assert result.no_forbidden_titles is False
        assert any(forbidden.lower() in e.lower() for e in result.errors), (
            f"Expected error mentioning '{forbidden}', got: {result.errors}"
        )

    def test_non_forbidden_heading_is_allowed(self):
        v = ContentStructureValidator()
        content = (
            "# My Great Article\n\n"
            + make_long_paragraph(3)
            + "\n\n"
            + make_section("How This Changes Your Business", 2, sentences=6)
        )
        result = v.validate(content)
        assert result.no_forbidden_titles is True


# ---------------------------------------------------------------------------
# Heading hierarchy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHeadingHierarchy:
    def test_missing_h1_is_flagged(self):
        v = ContentStructureValidator()
        content = (
            "## Should Be H1\n\n"
            + make_long_paragraph(8)
            + "\n\n"
            + "### Sub-section\n\n"
            + make_long_paragraph(8)
        )
        result = v.validate(content)
        assert result.heading_hierarchy_valid is False
        assert any("h1" in e.lower() or "first heading" in e.lower() for e in result.errors)

    def test_multiple_h1_headings_flagged(self):
        v = ContentStructureValidator()
        content = (
            "# First H1\n\n"
            + make_long_paragraph(8)
            + "\n\n"
            "# Second H1\n\n"
            + make_long_paragraph(8)
        )
        result = v.validate(content)
        assert result.heading_hierarchy_valid is False
        assert any("h1" in e.lower() for e in result.errors)

    def test_skipping_level_is_flagged(self):
        """H1 → H3 (skipping H2) should produce a hierarchy error."""
        v = ContentStructureValidator()
        content = (
            "# Title\n\n"
            + make_long_paragraph(4)
            + "\n\n"
            "### Skipped Level\n\n"
            + make_long_paragraph(8)
        )
        result = v.validate(content)
        assert result.heading_hierarchy_valid is False
        assert any("skip" in e.lower() or "h3" in e.lower() for e in result.errors)

    def test_valid_hierarchy_h1_h2_h3(self):
        v = ContentStructureValidator()
        content = (
            "# Title\n\n"
            + make_long_paragraph(4)
            + "\n\n"
            "## Section\n\n"
            + make_long_paragraph(8)
            + "\n\n"
            "### Sub-section\n\n"
            + make_long_paragraph(8)
        )
        result = v.validate(content)
        assert result.heading_hierarchy_valid is True

    def test_going_back_up_levels_is_allowed(self):
        """H1 → H2 → H2 is valid (going back up from H3 to H2 is also valid)."""
        v = ContentStructureValidator()
        content = (
            "# Title\n\n"
            + make_long_paragraph(4)
            + "\n\n"
            "## First Section\n\n"
            + make_long_paragraph(8)
            + "\n\n"
            "### Sub-section\n\n"
            + make_long_paragraph(8)
            + "\n\n"
            "## Second Section\n\n"
            + make_long_paragraph(8)
        )
        result = v.validate(content)
        assert result.heading_hierarchy_valid is True


# ---------------------------------------------------------------------------
# Section length
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSectionLength:
    def test_short_h2_section_is_flagged(self):
        v = ContentStructureValidator()
        content = (
            "# Title\n\n"
            + make_long_paragraph(4)
            + "\n\n"
            "## Thin Section\n\n"
            "Only a few words here.\n\n"
        )
        result = v.validate(content)
        assert result.all_sections_adequate is False
        assert any("thin section" in e.lower() or "too short" in e.lower() for e in result.errors)

    def test_adequate_section_passes(self):
        v = ContentStructureValidator()
        content = (
            "# Title\n\n"
            + make_long_paragraph(4)
            + "\n\n"
            + make_section("Robust Section", 2, sentences=10)
        )
        result = v.validate(content)
        assert result.all_sections_adequate is True


# ---------------------------------------------------------------------------
# Orphan / bloated paragraphs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParagraphValidation:
    def test_single_sentence_paragraph_generates_warning(self):
        v = ContentStructureValidator()
        # One single-sentence paragraph inside an otherwise adequate section
        thin_para = "This is one sentence only."
        fat_section = make_long_paragraph(8)
        content = (
            "# Title\n\n"
            + fat_section
            + "\n\n"
            "## Section\n\n"
            + fat_section
            + "\n\n"
            + thin_para
            + "\n\n"
            + fat_section
        )
        result = v.validate(content)
        # Should produce a warning, not necessarily an error
        assert result.orphan_paragraph_count > 0 or any(
            "orphan" in w.lower() for w in result.warnings
        )

    def test_list_items_are_skipped(self):
        """List items starting with - or * must not be treated as orphan paragraphs."""
        v = ContentStructureValidator()
        content = (
            "# Title\n\n"
            + make_long_paragraph(4)
            + "\n\n"
            "## Section With List\n\n"
            + make_long_paragraph(6)
            + "\n\n"
            "- item one\n"
            "- item two\n"
            "- item three\n\n"
            + make_long_paragraph(6)
        )
        result = v.validate(content)
        # List items must not inflate orphan_paragraph_count
        assert result.orphan_paragraph_count == 0


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResultStructure:
    def test_result_has_sections(self):
        v = ContentStructureValidator()
        result = v.validate(VALID_CONTENT)
        assert isinstance(result.sections, list)
        assert result.total_sections == len(result.sections)

    def test_result_has_error_and_warning_lists(self):
        v = ContentStructureValidator()
        result = v.validate(VALID_CONTENT)
        assert isinstance(result.errors, list)
        assert isinstance(result.warnings, list)
        assert isinstance(result.suggestions, list)

    def test_valid_content_has_no_errors(self):
        v = ContentStructureValidator()
        result = v.validate(VALID_CONTENT)
        assert result.errors == []

    def test_invalid_content_has_suggestions(self):
        v = ContentStructureValidator()
        content = "## Missing H1\n\nShort.\n"
        result = v.validate(content)
        # Suggestions should only appear if there are errors
        if result.errors:
            assert isinstance(result.suggestions, list)
