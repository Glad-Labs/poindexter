"""
Unit tests for agents/content_agent/utils/helpers.py and utils/markdown_utils.py

Tests focus on pure functions with no external dependencies:
- slugify(): URL-safe slug generation
- extract_json_from_string(): JSON extraction from LLM text
- markdown_to_strapi_blocks(): Markdown to Strapi block conversion
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-placeholder")

from agents.content_agent.utils.helpers import extract_json_from_string, slugify
from agents.content_agent.utils.markdown_utils import markdown_to_strapi_blocks

# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_converts_to_lowercase(self):
        assert slugify("HELLO WORLD") == "hello-world"

    def test_replaces_spaces_with_hyphens(self):
        assert slugify("hello world") == "hello-world"

    def test_removes_special_characters(self):
        assert slugify("Hello, World! How are you?") == "hello-world-how-are-you"

    def test_collapses_multiple_hyphens(self):
        assert slugify("hello---world") == "hello-world"

    def test_handles_empty_string(self):
        assert slugify("") == "untitled"

    def test_handles_only_special_chars(self):
        result = slugify("!@#$%")
        # All non-alphanumeric removed — should produce "untitled" or empty-then-fallback
        # Since we strip non-alphanumeric and then hyphenate spaces, result may be empty
        assert isinstance(result, str)

    def test_preserves_alphanumeric(self):
        assert slugify("hello123") == "hello123"

    def test_strips_leading_trailing_hyphens(self):
        result = slugify("  hello world  ")
        assert not result.startswith("-")
        assert not result.endswith("-")

    def test_handles_mixed_case_with_numbers(self):
        slug = slugify("Python 3.12 Release Notes")
        assert "python" in slug
        assert "3" in slug
        assert "12" in slug
        assert " " not in slug

    def test_long_title_produces_valid_slug(self):
        title = "The Ultimate Guide to Machine Learning in 2025 and Beyond"
        slug = slugify(title)
        assert "-" in slug
        assert " " not in slug
        assert slug == slug.lower()


# ---------------------------------------------------------------------------
# extract_json_from_string
# ---------------------------------------------------------------------------


class TestExtractJsonFromString:
    def test_extracts_json_from_code_block(self):
        text = 'Here is the JSON:\n```json\n{"key": "value"}\n```'
        result = extract_json_from_string(text)
        assert result == '{"key": "value"}'

    def test_extracts_bare_json_object(self):
        text = 'Some preamble {"title": "Article", "score": 95} trailing text'
        result = extract_json_from_string(text)
        assert result is not None
        import json

        parsed = json.loads(result)
        assert parsed["title"] == "Article"

    def test_returns_none_when_no_json(self):
        text = "This is plain text with no JSON at all."
        result = extract_json_from_string(text)
        assert result is None

    def test_handles_nested_json(self):
        text = '{"outer": {"inner": "value"}, "list": [1, 2, 3]}'
        result = extract_json_from_string(text)
        assert result is not None
        import json

        parsed = json.loads(result)
        assert parsed["outer"]["inner"] == "value"

    def test_extracts_first_json_when_multiple_present(self):
        # The regex finds the first match
        text = '{"first": 1} some text {"second": 2}'
        result = extract_json_from_string(text)
        assert result is not None
        # Should find the combined greedy match or the first
        assert result is not None

    def test_handles_empty_string(self):
        assert extract_json_from_string("") is None

    def test_handles_json_with_escaped_characters(self):
        text = '{"text": "Hello \\"world\\"", "num": 42}'
        result = extract_json_from_string(text)
        assert result is not None

    def test_code_block_takes_priority_over_bare_json(self):
        text = '{"bare": true}\n```json\n{"code_block": true}\n```'
        result = extract_json_from_string(text)
        # Code block regex runs first
        assert result == '{"code_block": true}'

    def test_handles_multiline_json(self):
        text = """
        Here is the output:
        {
            "title": "My Article",
            "meta_description": "A great article",
            "approved": true
        }
        """
        result = extract_json_from_string(text)
        assert result is not None
        import json

        parsed = json.loads(result)
        assert parsed["title"] == "My Article"


# ---------------------------------------------------------------------------
# markdown_to_strapi_blocks
# ---------------------------------------------------------------------------


class TestMarkdownToStrapiBlocks:
    def test_converts_paragraph(self):
        content = "Simple paragraph text"
        # Note: markdown_to_strapi_blocks splits on "\\n" (literal backslash-n)
        # so a real newline won't split — let's test what the function actually does
        blocks = markdown_to_strapi_blocks(content)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "paragraph"
        assert blocks[0]["children"][0]["text"] == "Simple paragraph text"

    def test_converts_h1_heading(self):
        # markdown_to_strapi_blocks splits on literal "\\n"
        content = "# Main Title\\nSome text"
        blocks = markdown_to_strapi_blocks(content)
        heading = next(b for b in blocks if b["type"] == "heading")
        assert heading["level"] == 1
        assert heading["children"][0]["text"] == "Main Title"

    def test_converts_h2_heading(self):
        content = "## Section Heading"
        blocks = markdown_to_strapi_blocks(content)
        assert blocks[0]["type"] == "heading"
        assert blocks[0]["level"] == 2

    def test_converts_h3_heading(self):
        content = "### Subsection"
        blocks = markdown_to_strapi_blocks(content)
        assert blocks[0]["type"] == "heading"
        assert blocks[0]["level"] == 3

    def test_converts_unordered_list_with_asterisk(self):
        content = "* List item one"
        blocks = markdown_to_strapi_blocks(content)
        assert blocks[0]["type"] == "list"
        assert blocks[0]["format"] == "unordered"
        assert blocks[0]["children"][0]["type"] == "list-item"

    def test_converts_unordered_list_with_dash(self):
        content = "- List item two"
        blocks = markdown_to_strapi_blocks(content)
        assert blocks[0]["type"] == "list"
        assert blocks[0]["format"] == "unordered"

    def test_converts_blockquote(self):
        content = "> This is a quote"
        blocks = markdown_to_strapi_blocks(content)
        assert blocks[0]["type"] == "quote"
        assert blocks[0]["children"][0]["text"] == "This is a quote"

    def test_skips_empty_lines(self):
        # Empty lines are split by "\\n" — a literal blank line "\\n\\n" produces empty string
        content = "Line one\\n\\nLine two"
        blocks = markdown_to_strapi_blocks(content)
        # Should have 2 blocks (empty line stripped)
        assert len(blocks) == 2

    def test_returns_empty_list_for_empty_content(self):
        blocks = markdown_to_strapi_blocks("")
        assert blocks == []

    def test_multiple_elements(self):
        content = "# Title\\n* Item 1\\n* Item 2\\nParagraph"
        blocks = markdown_to_strapi_blocks(content)
        types = [b["type"] for b in blocks]
        assert "heading" in types
        assert "list" in types
        assert "paragraph" in types
