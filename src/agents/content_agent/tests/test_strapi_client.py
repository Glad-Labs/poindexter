"""
Tests for Strapi Client
Tests API integration with Strapi v5 CMS
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import requests
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
    with patch('services.strapi_client.config') as mock_cfg:
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
        topic="Test Topic",
        primary_keyword="test",
        target_audience="testers",
        category="Testing"
    )
    post.title = "Test Blog Post"
    post.slug = "test-blog-post"
    post.body_content_blocks = [
        {"type": "paragraph", "children": [{"type": "text", "text": "Test content"}]}
    ]
    return post


class TestStrapiClientInitialization:
    """Test Strapi client initialization"""
    
    def test_client_initializes_with_config(self, mock_config):
        """Test that client initializes with API URL and token"""
        client = StrapiClient()
        
        assert client.api_url == "https://strapi.example.com"
        assert client.api_token == "test-token-123"
    
    def test_client_requires_api_url(self):
        """Test that client requires API URL"""
        with patch('services.strapi_client.config') as mock_cfg:
            mock_cfg.STRAPI_API_URL = None
            mock_cfg.STRAPI_API_TOKEN = "token"
            
            with pytest.raises((ValueError, AttributeError)):
                StrapiClient()
    
    def test_client_requires_api_token(self):
        """Test that client requires API token"""
        with patch('services.strapi_client.config') as mock_cfg:
            mock_cfg.STRAPI_API_URL = "https://strapi.example.com"
            mock_cfg.STRAPI_API_TOKEN = None
            
            with pytest.raises((ValueError, AttributeError)):
                StrapiClient()


class TestCreatePost:
    """Test post creation"""
    
    def test_create_post_makes_api_request(self, strapi_client, sample_blog_post):
        """Test that create_post makes POST request to Strapi"""
        with patch('services.strapi_client.requests.post') as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {
                "data": {"id": 123, "documentId": "doc-123"}
            }
            mock_response.raise_for_status = Mock()
            mock_post.return_value = mock_response
            
            post_id, post_url = strapi_client.create_post(sample_blog_post)
            
            mock_post.assert_called_once()
            assert post_id is not None
    
    def test_create_post_includes_auth_header(self, strapi_client, sample_blog_post):
        """Test that request includes authorization header"""
        with patch('services.strapi_client.requests.post') as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {"data": {"id": 123}}
            mock_response.raise_for_status = Mock()
            mock_post.return_value = mock_response
            
            strapi_client.create_post(sample_blog_post)
            
            call_args = mock_post.call_args
            headers = call_args[1]['headers']
            assert 'Authorization' in headers
            assert 'Bearer test-token-123' in headers['Authorization']
    
    def test_create_post_sends_correct_data(self, strapi_client, sample_blog_post):
        """Test that post data is formatted correctly"""
        with patch('services.strapi_client.requests.post') as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {"data": {"id": 123}}
            mock_response.raise_for_status = Mock()
            mock_post.return_value = mock_response
            
            strapi_client.create_post(sample_blog_post)
            
            call_args = mock_post.call_args
            json_data = call_args[1]['json']
            
            assert 'data' in json_data
            assert 'Title' in json_data['data']
            assert json_data['data']['Title'] == "Test Blog Post"
    
    def test_create_post_returns_id_and_url(self, strapi_client, sample_blog_post):
        """Test that create_post returns ID and URL"""
        with patch('services.strapi_client.requests.post') as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {
                "data": {
                    "id": 456,
                    "documentId": "doc-456"
                }
            }
            mock_response.raise_for_status = Mock()
            mock_post.return_value = mock_response
            
            post_id, post_url = strapi_client.create_post(sample_blog_post)
            
            assert post_id == 456
            assert "strapi.example.com" in post_url


class TestGetPost:
    """Test post retrieval"""
    
    def test_get_post_makes_api_request(self, strapi_client):
        """Test that get_post makes GET request"""
        with patch('services.strapi_client.requests.get') as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {
                "data": {
                    "id": 123,
                    "Title": "Test Post"
                }
            }
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response
            
            post = strapi_client.get_post(123)
            
            mock_get.assert_called_once()
            assert post is not None
    
    def test_get_post_includes_auth_header(self, strapi_client):
        """Test that GET request includes authorization"""
        with patch('services.strapi_client.requests.get') as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {"data": {"id": 123}}
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response
            
            strapi_client.get_post(123)
            
            call_args = mock_get.call_args
            headers = call_args[1]['headers']
            assert 'Authorization' in headers


class TestUpdatePost:
    """Test post updating"""
    
    def test_update_post_makes_put_request(self, strapi_client, sample_blog_post):
        """Test that update_post makes PUT request"""
        with patch('services.strapi_client.requests.put') as mock_put:
            mock_response = Mock()
            mock_response.json.return_value = {"data": {"id": 123}}
            mock_response.raise_for_status = Mock()
            mock_put.return_value = mock_response
            
            result = strapi_client.update_post(123, sample_blog_post)
            
            mock_put.assert_called_once()
    
    def test_update_post_sends_updated_data(self, strapi_client, sample_blog_post):
        """Test that update sends correct data"""
        with patch('services.strapi_client.requests.put') as mock_put:
            mock_response = Mock()
            mock_response.json.return_value = {"data": {"id": 123}}
            mock_response.raise_for_status = Mock()
            mock_put.return_value = mock_response
            
            sample_blog_post.title = "Updated Title"
            strapi_client.update_post(123, sample_blog_post)
            
            call_args = mock_put.call_args
            json_data = call_args[1]['json']
            assert json_data['data']['Title'] == "Updated Title"


class TestErrorHandling:
    """Test error handling"""
    
    def test_handles_http_error(self, strapi_client, sample_blog_post):
        """Test handling of HTTP errors"""
        with patch('services.strapi_client.requests.post') as mock_post:
            mock_post.side_effect = requests.exceptions.HTTPError("404")
            
            result = strapi_client.create_post(sample_blog_post)
            
            # Should return None or empty on error
            assert result is None or result == (None, None)
    
    def test_handles_connection_error(self, strapi_client, sample_blog_post):
        """Test handling of connection errors"""
        with patch('services.strapi_client.requests.post') as mock_post:
            mock_post.side_effect = requests.exceptions.ConnectionError("Connection failed")
            
            result = strapi_client.create_post(sample_blog_post)
            
            assert result is None or result == (None, None)
    
    def test_handles_timeout(self, strapi_client, sample_blog_post):
        """Test handling of request timeouts"""
        with patch('services.strapi_client.requests.post') as mock_post:
            mock_post.side_effect = requests.exceptions.Timeout("Request timeout")
            
            result = strapi_client.create_post(sample_blog_post)
            
            assert result is None or result == (None, None)
    
    def test_handles_invalid_json_response(self, strapi_client, sample_blog_post):
        """Test handling of invalid JSON in response"""
        with patch('services.strapi_client.requests.post') as mock_post:
            mock_response = Mock()
            mock_response.json.side_effect = ValueError("Invalid JSON")
            mock_response.raise_for_status = Mock()
            mock_post.return_value = mock_response
            
            result = strapi_client.create_post(sample_blog_post)
            
            assert result is None or result == (None, None)


@pytest.mark.integration
class TestStrapiClientIntegration:
    """Integration tests (require real Strapi instance)"""
    
    @pytest.mark.skip(reason="Requires actual Strapi instance")
    def test_real_post_creation(self):
        """Test with real Strapi instance"""
        client = StrapiClient()
        
        post = BlogPost(
            topic="Integration Test",
            primary_keyword="test",
            target_audience="testers",
            category="Testing"
        )
        post.title = "Integration Test Post"
        post.slug = "integration-test-post"
        post.body_content_blocks = [{"type": "paragraph", "children": [{"type": "text", "text": "Test"}]}]
        
        post_id, post_url = client.create_post(post)
        
        assert post_id is not None
        assert post_url is not None
        
        # Clean up
        # client.delete_post(post_id)


@pytest.mark.performance
class TestStrapiClientPerformance:
    """Performance tests"""
    
    def test_create_post_performance(self, strapi_client, sample_blog_post):
        """Test that post creation completes quickly"""
        import time
        
        with patch('services.strapi_client.requests.post') as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {"data": {"id": 123}}
            mock_response.raise_for_status = Mock()
            mock_post.return_value = mock_response
            
            start = time.time()
            strapi_client.create_post(sample_blog_post)
            duration = time.time() - start
        
        assert duration < 1.0
