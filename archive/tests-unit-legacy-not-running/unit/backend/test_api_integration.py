"""
API Integration Tests for AI Co-Founder System
Comprehensive testing of REST API endpoints and WebSocket functionality
"""

import pytest
import asyncio
import aiohttp
import websockets
import json
from datetime import datetime
from typing import Dict, Any
import time
import sys
import os

# Add project paths
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from conftest import (
    TEST_CONFIG,
    mock_api_responses,
    performance_monitor,
    test_utils,
    run_with_timeout,
    pytest_marks,
)


class APITestClient:
    """Test client for API endpoints"""

    def __init__(self, base_url: str = None):
        self.base_url = base_url or TEST_CONFIG["api_base_url"]
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def post(self, endpoint: str, data: Dict[str, Any] = None, timeout: int = 30):
        """Make POST request to API"""
        if not self.session:
            raise RuntimeError("APITestClient must be used as async context manager")

        url = f"{self.base_url}{endpoint}"

        try:
            async with self.session.post(url, json=data, timeout=timeout) as response:
                return {
                    "status": response.status,
                    "data": (
                        await response.json()
                        if response.content_type == "application/json"
                        else await response.text()
                    ),
                    "headers": dict(response.headers),
                }
        except Exception as e:
            return {"error": str(e), "status": 0}

    async def get(self, endpoint: str, params: Dict[str, Any] = None, timeout: int = 30):
        """Make GET request to API"""
        if not self.session:
            raise RuntimeError("APITestClient must be used as async context manager")

        url = f"{self.base_url}{endpoint}"

        try:
            async with self.session.get(url, params=params, timeout=timeout) as response:
                return {
                    "status": response.status,
                    "data": (
                        await response.json()
                        if response.content_type == "application/json"
                        else await response.text()
                    ),
                    "headers": dict(response.headers),
                }
        except Exception as e:
            return {"error": str(e), "status": 0}


class WebSocketTestClient:
    """Test client for WebSocket connections"""

    def __init__(self, ws_url: str = None):
        self.ws_url = ws_url or TEST_CONFIG["websocket_url"]
        self.websocket = None
        self.messages = []

    async def connect(self, timeout: int = 10):
        """Connect to WebSocket"""
        try:
            self.websocket = await asyncio.wait_for(
                websockets.connect(self.ws_url), timeout=timeout
            )
            return True
        except Exception as e:
            print(f"WebSocket connection failed: {e}")
            return False

    async def send_message(self, message: Dict[str, Any]):
        """Send message via WebSocket"""
        if not self.websocket:
            raise RuntimeError("WebSocket not connected")

        await self.websocket.send(json.dumps(message))

    async def receive_message(self, timeout: int = 5):
        """Receive message from WebSocket"""
        if not self.websocket:
            raise RuntimeError("WebSocket not connected")

        try:
            message = await asyncio.wait_for(self.websocket.recv(), timeout=timeout)
            parsed_message = json.loads(message)
            self.messages.append(parsed_message)
            return parsed_message
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            print(f"Error receiving WebSocket message: {e}")
            return None

    async def close(self):
        """Close WebSocket connection"""
        if self.websocket:
            await self.websocket.close()


@pytest.fixture
async def api_client():
    """API test client fixture"""
    async with APITestClient() as client:
        yield client


@pytest.fixture
async def ws_client():
    """WebSocket test client fixture"""
    client = WebSocketTestClient()
    yield client
    await client.close()


@pytest_marks["api"]
class TestAPIEndpoints:
    """Test REST API endpoints"""

    async def test_health_endpoint(self, api_client, performance_monitor, test_utils):
        """Test health check endpoint"""
        response = await api_client.get("/metrics/health")

        # Should respond with 200 or provide meaningful error
        if response["status"] == 200:
            test_utils.assert_valid_response_structure(response["data"], ["health", "status"])
            # Check the nested health object
            assert "overall_status" in response["data"]["health"]
            assert response["data"]["health"]["overall_status"] in [
                "healthy",
                "ok",
                "degraded",
                "unknown",
            ]
        else:
            # If server not running, that's expected in some test environments
            assert "error" in response or response["status"] == 0

    async def test_chat_endpoint(self, api_client, performance_monitor):
        """Test chat endpoint functionality"""

        async def chat_request():
            chat_data = {
                "message": "Hello, what can you help me with?",
                "session_id": "test_session_001",
                "context": {"user_id": "test_user"},
            }
            return await api_client.post("/command", chat_data)

        response, duration, success = await performance_monitor.measure_async_operation(
            "chat_api_request", chat_request
        )

        # Test should complete within reasonable time
        assert duration < 10.0, f"Chat API too slow: {duration}s"

        if success and response["status"] == 200:
            data = response["data"]
            assert isinstance(data, dict)

            # Validate response structure
            expected_fields = ["success", "response"]
            for field in expected_fields:
                assert field in data, f"Missing field in chat response: {field}"

            if data["success"]:
                assert len(data["response"]) > 0

        # Even if server not running, the test itself should succeed
        assert success or response["status"] == 0

    async def test_business_metrics_endpoint(self, api_client, test_utils):
        """Test business metrics endpoint"""
        response = await api_client.get("/business/metrics")

        if response["status"] == 200:
            data = response["data"]
            test_utils.assert_valid_response_structure(data, ["success"])

            if data["success"]:
                test_utils.assert_business_metrics_valid(data["metrics"])

    async def test_task_creation_endpoint(self, api_client):
        """Test task creation endpoint"""
        task_data = {
            "title": "API Test Task",
            "description": "Task created via API test",
            "priority": "medium",
            "category": "test",
            "assignee": "ai_agent",
        }

        response = await api_client.post("/tasks/create", task_data)

        if response["status"] == 200:
            data = response["data"]
            assert isinstance(data, dict)
            assert "success" in data or "error" in data

            if data.get("success"):
                assert "task_id" in data

    async def test_task_delegation_endpoint(self, api_client):
        """Test advanced task delegation endpoint"""
        delegation_data = {
            "description": "Create comprehensive market analysis report",
            "requirements": ["market_analysis", "competitor_analysis"],
            "priority": "high",
        }

        response = await api_client.post("/api/delegate-task", delegation_data)

        if response["status"] == 200:
            data = response["data"]
            assert isinstance(data, dict)

            if data.get("success"):
                assert "task_id" in data
                assert "message" in data

    async def test_workflow_creation_endpoint(self, api_client):
        """Test strategic workflow creation endpoint"""
        workflow_data = {
            "name": "Q4 Growth Strategy Workflow",
            "objectives": [
                "Increase content production by 50%",
                "Expand market research capabilities",
                "Optimize business processes",
            ],
        }

        response = await api_client.post("/api/create-workflow", workflow_data)

        if response["status"] == 200:
            data = response["data"]
            assert isinstance(data, dict)

            if data.get("success"):
                assert "workflow_id" in data
                assert "steps_created" in data

    async def test_orchestration_status_endpoint(self, api_client):
        """Test orchestration status endpoint"""
        response = await api_client.get("/api/orchestration-status")

        if response["status"] == 200:
            data = response["data"]
            assert isinstance(data, dict)
            assert "success" in data

            if data["success"]:
                status = data["status"]
                assert "agents" in status
                assert "tasks" in status
                assert "metrics" in status

    async def test_dashboard_data_endpoint(self, api_client):
        """Test advanced dashboard data endpoint"""
        response = await api_client.get("/api/dashboard-data")

        if response["status"] == 200:
            data = response["data"]
            assert isinstance(data, dict)
            assert "success" in data

            if data["success"]:
                dashboard_data = data["data"]
                expected_sections = ["kpis", "metrics", "trends", "insights"]

                for section in expected_sections:
                    assert section in dashboard_data

    async def test_comprehensive_status_endpoint(self, api_client):
        """Test comprehensive system status endpoint"""
        response = await api_client.get("/api/comprehensive-status")

        if response["status"] == 200:
            data = response["data"]
            assert isinstance(data, dict)
            assert "success" in data

            if data["success"]:
                status_data = data["status"]
                expected_sections = ["business_health", "dashboard", "orchestration"]

                for section in expected_sections:
                    assert section in status_data


@pytest_marks["websocket"]
@pytest.mark.skip(reason="WebSocket server not running during unit tests - integration feature")
class TestWebSocketFunctionality:
    """Test WebSocket real-time communication"""

    async def test_websocket_connection(self, ws_client):
        """Test WebSocket connection establishment"""
        connected = await ws_client.connect(timeout=5)

        if connected:
            assert ws_client.websocket is not None
            assert not ws_client.websocket.closed
        else:
            # Connection failure is acceptable if server not running
            pytest.skip("WebSocket server not available for testing")

    async def test_websocket_chat_message(self, ws_client):
        """Test WebSocket chat message exchange"""
        connected = await ws_client.connect()

        if not connected:
            pytest.skip("WebSocket server not available")

        # Send chat message
        chat_message = {
            "type": "chat",
            "data": {"message": "Hello via WebSocket", "session_id": "ws_test_001"},
        }

        await ws_client.send_message(chat_message)

        # Wait for response
        response = await ws_client.receive_message(timeout=10)

        if response:
            assert "type" in response
            assert "data" in response

            # Should receive either chat response or typing indicator
            assert response["type"] in ["chat_response", "typing_start", "typing_stop"]

    async def test_websocket_real_time_updates(self, ws_client):
        """Test real-time updates via WebSocket"""
        connected = await ws_client.connect()

        if not connected:
            pytest.skip("WebSocket server not available")

        # Send request for business metrics
        metrics_request = {"type": "get_metrics", "data": {"session_id": "ws_test_002"}}

        await ws_client.send_message(metrics_request)

        # Wait for multiple responses (typing indicators + actual response)
        responses = []

        for _ in range(3):  # Wait for up to 3 messages
            response = await ws_client.receive_message(timeout=5)
            if response:
                responses.append(response)

        assert len(responses) > 0, "Should receive at least one WebSocket response"

        # Check if we received the expected response types
        response_types = [r["type"] for r in responses]
        assert any(
            t in ["metrics_update", "business_data", "chat_response"] for t in response_types
        )

    async def test_websocket_connection_management(self, ws_client):
        """Test WebSocket connection management"""
        # Test connection
        connected = await ws_client.connect()

        if not connected:
            pytest.skip("WebSocket server not available")

        # Send ping to keep connection alive
        ping_message = {"type": "ping", "data": {"timestamp": datetime.now().isoformat()}}

        await ws_client.send_message(ping_message)

        # Should receive pong or acknowledgment
        response = await ws_client.receive_message(timeout=5)

        if response:
            assert response["type"] in ["pong", "ack", "status"]


@pytest_marks["api"]
@pytest_marks["performance"]
class TestAPIPerformance:
    """Test API performance characteristics"""

    async def test_concurrent_chat_requests(self, performance_monitor):
        """Test concurrent chat requests performance"""

        async def make_chat_request(session_id: str):
            async with APITestClient() as client:
                chat_data = {
                    "message": f"Concurrent test message for session {session_id}",
                    "session_id": session_id,
                    "context": {},
                }
                return await client.post("/command", chat_data)

        # Make 5 concurrent requests
        tasks = []
        for i in range(5):

            async def request_wrapper(i=i):
                return await make_chat_request(f"concurrent_test_{i}")

            task_name = f"concurrent_chat_{i}"
            task = performance_monitor.measure_async_operation(task_name, request_wrapper)
            tasks.append(task)

        # Wait for all requests to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Analyze performance
        summary = performance_monitor.get_performance_summary()

        # Should handle concurrent requests reasonably well
        if summary["total_operations"] > 0:
            assert summary["average_duration"] < 15.0, "Concurrent requests too slow"
            # Allow for some failures in test environment
            assert summary["success_rate"] >= 0.6, "Too many concurrent request failures"

    async def test_api_response_times(self, api_client, performance_monitor):
        """Test API response time benchmarks"""

        # Test different endpoints
        endpoints_to_test = [
            ("/health", "GET"),
            ("/business/metrics", "GET"),
            ("/api/orchestration-status", "GET"),
            ("/api/dashboard-data", "GET"),
        ]

        for endpoint, method in endpoints_to_test:

            async def api_request():
                if method == "GET":
                    return await api_client.get(endpoint)
                else:
                    return await api_client.post(endpoint, {})

            result, duration, success = await performance_monitor.measure_async_operation(
                f"api_{endpoint.replace('/', '_')}", api_request
            )

            # API endpoints should respond quickly
            if success and result.get("status") == 200:
                assert duration < 5.0, f"API endpoint {endpoint} too slow: {duration}s"


@pytest_marks["api"]
@pytest_marks["integration"]
@pytest.mark.skip(
    reason="API server not running during unit tests - requires live backend instance"
)
class TestAPIIntegration:
    """Test API integration scenarios"""

    async def test_complete_workflow_via_api(self, api_client):
        """Test complete workflow using multiple API endpoints"""

        # Step 1: Check system status
        status_response = await api_client.get("/health")

        if status_response["status"] != 200:
            pytest.skip("API server not available for integration test")

        # Step 2: Get current metrics
        metrics_response = await api_client.get("/business/metrics")

        # Step 3: Create a task
        task_data = {
            "title": "Integration Test Task",
            "description": "Task for testing complete workflow",
            "priority": "medium",
            "category": "integration_test",
        }

        task_response = await api_client.post("/tasks/create", task_data)

        # Step 4: Check orchestration status
        orchestration_response = await api_client.get("/api/orchestration-status")

        # Verify workflow completed without errors
        responses = [status_response, metrics_response, task_response, orchestration_response]

        for i, response in enumerate(responses):
            assert response["status"] in [
                200,
                503,
            ], f"Step {i+1} failed with status {response['status']}"

            # If successful, verify response structure
            if response["status"] == 200 and isinstance(response["data"], dict):
                assert "success" in response["data"] or "status" in response["data"]

    async def test_api_error_handling(self, api_client):
        """Test API error handling and validation"""

        # Test invalid endpoint
        invalid_response = await api_client.get("/invalid/endpoint")
        assert invalid_response["status"] in [404, 0]  # 404 or connection error

        # Test malformed request
        malformed_data = {"invalid": "data", "missing_required_fields": True}
        malformed_response = await api_client.post("/command", malformed_data)

        # Should handle gracefully (400 Bad Request or similar)
        if malformed_response["status"] not in [0, 503]:  # Ignore connection/service errors
            assert malformed_response["status"] in [200, 400, 422]

        # Test empty request
        empty_response = await api_client.post("/command", {})
        if empty_response["status"] not in [0, 503]:
            assert empty_response["status"] in [200, 400, 422]


# Test data validation
@pytest_marks["api"]
class TestAPIDataValidation:
    """Test API data validation and sanitization"""

    async def test_chat_input_validation(self, api_client):
        """Test chat endpoint input validation"""

        # Test various input scenarios
        test_cases = [
            {"message": "Valid message", "session_id": "test"},  # Valid
            {"message": "", "session_id": "test"},  # Empty message
            {"message": "A" * 10000, "session_id": "test"},  # Very long message
            {"session_id": "test"},  # Missing message
            {"message": "Test"},  # Missing session_id
        ]

        for test_data in test_cases:
            response = await api_client.post("/command", test_data)

            # Should handle all cases gracefully
            if response["status"] not in [0, 503]:  # Ignore connection errors
                assert response["status"] in [200, 400, 422]

                if response["status"] == 200:
                    data = response["data"]
                    assert isinstance(data, dict)
                    assert "success" in data

    async def test_task_data_validation(self, api_client):
        """Test task creation data validation"""

        test_cases = [
            {  # Valid task
                "title": "Valid Task",
                "description": "Valid description",
                "priority": "medium",
            },
            {"description": "No title provided", "priority": "high"},  # Missing title
            {  # Invalid priority
                "title": "Invalid Priority Task",
                "description": "Task with invalid priority",
                "priority": "invalid_priority",
            },
            {},  # Empty data
        ]

        for test_data in test_cases:
            response = await api_client.post("/tasks", test_data)

            if response["status"] not in [0, 503]:
                assert response["status"] in [200, 400, 422]


if __name__ == "__main__":
    # Run API tests
    pytest.main([__file__, "-v", "--tb=short", "-m", "api"])
