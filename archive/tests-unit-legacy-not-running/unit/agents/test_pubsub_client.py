"""
Tests for PubSub Client
Tests Google Cloud Pub/Sub integration
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
import types
import json

# Mock Google Cloud modules
if "google" not in sys.modules:
    sys.modules["google"] = types.ModuleType("google")
if "google.cloud" not in sys.modules:
    sys.modules["google.cloud"] = types.ModuleType("google.cloud")
if "google.cloud.pubsub_v1" not in sys.modules:
    sys.modules["google.cloud.pubsub_v1"] = types.ModuleType("google.cloud.pubsub_v1")


@pytest.fixture
def mock_pubsub_publisher():
    """Mock Pub/Sub publisher client"""
    mock_publisher = Mock()
    mock_future = Mock()
    mock_future.result.return_value = "message-id-123"
    mock_publisher.publish.return_value = mock_future
    return mock_publisher


@pytest.fixture
def mock_pubsub_subscriber():
    """Mock Pub/Sub subscriber client"""
    mock_subscriber = Mock()
    return mock_subscriber


@pytest.fixture
def mock_config():
    """Mock configuration"""
    with patch('services.pubsub_client.config') as mock_cfg:
        mock_cfg.GCP_PROJECT_ID = "test-project"
        mock_cfg.PUBSUB_TOPIC = "test-topic"
        mock_cfg.PUBSUB_SUBSCRIPTION = "test-subscription"
        yield mock_cfg


class TestPubSubClientInitialization:
    """Test PubSub client initialization"""
    
    def test_client_initializes_with_config(self, mock_config, mock_pubsub_publisher):
        """Test that client initializes with project and topic"""
        with patch('services.pubsub_client.pubsub_v1.PublisherClient', return_value=mock_pubsub_publisher):
            from services.pubsub_client import PubSubClient
            client = PubSubClient()
            
            assert client.project_id == "test-project"
            assert client.topic_name == "test-topic"


class TestMessagePublishing:
    """Test message publishing"""
    
    def test_publish_message(self, mock_config, mock_pubsub_publisher):
        """Test publishing a message to Pub/Sub"""
        with patch('services.pubsub_client.pubsub_v1.PublisherClient', return_value=mock_pubsub_publisher):
            from services.pubsub_client import PubSubClient
            client = PubSubClient()
            
            message_data = {"task_id": "test-123", "action": "create_post"}
            result = client.publish(message_data)
            
            mock_pubsub_publisher.publish.assert_called_once()
            assert result is not None
    
    def test_publish_encodes_json(self, mock_config, mock_pubsub_publisher):
        """Test that message is JSON encoded"""
        with patch('services.pubsub_client.pubsub_v1.PublisherClient', return_value=mock_pubsub_publisher):
            from services.pubsub_client import PubSubClient
            client = PubSubClient()
            
            message_data = {"key": "value"}
            client.publish(message_data)
            
            call_args = mock_pubsub_publisher.publish.call_args
            published_data = call_args[0][1]  # Second arg is the data
            
            # Should be bytes
            assert isinstance(published_data, bytes)
            
            # Should decode to original JSON
            decoded = json.loads(published_data.decode('utf-8'))
            assert decoded["key"] == "value"


class TestMessageSubscription:
    """Test message subscription"""
    
    def test_subscribe_to_messages(self, mock_config, mock_pubsub_subscriber):
        """Test subscribing to messages"""
        with patch('services.pubsub_client.pubsub_v1.SubscriberClient', return_value=mock_pubsub_subscriber):
            from services.pubsub_client import PubSubClient
            client = PubSubClient()
            
            callback = Mock()
            client.subscribe(callback)
            
            mock_pubsub_subscriber.subscribe.assert_called_once()


class TestErrorHandling:
    """Test error handling"""
    
    def test_handles_publish_error(self, mock_config, mock_pubsub_publisher):
        """Test handling of publish errors"""
        mock_pubsub_publisher.publish.side_effect = Exception("Publish failed")
        
        with patch('services.pubsub_client.pubsub_v1.PublisherClient', return_value=mock_pubsub_publisher):
            from services.pubsub_client import PubSubClient
            client = PubSubClient()
            
            result = client.publish({"test": "data"})
            
            # Should handle error gracefully
            assert result is None or result == ""


@pytest.mark.integration
class TestPubSubClientIntegration:
    """Integration tests (require actual Pub/Sub)"""
    
    @pytest.mark.skip(reason="Requires actual GCP Pub/Sub")
    def test_real_publish_subscribe(self):
        """Test with real Pub/Sub"""
        from services.pubsub_client import PubSubClient
        
        client = PubSubClient()
        
        # Publish
        message = {"test": "integration"}
        message_id = client.publish(message)
        
        assert message_id is not None
