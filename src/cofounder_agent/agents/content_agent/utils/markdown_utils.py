import re


def markdown_to_strapi_blocks(md_content: str) -> list:
    """
    Converts a markdown string into a list of Strapi v4 editor blocks.
    This is a simplified parser and may need to be expanded for more complex markdown.
    """
    blocks = []
    # Split content by lines and process each line
    for line in md_content.split("\\n"):
        line = line.strip()
        if not line:
            continue

        # Handle Headings (H1, H2, H3, etc.)
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            text = line.lstrip("# ").strip()
            blocks.append(
                {
                    "type": "heading",
                    "level": level,
                    "children": [{"type": "text", "text": text}],
                }
            )
        # Handle Unordered Lists
        elif line.startswith(("* ", "- ")):
            text = line[2:].strip()
            blocks.append(
                {
                    "type": "list",
                    "format": "unordered",
                    "children": [
                        {
                            "type": "list-item",
                            "children": [{"type": "text", "text": text}],
                        }
                    ],
                }
            )
        # Handle Blockquotes
        elif line.startswith("> "):
            text = line[2:].strip()
            blocks.append({"type": "quote", "children": [{"type": "text", "text": text}]})
        # Handle Paragraphs (default)
        else:
            blocks.append({"type": "paragraph", "children": [{"type": "text", "text": line}]})

    return blocks
