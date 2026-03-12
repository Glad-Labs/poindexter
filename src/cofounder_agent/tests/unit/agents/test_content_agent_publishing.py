"""
Unit tests for agents/content_agent/agents/publishing_agent.py — PublishingAgent

Tests focus on:
- _replace_image_placeholders(): placeholder substitution with real images
- _strip_unreplaced_placeholders(): cleans leftover [IMAGE-N] markers
- _clean_content(): artifact removal from LLM output
- run(): complete pipeline wiring
"""

import os
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-placeholder")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_blog_post(**kwargs):
    from agents.content_agent.utils.data_models import BlogPost, ImageDetails

    defaults = {
        "topic": "DevOps Practices",
        "primary_keyword": "devops",
        "target_audience": "Engineers",
        "category": "Technology",
    }
    defaults.update(kwargs)
    return BlogPost(**defaults)  # type: ignore[arg-type]


def make_image(public_url="https://cdn.example.com/img.jpg", alt_text="Alt", caption="Caption"):
    from agents.content_agent.utils.data_models import ImageDetails

    return ImageDetails(public_url=public_url, alt_text=alt_text, caption=caption)


def make_agent():
    with patch("agents.content_agent.agents.publishing_agent.CrewAIToolsFactory") as mock_tools:
        mock_tools.get_content_agent_tools.return_value = []
        strapi_client = MagicMock()

        from agents.content_agent.agents.publishing_agent import PublishingAgent

        agent = PublishingAgent(strapi_client=strapi_client)
    return agent, strapi_client


# ---------------------------------------------------------------------------
# _strip_unreplaced_placeholders
# ---------------------------------------------------------------------------


class TestStripUnreplacedPlaceholders:
    def test_removes_single_placeholder(self):
        from agents.content_agent.agents.publishing_agent import PublishingAgent

        content = "Some text [IMAGE-1] more text."
        result = PublishingAgent._strip_unreplaced_placeholders(content)
        assert "[IMAGE-1]" not in result
        assert "Some text" in result
        assert "more text." in result

    def test_removes_multiple_placeholders(self):
        from agents.content_agent.agents.publishing_agent import PublishingAgent

        content = "Start [IMAGE-1] middle [IMAGE-2] [IMAGE-3] end"
        result = PublishingAgent._strip_unreplaced_placeholders(content)
        assert "[IMAGE-1]" not in result
        assert "[IMAGE-2]" not in result
        assert "[IMAGE-3]" not in result

    def test_leaves_content_unchanged_when_no_placeholders(self):
        from agents.content_agent.agents.publishing_agent import PublishingAgent

        content = "No placeholders here at all."
        result = PublishingAgent._strip_unreplaced_placeholders(content)
        assert result == content

    def test_strips_double_digit_placeholder(self):
        from agents.content_agent.agents.publishing_agent import PublishingAgent

        content = "Text [IMAGE-10] and more text."
        result = PublishingAgent._strip_unreplaced_placeholders(content)
        assert "[IMAGE-10]" not in result


# ---------------------------------------------------------------------------
# _replace_image_placeholders
# ---------------------------------------------------------------------------


class TestReplaceImagePlaceholders:
    def test_replaces_placeholder_with_markdown_image(self):
        agent, _ = make_agent()
        img = make_image("https://example.com/photo.jpg", "A photo", "Caption")
        post = make_blog_post(
            raw_content="# Title\n\n[IMAGE-1]\n\nSome content.",
            images=[img],
        )

        result = agent._replace_image_placeholders(post)

        assert "![A photo](https://example.com/photo.jpg)" in result
        assert "[IMAGE-1]" not in result

    def test_replaces_multiple_images_in_order(self):
        agent, _ = make_agent()
        img1 = make_image("https://example.com/img1.jpg", "Alt1", "Cap1")
        img2 = make_image("https://example.com/img2.jpg", "Alt2", "Cap2")
        post = make_blog_post(
            raw_content="[IMAGE-1] text [IMAGE-2]",
            images=[img1, img2],
        )

        result = agent._replace_image_placeholders(post)

        assert "img1.jpg" in result
        assert "img2.jpg" in result
        assert "[IMAGE-1]" not in result
        assert "[IMAGE-2]" not in result

    def test_strips_unreplaced_placeholders_when_no_images(self):
        agent, _ = make_agent()
        post = make_blog_post(
            raw_content="# Title\n\n[IMAGE-1]\n\nContent here.",
            images=[],
        )

        result = agent._replace_image_placeholders(post)

        assert "[IMAGE-1]" not in result
        assert "Content here." in result

    def test_skips_image_with_no_public_url(self):
        agent, _ = make_agent()
        from agents.content_agent.utils.data_models import ImageDetails

        img = ImageDetails(public_url=None, alt_text="No URL", caption="No URL cap")
        post = make_blog_post(
            raw_content="[IMAGE-1] text",
            images=[img],
        )

        result = agent._replace_image_placeholders(post)

        # Placeholder not replaced (no URL) — then stripped
        assert "[IMAGE-1]" not in result

    def test_strips_extra_placeholders_beyond_image_count(self):
        agent, _ = make_agent()
        img = make_image("https://example.com/img.jpg")
        post = make_blog_post(
            raw_content="[IMAGE-1] text [IMAGE-2] more [IMAGE-3]",
            images=[img],  # only 1 image, 3 placeholders
        )

        result = agent._replace_image_placeholders(post)

        assert "[IMAGE-2]" not in result
        assert "[IMAGE-3]" not in result
        assert "img.jpg" in result

    def test_handles_empty_raw_content(self):
        agent, _ = make_agent()
        post = make_blog_post(raw_content=None, images=[])

        result = agent._replace_image_placeholders(post)

        assert result == ""


# ---------------------------------------------------------------------------
# _clean_content
# ---------------------------------------------------------------------------


class TestCleanContent:
    def test_removes_draft_artifact_line(self):
        agent, _ = make_agent()
        content = "### **Blog Post Draft**\n\nActual content here."
        result = agent._clean_content(content)
        assert "### **Blog Post Draft**" not in result
        assert "Actual content here." in result

    def test_removes_artifact_with_different_label(self):
        agent, _ = make_agent()
        content = "### **Final Content**\n\nBody text."
        result = agent._clean_content(content)
        assert "### **Final Content**" not in result

    def test_strips_leading_trailing_whitespace(self):
        agent, _ = make_agent()
        content = "\n\n  Content with whitespace.  \n\n"
        result = agent._clean_content(content)
        assert result == "Content with whitespace."

    def test_preserves_valid_content(self):
        agent, _ = make_agent()
        content = "# Title\n\n## Subheading\n\nParagraph text."
        result = agent._clean_content(content)
        assert "# Title" in result
        assert "## Subheading" in result
        assert "Paragraph text." in result

    def test_handles_empty_content(self):
        agent, _ = make_agent()
        assert agent._clean_content("") == ""


# ---------------------------------------------------------------------------
# run() — full pipeline wiring
# ---------------------------------------------------------------------------


class TestPublishingAgentRun:
    def test_returns_post_with_strapi_id_on_success(self):
        agent, strapi_client = make_agent()
        strapi_client.create_post.return_value = (42, "https://strapi.example.com/post/42")

        with patch("agents.content_agent.agents.publishing_agent.markdown_to_strapi_blocks") as mock_blocks:
            mock_blocks.return_value = [{"type": "paragraph"}]

            post = make_blog_post(
                title="My Post",
                raw_content="# My Post\n\nContent.",
                images=[],
            )
            result = agent.run(post)

        assert result.strapi_id == 42
        assert result.strapi_url == "https://strapi.example.com/post/42"

    def test_returns_post_without_strapi_id_when_publish_fails(self):
        agent, strapi_client = make_agent()
        strapi_client.create_post.side_effect = RuntimeError("Strapi connection failed")

        with patch("agents.content_agent.agents.publishing_agent.markdown_to_strapi_blocks") as mock_blocks:
            mock_blocks.return_value = []
            post = make_blog_post(
                title="My Post",
                raw_content="# Content",
                images=[],
            )
            result = agent.run(post)

        # Should not raise — returns post without strapi_id
        assert result.strapi_id is None

    def test_handles_none_strapi_id_from_create_post(self):
        agent, strapi_client = make_agent()
        # create_post returns (None, None) — no ID
        strapi_client.create_post.return_value = (None, None)

        with patch("agents.content_agent.agents.publishing_agent.markdown_to_strapi_blocks") as mock_blocks:
            mock_blocks.return_value = []
            post = make_blog_post(
                title="My Post",
                raw_content="# Content",
                images=[],
            )
            result = agent.run(post)

        # RuntimeError caught internally — returns post
        assert result is post
