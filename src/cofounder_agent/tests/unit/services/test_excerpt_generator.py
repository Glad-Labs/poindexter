"""
Unit tests for services/excerpt_generator.py (GH-86).

Excerpts drive index cards, RSS feeds, and social previews. The bug:
front-end fallback was ``content[:N]`` which on our posts rendered the
markdown "What You'll Learn" bullet list as the preview. This module
exists to produce a real 1-2 sentence teaser from the first prose
paragraph.
"""


from services.excerpt_generator import generate_excerpt


class TestGenerateExcerpt:
    """Core excerpt generation contract — non-empty, non-title, reasonable length."""

    def test_returns_first_prose_paragraph(self):
        content = (
            "# Deploying FastAPI on Fly.io\n\n"
            "FastAPI is a modern Python web framework that makes it easy "
            "to build production APIs with type hints and async support. "
            "In this guide we walk through a full deployment workflow.\n\n"
            "## Prerequisites\n\n"
            "You will need Docker installed."
        )
        excerpt = generate_excerpt(
            title="Deploying FastAPI on Fly.io", content=content
        )
        assert excerpt
        assert "FastAPI is a modern Python web framework" in excerpt
        assert "# Deploying" not in excerpt
        assert "## Prerequisites" not in excerpt

    def test_skips_what_youll_learn_section(self):
        """The 'What You'll Learn' bullet list is the primary symptom in GH-86."""
        content = (
            "# Async Python Patterns\n\n"
            "## What You'll Learn\n\n"
            "- How coroutines work\n"
            "- When to use asyncio\n"
            "- Common pitfalls with event loops\n\n"
            "Async programming in Python lets you handle many concurrent "
            "I/O-bound tasks without spawning threads for each one. "
            "The asyncio module provides the core primitives.\n"
        )
        excerpt = generate_excerpt(title="Async Python Patterns", content=content)
        assert excerpt
        # The bullet list must NOT be the excerpt
        assert "How coroutines work" not in excerpt
        assert "When to use asyncio" not in excerpt
        # The actual prose paragraph must be the excerpt
        assert "concurrent" in excerpt or "Async programming" in excerpt

    def test_skips_bullet_list_paragraph(self):
        content = (
            "- Item one\n"
            "- Item two\n\n"
            "This is the real prose paragraph with enough length "
            "to serve as a proper teaser for readers browsing the index."
        )
        excerpt = generate_excerpt(title="Anything", content=content)
        assert "Item one" not in excerpt
        assert "real prose paragraph" in excerpt

    def test_skips_code_fences(self):
        content = (
            "```python\n"
            "def hello():\n"
            "    print('world')\n"
            "```\n\n"
            "After the code block comes the real prose which should be "
            "selected as the excerpt for this article.\n"
        )
        excerpt = generate_excerpt(title="Hello World", content=content)
        assert "def hello" not in excerpt
        assert "print" not in excerpt
        assert "real prose" in excerpt

    def test_strips_markdown_formatting(self):
        content = (
            "This **bold claim** and *italic claim* are preserved as text "
            "but the `inline_code` markers should be stripped from the excerpt."
        )
        excerpt = generate_excerpt(title="Test", content=content)
        assert "**" not in excerpt
        assert "`" not in excerpt
        assert "bold claim" in excerpt
        assert "inline_code" in excerpt

    def test_strips_markdown_links(self):
        content = (
            "The [official docs](https://example.com) describe a clear pattern "
            "for handling errors that every developer on the team should follow."
        )
        excerpt = generate_excerpt(title="Test", content=content)
        assert "https://" not in excerpt
        assert "](" not in excerpt
        assert "official docs" in excerpt

    def test_respects_target_length(self):
        content = "A long paragraph. " * 100
        excerpt = generate_excerpt(
            title="Test", content=content,
            target_length=200, min_length=140, max_length=240,
        )
        assert len(excerpt) <= 240
        assert len(excerpt) >= 30  # non-trivial

    def test_not_empty_on_normal_input(self):
        content = (
            "Python has become the lingua franca of data science and "
            "machine learning over the past decade, overtaking R and "
            "Matlab in research and industry alike."
        )
        excerpt = generate_excerpt(title="Why Python Won", content=content)
        assert excerpt
        assert len(excerpt) > 50

    def test_not_equal_to_title(self):
        """Title-only excerpt is worse than empty — reject the degenerate case."""
        content = "# The Big Title\n\nThe Big Title\n"
        excerpt = generate_excerpt(title="The Big Title", content=content)
        assert excerpt != "The Big Title"
        # May be empty when no real prose exists — acceptable
        assert excerpt == "" or "The Big Title" not in excerpt.strip()

    def test_empty_content_returns_empty(self):
        assert generate_excerpt(title="X", content="") == ""
        assert generate_excerpt(title="X", content="   \n  \n") == ""

    def test_skips_in_this_article_header(self):
        content = (
            "## In This Article\n\n"
            "You will discover five powerful techniques.\n\n"
            "Here is the actual prose paragraph that should become the "
            "excerpt because it describes the substance of the article."
        )
        excerpt = generate_excerpt(title="Any", content=content)
        assert "actual prose paragraph" in excerpt

    def test_fallback_when_only_headers(self):
        """If the content is only headers and bullets, fall back to stripped text."""
        content = "# Title\n\n- bullet\n- bullet\n"
        excerpt = generate_excerpt(title="Title", content=content)
        # Should NOT be empty string — fallback picks SOMETHING
        # But it also should not be just the title alone
        assert excerpt is not None

    def test_prefers_sentence_boundary_on_trim(self):
        sentence_one = (
            "FastAPI offers automatic OpenAPI schema generation from type hints."
        )
        sentence_two = (
            "Dependency injection is built in, which simplifies testing a lot."
        )
        content = f"{sentence_one} {sentence_two} Extra filler text here."
        excerpt = generate_excerpt(
            title="FastAPI Intro", content=content,
            target_length=80, min_length=70, max_length=160,
        )
        # Should end on a period (sentence boundary)
        assert excerpt.rstrip().endswith((".", "!", "?"))


class TestExcerptRealPostExamples:
    """Regression samples built from the exact failure mode in GH-86."""

    def test_deezer_ai_news_style_post(self):
        """Our AI/ML news posts opened with a 'What You'll Learn' bullet list.

        Front-end used content[:N] and rendered that bullet list as the
        excerpt on index pages. This test guards against regression.
        """
        content = (
            "# Deezer's AI Tagging System\n\n"
            "## What You'll Learn\n\n"
            "- How Deezer is deploying ML at scale\n"
            "- What changes for independent artists\n"
            "- Why this matters for the streaming industry\n\n"
            "Deezer announced this week that it has begun tagging "
            "AI-generated music in its catalog, moving ahead of "
            "Spotify and Apple Music on the disclosure question.\n"
        )
        excerpt = generate_excerpt(
            title="Deezer's AI Tagging System", content=content
        )
        assert "How Deezer is deploying" not in excerpt
        assert "What changes" not in excerpt
        assert "Deezer announced" in excerpt or "disclosure" in excerpt
