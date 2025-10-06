import pytest
from utils.markdown_utils import markdown_to_strapi_blocks

def test_markdown_to_strapi_blocks_conversion():
    """
    Tests that a simple markdown string with a heading and a paragraph
    is correctly converted into a list of Strapi blocks.
    """
    markdown_input = "# My Heading\\n\\nThis is a paragraph."
    expected_output = [
        {
            "type": "heading",
            "level": 1,
            "children": [{"type": "text", "text": "My Heading"}]
        },
        {
            "type": "paragraph",
            "children": [{"type": "text", "text": "This is a paragraph."}]
        }
    ]
    
    result = markdown_to_strapi_blocks(markdown_input)
    assert result == expected_output
