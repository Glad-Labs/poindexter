"""Tests for services.title_generation.build_title_grounding_digest.

The digest is the article-grounding payload for the title prompts
(content.generate_title + seo.generate_all_metadata): opening excerpt plus
the full-article section-heading skeleton (2026-07-24 topic-label titling fix).
"""

from __future__ import annotations

from services.title_generation import (
    DEFAULT_TITLE_EXCERPT_CHARS,
    build_title_grounding_digest,
)


def test_empty_content_returns_empty():
    assert build_title_grounding_digest("") == ""
    assert build_title_grounding_digest(None) == ""  # type: ignore[arg-type]
    assert build_title_grounding_digest("   \n  ") == ""


def test_excerpt_truncates_at_max_chars():
    body = "word " * 1000
    digest = build_title_grounding_digest(body, max_chars=100)
    assert len(digest) <= 100  # no headings in this body


def test_headings_appended_from_full_article():
    body = (
        "Intro paragraph about the operator console rebuild.\n\n"
        + ("filler " * 400)
        + "\n\n## Migrating the Dashboard\n\ncontent\n\n"
        "### Why Grafana Stayed\n\ncontent\n\n"
        "## The Cutover Weekend\n\ncontent\n"
    )
    digest = build_title_grounding_digest(body, max_chars=200)
    # Excerpt is clipped, but headings from BEYOND the clip still appear.
    assert "SECTION HEADINGS (full article):" in digest
    assert "- Migrating the Dashboard" in digest
    assert "- Why Grafana Stayed" in digest
    assert "- The Cutover Weekend" in digest


def test_h1_not_treated_as_section_heading():
    body = "# The Working Title\n\nBody prose here.\n\n## Real Section\n\nMore."
    digest = build_title_grounding_digest(body)
    assert "- Real Section" in digest
    assert "- The Working Title" not in digest


def test_image_marker_lines_stripped():
    body = (
        "[HERO-IMAGE: a neon dashboard]\n"
        "Opening prose.\n"
        "[IMAGE: some chart]\n"
        "More prose.\n"
    )
    digest = build_title_grounding_digest(body)
    assert "HERO-IMAGE" not in digest
    assert "[IMAGE:" not in digest
    assert "Opening prose." in digest
    assert "More prose." in digest


def test_no_headings_yields_plain_excerpt():
    body = "Just prose. " * 10
    digest = build_title_grounding_digest(body)
    assert "SECTION HEADINGS" not in digest
    assert digest.startswith("Just prose.")


def test_headings_capped():
    body = "intro\n\n" + "\n\n".join(f"## Heading {i}" for i in range(30))
    digest = build_title_grounding_digest(body, max_chars=50)
    assert digest.count("- Heading") == 12


def test_default_excerpt_size_is_1500():
    assert DEFAULT_TITLE_EXCERPT_CHARS == 1500
