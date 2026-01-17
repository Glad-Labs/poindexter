"""
Tests for Publishing Agent
Tests content formatting, image processing, and Strapi publishing
"""

import pytest
from unittest.mock import Mock, patch
import sys
import types

# Mock Google Cloud modules
if "google" not in sys.modules:
    sys.modules["google"] = types.ModuleType("google")
if "google.cloud" not in sys.modules:
    sys.modules["google.cloud"] = types.ModuleType("google.cloud")

from agents.publishing_agent import PublishingAgent
from utils.data_models import BlogPost, ImageData


@pytest.fixture
def mock_strapi_client():
    """Create mock Strapi client"""
    mock_client = Mock()
    mock_client.create_post.return_value = (
        "test-id-123",
        "https://strapi.example.com/posts/test-id-123",
    )
    return mock_client


@pytest.fixture
def publishing_agent(mock_strapi_client):
    """Create PublishingAgent with mocked Strapi client"""
    agent = PublishingAgent(mock_strapi_client)
    return agent


@pytest.fixture
def sample_blog_post_with_images():
    """Create blog post with images for testing"""
    post = BlogPost(
        title="Test Blog Post",
        topic="AI Testing",
        primary_keyword="test",
        raw_content="# Introduction\n\n[IMAGE-1]\n\nSome content here.\n\n[IMAGE-2]\n\nMore content.",
    )
    post.images = [
        ImageData(
            public_url="https://storage.example.com/image1.jpg",
            alt_text="First test image",
            file_path="/local/image1.jpg",
        ),
        ImageData(
            public_url="https://storage.example.com/image2.jpg",
            alt_text="Second test image",
            file_path="/local/image2.jpg",
        ),
    ]
    return post


class TestPublishingAgentInitialization:
    """Test PublishingAgent initialization"""

    def test_agent_initializes_with_strapi_client(self, mock_strapi_client):
        """Test that agent initializes with Strapi client"""
        agent = PublishingAgent(mock_strapi_client)

        assert agent.strapi_client == mock_strapi_client


class TestImagePlaceholderReplacement:
    """Test image placeholder replacement"""

    def test_replaces_image_placeholders(self, publishing_agent, sample_blog_post_with_images):
        """Test that image placeholders are replaced with markdown"""
        result = publishing_agent._replace_image_placeholders(sample_blog_post_with_images)

        assert "[IMAGE-1]" not in result
        assert "[IMAGE-2]" not in result
        assert "![First test image](https://storage.example.com/image1.jpg)" in result
        assert "![Second test image](https://storage.example.com/image2.jpg)" in result

    def test_handles_post_without_images(self, publishing_agent):
        """Test handling of post without images"""
        post = BlogPost(title="No Images", raw_content="Content without images")

        result = publishing_agent._replace_image_placeholders(post)

        assert result == "Content without images"

    def test_handles_image_without_public_url(self, publishing_agent):
        """Test handling of image without public URL"""
        post = BlogPost(title="Test", raw_content="[IMAGE-1]")
        post.images = [
            ImageData(public_url=None, alt_text="No URL image", file_path="/local/image.jpg")
        ]

        result = publishing_agent._replace_image_placeholders(post)

        # Placeholder should remain if no URL
        assert "[IMAGE-1]" in result


class TestContentCleaning:
    """Test content cleaning"""

    def test_cleans_draft_headers(self, publishing_agent):
        """Test removal of draft headers"""
        content = "### **Blog Post Draft**\n\nActual content here."

        result = publishing_agent._clean_content(content)

        assert "Blog Post Draft" not in result
        assert "Actual content here." in result

    def test_removes_whitespace(self, publishing_agent):
        """Test removal of extra whitespace"""
        content = "\n\n\n  Content with spaces  \n\n\n"

        result = publishing_agent._clean_content(content)

        assert result == "Content with spaces"

    def test_handles_clean_content(self, publishing_agent):
        """Test that already clean content is preserved"""
        content = "Clean content"

        result = publishing_agent._clean_content(content)

        assert result == "Clean content"


class TestPublishing:
    """Test publishing to Strapi"""

    def test_run_publishes_to_strapi(
        self, publishing_agent, sample_blog_post_with_images, mock_strapi_client
    ):
        """Test that run publishes post to Strapi"""
        with patch("agents.publishing_agent.markdown_to_strapi_blocks", return_value=[]):
            result = publishing_agent.run(sample_blog_post_with_images)

        mock_strapi_client.create_post.assert_called_once()
        assert result.strapi_id == "test-id-123"
        assert result.strapi_url == "https://strapi.example.com/posts/test-id-123"

    def test_run_converts_markdown_to_blocks(
        self, publishing_agent, sample_blog_post_with_images, mock_strapi_client
    ):
        """Test that markdown is converted to Strapi blocks"""
        mock_blocks = [{"type": "paragraph", "children": [{"text": "test"}]}]

        with patch(
            "agents.publishing_agent.markdown_to_strapi_blocks", return_value=mock_blocks
        ) as mock_convert:
            result = publishing_agent.run(sample_blog_post_with_images)

        mock_convert.assert_called_once()
        assert result.body_content_blocks == mock_blocks

    def test_run_processes_images_before_publishing(
        self, publishing_agent, sample_blog_post_with_images, mock_strapi_client
    ):
        """Test that images are processed before publishing"""
        with patch(
            "agents.publishing_agent.markdown_to_strapi_blocks", return_value=[]
        ) as mock_convert:
            publishing_agent.run(sample_blog_post_with_images)

            # Check that markdown conversion received content with replaced images
            call_args = mock_convert.call_args[0][0]
            assert "![First test image]" in call_args
            assert "[IMAGE-1]" not in call_args


class TestErrorHandling:
    """Test error handling"""

    def test_handles_strapi_error(
        self, publishing_agent, sample_blog_post_with_images, mock_strapi_client
    ):
        """Test handling of Strapi API errors"""
        mock_strapi_client.create_post.side_effect = Exception("Strapi API Error")

        with patch("agents.publishing_agent.markdown_to_strapi_blocks", return_value=[]):
            result = publishing_agent.run(sample_blog_post_with_images)

        # Should handle error gracefully
        assert result is not None

    def test_handles_no_strapi_id_returned(
        self, publishing_agent, sample_blog_post_with_images, mock_strapi_client
    ):
        """Test handling when Strapi doesn't return ID"""
        mock_strapi_client.create_post.return_value = (None, None)

        with patch("agents.publishing_agent.markdown_to_strapi_blocks", return_value=[]):
            result = publishing_agent.run(sample_blog_post_with_images)

        # Should handle gracefully
        assert result is not None

    def test_handles_markdown_conversion_error(
        self, publishing_agent, sample_blog_post_with_images, mock_strapi_client
    ):
        """Test handling of markdown conversion errors"""
        with patch(
            "agents.publishing_agent.markdown_to_strapi_blocks",
            side_effect=Exception("Conversion error"),
        ):
            result = publishing_agent.run(sample_blog_post_with_images)

        # Should handle error gracefully
        assert result is not None


class TestFullPublishingWorkflow:
    """Test complete publishing workflow"""

    def test_complete_publishing_pipeline(
        self, publishing_agent, sample_blog_post_with_images, mock_strapi_client
    ):
        """Test complete end-to-end publishing"""
        with patch(
            "agents.publishing_agent.markdown_to_strapi_blocks",
            return_value=[{"type": "paragraph"}],
        ):
            result = publishing_agent.run(sample_blog_post_with_images)

        # Verify all steps completed
        assert result.strapi_id is not None
        assert result.strapi_url is not None
        assert result.body_content_blocks is not None
        mock_strapi_client.create_post.assert_called_once_with(result)


@pytest.mark.integration
class TestPublishingAgentIntegration:
    """Integration tests"""

    @pytest.mark.skip(reason="Requires actual Strapi instance")
    def test_real_strapi_publishing(self, sample_blog_post_with_images):
        """Test with real Strapi client"""
        from services.strapi_client import StrapiClient

        strapi_client = StrapiClient()
        agent = PublishingAgent(strapi_client)

        result = agent.run(sample_blog_post_with_images)

        assert result.strapi_id is not None
        assert result.strapi_url is not None


@pytest.mark.performance
class TestPublishingAgentPerformance:
    """Performance tests"""

    def test_publishing_performance(
        self, publishing_agent, sample_blog_post_with_images, mock_strapi_client
    ):
        """Test that publishing completes quickly"""
        import time

        with patch("agents.publishing_agent.markdown_to_strapi_blocks", return_value=[]):
            start = time.time()
            publishing_agent.run(sample_blog_post_with_images)
            duration = time.time() - start

        assert duration < 1.0  # With mocked services
