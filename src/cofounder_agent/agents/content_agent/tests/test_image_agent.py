"""
Tests for Image Agent
Tests image generation, selection, and processing functionality
"""

import sys
import types
from unittest.mock import MagicMock, Mock, patch

import pytest

# Mock Google Cloud modules
if "google" not in sys.modules:
    sys.modules["google"] = types.ModuleType("google")
if "google.cloud" not in sys.modules:
    sys.modules["google.cloud"] = types.ModuleType("google.cloud")
if "google.cloud.storage" not in sys.modules:
    sys.modules["google.cloud.storage"] = types.ModuleType("google.cloud.storage")

from agents.image_agent import ImageAgent
from utils.data_models import BlogPost, ImageDetails


@pytest.fixture
def mock_clients():
    """Create mock clients for testing"""
    mock_image_gen = Mock()
    mock_pexels = Mock()
    mock_gcs = Mock()
    return mock_image_gen, mock_pexels, mock_gcs


@pytest.fixture
def image_agent(mock_clients):
    """Create ImageAgent with mocked clients"""
    image_gen, pexels, gcs = mock_clients
    with (
        patch("agents.image_agent.ImageGenClient", return_value=image_gen),
        patch("agents.image_agent.PexelsClient", return_value=pexels),
        patch("agents.image_agent.GCSClient", return_value=gcs),
    ):
        agent = ImageAgent()
    return agent


@pytest.fixture
def sample_blog_post():
    """Create a sample blog post for testing"""
    return BlogPost(
        title="Test Blog Post",
        subtitle="A test subtitle",
        content="This is test content for the blog post.",
        tags=["test", "sample"],
        category="Technology",
        featured=True,
    )


class TestImageAgentInitialization:
    """Test ImageAgent initialization"""

    def test_agent_initializes_with_clients(self, mock_clients):
        """Test that ImageAgent initializes with all required clients"""
        image_gen, pexels, gcs = mock_clients

        with (
            patch("agents.image_agent.ImageGenClient", return_value=image_gen),
            patch("agents.image_agent.PexelsClient", return_value=pexels),
            patch("agents.image_agent.GCSClient", return_value=gcs),
        ):
            agent = ImageAgent()

        assert agent.image_gen_client == image_gen
        assert agent.pexels_client == pexels
        assert agent.gcs_client == gcs


class TestImageGeneration:
    """Test image generation functionality"""

    def test_generate_images_creates_correct_number(
        self, image_agent, sample_blog_post, mock_clients
    ):
        """Test that generate_images creates the specified number of images"""
        image_gen, _, _ = mock_clients
        image_gen.generate_image.return_value = "generated_image.jpg"

        with patch.object(image_agent, "_create_image_prompt", return_value="test prompt"):
            images = image_agent.generate_images(sample_blog_post, count=3)

        assert len(images) == 3
        assert image_gen.generate_image.call_count == 3

    def test_generate_images_with_custom_prompt(self, image_agent, sample_blog_post, mock_clients):
        """Test image generation with custom prompt"""
        image_gen, _, _ = mock_clients
        image_gen.generate_image.return_value = "generated_image.jpg"

        custom_prompt = "A beautiful landscape"
        images = image_agent.generate_images(sample_blog_post, count=1, custom_prompt=custom_prompt)

        image_gen.generate_image.assert_called_once_with(custom_prompt)

    def test_generate_images_handles_failure_gracefully(
        self, image_agent, sample_blog_post, mock_clients
    ):
        """Test that image generation failures are handled gracefully"""
        image_gen, _, _ = mock_clients
        image_gen.generate_image.side_effect = Exception("Generation failed")

        with patch.object(image_agent, "_create_image_prompt", return_value="test prompt"):
            images = image_agent.generate_images(sample_blog_post, count=3)

        # Should return empty list or partial results, not raise exception
        assert isinstance(images, list)


class TestImageSearch:
    """Test image search functionality"""

    def test_search_pexels_returns_images(self, image_agent, mock_clients):
        """Test that Pexels search returns image results"""
        _, pexels, _ = mock_clients
        pexels.search.return_value = [
            {"src": {"large": "https://example.com/image1.jpg"}, "alt": "Test image 1"},
            {"src": {"large": "https://example.com/image2.jpg"}, "alt": "Test image 2"},
        ]

        results = image_agent.search_pexels("technology", per_page=2)

        assert len(results) == 2
        pexels.search.assert_called_once_with("technology", per_page=2)

    def test_search_pexels_handles_no_results(self, image_agent, mock_clients):
        """Test handling of empty search results"""
        _, pexels, _ = mock_clients
        pexels.search.return_value = []

        results = image_agent.search_pexels("nonexistentquery")

        assert results == []

    def test_search_pexels_handles_api_error(self, image_agent, mock_clients):
        """Test handling of Pexels API errors"""
        _, pexels, _ = mock_clients
        pexels.search.side_effect = Exception("API Error")

        results = image_agent.search_pexels("test")

        assert results == [] or results is None


class TestImageSelection:
    """Test image selection logic"""

    def test_select_best_image_from_generated(self, image_agent, sample_blog_post):
        """Test selection of best image from generated options"""
        generated_images = ["image1.jpg", "image2.jpg", "image3.jpg"]

        with patch.object(image_agent, "_score_image", side_effect=[0.7, 0.9, 0.6]):
            best = image_agent.select_best_image(generated_images, sample_blog_post)

        assert best == "image2.jpg"

    def test_select_best_image_returns_none_if_empty(self, image_agent, sample_blog_post):
        """Test that None is returned if no images provided"""
        best = image_agent.select_best_image([], sample_blog_post)

        assert best is None


class TestImageUpload:
    """Test image upload to GCS"""

    def test_upload_image_to_gcs(self, image_agent, mock_clients):
        """Test uploading image to Google Cloud Storage"""
        _, _, gcs = mock_clients
        gcs.upload_file.return_value = "https://storage.googleapis.com/bucket/image.jpg"

        url = image_agent.upload_image("local_image.jpg", "remote_image.jpg")

        assert url == "https://storage.googleapis.com/bucket/image.jpg"
        gcs.upload_file.assert_called_once_with("local_image.jpg", "remote_image.jpg")

    def test_upload_image_handles_failure(self, image_agent, mock_clients):
        """Test handling of upload failures"""
        _, _, gcs = mock_clients
        gcs.upload_file.side_effect = Exception("Upload failed")

        url = image_agent.upload_image("local_image.jpg", "remote_image.jpg")

        assert url is None or url == ""


class TestImageProcessing:
    """Test image processing and optimization"""

    def test_process_image_for_blog(self, image_agent):
        """Test image processing for blog post"""
        with patch("agents.image_agent.Image") as mock_image_class:
            mock_img = Mock()
            mock_image_class.open.return_value = mock_img

            result = image_agent.process_image_for_blog("test_image.jpg")

            mock_image_class.open.assert_called_once()

    def test_create_image_prompt_from_blog_post(self, image_agent, sample_blog_post):
        """Test creation of AI image generation prompt from blog post"""
        prompt = image_agent._create_image_prompt(sample_blog_post)

        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "test" in prompt.lower() or "technology" in prompt.lower()


class TestImageMetadata:
    """Test image metadata handling"""

    def test_extract_image_metadata(self, image_agent):
        """Test extraction of image metadata"""
        with patch("agents.image_agent.Image") as mock_image_class:
            mock_img = Mock()
            mock_img.size = (1920, 1080)
            mock_img.format = "JPEG"
            mock_image_class.open.return_value = mock_img

            metadata = image_agent.get_image_metadata("test_image.jpg")

            assert metadata["width"] == 1920
            assert metadata["height"] == 1080
            assert metadata["format"] == "JPEG"

    def test_create_image_details(self, image_agent):
        """Test creation of ImageDetails object"""
        details = ImageDetails(
            url="https://example.com/image.jpg",
            alt_text="Test image",
            caption="A test image caption",
        )

        assert details.url == "https://example.com/image.jpg"
        assert details.alt_text == "Test image"
        assert details.caption == "A test image caption"


@pytest.mark.integration
class TestImageAgentIntegration:
    """Integration tests for ImageAgent (require actual API keys)"""

    @pytest.mark.skip(reason="Requires actual API keys")
    def test_full_image_generation_pipeline(self, sample_blog_post):
        """Test complete image generation pipeline"""
        agent = ImageAgent()

        # Generate images
        images = agent.generate_images(sample_blog_post, count=2)
        assert len(images) > 0

        # Select best
        best = agent.select_best_image(images, sample_blog_post)
        assert best is not None

        # Upload
        url = agent.upload_image(best, f"blog_{sample_blog_post.title}.jpg")
        assert url.startswith("https://")

    @pytest.mark.skip(reason="Requires Pexels API key")
    def test_pexels_search_integration(self):
        """Test actual Pexels API search"""
        agent = ImageAgent()
        results = agent.search_pexels("technology", per_page=5)

        assert len(results) > 0
        assert all("src" in r for r in results)


class TestErrorHandling:
    """Test error handling and edge cases"""

    def test_handles_missing_blog_post_fields(self, image_agent):
        """Test handling of blog post with missing fields"""
        incomplete_post = BlogPost(title="Test", content="Content")

        # Should not raise exception
        prompt = image_agent._create_image_prompt(incomplete_post)
        assert isinstance(prompt, str)

    def test_handles_empty_string_inputs(self, image_agent, mock_clients):
        """Test handling of empty string inputs"""
        _, pexels, _ = mock_clients
        pexels.search.return_value = []

        results = image_agent.search_pexels("")
        assert results == [] or results is None

    def test_handles_none_inputs(self, image_agent):
        """Test handling of None inputs"""
        result = image_agent.select_best_image(None, None)
        assert result is None


@pytest.mark.performance
class TestImageAgentPerformance:
    """Performance tests for ImageAgent"""

    def test_image_generation_performance(self, image_agent, sample_blog_post, mock_clients):
        """Test that image generation completes within acceptable time"""
        import time

        image_gen, _, _ = mock_clients
        image_gen.generate_image.return_value = "test_image.jpg"

        start = time.time()
        with patch.object(image_agent, "_create_image_prompt", return_value="test"):
            images = image_agent.generate_images(sample_blog_post, count=5)
        duration = time.time() - start

        # Should complete quickly with mocked clients
        assert duration < 1.0
        assert len(images) == 5
