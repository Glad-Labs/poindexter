"""Shared helpers for converting and excerpting markdown post content.

Extracted from ``routes.cms_routes`` so that ``PostsService`` and route
handlers can both use them without a layering violation
(Glad-Labs/poindexter#1341).
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

# Cheap heuristic: look for any of ``##`` headers, ``**bold**``, fenced code
# blocks, markdown links, or list bullets. Much faster than a full parse
# and good enough to gate the HTML-passthrough shortcut.
_MARKDOWN_MARKER_RE = re.compile(
    r"(?m)"  # multiline
    r"(?:"
    r"^\#{1,6}\s"                 # headers
    r"|\*\*[^*\n]{1,200}\*\*"    # bold
    r"|```"                       # code fence
    r"|^\s*[-*+]\s+\w"           # bulleted list
    r"|\[[^\]]+\]\([^)]+\)"      # markdown link
    r")"
)

_STRIP_MD_RE = re.compile(
    r"\*{1,3}|_{1,3}"            # bold/italic markers
    r"|!\[[^\]]*\]\([^)]*\)"     # images
    r"|\[[^\]]*\]\([^)]*\)"      # links → keeps anchor text below
    r"|```[^`]*```"              # fenced code blocks
    r"|`[^`]+`"                  # inline code
    r"|^\s*>+\s?"                # blockquotes
    r"|^\s*[-*+]\s"              # list markers
    r"|^\#{1,6}\s"               # headers
)

_LINK_TEXT_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def convert_markdown_to_html(markdown_content: str) -> str:
    """Convert markdown content to HTML. Falls back to raw content on error.

    Content is stored as markdown in the DB; converted to HTML on read.
    Mixed content (leading ``<img>`` tag + markdown body) is handled
    correctly — python-markdown passes existing HTML tags through unchanged.
    """
    if not markdown_content:
        return ""

    try:
        import markdown as md  # noqa: PLC0415 — lazy import keeps startup fast

        stripped = markdown_content.strip()

        # Only skip conversion if the content has NO markdown markers —
        # i.e. it's pure HTML / plain text. If markdown is present
        # anywhere, convert the whole thing; python-markdown passes
        # existing HTML tags through unchanged.
        _has_markdown = bool(_MARKDOWN_MARKER_RE.search(stripped))
        if stripped.startswith("<") and not _has_markdown:
            return markdown_content

        return md.markdown(
            stripped,
            extensions=["extra", "codehilite", "sane_lists", "smarty"],
            output_format="html",
        )
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).error("Error converting markdown", exc_info=True)
        return markdown_content


def generate_excerpt_from_content(content: str, length: int = 200) -> str:
    """Generate a plain-text excerpt from markdown content."""
    if not content:
        return ""

    lines = content.split("\n")
    excerpt_parts: list[str] = []

    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue

        cleaned = _LINK_TEXT_RE.sub(r"\1", line)
        cleaned = _STRIP_MD_RE.sub("", cleaned).strip()

        if cleaned:
            excerpt_parts.append(cleaned)

        if len(" ".join(excerpt_parts)) >= length:
            break

    excerpt = " ".join(excerpt_parts)[:length].strip()
    if len(" ".join(excerpt_parts)) > length:
        excerpt = excerpt.rsplit(" ", 1)[0] + "..."

    return excerpt


def map_featured_image_to_coverimage(post: dict) -> dict:
    """Map ``featured_image_url`` → Strapi-compatible ``coverImage`` shape.

    Frontend expects: ``coverImage.data.attributes.url``.
    Database returns: ``featured_image_url``.
    """
    if post.get("featured_image_url"):
        post["coverImage"] = {
            "data": {
                "attributes": {
                    "url": post["featured_image_url"],
                    "alternativeText": f"Cover image for {post.get('title', 'post')}",
                }
            }
        }
    else:
        post["coverImage"] = None

    return post
