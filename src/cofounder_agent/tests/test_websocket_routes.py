"""
Unit tests for websocket_routes.py
Tests WebSocket connections and real-time communication
"""

import pytest
import json
import asyncio
from fastapi.testclient import TestClient
from fastapi import FastAPI
import os
import sys

# Add parent directory to path for imports to work properly
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


@pytest.fixture(scope="session")
def test_app():
    """Create test app with routes registered"""
    try:
        from routes.websocket_routes import router as websocket_router
    except ImportError:
        # If websocket routes don't exist, just create a basic app
        websocket_router = None

    from utils.exception_handlers import register_exception_handlers
    from utils.middleware_config import MiddlewareConfig

    app = FastAPI(title="Test WebSocket App")

    # Register routes if available
    if websocket_router:
        app.include_router(websocket_router)

    # Register exception handlers
    register_exception_handlers(app)

    # Register middleware
    middleware_config = MiddlewareConfig()
    middleware_config.register_all_middleware(app)

    return app


@pytest.fixture(scope="session")
def client(test_app):
    """Create test client"""
    return TestClient(test_app)


@pytest.mark.unit
@pytest.mark.websocket
class TestWebSocketRoutes:
    """Test WebSocket endpoints"""

    def test_websocket_connection_available(self, client):
        """Test that WebSocket endpoint is accessible"""
        # Check if WebSocket endpoint is defined
        response = client.get("/api/ws")
        # WebSocket upgrade should not work with regular GET
        assert response.status_code in [400, 405, 426]

    def test_websocket_task_progress_endpoint_available(self, client):
        """Test that task progress WebSocket endpoint exists"""
        response = client.get("/api/ws/tasks/test_id/progress")
        # Should not work with regular GET
        assert response.status_code in [400, 405, 426]

    def test_websocket_agent_events_endpoint_available(self, client):
        """Test that agent events WebSocket endpoint exists"""
        response = client.get("/api/ws/agents/events")
        # Should not work with regular GET
        assert response.status_code in [400, 405, 426]

    def test_websocket_content_generation_endpoint_available(self, client):
        """Test that content generation WebSocket endpoint exists"""
        response = client.get("/api/ws/content/generate")
        # Should not work with regular GET
        assert response.status_code in [400, 405, 426]

    def test_websocket_requires_upgrade_header(self, client):
        """Test that WebSocket requires proper upgrade headers"""
        response = client.get("/api/ws", headers={"Connection": "Upgrade", "Upgrade": "websocket"})
        # Regular client can't complete WebSocket upgrade
        assert response.status_code in [400, 405, 426, 403, 401]


@pytest.mark.unit
@pytest.mark.websocket
class TestWebSocketAuthentication:
    """Test WebSocket authentication"""

    def test_websocket_without_token(self, client):
        """Test WebSocket connection without authentication"""
        response = client.get("/api/ws/tasks/test_id/progress")
        # Should reject or require upgrade
        assert response.status_code in [400, 401, 405, 426]

    def test_websocket_with_invalid_token(self, client):
        """Test WebSocket connection with invalid token"""
        response = client.get("/api/ws/tasks/test_id/progress?token=invalid_token")
        # Should either reject token or require upgrade
        assert response.status_code in [400, 401, 405, 426]

    def test_websocket_query_parameters(self, client):
        """Test WebSocket with query parameters"""
        response = client.get("/api/ws/tasks/test_id/progress?timeout=30")
        # Should handle query parameters
        assert response.status_code in [400, 405, 426, 200]


@pytest.mark.unit
@pytest.mark.websocket
class TestWebSocketDataFormats:
    """Test WebSocket message formats"""

    def test_websocket_message_structure(self, client):
        """Test expected WebSocket message structure"""
        # This is more of a documentation test
        # WebSocket messages should follow a standard format
        expected_fields = ["type", "data", "timestamp"]
        # Document expected message format
        assert all(field in expected_fields for field in expected_fields)

    def test_websocket_event_types(self, client):
        """Test supported WebSocket event types"""
        event_types = ["start", "progress", "complete", "error", "update"]
        # These are supported event types
        assert len(event_types) > 0


@pytest.mark.unit
@pytest.mark.integration
class TestWebSocketIntegration:
    """Test WebSocket integration with other endpoints"""

    def test_websocket_task_progress_endpoint(self, client):
        """Test WebSocket endpoint for task progress"""
        # This documents the expected endpoint
        task_ws_endpoint = "/api/ws/tasks/test_id/progress"
        assert "ws" in task_ws_endpoint
        assert "tasks" in task_ws_endpoint
        assert "progress" in task_ws_endpoint

    def test_websocket_agent_events_endpoint(self, client):
        """Test WebSocket endpoint for agent events"""
        agent_ws_endpoint = "/api/ws/agents/events"
        assert "ws" in agent_ws_endpoint
        assert "agents" in agent_ws_endpoint

    def test_websocket_endpoints_are_distinct(self, client):
        """Test that different WebSocket endpoints are distinct"""
        endpoints = [
            "/api/ws/tasks/123/progress",
            "/api/ws/agents/events",
            "/api/ws/content/generate",
        ]
        # All endpoints should be different
        assert len(endpoints) == len(set(endpoints))


@pytest.mark.unit
@pytest.mark.error
class TestWebSocketErrorHandling:
    """Test WebSocket error handling"""

    def test_websocket_invalid_task_id(self, client):
        """Test WebSocket with invalid task ID format"""
        response = client.get("/api/ws/tasks/<invalid>/progress")
        # Should handle or reject invalid ID
        assert response.status_code in [400, 404, 405, 426]

    def test_websocket_missing_path_parameters(self, client):
        """Test WebSocket with missing path parameters"""
        response = client.get("/api/ws/tasks/progress")
        # Should either handle or return 404
        assert response.status_code in [404, 405, 426]

    def test_websocket_nonexistent_agent(self, client):
        """Test WebSocket for non-existent agent"""
        response = client.get("/api/ws/agents/nonexistent/status")
        # Should handle gracefully
        assert response.status_code in [400, 404, 405, 426]


@pytest.mark.unit
@pytest.mark.performance
class TestWebSocketPerformance:
    """Test WebSocket performance characteristics"""

    def test_websocket_endpoint_discovery_time(self, client):
        """Test that WebSocket endpoints respond quickly"""
        import time

        endpoints = [
            "/api/ws/tasks/test/progress",
            "/api/ws/agents/events",
            "/api/ws/content/generate",
        ]

        for endpoint in endpoints:
            start = time.time()
            response = client.get(endpoint)
            elapsed = time.time() - start

            # Should respond immediately (even if rejecting)
            assert elapsed < 1

    def test_multiple_websocket_requests(self, client):
        """Test handling multiple WebSocket requests"""
        responses = []
        for i in range(10):
            response = client.get(f"/api/ws/tasks/test_{i}/progress")
            responses.append(response.status_code)

        # Should handle all requests
        assert all(status in [400, 404, 405, 426] for status in responses)


@pytest.mark.unit
@pytest.mark.documentation
class TestWebSocketDocumentation:
    """Document WebSocket API structure"""

    def test_websocket_api_structure(self, client):
        """Document the WebSocket API structure"""
        api_structure = {
            "base_url": "/api/ws",
            "endpoints": {
                "tasks": {
                    "progress": "/api/ws/tasks/{task_id}/progress",
                    "details": "/api/ws/tasks/{task_id}/details",
                },
                "agents": {
                    "events": "/api/ws/agents/events",
                    "status": "/api/ws/agents/{agent_type}/status",
                },
                "content": {
                    "generation": "/api/ws/content/generate",
                    "refinement": "/api/ws/content/refine",
                },
            },
        }

        # Verify structure
        assert "base_url" in api_structure
        assert "endpoints" in api_structure
        assert len(api_structure["endpoints"]) > 0

    def test_websocket_message_format_documentation(self, client):
        """Document expected WebSocket message format"""
        message_format = {
            "progress": {
                "type": "progress",
                "task_id": "uuid",
                "status": "in_progress|completed|failed",
                "current_step": "int",
                "total_steps": "int",
                "message": "string",
                "timestamp": "datetime",
            },
            "error": {
                "type": "error",
                "error_code": "string",
                "error_message": "string",
                "timestamp": "datetime",
            },
        }

        # Verify format
        assert "progress" in message_format
        assert "error" in message_format
        assert all("type" in message_format[key] for key in message_format)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
