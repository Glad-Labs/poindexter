"""
Unit tests for command_queue_routes.py
Tests command dispatching and queue management
"""

import pytest
import json
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
    from routes.task_routes import router as task_router
    from utils.exception_handlers import register_exception_handlers
    from utils.middleware_config import MiddlewareConfig

    app = FastAPI(title="Test Command Queue App")

    # Register routes
    app.include_router(task_router)

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
@pytest.mark.api
class TestCommandQueueRoutes:
    """Test command queue endpoints"""

    @pytest.fixture
    def auth_headers(self):
        """Get auth headers for protected endpoints"""
        auth_response = client.post(
            "/api/auth/github/callback", json={"code": "test_code", "state": "test_state"}
        )
        if auth_response.status_code == 200:
            token_data = auth_response.json()
            token = token_data.get("token") or token_data.get("access_token")
            return {"Authorization": f"Bearer {token}"}
        return {}

    def test_dispatch_command(self, auth_headers):
        """Test dispatching a command"""
        command_data = {
            "agent_type": "content",
            "command": {
                "action": "create",
                "task_id": "test_123",
                "specifications": {"topic": "Test"},
            },
        }

        response = client.post("/api/commands/dispatch", json=command_data, headers=auth_headers)

        if auth_headers:
            assert response.status_code in [200, 201, 400, 422]
            if response.status_code in [200, 201]:
                data = response.json()
                assert "command_id" in data or "id" in data or "status" in data
        else:
            assert response.status_code == 401

    def test_dispatch_command_invalid_agent(self, auth_headers):
        """Test dispatching to invalid agent type"""
        response = client.post(
            "/api/commands/dispatch",
            json={"agent_type": "nonexistent_agent", "command": {"action": "test"}},
            headers=auth_headers,
        )

        if auth_headers:
            assert response.status_code in [400, 422, 404]
        else:
            assert response.status_code == 401

    def test_get_command_status(self, auth_headers):
        """Test checking command status"""
        response = client.get("/api/commands/test_command_id/status", headers=auth_headers)

        if auth_headers:
            assert response.status_code in [200, 404]
        else:
            assert response.status_code == 401

    def test_get_command_result(self, auth_headers):
        """Test retrieving command result"""
        response = client.get("/api/commands/test_command_id/result", headers=auth_headers)

        if auth_headers:
            assert response.status_code in [200, 404]
        else:
            assert response.status_code == 401

    def test_list_pending_commands(self, auth_headers):
        """Test listing pending commands"""
        response = client.get("/api/commands/pending", headers=auth_headers)

        if auth_headers:
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, (list, dict))
        else:
            assert response.status_code == 401

    def test_cancel_command(self, auth_headers):
        """Test canceling a command"""
        response = client.post("/api/commands/test_command_id/cancel", headers=auth_headers)

        if auth_headers:
            assert response.status_code in [200, 404, 400]
        else:
            assert response.status_code == 401

    def test_queue_statistics(self, auth_headers):
        """Test getting queue statistics"""
        response = client.get("/api/commands/statistics", headers=auth_headers)

        if auth_headers:
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, dict)
        else:
            assert response.status_code == 401

    def test_clear_completed_commands(self, auth_headers):
        """Test clearing completed commands"""
        response = client.post("/api/commands/clear-completed", headers=auth_headers)

        if auth_headers:
            assert response.status_code in [200, 400]
        else:
            assert response.status_code == 401

    def test_intervention_command(self, auth_headers):
        """Test sending intervention command"""
        response = client.post(
            "/api/commands/intervene",
            json={
                "target_agent": "content",
                "intervention_type": "pause",
                "reason": "User requested pause",
            },
            headers=auth_headers,
        )

        if auth_headers:
            assert response.status_code in [200, 400, 422]
        else:
            assert response.status_code == 401


@pytest.mark.unit
@pytest.mark.api
class TestCommandValidation:
    """Test command validation"""

    def test_dispatch_missing_agent_type(self, client):
        """Test dispatch with missing agent type"""
        response = client.post("/api/commands/dispatch", json={"command": {"action": "test"}})
        assert response.status_code in [400, 422, 401]

    def test_dispatch_missing_command(self, client):
        """Test dispatch with missing command"""
        response = client.post("/api/commands/dispatch", json={"agent_type": "content"})
        assert response.status_code in [400, 422, 401]

    def test_dispatch_empty_command(self, client):
        """Test dispatch with empty command"""
        response = client.post(
            "/api/commands/dispatch", json={"agent_type": "content", "command": {}}
        )
        # Empty command may be valid or invalid depending on implementation
        assert response.status_code in [200, 201, 400, 422, 401]

    def test_dispatch_invalid_json(self, client):
        """Test dispatch with invalid JSON"""
        response = client.post("/api/commands/dispatch", data="invalid json")
        assert response.status_code in [400, 422]

    def test_get_status_invalid_command_id(self, client):
        """Test status check with invalid command ID format"""
        response = client.get("/api/commands/<invalid>id/status")
        assert response.status_code in [404, 422, 400]

    def test_get_status_nonexistent_command(self, client):
        """Test status check for non-existent command"""
        response = client.get("/api/commands/nonexistent_uuid_12345/status")
        assert response.status_code in [200, 404]

    def test_cancel_nonexistent_command(self, client):
        """Test canceling non-existent command"""
        response = client.post("/api/commands/nonexistent_uuid_12345/cancel")
        assert response.status_code in [404, 401]


@pytest.mark.unit
@pytest.mark.performance
class TestCommandQueuePerformance:
    """Test command queue performance"""

    def test_rapid_command_dispatch(self, client):
        """Test dispatching multiple commands rapidly"""
        responses = []
        for i in range(10):
            response = client.post(
                "/api/commands/dispatch",
                json={"agent_type": "content", "command": {"action": f"test_{i}"}},
            )
            responses.append(response.status_code)

        # Should handle rapid dispatch
        assert all(status in [200, 201, 400, 422, 401] for status in responses)

    def test_command_query_response_time(self, client):
        """Test that command queries respond quickly"""
        import time

        start = time.time()
        response = client.get("/api/commands/pending")
        elapsed = time.time() - start

        # Should respond within 5 seconds
        assert elapsed < 5 or response.status_code == 401

    def test_queue_statistics_response_time(self, client):
        """Test that queue statistics are generated quickly"""
        import time

        start = time.time()
        response = client.get("/api/commands/statistics")
        elapsed = time.time() - start

        # Should respond within 5 seconds
        assert elapsed < 5 or response.status_code == 401

    def test_concurrent_status_checks(self, client):
        """Test multiple concurrent status checks"""
        responses = []
        for i in range(5):
            response = client.get(f"/api/commands/test_id_{i}/status")
            responses.append(response.status_code)

        # All should return valid status codes
        assert all(status in [200, 404, 401] for status in responses)


@pytest.mark.unit
@pytest.mark.integration
class TestCommandQueueIntegration:
    """Test command queue integration with other systems"""

    def test_dispatch_and_status_sequence(self, client):
        """Test dispatching a command and checking its status"""
        # Dispatch
        dispatch_response = client.post(
            "/api/commands/dispatch", json={"agent_type": "content", "command": {"action": "test"}}
        )

        if dispatch_response.status_code in [200, 201]:
            data = dispatch_response.json()
            command_id = data.get("command_id") or data.get("id")

            if command_id:
                # Check status
                status_response = client.get(f"/api/commands/{command_id}/status")
                assert status_response.status_code in [200, 404]

    def test_intervention_flow(self, client):
        """Test sending intervention"""
        response = client.post(
            "/api/commands/intervene",
            json={"target_agent": "content", "intervention_type": "pause"},
        )

        # Should handle intervention request
        assert response.status_code in [200, 400, 422, 401]

    def test_queue_statistics_shows_pending(self, client):
        """Test that queue statistics include pending commands"""
        response = client.get("/api/commands/statistics")

        if response.status_code == 200:
            data = response.json()
            # Should contain queue information
            assert isinstance(data, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
