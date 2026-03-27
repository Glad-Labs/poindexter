"""
Unit tests for agents/content_agent/utils/markdown_utils.py

Tests for markdown_to_strapi_blocks converter.

NOTE: The markdown_to_strapi_blocks implementation splits on the literal
two-character sequence "\\n" (backslash + n), NOT on a real newline character.
This means it is designed to process content where line breaks are stored as
the JSON/string escape sequence "\\n" (as returned by LLMs), not Python
multi-line strings. Single-line inputs are tested here; multi-line behaviour
is tested using the correct "\\n" delimiter.
"""

from agents.content_agent.utils.markdown_utils import markdown_to_strapi_blocks

# The separator the parser actually splits on (literal backslash-n in source)
SEP = "\\n"


class TestMarkdownToStrapiBlocks:
    # ------------------------------------------------------------------
    # Headings
    # ------------------------------------------------------------------

    def test_h1_heading(self):
        blocks = markdown_to_strapi_blocks("# Hello World")
        assert len(blocks) == 1
        block = blocks[0]
        assert block["type"] == "heading"
        assert block["level"] == 1
        assert block["children"][0]["text"] == "Hello World"

    def test_h2_heading(self):
        blocks = markdown_to_strapi_blocks("## Section Title")
        assert len(blocks) == 1
        assert blocks[0]["level"] == 2
        assert blocks[0]["children"][0]["text"] == "Section Title"

    def test_h3_heading(self):
        blocks = markdown_to_strapi_blocks("### Sub-section")
        assert len(blocks) == 1
        assert blocks[0]["level"] == 3

    def test_h4_heading(self):
        blocks = markdown_to_strapi_blocks("#### Deep heading")
        assert len(blocks) == 1
        assert blocks[0]["level"] == 4

    # ------------------------------------------------------------------
    # Unordered lists
    # ------------------------------------------------------------------

    def test_asterisk_list_item(self):
        blocks = markdown_to_strapi_blocks("* First item")
        assert len(blocks) == 1
        block = blocks[0]
        assert block["type"] == "list"
        assert block["format"] == "unordered"
        assert block["children"][0]["type"] == "list-item"
        assert block["children"][0]["children"][0]["text"] == "First item"

    def test_dash_list_item(self):
        blocks = markdown_to_strapi_blocks("- Dash item")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "list"
        assert blocks[0]["children"][0]["children"][0]["text"] == "Dash item"

    def test_multiple_list_items(self):
        # Use the actual SEP that the parser splits on
        md = SEP.join(["* Item one", "* Item two", "* Item three"])
        blocks = markdown_to_strapi_blocks(md)
        assert len(blocks) == 3
        assert all(b["type"] == "list" for b in blocks)

    # ------------------------------------------------------------------
    # Blockquotes
    # ------------------------------------------------------------------

    def test_blockquote(self):
        blocks = markdown_to_strapi_blocks("> A quote")
        assert len(blocks) == 1
        block = blocks[0]
        assert block["type"] == "quote"
        assert block["children"][0]["text"] == "A quote"

    def test_blockquote_children_type(self):
        blocks = markdown_to_strapi_blocks("> Some wisdom")
        assert blocks[0]["children"][0]["type"] == "text"

    # ------------------------------------------------------------------
    # Paragraphs
    # ------------------------------------------------------------------

    def test_plain_paragraph(self):
        blocks = markdown_to_strapi_blocks("This is a paragraph.")
        assert len(blocks) == 1
        block = blocks[0]
        assert block["type"] == "paragraph"
        assert block["children"][0]["text"] == "This is a paragraph."

    def test_paragraph_children_structure(self):
        blocks = markdown_to_strapi_blocks("Hello")
        assert blocks[0]["children"][0]["type"] == "text"

    # ------------------------------------------------------------------
    # Empty / whitespace lines
    # ------------------------------------------------------------------

    def test_empty_string_returns_no_blocks(self):
        blocks = markdown_to_strapi_blocks("")
        assert blocks == []

    def test_whitespace_only_line_skipped(self):
        # A single whitespace-only "line" (single line, all spaces)
        blocks = markdown_to_strapi_blocks("   ")
        assert blocks == []

    def test_blank_lines_skipped_using_correct_sep(self):
        # Empty segments between separators should be skipped
        md = SEP.join(["# Heading", "", "Some paragraph", "", "- Item"])
        blocks = markdown_to_strapi_blocks(md)
        assert len(blocks) == 3
        assert blocks[0]["type"] == "heading"
        assert blocks[1]["type"] == "paragraph"
        assert blocks[2]["type"] == "list"

    # ------------------------------------------------------------------
    # Mixed content
    # ------------------------------------------------------------------

    def test_mixed_content_order(self):
        md = SEP.join(["# Title", "## Sub", "Paragraph text", "* List item", "> Quote"])
        blocks = markdown_to_strapi_blocks(md)
        types = [b["type"] for b in blocks]
        assert types == ["heading", "heading", "paragraph", "list", "quote"]

    def test_heading_text_stripped(self):
        blocks = markdown_to_strapi_blocks("#   Spaced Title   ")
        assert blocks[0]["children"][0]["text"] == "Spaced Title"

    def test_list_item_text_stripped(self):
        blocks = markdown_to_strapi_blocks("*   Spaced item   ")
        assert blocks[0]["children"][0]["children"][0]["text"] == "Spaced item"

    def test_blockquote_text_stripped(self):
        blocks = markdown_to_strapi_blocks(">   Spaced quote   ")
        assert blocks[0]["children"][0]["text"] == "Spaced quote"

    # ------------------------------------------------------------------
    # Children structure validation
    # ------------------------------------------------------------------

    def test_heading_children_type_is_text(self):
        blocks = markdown_to_strapi_blocks("# Heading")
        assert blocks[0]["children"][0]["type"] == "text"

    def test_all_blocks_have_children(self):
        md = SEP.join(["# H", "Para", "* List", "> Quote"])
        blocks = markdown_to_strapi_blocks(md)
        for block in blocks:
            assert "children" in block
            assert len(block["children"]) > 0

    def test_large_document(self):
        lines = []
        for i in range(10):
            lines.append(f"# Heading {i}")
            lines.append(f"Paragraph {i}")
            lines.append(f"* List item {i}")
        md = SEP.join(lines)
        blocks = markdown_to_strapi_blocks(md)
        assert len(blocks) == 30
