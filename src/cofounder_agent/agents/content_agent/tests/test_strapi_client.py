"""
Tests for Strapi Client
Tests API integration with Strapi v5 CMS
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
import httpx
import sys
import types

# Mock Google Cloud modules
if "google" not in sys.modules:
    sys.modules["google"] = types.ModuleType("google")
if "google.cloud" not in sys.modules:
    sys.modules["google.cloud"] = types.ModuleType("google.cloud")

from services.strapi_client import StrapiClient
from utils.data_models import BlogPost


@pytest.fixture
def mock_config():
    """Mock configuration"""
    with patch("services.strapi_client.config") as mock_cfg:
        mock_cfg.STRAPI_API_URL = "https://strapi.example.com"
        mock_cfg.STRAPI_API_TOKEN = "test-token-123"
        yield mock_cfg


@pytest.fixture
def strapi_client(mock_config):
    """Create StrapiClient with mocked config"""
    client = StrapiClient()
    return client


@pytest.fixture
def sample_blog_post():
    """Create sample blog post"""
    post = BlogPost(
        topic="Test Topic", primary_keyword="test", target_audience="testers", category="Testing"
    )
    post.title = "Test Blog Post"
    post.slug = "test-blog-post"
    post.body_content_blocks = [
        {"type": "paragraph", "children": [{"type": "text", "text": "Test content"}]}
    ]
    return post


class TestStrapiClientInitialization:
    """Test Strapi client initialization"""

    @pytest.mark.asyncio
    async def test_client_initializes_with_config(self, mock_config):
        """Test that client initializes with API URL and token"""
        client = StrapiClient()

        assert client.api_url == "https://strapi.example.com"
        assert client.api_token == "test-token-123"

    @pytest.mark.asyncio
    async def test_client_requires_api_url(self):
        """Test that client requires API URL"""
        with patch("services.strapi_client.config") as mock_cfg:
            mock_cfg.STRAPI_API_URL = None
            mock_cfg.STRAPI_API_TOKEN = "token"

            with pytest.raises((ValueError, AttributeError)):
                StrapiClient()

    @pytest.mark.asyncio
    async def test_client_requires_api_token(self):
        """Test that client requires API token"""
        with patch("services.strapi_client.config") as mock_cfg:
            mock_cfg.STRAPI_API_URL = "https://strapi.example.com"
            mock_cfg.STRAPI_API_TOKEN = None

            with pytest.raises((ValueError, AttributeError)):
                StrapiClient()


class TestCreatePost:
    """Test post creation"""

    @pytest.mark.asyncio
    async def test_create_post_makes_api_request(self, strapi_client, sample_blog_post):
        """Test that create_post makes POST request to Strapi"""
        mock_response = AsyncMock()
        mock_response.json.return_value = {"data": {"id": 123, "documentId": "doc-123"}}
        mock_response.raise_for_status = Mock()

        with patch("services.strapi_client.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            post_id, post_url = await strapi_client.create_post(sample_blog_post)

            mock_client.post.assert_called_once()
            assert post_id is not None

    @pytest.mark.asyncio
    async def test_create_post_includes_auth_header(self, strapi_client, sample_blog_post):
        """Test that request includes authorization header"""
        mock_response = AsyncMock()
        mock_response.json.return_value = {"data": {"id": 123}}
        mock_response.raise_for_status = Mock()

        with patch("services.strapi_client.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            await strapi_client.create_post(sample_blog_post)

            call_args = mock_client.post.call_args
            headers = call_args[1]["headers"]
            assert "Authorization" in headers
            assert "Bearer test-token-123" in headers["Authorization"]

    @pytest.mark.asyncio
    async def test_create_post_sends_correct_data(self, strapi_client, sample_blog_post):
        """Test that post data is formatted correctly"""
        mock_response = AsyncMock()
        mock_response.json.return_value = {"data": {"id": 123}}
        mock_response.raise_for_status = Mock()

        with patch("services.strapi_client.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            await strapi_client.create_post(sample_blog_post)

            call_args = mock_client.post.call_args
            json_data = call_args[1]["json"]

            assert "data" in json_data
            assert "Title" in json_data["data"]
            assert json_data["data"]["Title"] == "Test Blog Post"

    @pytest.mark.asyncio
    async def test_create_post_returns_id_and_url(self, strapi_client, sample_blog_post):
        """Test that create_post returns ID and URL"""
        mock_response = AsyncMock()
        mock_response.json.return_value = {"data": {"id": 456, "documentId": "doc-456"}}
        mock_response.raise_for_status = Mock()

        with patch("services.strapi_client.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            post_id, post_url = await strapi_client.create_post(sample_blog_post)

            assert post_id == 456
            assert "strapi.example.com" in post_url


class TestGetPost:
    """Test post retrieval"""

    @pytest.mark.asyncio
    async def test_get_post_makes_api_request(self, strapi_client):
        """Test that get_post makes GET request"""
        mock_response = AsyncMock()
        mock_response.json.return_value = {"data": {"id": 123, "Title": "Test Post"}}
        mock_response.raise_for_status = Mock()

        with patch("services.strapi_client.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            post = await strapi_client.get_post(123)

            mock_client.get.assert_called_once()
            assert post is not None

    @pytest.mark.asyncio
    async def test_get_post_includes_auth_header(self, strapi_client):
        """Test that GET request includes authorization"""
        mock_response = AsyncMock()
        mock_response.json.return_value = {"data": {"id": 123}}
        mock_response.raise_for_status = Mock()

        with patch("services.strapi_client.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            await strapi_client.get_post(123)

            call_args = mock_client.get.call_args
            headers = call_args[1]["headers"]
            assert "Authorization" in headers


class TestUpdatePost:
    """Test post updating"""

    @pytest.mark.asyncio
    async def test_update_post_makes_put_request(self, strapi_client, sample_blog_post):
        """Test that update_post makes PUT request"""
        mock_response = AsyncMock()
        mock_response.json.return_value = {"data": {"id": 123}}
        mock_response.raise_for_status = Mock()

        with patch("services.strapi_client.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.put.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await strapi_client.update_post(123, sample_blog_post)

            mock_client.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_post_sends_updated_data(self, strapi_client, sample_blog_post):
        """Test that update sends correct data"""
        mock_response = AsyncMock()
        mock_response.json.return_value = {"data": {"id": 123}}
        mock_response.raise_for_status = Mock()

        with patch("services.strapi_client.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.put.return_value = mock_response
            mock_client_class.return_value = mock_client

            sample_blog_post.title = "Updated Title"
            await strapi_client.update_post(123, sample_blog_post)

            call_args = mock_client.put.call_args
            json_data = call_args[1]["json"]
            assert json_data["data"]["Title"] == "Updated Title"


class TestErrorHandling:
    """Test error handling"""

    @pytest.mark.asyncio
    async def test_handles_http_error(self, strapi_client, sample_blog_post):
        """Test handling of HTTP errors"""
        with patch("services.strapi_client.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.side_effect = httpx.HTTPError("404")
            mock_client_class.return_value = mock_client

            result = await strapi_client.create_post(sample_blog_post)

            # Should return None or empty on error
            assert result is None or result == (None, None)

    @pytest.mark.asyncio
    async def test_handles_connection_error(self, strapi_client, sample_blog_post):
        """Test handling of connection errors"""
        with patch("services.strapi_client.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.side_effect = httpx.ConnectError("Connection failed")
            mock_client_class.return_value = mock_client

            result = await strapi_client.create_post(sample_blog_post)

            assert result is None or result == (None, None)

    @pytest.mark.asyncio
    async def test_handles_timeout(self, strapi_client, sample_blog_post):
        """Test handling of request timeouts"""
        with patch("services.strapi_client.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.side_effect = httpx.TimeoutException("Request timeout")
            mock_client_class.return_value = mock_client

            result = await strapi_client.create_post(sample_blog_post)

            assert result is None or result == (None, None)

    @pytest.mark.asyncio
    async def test_handles_invalid_json_response(self, strapi_client, sample_blog_post):
        """Test handling of invalid JSON in response"""
        mock_response = AsyncMock()
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.raise_for_status = Mock()

        with patch("services.strapi_client.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await strapi_client.create_post(sample_blog_post)

            assert result is None or result == (None, None)


@pytest.mark.integration
class TestStrapiClientIntegration:
    """Integration tests (require real Strapi instance)"""

    @pytest.mark.skip(reason="Requires actual Strapi instance")
    @pytest.mark.asyncio
    async def test_real_post_creation(self):
        """Test with real Strapi instance"""
        client = StrapiClient()

        post = BlogPost(
            topic="Integration Test",
            primary_keyword="test",
            target_audience="testers",
            category="Testing",
        )
        post.title = "Integration Test Post"
        post.slug = "integration-test-post"
        post.body_content_blocks = [
            {"type": "paragraph", "children": [{"type": "text", "text": "Test"}]}
        ]

        post_id, post_url = await client.create_post(post)

        assert post_id is not None
        assert post_url is not None

        # Clean up
        # await client.delete_post(post_id)


@pytest.mark.performance
class TestStrapiClientPerformance:
    """Performance tests"""

    @pytest.mark.asyncio
    async def test_create_post_performance(self, strapi_client, sample_blog_post):
        """Test that post creation completes quickly"""
        import time

        mock_response = AsyncMock()
        mock_response.json.return_value = {"data": {"id": 123}}
        mock_response.raise_for_status = Mock()

        with patch("services.strapi_client.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            start = time.time()
            await strapi_client.create_post(sample_blog_post)
            duration = time.time() - start

        assert duration < 1.0
