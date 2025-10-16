"""
Integration tests for content pipeline
Tests end-to-end content creation workflow: API → Firestore → Content Agent → Strapi → Public Site
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock slowapi before importing main
class MockRateLimitExceeded(Exception):
    """Mock exception for rate limiting"""
    pass

slowapi_mock = MagicMock()
slowapi_mock.Limiter = MagicMock()
slowapi_mock._rate_limit_exceeded_handler = MagicMock()
slowapi_util_mock = MagicMock()
slowapi_util_mock.get_remote_address = MagicMock()
slowapi_errors_mock = MagicMock()
slowapi_errors_mock.RateLimitExceeded = MockRateLimitExceeded

sys.modules['slowapi'] = slowapi_mock
sys.modules['slowapi.util'] = slowapi_util_mock
sys.modules['slowapi.errors'] = slowapi_errors_mock

from fastapi.testclient import TestClient
from main import app


class TestContentPipelineIntegration:
    """Integration tests for the content creation pipeline"""
    
    @pytest.fixture
    def client(self):
        """FastAPI test client"""
        return TestClient(app)
    
    @pytest.fixture
    def mock_firestore(self):
        """Mock Firestore client for testing"""
        with patch('main.firestore_client') as mock_fs:
            mock_fs.add_content_task = AsyncMock(return_value="test-task-123")
            mock_fs.get_content_task = AsyncMock(return_value={
                "id": "test-task-123",
                "topic": "Test Topic",
                "status": "New",
                "category": "technology",
                "created_at": datetime.utcnow().isoformat()
            })
            mock_fs.get_task_runs = AsyncMock(return_value=[])
            mock_fs.log_webhook_event = AsyncMock(return_value="webhook-123")
            yield mock_fs
    
    @pytest.fixture
    def mock_pubsub(self):
        """Mock Pub/Sub client for testing"""
        with patch('main.pubsub_client') as mock_ps:
            mock_ps.publish_content_request = AsyncMock(return_value="msg-123")
            yield mock_ps
    
    def test_create_content_endpoint_exists(self, client):
        """Test that the content creation endpoint exists"""
        response = client.post(
            "/api/content/create",
            json={
                "topic": "Test Topic",
                "primary_keyword": "test, keywords",
                "target_audience": "developers",
                "category": "technology",
                "auto_publish": False
            }
        )
        # Should not return 404
        assert response.status_code != 404
    
    def test_create_content_requires_topic(self, client):
        """Test that content creation requires a topic"""
        response = client.post(
            "/api/content/create",
            json={
                "topic": "",  # Empty topic
                "primary_keyword": "test",
                "target_audience": "developers",
                "category": "technology"
            }
        )
        assert response.status_code == 400
        assert "Topic must be at least 3 characters" in response.json()["detail"]
    
    @patch('main.GOOGLE_CLOUD_AVAILABLE', False)
    def test_create_content_dev_mode(self, client):
        """Test content creation in development mode (no Google Cloud)"""
        response = client.post(
            "/api/content/create",
            json={
                "topic": "Test Topic for Dev Mode",
                "primary_keyword": "test",
                "target_audience": "developers",
                "category": "technology",
                "auto_publish": False
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["topic"] == "Test Topic for Dev Mode"
        assert "dev-content-" in data["task_id"]
        assert data["message"].endswith("(development mode)")
    
    @patch('main.GOOGLE_CLOUD_AVAILABLE', True)
    def test_create_content_with_google_cloud(self, client, mock_firestore, mock_pubsub):
        """Test content creation with Google Cloud services"""
        response = client.post(
            "/api/content/create",
            json={
                "topic": "AI-Powered Content Creation",
                "primary_keyword": "AI, machine learning, automation",
                "target_audience": "tech enthusiasts",
                "category": "artificial-intelligence",
                "auto_publish": True
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["task_id"] == "test-task-123"
        assert data["auto_publish"] == True
        assert "/api/content/status/" in data["tracking_url"]
    
    @patch('main.GOOGLE_CLOUD_AVAILABLE', False)
    def test_get_content_status_dev_mode(self, client):
        """Test getting content status in dev mode"""
        response = client.get("/api/content/status/test-task-123")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unknown"
        assert "development mode" in data["message"]
    
    @patch('main.GOOGLE_CLOUD_AVAILABLE', True)
    def test_get_content_status_with_google_cloud(self, client, mock_firestore):
        """Test getting content status with Google Cloud"""
        response = client.get("/api/content/status/test-task-123")
        
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "test-task-123"
        assert data["topic"] == "Test Topic"
        assert data["status"] == "New"
    
    @patch('main.GOOGLE_CLOUD_AVAILABLE', True)
    def test_get_content_status_not_found(self, client, mock_firestore):
        """Test getting status for non-existent task"""
        mock_firestore.get_content_task.return_value = None
        
        response = client.get("/api/content/status/non-existent-task")
        
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]
    
    def test_webhook_endpoint_exists(self, client):
        """Test that the webhook endpoint exists"""
        response = client.post(
            "/api/webhooks/content-created",
            json={
                "event": "entry.create",
                "model": "article",
                "entry": {
                    "id": 123,
                    "title": "Test Article"
                }
            }
        )
        # Should not return 404
        assert response.status_code != 404
    
    def test_webhook_requires_valid_payload(self, client):
        """Test that webhook requires valid payload"""
        response = client.post(
            "/api/webhooks/content-created",
            json={
                "event": "",  # Invalid empty event
                "model": "article",
                "entry": {}
            }
        )
        assert response.status_code == 400
        assert "Invalid webhook payload" in response.json()["detail"]
    
    def test_webhook_requires_entry_id(self, client):
        """Test that webhook requires entry ID"""
        response = client.post(
            "/api/webhooks/content-created",
            json={
                "event": "entry.create",
                "model": "article",
                "entry": {
                    "title": "Test Article"
                    # Missing "id"
                }
            }
        )
        assert response.status_code == 400
        assert "Entry ID missing" in response.json()["detail"]
    
    @patch('main.GOOGLE_CLOUD_AVAILABLE', True)
    def test_webhook_entry_create(self, client, mock_firestore):
        """Test webhook handling for entry.create event"""
        response = client.post(
            "/api/webhooks/content-created",
            json={
                "event": "entry.create",
                "model": "article",
                "entry": {
                    "id": 456,
                    "title": "New Test Article"
                }
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "received"
        assert data["event"] == "entry.create"
        assert data["entry_id"] == 456
        
        # Verify Firestore logging was called
        mock_firestore.log_webhook_event.assert_called_once()
    
    @patch('main.GOOGLE_CLOUD_AVAILABLE', True)
    def test_webhook_entry_publish(self, client, mock_firestore):
        """Test webhook handling for entry.publish event (triggers rebuild)"""
        response = client.post(
            "/api/webhooks/content-created",
            json={
                "event": "entry.publish",
                "model": "article",
                "entry": {
                    "id": 789,
                    "title": "Published Article"
                }
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "received"
        assert data["event"] == "entry.publish"
        assert data["entry_id"] == 789
        
        # Verify Firestore logging was called
        mock_firestore.log_webhook_event.assert_called_once()
    
    def test_webhook_entry_unpublish(self, client):
        """Test webhook handling for entry.unpublish event"""
        response = client.post(
            "/api/webhooks/content-created",
            json={
                "event": "entry.unpublish",
                "model": "article",
                "entry": {
                    "id": 999,
                    "title": "Unpublished Article"
                }
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "received"
        assert data["event"] == "entry.unpublish"
        assert data["entry_id"] == 999
    
    def test_webhook_unknown_event(self, client):
        """Test webhook handling for unknown event types"""
        response = client.post(
            "/api/webhooks/content-created",
            json={
                "event": "entry.random_event",
                "model": "article",
                "entry": {
                    "id": 111,
                    "title": "Test Article"
                }
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"
        assert "not handled" in data["message"]


class TestContentPipelineEndToEnd:
    """End-to-end tests simulating the full content creation workflow"""
    
    @pytest.fixture
    def client(self):
        """FastAPI test client"""
        return TestClient(app)
    
    @patch('main.GOOGLE_CLOUD_AVAILABLE', False)
    def test_full_content_workflow_dev_mode(self, client):
        """
        Test complete content creation workflow in development mode:
        1. Create content request
        2. Check status
        3. Simulate Strapi webhook
        """
        # Step 1: Create content
        create_response = client.post(
            "/api/content/create",
            json={
                "topic": "The Future of AI in Software Development",
                "primary_keyword": "AI, software, automation, development",
                "target_audience": "software developers",
                "category": "technology",
                "auto_publish": True
            }
        )
        
        assert create_response.status_code == 200
        task_data = create_response.json()
        assert task_data["status"] == "queued"
        task_id = task_data["task_id"]
        
        # Step 2: Check status (should show queued/in-progress)
        status_response = client.get(f"/api/content/status/{task_id}")
        assert status_response.status_code == 200
        status_data = status_response.json()
        assert "task_id" in status_data
        
        # Step 3: Simulate Strapi webhook (content published)
        webhook_response = client.post(
            "/api/webhooks/content-created",
            json={
                "event": "entry.publish",
                "model": "article",
                "entry": {
                    "id": 1001,
                    "title": "The Future of AI in Software Development",
                    "publishedAt": datetime.utcnow().isoformat()
                }
            }
        )
        
        assert webhook_response.status_code == 200
        webhook_data = webhook_response.json()
        assert webhook_data["status"] == "received"
        assert webhook_data["entry_id"] == 1001


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
